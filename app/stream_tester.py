"""StreamTester 模块 — 增强版流媒体测试类

从 app/__init__.py 中提取的独立模块，负责：
- 使用 ffprobe / ffmpeg 测试流媒体源连通性与质量
- 提取视频/音频元数据（分辨率、编码、帧率、比特率等）
- 批量并发测试（ThreadPoolExecutor + Semaphore 限流）
- 看门狗定时器防止批量测试挂起
- 指数退避重试机制
- 测试结果缓存（线程安全）
- 质量合格性评估（延迟 / 分辨率 / 比特率 / 速度）
"""

import concurrent.futures
import contextlib
import json
import multiprocessing
import os
import re
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta

from app.config import Config
from app.exceptions import StreamTestError


def _classify_stream_error(msg: str) -> str:
    """将 ffprobe/ffmpeg 的 stderr 错误文本归类为可读的诊断类别。

    避免测试把所有失败都叫 'ffprobe_error: Unknown error'（比如源是运营商
    IPTV 内网/CDN、从本机根本连不上时），从而把「网络不可达」与「ffprobe 真崩了」
    区分开，便于排查。
    """
    if not msg:
        return 'ffprobe_failed_no_output'
    t = msg.lower()
    # 连接被拒
    if 'connection refused' in t or 'error number -111' in t:
        return 'connection_refused'
    # 连接超时 / 不可达（运营商内网 CDN 典型表现）
    if (
        'connection timed out' in t
        or 'timed out' in t
        or 'error number -138' in t
        or 'network unreachable' in t
        or 'no route' in t
        or 'error number -101' in t
        or 'host is down' in t
        or 'error number -64' in t
        or 'connection failed' in t
        or 'could not connect' in t
        or 'failed: connection' in t
    ):
        return 'connection_failed'
    # DNS 解析失败
    if (
        'name or service not known' in t
        or 'could not resolve' in t
        or 'nodename nor servname' in t
        or 'getaddrinfo' in t
        or 'resolve' in t
        or 'dns error' in t
    ):
        return 'dns_failed'
    # 鉴权 / 防盗链（如腾讯云 txSecret 过期）
    if (
        '403' in t
        or '401' in t
        or 'forbidden' in t
        or 'unauthorized' in t
        or 'expired' in t
        or 'txsecret' in t
        or 'txtime' in t
    ):
        return 'auth_blocked'
    # 资源不存在
    if '404' in t or 'not found' in t or 'no such' in t:
        return 'not_found'
    return 'ffprobe_error'


# 第三方库可选导入（try/except 保证模块在缺少依赖时仍可加载）
try:
    import aiohttp
except ImportError:
    aiohttp = None

try:
    import aiofiles
except ImportError:
    aiofiles = None

try:
    import aiohttp_socks
except ImportError:
    aiohttp_socks = None


class StreamTester:
    """增强版流媒体测试类

    类变量:
        _ffprobe_verified: 类级缓存 ffprobe 可用性（N-5: 避免每次实例化都跑子进程）
        _ffprobe_path: ffprobe 可执行文件的完整路径
        _ffmpeg_path: ffmpeg 可执行文件的完整路径
    """

    _ffprobe_verified = None
    _ffprobe_path = None
    _ffmpeg_path = None

    @classmethod
    def _find_executable(cls, name: str) -> str | None:
        """查找可执行文件的完整路径

        搜索顺序:
        1. tools/ffmpeg/ 目录（项目本地）
        2. 系统 PATH
        3. 常见安装目录
        4. imageio-ffmpeg pip 包（仅 ffmpeg）
        5. static_ffmpeg pip 包（ffmpeg + ffprobe）
        """
        # 1. 项目本地目录
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local_path = os.path.join(project_root, 'tools', 'ffmpeg', f'{name}.exe')
        if os.path.exists(local_path):
            return local_path

        # 2. 系统 PATH
        for path_dir in os.environ.get('PATH', '').split(os.pathsep):
            exe_path = os.path.join(path_dir, f'{name}.exe')
            if os.path.exists(exe_path):
                return exe_path

        # 3. 常见安装目录（Windows）
        common_dirs = [
            'C:\\Program Files\\ffmpeg\\bin',
            'C:\\Program Files (x86)\\ffmpeg\\bin',
            os.path.expanduser('~\\ffmpeg\\bin'),
        ]
        for dir_path in common_dirs:
            exe_path = os.path.join(dir_path, f'{name}.exe')
            if os.path.exists(exe_path):
                return exe_path

        # 4. imageio-ffmpeg pip 包（仅提供 ffmpeg）
        if name == 'ffmpeg':
            try:
                import imageio_ffmpeg

                exe = imageio_ffmpeg.get_ffmpeg_exe()
                if exe and os.path.exists(exe):
                    return exe
            except Exception:
                pass

        # 5. static_ffmpeg pip 包（提供 ffmpeg + ffprobe）
        try:
            import static_ffmpeg

            ffmpeg, ffprobe = static_ffmpeg.run.get_or_fetch_platform_executables_else_raise()
            target = ffprobe if name == 'ffprobe' else ffmpeg
            if target and os.path.exists(target):
                return target
        except Exception:
            pass

        return None

    def __init__(self, config: Config, logger):
        """初始化测试器

        Args:
            config: 配置管理器实例
            logger: 日志记录器实例
        """
        self.config = config
        self.logger = logger
        self.testing_params = config.get_testing_params()
        self.filter_params = config.get_filter_params()

        # 实例级缓存（替代模块级全局变量）
        self._url_cache = {}
        self._last_cache_cleanup = datetime.now()
        self._CACHE_CLEANUP_INTERVAL = 300
        self._cache_lock = threading.Lock()

        # ---- 纪码增强：看门狗定时器 ----
        self._watchdog_timer = None
        self._watchdog_triggered = False
        self._active_testing = False
        self._watchdog_timeout = self.testing_params.get('timeout', 10) * 2
        # 纪码修复 P1-2: 跟踪所有活跃 future 以便看门狗超时时取消
        self._active_futures = set()
        self._active_futures_lock = threading.Lock()

        # ---- 实时测试次数开关：1=每个地址测一次; 2=测两次(含1次重试); 默认1 ----
        # 配置值语义为「总测试次数」，转换为重试次数 = 次数 - 1，并钳制到 [1,2]
        _attempts = self.config.getint('Testing', 'max_test_attempts', 1)
        if _attempts < 1:
            _attempts = 1
        elif _attempts > 2:
            _attempts = 2
        self.max_retries = _attempts - 1

        # ---- 纪码增强：细化超时层级 ----
        self.connect_timeout = self._get_config_timeout('connect_timeout', 8)
        self.read_timeout = self._get_config_timeout('read_timeout', 10)
        self.probe_timeout = self._get_config_timeout('ffprobe_timeout', self.testing_params['timeout'])

        # ffprobe可用性标志（初始化为True，_verify_ffprobe失败时设为False）
        self.ffprobe_available = True
        # 是否支持 -rw_timeout（细化 read 超时，_verify_ffprobe 探测）
        self._ffprobe_supports_rw_timeout = False

        # ---- 纪枢方案 F-8: Semaphore 限制 ffprobe 子进程并发数 ----
        max_ffprobe = int(self._get_config_timeout('max_concurrent_ffprobe', 4))
        self._ffprobe_semaphore = threading.Semaphore(max_ffprobe)

        # ---- 实时测试中断机制：供 Web 层「暂停/取消」立即终止正在运行的子进程 ----
        self._abort = threading.Event()
        self._proc_lock = threading.Lock()
        self._active_procs = []

        # ---- P0（对标 Guovin/iptv-api）：同 Host 测速复用 ----
        # 同 CDN/Host 仅 ffprobe 一次，其余同 Host 源直接复用结果，ffprobe 调用可降一个数量级
        self._host_speed_share = bool(self.testing_params.get('enable_host_speed_share', True))
        self._host_speed_cache = {}  # host -> {status, response_time, metadata, timestamp}
        self._host_cache_lock = threading.Lock()

        # ---- P0（对标 Guovin/iptv-api）：失败源指数退避冻结 ----
        # 连续失败的源按 2^n × base 秒指数退避拉黑冷却，避免每次全量重测所有死源浪费资源
        self._source_freeze = bool(self.testing_params.get('enable_source_freeze', True))
        self._freeze_fail_threshold = int(self.testing_params.get('freeze_fail_threshold', 3))
        self._freeze_base_seconds = int(self.testing_params.get('freeze_base_seconds', 60))
        self._freeze_max_seconds = int(self.testing_params.get('freeze_max_hours', 24)) * 3600
        self._status_dir = self._resolve_status_dir()
        self._frozen_map = self._load_frozen_map()  # norm_url -> {fail_count, frozen_until}
        self._frozen_map_lock = threading.Lock()

        # ---- P1/P2（对标 Guovin/iptv-api）：广告检测 + 全局黑白名单 ----
        # 广告/循环占位源检测：成功 ffprobe 后额外拉取 m3u8 头部检查关键字与循环标志
        self._ad_enabled = bool(self.testing_params.get('enable_ad_detect', True))
        self._ad_keywords = self._parse_filter_list(self.testing_params.get('ad_keywords', ''))
        self._ad_max_duration = int(self.testing_params.get('ad_max_duration', 90))
        # 全局黑白名单：URL 或 host 命中
        self._blacklist = self._parse_filter_list(self.testing_params.get('global_blacklist', ''))
        self._whitelist = self._parse_filter_list(self.testing_params.get('global_whitelist', ''))

        # 验证ffprobe可用性
        self._verify_ffprobe()

    # ────────────────────────────────────────────────
    # 实时测试中断控制（供 Web 层暂停/取消立即生效）
    # ────────────────────────────────────────────────
    def abort(self) -> None:
        """立即终止当前所有正在运行的 ffprobe/ffmpeg 子进程。"""
        self._abort.set()
        self.terminate_active_procs()

    def clear_abort(self) -> None:
        """清除中断标志，允许后续测试正常进行（恢复测试时调用）。"""
        self._abort.clear()

    def terminate_active_procs(self) -> None:
        """终止所有已记录的活跃子进程（幂等，对已结束进程无副作用）。

        关键点：ffprobe/ffmpeg 可能派生子进程（实际解码/I/O 进程）并继承管道。
        仅 terminate 父进程时，子进程仍持有 stdout/stderr 管道，会导致调用方
        proc.communicate() 阻塞直到超时——这正是「暂停/取消不立即生效」的根因。
        因此优先杀掉整个进程树（父+子），确保管道立即关闭、communicate 立即返回。
        """
        with self._proc_lock:
            procs = list(self._active_procs)
        for p in procs:
            self._kill_proc_tree(p)

    @staticmethod
    def _kill_proc_tree(proc) -> None:
        """递归杀掉 subprocess.Popen 进程及其所有子进程。"""
        # 先杀子进程（若 ffprobe 派生子进程持有管道），再杀父进程
        try:
            import psutil

            try:
                parent = psutil.Process(proc.pid)
                children = parent.children(recursive=True)
            except Exception:
                children = []
            for c in children:
                with contextlib.suppress(Exception):
                    c.kill()
        except Exception:
            pass
        # 父进程：优先 terminate（SIGTERM/TerminateProcess），失败再 kill
        try:
            proc.terminate()
        except Exception:
            with contextlib.suppress(Exception):
                proc.kill()

    def _verify_ffprobe(self):
        """验证ffprobe工具是否可用（N-5: 类级缓存避免重复子进程）

        优先查找 ffprobe，找不到则查找 ffmpeg 作为降级方案。
        失败时仅记录警告并不阻断启动，后续流测试会因ffprobe不可用
        而返回合理的降级结果（纪枢 B-2）。
        """
        if StreamTester._ffprobe_verified is not None:
            self.ffprobe_available = StreamTester._ffprobe_verified
            return

        # 查找 ffprobe 可执行文件
        if StreamTester._ffprobe_path is None:
            StreamTester._ffprobe_path = self._find_executable('ffprobe')

        # 查找 ffmpeg 可执行文件（作为降级方案）
        if StreamTester._ffmpeg_path is None:
            StreamTester._ffmpeg_path = self._find_executable('ffmpeg')

        if StreamTester._ffprobe_path:
            try:
                result = subprocess.run(
                    [StreamTester._ffprobe_path, '-version'],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    self.logger.info(f'✓ FFprobe工具验证成功: {StreamTester._ffprobe_path}')
                    StreamTester._ffprobe_verified = True
                    self.ffprobe_available = True
                    # 探测 -rw_timeout 支持（用于细化 read 超时）
                    try:
                        h = subprocess.run(
                            [StreamTester._ffprobe_path, '-h', 'full'],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        out = f'{h.stdout or ""}{h.stderr or ""}'
                        self._ffprobe_supports_rw_timeout = 'rw_timeout' in out
                    except Exception:
                        self._ffprobe_supports_rw_timeout = False
                    if self._ffprobe_supports_rw_timeout:
                        self.logger.info('✓ FFprobe 支持 -rw_timeout（细化 read 超时生效）')
                else:
                    self.logger.warning(f'⚠ FFprobe执行返回非零: {result.stderr}')
                    # 不立即设为 False——先尝试 ffmpeg 降级（见下方）
                    self.ffprobe_available = False
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
                self.logger.warning(f'⚠ FFprobe工具不可用: {e}')
                self.ffprobe_available = False
        else:
            # ffprobe 未找到 → 标记不可用，后续尝试 ffmpeg 降级
            self.ffprobe_available = False
        # 注意：此处将原 elif 改为 if——即使 ffprobe 路径存在但验证失败，
        # 也应尝试 ffmpeg 降级，避免"找到但坏掉的 ffprobe"阻断所有测试
        if not self.ffprobe_available and StreamTester._ffmpeg_path:
            # 降级：ffprobe 不可用或验证失败，尝试 ffmpeg
            try:
                result = subprocess.run(
                    [StreamTester._ffmpeg_path, '-version'],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    self.logger.info(f'✓ FFprobe不可用，降级使用FFmpeg: {StreamTester._ffmpeg_path}')
                    StreamTester._ffprobe_verified = True
                    self.ffprobe_available = True
                else:
                    self.logger.warning('⚠ FFmpeg执行返回非零，流测试不可用')
                    StreamTester._ffprobe_verified = False
                    self.ffprobe_available = False
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
                self.logger.warning(f'⚠ FFmpeg工具不可用: {e}')
                StreamTester._ffprobe_verified = False
                self.ffprobe_available = False
        else:
            self.logger.warning('⚠ FFprobe和FFmpeg均未找到，流测试不可用')
            StreamTester._ffprobe_verified = False
            self.ffprobe_available = False

    def _get_config_timeout(self, key: str, default: int) -> int:
        """从配置获取细化超时值"""
        return self.config.getint('Testing', key, default)

    def test_all_sources(self, sources: list[dict]) -> list[dict]:
        """批量测试所有流媒体源

        实现分层测试策略:
        1. 基础连通性测试
        2. 详细质量分析(如果基础测试通过)
        3. 质量合格性评估

        Args:
            sources: 源数据列表，每个源包含name、url等基本信息

        Returns:
            List[Dict]: 包含测试结果的源数据列表
        """
        # 清理过期缓存
        self.cleanup_cache()

        # ---- 纪码增强：启动看门狗定时器 ----
        self._start_watchdog()
        self._active_testing = True

        total_sources = len(sources)
        self.logger.info(f'开始测试 {total_sources} 个流媒体源')
        self.logger.info(f'并发线程数: {self.testing_params["concurrent_threads"]}')
        self.logger.info(f'测试超时: {self.testing_params["timeout"]}秒')

        # 纪码修复 P1-1: 动态调整并发线程数以匹配 ffprobe Semaphore 上限
        # 避免 ThreadPoolExecutor(max_workers=40) 中 36 个线程阻塞在 Semaphore(4) 上浪费资源
        max_workers = self._calculate_optimal_workers()
        ffprobe_max = int(self._get_config_timeout('max_concurrent_ffprobe', 4))
        if max_workers > ffprobe_max * 2:
            adjusted = min(max_workers, int(ffprobe_max * 1.5))
            self.logger.info(f'并发线程数从 {max_workers} 调整为 {adjusted}（ffprobe 并发上限 {ffprobe_max}）')
            max_workers = adjusted

        # 创建进度显示
        try:
            from tqdm import tqdm

            pbar = tqdm(total=total_sources, desc='测试流媒体源', unit='源')
            use_tqdm = True
        except ImportError:
            self.logger.warning('tqdm模块未安装，使用简单进度显示')
            pbar = None
            use_tqdm = False

        test_results = []
        successful_count = 0
        failed_count = 0

        # 使用线程池执行并发测试
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有测试任务
            future_to_source = {}
            for source in sources:
                future = executor.submit(self.test_single_stream, source)
                future_to_source[future] = source
                # 纪码修复 P1-2: 跟踪活跃 future 供看门狗超时取消
                with self._active_futures_lock:
                    self._active_futures.add(future)

            # 处理完成的任务
            for future in concurrent.futures.as_completed(future_to_source):
                # 纪码修复 P1-2: 从活跃集合中移除已完成的 future
                with self._active_futures_lock:
                    self._active_futures.discard(future)
                source = future_to_source[future]
                try:
                    # 获取测试结果，设置超时防止线程挂起
                    result = future.result(timeout=self.testing_params['timeout'] + 15)
                    test_results.append(result)

                    # 质量合格性检查
                    is_qualified = self.check_if_qualified(result)
                    result['is_qualified'] = is_qualified

                    # 统计和日志记录
                    if result.get('status') == 'success':
                        successful_count += 1
                        log_level = 'info' if is_qualified else 'warning'
                    elif result.get('status') == 'frozen':
                        # 冻结属预期冷却，计入失败但不刷 error 日志
                        failed_count += 1
                        log_level = 'info'
                    else:
                        failed_count += 1
                        log_level = 'error'

                    # 记录详细测试结果
                    self.log_test_result(source, result, log_level)

                    # 更新进度显示
                    if use_tqdm:
                        pbar.update(1)
                        status_info = f'有效:{successful_count} 失败:{failed_count}'
                        pbar.set_postfix_str(status_info)
                    else:
                        if len(test_results) % 10 == 0:  # 每10个源输出一次进度
                            self.logger.info(f'测试进度: {len(test_results)}/{total_sources}')

                except concurrent.futures.TimeoutError:
                    # 处理测试超时
                    self.logger.error(f'测试超时: {source["name"]} - {source["url"]}')
                    timeout_result = {
                        **source,
                        'status': 'timeout',
                        'response_time': None,
                        'is_qualified': False,
                    }
                    test_results.append(timeout_result)
                    failed_count += 1

                    if use_tqdm:
                        pbar.update(1)
                except Exception as e:
                    # 处理其他异常
                    self.logger.error(f'测试异常 {source["name"]}: {e}')
                    error_result = {
                        **source,
                        'status': 'error',
                        'response_time': None,
                        'is_qualified': False,
                    }
                    test_results.append(error_result)
                    failed_count += 1

                    if use_tqdm:
                        pbar.update(1)

        # 关闭进度条
        if use_tqdm:
            pbar.close()

        # 输出测试统计
        qualified_count = sum(1 for r in test_results if r.get('is_qualified'))
        # C4: 修复 ZeroDivisionError 风险
        total_pct = total_sources if total_sources > 0 else 1
        self.logger.info('测试完成统计:')
        self.logger.info(f'  - 总测试数: {total_sources}')
        self.logger.info(f'  - 成功数: {successful_count} ({successful_count / total_pct * 100:.1f}%)')
        self.logger.info(f'  - 合格数: {qualified_count} ({qualified_count / total_pct * 100:.1f}%)')
        self.logger.info(f'  - 失败数: {failed_count} ({failed_count / total_pct * 100:.1f}%)')

        # ---- P0-②：持久化冻结状态（跨进程保留）----
        if self._source_freeze:
            self._save_frozen_map()

        # ---- 纪码增强：停止看门狗 ----
        self._stop_watchdog()
        self._active_testing = False
        if self._watchdog_triggered:
            self.logger.warning(f'看门狗曾在 {self._watchdog_timeout}s 超时后触发，部分测试可能被强制终止')

        return test_results

    def _calculate_optimal_workers(self) -> int:
        """计算最优并发工作线程数

        基于系统资源和配置参数动态计算

        Returns:
            int: 推荐的并发线程数
        """
        # 获取配置的并发数
        config_workers = self.testing_params['concurrent_threads']

        # 获取系统CPU核心数
        cpu_cores = multiprocessing.cpu_count()

        # 计算基于系统资源的最大建议数
        system_max_workers = min(cpu_cores * 4, 50)  # 限制最大50线程

        # 取配置值和系统建议值的最小值
        optimal_workers = min(config_workers, system_max_workers)

        self.logger.debug(f'并发优化: 配置={config_workers}, CPU核心={cpu_cores}, 最终={optimal_workers}')
        return optimal_workers

    def test_single_stream(self, source: dict) -> dict:
        """测试单个流媒体源 - 纪码增强（指数退避重试 + 资源清理）

        实现智能测试流程:
        1. 缓存检查避免重复测试
        2. 基础连通性测试
        3. 详细流媒体分析(如果连通)
        4. 速度测试(如果配置启用)

        增强（纪码追加）：
        - 指数退避重试：失败/超时后自动重试，间隔 2^retry * 0.5 秒
        - 细化超时层级：connect_timeout / read_timeout / probe_timeout
        - finally 资源清理：关闭所有 socket 和文件句柄

        Args:
            source: 源数据字典，包含url、name等信息

        Returns:
            Dict: 包含测试结果的源数据

        Raises:
            StreamTestError: 测试过程发生不可恢复错误时抛出
        """
        url = source['url']
        user_agent = source.get('user_agent')
        url_norm = self.normalize_url(url)
        host = self._extract_host(url)

        # ---- P2-⑥：全局黑白名单（测试最优先拦截）----
        # 用原始 url 匹配（normalize_url 会编码查询参数，子串黑名单可能漏匹配）
        if self._whitelist and self._url_in_list(url, self._whitelist):
            pass  # 白名单：不跳过
        elif self._blacklist and self._url_in_list(url, self._blacklist):
            self.logger.info(f'源命中全局黑名单，跳过测试: {source.get("name", "")}')
            return {
                **source,
                'status': 'blacklisted',
                'response_time': None,
                'is_qualified': False,
                'error_reason': 'global_blacklist',
            }

        try:
            # ---- P0-②：失败源冻结检查（一切测试前，跳过冷却中的死源省资源）----
            if self._source_freeze:
                frozen_until = self._check_frozen(url_norm)
                if frozen_until and frozen_until > time.time():
                    self.logger.info(f'源已冻结冷却中，跳过测试: {source.get("name", "")}')
                    return {
                        **source,
                        'status': 'frozen',
                        'response_time': None,
                        'is_qualified': False,
                        'error_reason': f'frozen until {datetime.fromtimestamp(frozen_until):%Y-%m-%d %H:%M:%S}',
                        'frozen_until': frozen_until,
                    }

            # 生成缓存键(规范化URL)
            cache_key = url_norm

            # 检查缓存命中
            cache_result = self._get_cached_result(cache_key)
            if cache_result:
                self.logger.debug(f'缓存命中: {url}')
                return {**source, **cache_result}

            # 网络环境检查
            if not self._check_network_compatibility(url):
                return {
                    **source,
                    'status': 'failed',
                    'response_time': None,
                    'is_qualified': False,
                    'error_reason': 'network_incompatible',
                }

            # ---- P0-①：同 Host 测速复用（同 CDN 只 ffprobe 一次）----
            if self._host_speed_share:
                host_cached = self._get_host_cached_result(host)
                if host_cached is not None:
                    self.logger.debug(f'同 Host 复用测速结果 [{host}]: {source.get("name", "")}')
                    return {**source, **host_cached, 'host_shared': True}

            # ---- 纪码增强：指数退避重试 ----
            last_error_reason = ''
            for attempt in range(self.max_retries + 1):
                if attempt > 0:
                    # 指数退避：2^retry * 0.5 秒
                    backoff = (2**attempt) * 0.5
                    self.logger.info(
                        f'重试 #{attempt}/{self.max_retries} [{source.get("name", "")}] 等待 {backoff:.1f}s 后重试'
                    )
                    time.sleep(backoff)

                # 执行流媒体测试（使用细化超时）
                start_time = time.time()
                test_status, metadata = self.test_stream_url(
                    url,
                    user_agent,
                    connect_timeout=self.connect_timeout,
                    read_timeout=self.read_timeout,
                    probe_timeout=self.probe_timeout,
                )
                response_time = round((time.time() - start_time) * 1000)

                # 被 Web 层中断（暂停/取消）：立即返回，不重试
                if test_status == 'interrupted':
                    return {**source, 'status': 'interrupted', 'response_time': None}

                # 成功则跳出重试循环
                if test_status == 'success':
                    # 速度测试(如果启用)
                    if self.testing_params['enable_speed_test']:
                        download_speed = self.test_download_speed(url, user_agent)
                        metadata['download_speed'] = download_speed
                    metadata['media_type'] = self._determine_media_type(metadata)

                    test_result = {
                        'status': test_status,
                        'response_time': response_time,
                        **metadata,
                    }

                    # ---- P1：广告/循环占位源检测（成功连接但可能是假活源）----
                    # 命中则降级为 failed 并标记 is_ad，既不解除冻结也不计入死源失败
                    if self._ad_enabled:
                        try:
                            if self._detect_ad_playlist(url, user_agent, metadata):
                                test_result['status'] = 'failed'
                                test_result['is_ad'] = True
                                test_result['error_reason'] = 'ad_playlist'
                                self.logger.info(f'检测到广告/循环占位源，剔除: {source.get("name", "")}')
                        except Exception as e:
                            self.logger.debug(f'广告检测异常（忽略，按正常源处理）: {e}')

                    self._cache_result(cache_key, test_result)
                    # ---- P0-①：写入同 Host 复用缓存（仅成功态，避免死 host 复用扩散）----
                    if self._host_speed_share:
                        self._cache_host_result(host, test_result)
                    # ---- P0-②：成功则解除该源冻结（广告源不解除，因其非真活源）----
                    if test_result['status'] == 'success' and self._source_freeze:
                        self._record_success(url_norm)
                    return {**source, **test_result}
                else:
                    last_error_reason = metadata.get('error_reason', 'unknown')
                    self.logger.debug(
                        f'测试失败 (attempt {attempt + 1}/{self.max_retries + 1}): '
                        f'{source.get("name", "")} - {last_error_reason}'
                    )

            # 所有重试均失败
            self.logger.warning(
                f'测试彻底失败 [{source.get("name", "")}]: '
                f'重试 {self.max_retries} 次后仍失败, 最后原因: {last_error_reason}'
            )
            test_result = {
                'status': 'failed',
                'response_time': None,
                'error_reason': (
                    f'after_{self.max_retries}_retries: {last_error_reason}'
                    if self.max_retries > 0
                    else last_error_reason
                ),
            }
            # ---- P0-②：记录失败，连续失败达阈值则冻结冷却 ----
            if self._source_freeze:
                self._record_failure(url_norm)
            return {**source, **test_result}

        except Exception:
            raise

    def test_stream_url(
        self,
        url: str,
        user_agent: str | None = None,
        # ---- 纪码增强：细化超时层级参数 ----
        connect_timeout: int | None = None,
        read_timeout: int | None = None,
        probe_timeout: int | None = None,
    ) -> tuple[str, dict]:
        """使用ffprobe测试流媒体URL - 纪码增强（细化超时层级 + 资源清理）

        Args:
            url: 流媒体URL
            user_agent: 可选的User-Agent头
            connect_timeout: 连接超时（秒）
            read_timeout: 读取超时（秒）
            probe_timeout: ffprobe分析超时（秒）

        Returns:
            Tuple[str, Dict]: (测试状态, 元数据字典)

        增强（纪码追加）：
        - 细化超时层级：区分 connect / read / probe
        - finally 资源清理：确保 socket 和文件句柄关闭
        """
        try:
            # ---- 纪码增强：使用细化超时 ----
            actual_probe_timeout = probe_timeout or self.testing_params['timeout']
            # 细化超时层级落地：
            #   connect_timeout → ffprobe -timeout（socket 超时，微秒，控制连接与单次 I/O）
            #   read_timeout    → ffprobe -rw_timeout（读等待，微秒，需版本支持）
            connect_us = int((connect_timeout if connect_timeout is not None else self.connect_timeout) * 1_000_000)
            read_us = int((read_timeout if read_timeout is not None else self.read_timeout) * 1_000_000)

            if StreamTester._ffprobe_path and self.ffprobe_available:
                # 优先使用 ffprobe（完整元数据）
                ffprobe_cmd = StreamTester._ffprobe_path
                cmd = [
                    ffprobe_cmd,
                    '-v',
                    'error',  # 显示错误级以上日志，确保连接失败原因透出到 stderr（quiet 会吞掉连接错误）
                    '-print_format',
                    'json',  # JSON输出格式
                    '-show_streams',  # 显示流信息
                    '-show_format',  # 显示格式信息
                    '-timeout',
                    str(connect_us),  # 连接超时（微秒）
                    url,
                ]
                if self._ffprobe_supports_rw_timeout:
                    # 在 url 前插入 -rw_timeout（读超时，微秒）
                    cmd = [*cmd[:-1], '-rw_timeout', str(read_us), cmd[-1]]

                # 添加User-Agent头(如果提供)
                if user_agent:
                    cmd.extend(['-headers', f'User-Agent: {user_agent}'])

                # 执行ffprobe命令
                # ---- F-8: Semaphore 限流 ffprobe 子进程 ----
                with self._ffprobe_semaphore:
                    if self._abort.is_set():
                        return 'interrupted', {'error_reason': 'aborted'}
                    self.logger.debug(f'执行ffprobe命令: {" ".join(cmd)}')
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    with self._proc_lock:
                        self._active_procs.append(proc)
                    try:
                        stdout, stderr = proc.communicate(timeout=actual_probe_timeout + 2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            stdout, stderr = proc.communicate()
                        except Exception:
                            stdout, stderr = '', ''
                        if self._abort.is_set():
                            return 'interrupted', {'error_reason': 'aborted'}
                        return 'timeout', {'error_reason': 'timeout'}
                    except Exception:
                        if self._abort.is_set():
                            return 'interrupted', {'error_reason': 'aborted'}
                        raise
                    finally:
                        with self._proc_lock:
                            if proc in self._active_procs:
                                self._active_procs.remove(proc)

                # 分析命令执行结果
                if proc.returncode == 0:
                    data = json.loads(stdout)
                    if data.get('streams') and len(data['streams']) > 0:
                        metadata = self.extract_metadata(data)
                        return 'success', metadata
                    else:
                        if self._abort.is_set():
                            return 'interrupted', {'error_reason': 'aborted'}
                        return 'failed', {'error_reason': 'no_valid_streams'}
                else:
                    if self._abort.is_set():
                        return 'interrupted', {'error_reason': 'aborted'}
                    raw = (stderr or '').strip() or (stdout or '').strip()
                    cat = _classify_stream_error(raw)
                    self.logger.debug(f'FFprobe执行失败: {raw}')
                    return 'failed', {'error_reason': f'{cat}: {raw}'}

            elif StreamTester._ffmpeg_path:
                # 降级：使用 ffmpeg -i 测试流可连接性（无详细元数据）
                ffmpeg_cmd = StreamTester._ffmpeg_path
                cmd = [
                    ffmpeg_cmd,
                    '-v',
                    'quiet',
                    '-i',
                    url,
                    '-t',
                    '1',  # 只取1秒
                    '-f',
                    'null',  # 不输出文件
                    '-',  # 输出到null
                ]

                if user_agent:
                    cmd.extend(['-user_agent', user_agent])

                with self._ffprobe_semaphore:
                    if self._abort.is_set():
                        return 'interrupted', {'error_reason': 'aborted'}
                    self.logger.debug(f'降级使用ffmpeg测试: {" ".join(cmd)}')
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    with self._proc_lock:
                        self._active_procs.append(proc)
                    try:
                        stdout, stderr = proc.communicate(timeout=actual_probe_timeout + 2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            stdout, stderr = proc.communicate()
                        except Exception:
                            stdout, stderr = '', ''
                        if self._abort.is_set():
                            return 'interrupted', {'error_reason': 'aborted'}
                        return 'timeout', {'error_reason': 'timeout'}
                    except Exception:
                        if self._abort.is_set():
                            return 'interrupted', {'error_reason': 'aborted'}
                        raise
                    finally:
                        with self._proc_lock:
                            if proc in self._active_procs:
                                self._active_procs.remove(proc)

                if proc.returncode == 0:
                    # ffmpeg 成功连接流，返回基本元数据
                    return 'success', {'probe_mode': 'ffmpeg_fallback'}
                else:
                    if self._abort.is_set():
                        return 'interrupted', {'error_reason': 'aborted'}
                    raw = (stderr or '').strip() or (stdout or '').strip()
                    cat = _classify_stream_error(raw)
                    self.logger.debug(f'FFmpeg降级测试失败: {raw}')
                    return 'failed', {'error_reason': f'{cat}: {raw}'}

            else:
                return 'failed', {'error_reason': 'no_probe_tool_available'}

        except subprocess.TimeoutExpired:
            # 处理超时
            self.logger.debug(f'FFprobe测试超时: {url}')
            return 'timeout', {'error_reason': 'timeout'}
        except json.JSONDecodeError as e:
            # JSON解析错误
            self.logger.debug(f'FFprobe输出JSON解析失败: {e}')
            return 'failed', {'error_reason': 'json_parse_error'}
        except StreamTestError:
            raise
        except Exception as e:
            # 其他异常
            self.logger.debug(f'FFprobe测试异常 {url}: {e}')
            return 'failed', {'error_reason': f'exception: {e!s}'}

    # ============================================================
    # 纪码增强：看门狗定时器
    # ============================================================

    def _start_watchdog(self):
        """启动看门狗定时器，超时后强制终止所有测试"""
        if self._watchdog_timeout <= 0:
            return
        self._watchdog_triggered = False
        timer = threading.Timer(self._watchdog_timeout, self._watchdog_timeout_handler)
        timer.daemon = True
        timer.start()
        self._watchdog_timer = timer
        self.logger.debug(f'看门狗已启动，超时时间: {self._watchdog_timeout}s')

    def _watchdog_timeout_handler(self):
        """看门狗超时回调：取消所有活跃 future 并释放 Semaphore

        纪码修复 P1-2: 遍历 _active_futures 调用 cancel()，确保 ffprobe 子进程和 Semaphore 都被释放。
        """
        self._watchdog_triggered = True
        self.logger.error(
            f'⚠ 看门狗触发！批量测试已执行超过 {self._watchdog_timeout}s，正在取消所有活跃的 ffprobe 任务...'
        )
        with self._active_futures_lock:
            to_cancel = list(self._active_futures)
            for future in to_cancel:
                cancelled = future.cancel()
                if cancelled:
                    self._active_futures.discard(future)
                    # 释放 Semaphore：每个取消的 future 对应一个 acquire()
                    with contextlib.suppress(ValueError):
                        self._ffprobe_semaphore.release()
                    self.logger.debug(f'看门狗取消了 future ({cancelled})')

    def _stop_watchdog(self):
        """停止看门狗定时器"""
        if self._watchdog_timer and self._watchdog_timer.is_alive():
            self._watchdog_timer.cancel()
            self._watchdog_timer = None
            self.logger.debug('看门狗已停止（正常完成）')

    def _is_watchdog_triggered(self) -> bool:
        """看门狗是否已触发"""
        return self._watchdog_triggered

    def extract_metadata(self, data: dict) -> dict:
        """从ffprobe输出中提取详细的流媒体元数据

        提取的信息包括:
        - 基础信息: 比特率、时长、格式
        - 视频流: 分辨率、编码、帧率、宽高比
        - 音频流: 编码、采样率、声道数
        - 质量标识: HD/4K标志、流类型

        Args:
            data: ffprobe的JSON输出数据

        Returns:
            Dict: 包含所有提取的元数据
        """
        metadata = {
            # 基础信息
            'bitrate': 0,
            'duration': 0,
            'format_name': '',
            # 视频流信息
            'resolution': '',
            'is_hd': False,
            'is_4k': False,
            'video_codec': '',
            'video_profile': '',
            'video_level': 0,
            'frame_rate': 0,
            'pixel_format': '',
            'has_video_stream': False,
            # 音频流信息
            'audio_codec': '',
            'audio_sample_rate': 0,
            'audio_channels': 0,
            'audio_bitrate': 0,
            'has_audio_stream': False,
            # 流统计
            'stream_count': 0,
            'video_stream_count': 0,
            'audio_stream_count': 0,
            # 媒体类型(后续计算)
            'media_type': 'unknown',
        }

        # 提取格式信息
        if 'format' in data:
            format_info = data['format']

            # 比特率(转换为kbps)
            if 'bit_rate' in format_info:
                with contextlib.suppress(ValueError, TypeError):
                    metadata['bitrate'] = int(format_info['bit_rate']) // 1000

            # 时长(秒)
            if 'duration' in format_info:
                with contextlib.suppress(ValueError, TypeError):
                    metadata['duration'] = float(format_info['duration'])

            # 格式名称
            if 'format_name' in format_info:
                metadata['format_name'] = format_info['format_name']

        # 分析所有流
        video_streams = []
        audio_streams = []
        other_streams = []

        for stream in data.get('streams', []):
            metadata['stream_count'] += 1
            codec_type = stream.get('codec_type', 'unknown')

            if codec_type == 'video':
                metadata['video_stream_count'] += 1
                metadata['has_video_stream'] = True
                video_streams.append(stream)

                # 提取视频流详细信息
                video_info = self._extract_video_stream_info(stream)
                metadata.update(video_info)

            elif codec_type == 'audio':
                metadata['audio_stream_count'] += 1
                metadata['has_audio_stream'] = True
                audio_streams.append(stream)

                # 提取音频流详细信息
                audio_info = self._extract_audio_stream_info(stream)
                metadata.update(audio_info)
            else:
                other_streams.append(stream)

        # 确定主要视频流(如果有多个)
        if video_streams:
            # 选择第一个视频流作为主要流
            main_video = video_streams[0]
            # 如果之前没有提取分辨率，现在提取
            if not metadata['resolution']:
                width = main_video.get('width', 0)
                height = main_video.get('height', 0)
                if width and height:
                    metadata['resolution'] = f'{width}x{height}'
                    metadata['is_hd'] = height >= 720
                    metadata['is_4k'] = height >= 2160

        # 确定主要音频流(如果有多个)
        if audio_streams and not metadata['audio_codec']:
            main_audio = audio_streams[0]
            if 'codec_name' in main_audio:
                metadata['audio_codec'] = main_audio['codec_name']

        return metadata

    def _extract_video_stream_info(self, stream: dict) -> dict:
        """提取视频流详细信息

        Args:
            stream: 视频流数据

        Returns:
            Dict: 视频流信息
        """
        info = {}

        # 分辨率
        width = stream.get('width', 0)
        height = stream.get('height', 0)
        if width and height:
            info['resolution'] = f'{width}x{height}'
            info['is_hd'] = height >= 720
            info['is_4k'] = height >= 2160

        # 视频编码
        if 'codec_name' in stream:
            info['video_codec'] = stream['codec_name']

        # 编码配置
        if 'profile' in stream:
            info['video_profile'] = stream['profile']

        # 编码级别
        if 'level' in stream:
            with contextlib.suppress(ValueError, TypeError):
                info['video_level'] = int(stream['level'])

        # 帧率
        if 'avg_frame_rate' in stream:
            frame_rate_str = stream['avg_frame_rate']
            if frame_rate_str and '/' in frame_rate_str:
                try:
                    num, den = map(int, frame_rate_str.split('/'))
                    if den > 0:
                        info['frame_rate'] = round(num / den, 2)
                except (ValueError, ZeroDivisionError):
                    pass

        # 像素格式
        if 'pix_fmt' in stream:
            info['pixel_format'] = stream['pix_fmt']

        return info

    def _extract_audio_stream_info(self, stream: dict) -> dict:
        """提取音频流详细信息

        Args:
            stream: 音频流数据

        Returns:
            Dict: 音频流信息
        """
        info = {}

        # 音频编码
        if 'codec_name' in stream:
            info['audio_codec'] = stream['codec_name']

        # 采样率
        if 'sample_rate' in stream:
            with contextlib.suppress(ValueError, TypeError):
                info['audio_sample_rate'] = int(stream['sample_rate'])

        # 声道数
        if 'channels' in stream:
            with contextlib.suppress(ValueError, TypeError):
                info['audio_channels'] = int(stream['channels'])

        # 音频比特率
        if 'bit_rate' in stream:
            with contextlib.suppress(ValueError, TypeError):
                info['audio_bitrate'] = int(stream['bit_rate']) // 1000

        return info

    def _determine_media_type(self, metadata: dict) -> str:
        """根据元数据确定媒体类型

        Args:
            metadata: 流媒体元数据

        Returns:
            str: 媒体类型 (video/audio/radio/unknown)
        """
        has_video = metadata.get('has_video_stream', False)
        resolution = metadata.get('resolution', '')

        # 如果没有视频流，肯定是音频
        if not has_video:
            return 'audio'

        # 检查是否是极低分辨率的视频(可能是误判的音频)
        if resolution and 'x' in resolution:
            try:
                width, height = map(int, resolution.split('x'))
                if width < 100 or height < 100:
                    return 'audio'
            except (ValueError, TypeError):
                pass  # 非整数分辨率，按video处理

        # 正常视频内容
        return 'video'

    def test_download_speed(self, url: str, user_agent: str | None = None) -> float:
        """测试下载速度

        通过下载部分数据来计算平均下载速度

        Args:
            url: 测试URL
            user_agent: 可选的User-Agent头

        Returns:
            float: 下载速度(KB/s)
        """
        try:
            import requests

            # 设置请求头
            headers = {'User-Agent': user_agent} if user_agent else {}

            # 开始下载测试
            start_time = time.time()
            with requests.get(
                url,
                stream=True,
                timeout=self.testing_params['timeout'],
                headers=headers,
            ) as response:
                total_downloaded = 0
                test_duration = self.testing_params['speed_test_duration']
                chunk_size = 64 * 1024  # 64KB chunks

                # 下载数据直到达到测试时长
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if time.time() - start_time >= test_duration:
                        break
                    if chunk:
                        total_downloaded += len(chunk)

            # 计算平均速度(KB/s)
            elapsed = time.time() - start_time
            if elapsed > 0:
                speed = total_downloaded / 1024 / elapsed
                self.logger.debug(f'速度测试: {url} - {speed:.2f} KB/s')
                return speed

            return 0.0

        except Exception as e:
            self.logger.debug(f'速度测试失败 {url}: {e}')
            return 0.0

    def check_if_qualified(self, result: dict) -> bool:
        """检查源是否满足质量要求

        实现分层质量检查:
        1. 基本连通性检查
        2. 性能指标检查(延迟、速度)
        3. 技术规格检查(分辨率、比特率)
        4. 特殊要求检查(HD/4K)

        Args:
            result: 测试结果数据

        Returns:
            bool: 是否合格
        """
        # 基本状态检查
        if result.get('status') != 'success':
            return False

        # 媒体类型特定检查
        media_type = result.get('media_type', 'video')

        if media_type in ['radio', 'audio']:
            # 音频内容简化检查 - 主要检查延迟
            response_time = result.get('response_time', 9999)
            return response_time <= self.filter_params['max_latency']

        # 视频内容详细检查

        # 延迟检查
        response_time = result.get('response_time', 9999)
        if response_time > self.filter_params['max_latency']:
            return False

        # 分辨率检查
        min_resolution = self.filter_params['min_resolution']
        max_resolution = self.filter_params['max_resolution']
        resolution_filter_mode = self.filter_params.get('resolution_filter_mode', 'range')

        if min_resolution or max_resolution:
            resolution = result.get('resolution', '')

            if resolution_filter_mode == 'range':
                # 区间模式：必须同时满足最小和最大分辨率
                if min_resolution and not self.is_resolution_meet_min(resolution, min_resolution):
                    return False
                if max_resolution and not self.is_resolution_meet_max(resolution, max_resolution):
                    return False
            elif resolution_filter_mode == 'min_only':
                # 仅最低：只检查最低分辨率
                if min_resolution and not self.is_resolution_meet_min(resolution, min_resolution):
                    return False
            elif resolution_filter_mode == 'max_only':
                # 仅最高：只检查最高分辨率
                if max_resolution and not self.is_resolution_meet_max(resolution, max_resolution):
                    return False

        # 比特率检查
        bitrate = result.get('bitrate', 0)
        if bitrate > 0 and bitrate < self.filter_params['min_bitrate']:
            return False

        # 特殊质量要求检查
        if self.filter_params['must_hd'] and not result.get('is_hd', False):
            return False

        if self.filter_params['must_4k'] and not result.get('is_4k', False):
            return False

        # 下载速度检查
        speed = result.get('download_speed', 0)
        return not (speed > 0 and speed < self.filter_params['min_speed'])

    def is_resolution_meet_min(self, resolution: str, min_resolution: str) -> bool:
        """检查分辨率是否满足最低要求

        Args:
            resolution: 实际分辨率 (如 "1920x1080" 或 "1080p")
            min_resolution: 要求的最低分辨率

        Returns:
            bool: 是否满足要求
        """
        if not resolution or not min_resolution:
            return True

        def parse_resolution(res):
            """将分辨率字符串解析为(宽度, 高度)元组"""
            if 'x' in res:
                # 格式: "1920x1080"
                parts = res.split('x')
                if len(parts) == 2:
                    try:
                        return int(parts[0]), int(parts[1])
                    except (ValueError, TypeError):
                        return 0, 0
            elif res.endswith('p'):
                # 格式: "1080p"
                try:
                    height = int(res[:-1])
                    # 假设宽高比为16:9计算宽度
                    width = int(height * 16 / 9)
                    return width, height
                except (ValueError, TypeError):
                    return 0, 0
            return 0, 0

        res_width, res_height = parse_resolution(resolution)
        min_width, min_height = parse_resolution(min_resolution)

        # 比较分辨率尺寸
        return res_width >= min_width and res_height >= min_height

    def is_resolution_meet_max(self, resolution: str, max_resolution: str) -> bool:
        """检查分辨率是否不超过最高限制

        Args:
            resolution: 实际分辨率
            max_resolution: 要求的最高分辨率

        Returns:
            bool: 是否满足要求
        """
        if not resolution or not max_resolution:
            return True

        def parse_resolution(res):
            """将分辨率字符串解析为(宽度, 高度)元组"""
            if 'x' in res:
                parts = res.split('x')
                if len(parts) == 2:
                    try:
                        return int(parts[0]), int(parts[1])
                    except (ValueError, TypeError):
                        return 9999, 9999  # 返回极大值确保检查失败
            elif res.endswith('p'):
                try:
                    height = int(res[:-1])
                    width = int(height * 16 / 9)
                    return width, height
                except (ValueError, TypeError):
                    return 9999, 9999
            return 9999, 9999

        res_width, res_height = parse_resolution(resolution)
        max_width, max_height = parse_resolution(max_resolution)

        # 比较分辨率尺寸
        return res_width <= max_width and res_height <= max_height

    def log_test_result(self, source: dict, result: dict, log_level: str = 'info'):
        """记录测试结果日志

        Args:
            source: 原始源数据
            result: 测试结果数据
            log_level: 日志级别
        """
        status = result.get('status', 'unknown')
        is_qualified = result.get('is_qualified', False)

        # 构建基础日志信息
        log_message = f'测试结果: 频道={source["name"]}, URL={source["url"]}, 状态={status}, 合格={is_qualified}'

        # 添加详细信息(如果测试成功)
        if status == 'success':
            log_message += f', 延迟={result.get("response_time")}ms'

            # 媒体类型信息
            media_type = result.get('media_type', 'unknown')
            log_message += f', 媒体类型={media_type}'

            # 视频相关信息
            if media_type == 'video':
                log_message += f', 分辨率={result.get("resolution", "未知")}'
                log_message += f', 比特率={result.get("bitrate", 0)}kbps'

            # 速度信息
            if result.get('download_speed'):
                log_message += f', 速度={result.get("download_speed", 0):.2f}KB/s'

        # 根据日志级别记录
        log_method = getattr(self.logger, log_level, self.logger.info)
        log_method(log_message)

    def normalize_url(self, url: str) -> str:
        """规范化URL用于缓存键

        移除可能变化的参数(如时间戳、随机数)，
        确保相同资源的URL能够命中缓存

        Args:
            url: 原始URL

        Returns:
            str: 规范化后的URL
        """
        try:
            from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

            parsed = urlparse(url)

            # 解析查询参数
            query_params = parse_qs(parsed.query)

            # 过滤掉可能变化的参数
            dynamic_params = ['t', 'time', 'timestamp', 'r', 'random', 'nonce', 'token']
            filtered_params = {k: v for k, v in query_params.items() if k not in dynamic_params}

            # 重建URL
            normalized_query = urlencode(filtered_params, doseq=True)

            normalized_url = urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    normalized_query,
                    parsed.fragment,
                )
            )

            return normalized_url

        except Exception as e:
            self.logger.debug(f'URL规范化失败 {url}: {e}')
            return url  # 失败时返回原URL

    def _check_network_compatibility(self, url: str) -> bool:
        """检查网络兼容性

        主要检查IPv6支持情况

        Args:
            url: 要检查的URL

        Returns:
            bool: 是否兼容当前网络环境
        """
        # 检查是否是IPv6地址
        if '[' in url and ']' in url and not self.check_ipv6_support():
            # 包含IPv6地址标记且系统不支持IPv6
            self.logger.debug(f'跳过IPv6地址(系统不支持): {url}')
            return False

        return True

    def check_ipv6_support(self) -> bool:
        """检查系统是否支持IPv6

        Returns:
            bool: 是否支持IPv6
        """
        try:
            # 尝试创建IPv6 socket来检测支持情况
            sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
            sock.close()
            return True
        except Exception:
            self.logger.warning('系统不支持IPv6，将跳过IPv6地址的测试')
            return False

    def _get_cached_result(self, cache_key: str) -> dict | None:
        """从缓存获取测试结果

        Args:
            cache_key: 缓存键

        Returns:
            Optional[Dict]: 缓存结果，如果不存在或过期返回None
        """
        # 线程安全锁保护
        with self._cache_lock:
            if cache_key in self._url_cache:
                cached_data = self._url_cache[cache_key]
                cache_age = datetime.now() - cached_data['timestamp']

                # 检查缓存是否过期
                cache_ttl = timedelta(minutes=self.testing_params['cache_ttl'])
                if cache_age < cache_ttl:
                    return {
                        'status': cached_data['status'],
                        'response_time': cached_data['response_time'],
                        **cached_data.get('metadata', {}),
                    }
                else:
                    # 移除过期缓存
                    del self._url_cache[cache_key]

        return None

    def _cache_result(self, cache_key: str, result: dict):
        """缓存测试结果

        Args:
            cache_key: 缓存键
            result: 测试结果
        """
        # 线程安全锁保护
        with self._cache_lock:
            self._url_cache[cache_key] = {
                'status': result['status'],
                'response_time': result['response_time'],
                'metadata': {k: v for k, v in result.items() if k not in ['status', 'response_time']},
                'timestamp': datetime.now(),
            }

    def cleanup_cache(self):
        """清理过期的缓存项"""
        now = datetime.now()
        if (now - self._last_cache_cleanup).total_seconds() > self._CACHE_CLEANUP_INTERVAL:
            expired_keys = []
            cache_ttl = timedelta(minutes=self.testing_params['cache_ttl'])

            # 线程安全锁保护
            with self._cache_lock:
                for key, data in self._url_cache.items():
                    if now - data['timestamp'] > cache_ttl:
                        expired_keys.append(key)

                # 移除过期项
                for key in expired_keys:
                    del self._url_cache[key]

            if expired_keys:
                self.logger.debug(f'缓存清理: 移除了 {len(expired_keys)} 个过期项')

            self._last_cache_cleanup = now

    # ============================================================
    # 性能优化（对标 Guovin/iptv-api P0）
    # ============================================================

    # ---- P0-①：同 Host 测速复用 ----
    def _extract_host(self, url: str) -> str:
        """从 URL 提取 host（含端口），作为同 Host 测速复用分组键。

        同 CDN/Host 下数十个频道只需 ffprobe 一次，其余复用结果，
        ffprobe 子进程调用量可降一个数量级。
        """
        try:
            from urllib.parse import urlparse

            netloc = urlparse(url).netloc
            return netloc.lower() or url
        except Exception:
            return url

    def _get_host_cached_result(self, host: str) -> dict | None:
        """获取同 Host 已缓存的测速结果（TTL 内有效，仅成功态）。"""
        if not host:
            return None
        with self._host_cache_lock:
            data = self._host_speed_cache.get(host)
            if not data:
                return None
            age = datetime.now() - data['timestamp']
            if age < timedelta(minutes=self.testing_params.get('cache_ttl', 120)):
                return {
                    'status': data['status'],
                    'response_time': data['response_time'],
                    **data.get('metadata', {}),
                }
            # 过期移除
            self._host_speed_cache.pop(host, None)
        return None

    def _cache_host_result(self, host: str, result: dict):
        """缓存同 Host 测速结果（仅成功态，避免死 host 复用扩散误伤）。"""
        if not host or result.get('status') != 'success':
            return
        with self._host_cache_lock:
            self._host_speed_cache[host] = {
                'status': result['status'],
                'response_time': result['response_time'],
                'metadata': {k: v for k, v in result.items() if k not in ('status', 'response_time')},
                'timestamp': datetime.now(),
            }

    # ---- P0-②：失败源指数退避冻结 ----
    def _resolve_status_dir(self) -> str:
        """定位 data/status 目录（与 web.models.DATA_DIR 同级），用于跨进程持久化冻结状态。"""
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'web',
            'data',
            'status',
        )
        with contextlib.suppress(Exception):
            os.makedirs(base, exist_ok=True)
        return base

    def _frozen_path(self) -> str:
        return os.path.join(self._status_dir, 'frozen_sources.json')

    def _load_frozen_map(self) -> dict:
        """从磁盘加载冻结状态（进程重启后保持），仅保留合法条目。"""
        try:
            p = self._frozen_path()
            if os.path.exists(p):
                with open(p, encoding='utf-8') as f:
                    data = json.load(f)
                return {k: v for k, v in data.items() if isinstance(v, dict) and 'frozen_until' in v}
        except Exception as e:
            self.logger.warning(f'加载冻结状态失败（忽略）: {e}')
        return {}

    def _save_frozen_map(self):
        """持久化冻结状态到磁盘（test_all_sources 结束时调用一次，避免频繁 IO）。"""
        try:
            with self._frozen_map_lock:
                data = dict(self._frozen_map)
            with open(self._frozen_path(), 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning(f'保存冻结状态失败（忽略）: {e}')

    def _check_frozen(self, url_norm: str) -> float | None:
        """返回冻结到期时间戳（epoch 秒），未冻结或已解冻返回 None。

        注意：frozen_until=0 表示「未冻结但已有失败计数」，此时仅返回 None，
        不删除条目，以免丢失连续失败计数导致永远达不到冻结阈值。
        """
        with self._frozen_map_lock:
            fr = self._frozen_map.get(url_norm)
        if not fr:
            return None
        frozen_until = fr.get('frozen_until', 0)
        if frozen_until:
            if frozen_until <= time.time():
                # 曾冻结且已过期：清理并解除
                with self._frozen_map_lock:
                    self._frozen_map.pop(url_norm, None)
                return None
            return frozen_until
        return None

    def _record_failure(self, url_norm: str):
        """记录一次失败；连续失败达到阈值后对源做指数退避冻结。

        冻结时长 = min(2^fail_count × base, max_seconds)，与 Guovin 退避策略一致。
        """
        with self._frozen_map_lock:
            fr = self._frozen_map.get(url_norm, {'fail_count': 0, 'frozen_until': 0})
            fr['fail_count'] = fr.get('fail_count', 0) + 1
            if fr['fail_count'] >= self._freeze_fail_threshold:
                delay = min(
                    (2 ** fr['fail_count']) * self._freeze_base_seconds,
                    self._freeze_max_seconds,
                )
                fr['frozen_until'] = time.time() + delay
                self.logger.info(f'源连续失败 {fr["fail_count"]} 次，冻结冷却 {delay:.0f}s: {url_norm[:80]}')
            self._frozen_map[url_norm] = fr

    def _record_success(self, url_norm: str):
        """源测试成功：重置失败计数并解除冻结。"""
        with self._frozen_map_lock:
            if url_norm in self._frozen_map:
                self.logger.debug(f'源恢复，解除冻结: {url_norm[:80]}')
                self._frozen_map.pop(url_norm, None)

    # ────────────────────────────────────────────────
    # P1/P2（对标 Guovin/iptv-api）：广告检测 + 全局黑白名单
    # ────────────────────────────────────────────────
    @staticmethod
    def _parse_filter_list(raw: str) -> list[str]:
        """解析逗号/换行/分号分隔的列表（去空、去首尾空白、保持原大小写）。"""
        if not raw:
            return []
        items = []
        for part in re.split(r'[\n,;]', raw):
            p = part.strip()
            if p:
                items.append(p)
        return items

    def _url_in_list(self, url_norm: str, entries: list[str]) -> bool:
        """判断归一化 URL 是否命中名单：host 精确匹配 或 URL 含名单条目子串（大小写不敏感）。"""
        if not entries:
            return False
        u = (url_norm or '').lower()
        host = self._extract_host(url_norm).lower()
        for e in entries:
            el = (e or '').lower()
            if not el:
                continue
            if el == host or el in u:
                return True
        return False

    def _detect_ad_playlist(self, url: str, user_agent: str | None, metadata: dict) -> bool:
        """检测广告/循环占位源（对标 Guovin is_ad_playlist）。

        仅对 HLS（m3u8）源生效：成功 ffprobe 连接后，拉取 playlist 头部检查：
          1) 广告关键字（ad_keywords，如 no_signal、/ad/、advertisement、测试卡等）；
          2) 含 #EXT-X-ENDLIST（点播/VOD 而非直播）且累计分片时长 <= ad_max_duration
             → 判定为循环占位（机顶盒广告卡/测试卡）。
        直播源 playlist 通常无 ENDLIST，属正常，不误判。
        网络拉取失败/超时均返回 False（不误杀真实源）。
        """
        # 仅 HLS 源有意义
        if '.m3u8' not in url and 'm3u' not in url.lower():
            return False
        try:
            from urllib.request import Request, urlopen

            req = Request(url)
            if user_agent:
                req.add_header('User-Agent', user_agent)
            # 仅读取前 64KB 足够检测关键字与 ENDLIST，避免大 playlist 全量下载
            with urlopen(req, timeout=5) as resp:
                raw = resp.read(64 * 1024).decode('utf-8', errors='ignore')
        except Exception:
            return False

        if not raw:
            return False

        lowered = raw.lower()
        # 1) 广告关键字命中
        for kw in self._ad_keywords:
            if kw and kw.lower() in lowered:
                return True

        # 2) 循环占位：VOD（有 ENDLIST）且累计时长 <= 阈值
        if '#ext-x-endlist' in lowered:
            total = 0.0
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith('#EXTINF'):
                    # #EXTINF:<duration>[,<title>]
                    try:
                        dur_part = line.split(':', 1)[1].split(',', 1)[0].strip()
                        total += float(dur_part)
                    except (ValueError, IndexError):
                        pass
            if 0 < total <= self._ad_max_duration:
                return True

        return False
