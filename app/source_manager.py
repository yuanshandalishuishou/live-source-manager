#!/usr/bin/env python3
"""
源管理器模块 — SourceManager

从 app/__init__.py 拆分而来，负责源文件的下载、解析与管理。
功能包括：
- 在线源文件下载（支持直连/代理/SOCKS5）
- GitHub 仓库源发现与下载
- M3U/TXT 源文件解析
- URL 安全审查
- 文件级 / 频道级 User-Agent 配置注入
"""

import asyncio
import json
import os
import re
import socket
import threading
from urllib.parse import urlparse

# ══════════════════════════════════════════════════
# 可选第三方库（失败时降级）
# ══════════════════════════════════════════════════
try:
    import aiofiles
    import aiohttp
    import aiohttp_socks

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    aiohttp = None
    aiofiles = None
    aiohttp_socks = None

try:
    from tqdm import tqdm

    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    tqdm = None

# ══════════════════════════════════════════════════
# 项目内部依赖
# ══════════════════════════════════════════════════
from app.config import Config
from app.exceptions import SourceDownloadError, SourceParseError
from app.rules import ChannelRules
from app.security import is_safe_url


class SourceManager:
    """源管理类 - 增强网络容错修复版"""

    def __init__(self, config: Config, logger, channel_rules: ChannelRules):
        """
        初始化源管理器

        Args:
            config: 配置管理器实例
            logger: 日志记录器实例
            channel_rules: 频道规则管理器实例
        """
        self.config = config
        self.logger = logger
        self.channel_rules = channel_rules
        self.network_config = config.get_network_config()
        self.github_config = config.get_github_config()
        self.user_agents = config.get_user_agents()
        self.ua_enabled = config.is_ua_enabled()
        # 从 local_dirs 配置派生 online_dir（同级 online 目录），兼容 Docker/本地
        # 规范化：过滤空字符串/空白项（DB 中可能存为 ['']），否则会被视为「有值」而误把
        # online_dir 解析到错误的根 online/ 目录，与 source-files API 使用的 config/online 不一致。
        _local_dirs = config.get_sources().get('local_dirs', [])
        _local_dirs = [d for d in (_local_dirs or []) if isinstance(d, str) and d.strip()]
        if _local_dirs:
            self.online_dir = os.path.join(os.path.dirname(os.path.abspath(_local_dirs[0])), 'online')
        else:
            # 默认使用项目根下的 config/online（与 web.routes.sources 的 _get_online_file_path 一致）
            _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.online_dir = os.path.join(_project_root, 'config', 'online')
        # M4: 共享session生命周期管理
        self._session = None
        self._session_lock = threading.Lock()

        # ---- 纪码增强:GitHub API Token注入 ----
        self.api_token = config.get('GitHub', 'api_token', '') or os.environ.get('GITHUB_TOKEN', '')
        if self.api_token:
            self.logger.info(f'✓ GitHub API Token已注入(前8位: {self.api_token[:8]}...)')
        else:
            self.logger.info('i GitHub API Token未设置,使用匿名请求')

        # 确保在线源目录存在
        os.makedirs(self.online_dir, exist_ok=True)

        # GitHub 仓库条目 → 下载文件名 映射（供文件级 UA 精确匹配，采集时落盘）
        self._github_entry_map: dict[str, list[str]] = {}
        self._github_entry_map_path = os.path.join(self.online_dir, '.github_entry_map.json')
        self._load_github_entry_map()

    def _load_github_entry_map(self) -> None:
        """加载 GitHub 条目→文件名 映射（采集时落盘，重启后可恢复，供文件级 UA 精确匹配）"""
        try:
            if os.path.exists(self._github_entry_map_path):
                with open(self._github_entry_map_path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._github_entry_map = {
                        k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, list)
                    }
                self.logger.debug(f'已加载 GitHub 条目映射: {len(self._github_entry_map)} 条')
        except Exception as e:
            self.logger.warning(f'加载 GitHub 条目映射失败(忽略): {e}')
            self._github_entry_map = {}

    def _save_github_entry_map(self) -> None:
        """持久化 GitHub 条目→文件名 映射"""
        try:
            os.makedirs(os.path.dirname(self._github_entry_map_path), exist_ok=True)
            with open(self._github_entry_map_path, 'w', encoding='utf-8') as f:
                json.dump(self._github_entry_map, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning(f'保存 GitHub 条目映射失败(忽略): {e}')

    async def create_session(self, use_proxy: bool = False) -> aiohttp.ClientSession:
        # M4: 当前调用create_session的地方改为使用get_session
        # 保留此方法供直接调用兼容
        return await self.get_session(use_proxy)

    async def get_session(self, use_proxy: bool = False) -> aiohttp.ClientSession:
        """M4: 获取或创建共享的aiohttp会话(复用session代替每次创建销毁)

        当 session 已存在且未关闭时复用,避免每个请求创建/销毁 session。

        Args:
            use_proxy: 是否使用代理

        Returns:
            aiohttp.ClientSession: HTTP会话实例
        """
        # 检查现有session是否可用
        if self._session is not None and not self._session.closed:
            return self._session

        with self._session_lock:
            # 双重检查
            if self._session is not None and not self._session.closed:
                return self._session

            connector = None

            # 设置更宽松的超时配置
            timeout = aiohttp.ClientTimeout(total=60, connect=30, sock_connect=30, sock_read=30)

            # 设置地址族(支持IPv6)
            family = socket.AF_INET
            if self.network_config['ipv6_enabled']:
                family = socket.AF_UNSPEC

            # 代理配置处理
            if use_proxy and self.network_config['proxy_enabled']:
                proxy_type = self.network_config['proxy_type'].lower()
                proxy_host = self.network_config['proxy_host']
                proxy_port = self.network_config['proxy_port']
                proxy_username = self.network_config['proxy_username']
                proxy_password = self.network_config['proxy_password']

                try:
                    if proxy_type in ['socks5', 'socks5h']:
                        # SOCKS5代理配置
                        if proxy_username and proxy_password:
                            proxy_url = f'{proxy_type}://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}'
                        else:
                            proxy_url = f'{proxy_type}://{proxy_host}:{proxy_port}'

                        connector = aiohttp_socks.ProxyConnector.from_url(
                            proxy_url, family=family, verify_ssl=False, limit=100
                        )
                    else:
                        # HTTP代理配置
                        connector = aiohttp.TCPConnector(family=family, verify_ssl=False, limit=100)
                except Exception as e:
                    self.logger.warning(f'创建代理连接器失败: {e}, 将使用直连')
                    connector = aiohttp.TCPConnector(family=family, verify_ssl=False, limit=100)
            else:
                # 直连配置
                connector = aiohttp.TCPConnector(family=family, verify_ssl=False, limit=100)

            self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
            return self._session

    async def download_all_sources(self, github_download_methods: dict | None = None) -> list[str]:
        """
        下载所有源文件 - 增强容错版（含 GitHub 仓库源发现）

        Args:
            github_download_methods: GitHub 条目→下载方式映射，如 {"owner/repo": "raw"}

        Returns:
            List[str]: 成功下载的文件路径列表
        """
        downloaded_files = []

        # 获取在线URL列表
        sources_cfg = self.config.get_sources()
        online_urls = list(sources_cfg['online_urls'])
        github_sources = sources_cfg.get('github_sources', [])

        # 从 GitHub 仓库发现源文件 URL
        if github_sources:
            self.logger.info(f'开始从 {len(github_sources)} 个 GitHub 仓库发现源文件...')
            gh_infos = await self._discover_github_source_urls(github_sources, github_download_methods)
            self.logger.info(f'GitHub 仓库共发现 {len(gh_infos)} 个源文件 URL')
            # gh_infos: [{'url': str, 'method': str, 'entry': str}, ...]
            # 转换为带下载方式的下载任务
            for info in gh_infos:
                online_urls.append(info)  # 在线 URL 列表现在混合了 str 和 dict

        self.logger.info(f'开始下载 {len(online_urls)} 个源文件')

        # 分批下载,避免过多并发
        batch_size = 3
        total_batches = (len(online_urls) - 1) // batch_size + 1

        for i in range(0, len(online_urls), batch_size):
            batch_items = online_urls[i : i + batch_size]
            self.logger.info(f'下载批次 {i // batch_size + 1}/{total_batches}')

            # 创建下载任务
            tasks = []
            for item in batch_items:
                if isinstance(item, dict):
                    # GitHub 源带下载方式
                    tasks.append(
                        self.download_with_retry(
                            item['url'], method=item.get('method', 'raw'), entry=item.get('entry', '')
                        )
                    )
                else:
                    # 普通 URL
                    tasks.append(self.download_with_retry(item))

            # 并行执行下载任务
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理下载结果
            for j, result in enumerate(results):
                item = batch_items[j]
                url = item['url'] if isinstance(item, dict) else item
                if isinstance(result, Exception):
                    self.logger.error(f'下载失败 {url}: {result}')
                elif result:
                    downloaded_files.append(result)
                    self.logger.info(f'下载成功: {url}')
                    # 记录 GitHub 条目 → 文件名 映射（供文件级 UA 精确匹配）
                    if isinstance(item, dict) and item.get('entry'):
                        fname = os.path.basename(result)
                        self._github_entry_map.setdefault(item['entry'], [])
                        if fname not in self._github_entry_map[item['entry']]:
                            self._github_entry_map[item['entry']].append(fname)

            # 批次之间短暂暂停,避免过于频繁的请求
            await asyncio.sleep(1)

        # 持久化 GitHub 条目映射（供文件级 UA 精确匹配）
        self._save_github_entry_map()

        self.logger.info(f'成功下载 {len(downloaded_files)} 个源文件')
        return downloaded_files

    async def _discover_github_source_urls(self, github_sources: list[str], methods: dict | None = None) -> list[dict]:
        """从 GitHub 仓库条目发现可下载的源文件 URL

        支持的条目格式:
            owner/repo              — 使用默认分支,通过 API 查找 m3u/txt 文件
            owner/repo/branch       — 指定分支,通过 API 查找 m3u/txt 文件
            owner/repo/branch/path  — 指定分支和文件路径,直接构建 URL

        Args:
            github_sources: GitHub 仓库条目列表
            methods: 条目→下载方式映射，如 {"owner/repo": "raw"}。
                    支持: raw(默认)/api/proxy/mirror

        Returns:
            List[dict]: [{'url': str, 'method': str, 'entry': str}, ...]
        """
        if methods is None:
            methods = {}
        discovered = []
        session = await self.get_session(use_proxy=False)
        mirror_url = self.network_config.get('github_mirror', 'https://ghproxy.com/').rstrip('/')

        for entry in github_sources:
            method = methods.get(entry, 'raw')
            if method not in ('raw', 'api', 'proxy', 'mirror'):
                method = 'raw'

            parts = [p.strip() for p in entry.split('/') if p.strip()]
            if len(parts) < 2:
                self.logger.warning(f'GitHub 仓库条目格式无效,跳过: {entry}')
                continue

            owner, repo = parts[0], parts[1]

            # 格式: owner/repo/branch/path → 直接构建 URL
            if len(parts) >= 4:
                branch = parts[2]
                file_path = '/'.join(parts[3:])
                url_info = self._build_github_download_url(owner, repo, branch, file_path, method, mirror_url)
                url_info['entry'] = entry
                discovered.append(url_info)
                self.logger.info(f'GitHub 源({method}): {url_info["url"]}')
                continue

            # 格式: owner/repo/branch 或 owner/repo → 通过 API 查找源文件
            branch = parts[2] if len(parts) >= 3 else None
            api_results = await self._find_source_files_in_repo(session, owner, repo, branch, method, mirror_url)
            for r in api_results:
                r['entry'] = entry
                discovered.append(r)

            if api_results:
                self.logger.info(f'GitHub 仓库 {owner}/{repo}({method}): 发现 {len(api_results)} 个源文件')
            else:
                self.logger.warning(f'GitHub 仓库 {owner}/{repo}: 未找到源文件')

        return discovered

    def _build_github_download_url(
        self,
        owner: str,
        repo: str,
        branch: str,
        file_path: str,
        method: str = 'raw',
        mirror_url: str = 'https://ghproxy.com/',
    ) -> dict:
        """根据下载方式构建 GitHub 文件下载 URL

        Args:
            owner, repo, branch, file_path: 仓库信息
            method: raw | api | proxy | mirror
            mirror_url: 镜像站 URL（仅 mirror 方式使用）

        Returns:
            {'url': str, 'method': str}
        """
        if method == 'api':
            url = f'https://api.github.com/repos/{owner}/{repo}/contents/{file_path}?ref={branch}'
        elif method == 'mirror':
            url = f'{mirror_url}/https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}'
        else:
            # raw / proxy: 都用 raw.githubusercontent.com URL
            url = f'https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}'
        return {'url': url, 'method': method}

    async def _find_source_files_in_repo(
        self,
        session: aiohttp.ClientSession,
        owner: str,
        repo: str,
        branch: str | None = None,
        method: str = 'raw',
        mirror_url: str = 'https://ghproxy.com/',
    ) -> list[dict]:
        """通过 GitHub API 查找仓库中的 m3u/txt 源文件

        Args:
            session: aiohttp 会话
            owner: 仓库所有者
            repo: 仓库名
            branch: 分支名（None 则获取默认分支）
            method: 下载方式 (raw/api/proxy/mirror)
            mirror_url: 镜像站 URL

        Returns:
            List[dict]: [{'url': str, 'method': str}, ...]
        """
        try:
            headers = {}
            if self.api_token:
                headers['Authorization'] = f'token {self.api_token}'

            # 如果未指定分支,先获取默认分支
            if not branch:
                repo_api = f'https://api.github.com/repos/{owner}/{repo}'
                async with session.get(repo_api, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        self.logger.warning(f'GitHub API 获取仓库信息失败 {owner}/{repo}: HTTP {resp.status}')
                        return []
                    repo_data = await resp.json()
                    branch = repo_data.get('default_branch', 'main')

            # 获取仓库文件树
            tree_api = f'https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1'
            async with session.get(tree_api, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    self.logger.warning(f'GitHub API 获取文件树失败 {owner}/{repo}/{branch}: HTTP {resp.status}')
                    return []
                tree_data = await resp.json()

            # 查找 m3u/m3u8/txt 文件（排除 README、LICENSE 等非源文件）
            source_results = []
            excluded_names = {'readme', 'license', 'changelog', 'contributing', '.gitignore'}
            for item in tree_data.get('tree', []):
                if item.get('type') != 'blob':
                    continue
                path = item.get('path', '').lower()
                name = path.rsplit('/', 1)[-1]
                if any(path.endswith(ext) for ext in ('.m3u', '.m3u8', '.txt')):
                    # 排除明显的非源文件
                    if name.split('.')[0] in excluded_names:
                        continue
                    url_info = self._build_github_download_url(owner, repo, branch, item['path'], method, mirror_url)
                    source_results.append(url_info)

            return source_results

        except Exception as e:
            self.logger.warning(f'GitHub API 查找源文件异常 {owner}/{repo}: {e}')
            return []

    async def download_with_retry(
        self, url: str, max_retries: int = 2, method: str = 'raw', entry: str = ''
    ) -> str | None:
        """
        带重试机制的下载 - 增强超时处理版

        Args:
            url: 下载URL
            max_retries: 最大重试次数
            method: 下载方式 (raw/api/proxy/mirror)，用于 GitHub 源
            entry: GitHub 条目名，用于日志

        Returns:
            Optional[str]: 成功下载的文件路径,失败返回None
        """
        # ---- 纪码增强:URL安全审查 ----
        safe, reason = is_safe_url(url)
        if not safe:
            self.logger.warning(f'URL安全审查未通过,跳过下载: {url} - {reason}')
            return None

        # 根据下载方式选择策略
        if method == 'proxy':
            # proxy 方式：优先走代理，失败再直连
            strategies = [
                {'type': 'proxy', 'use_proxy': True},
                {'type': 'direct', 'use_proxy': False},
            ]
        elif method == 'api':
            # api 方式：走直连（GitHub API 通常不需要代理）
            strategies = [
                {'type': 'api_direct', 'use_proxy': False},
                {'type': 'api_proxy', 'use_proxy': True},
            ]
        elif method == 'mirror':
            # mirror 方式：走直连（mirror URL 本身就能访问）
            strategies = [
                {'type': 'mirror', 'use_proxy': False},
            ]
        else:
            # raw 方式（默认）
            strategies = [{'type': 'direct', 'use_proxy': False}, {'type': 'proxy', 'use_proxy': True}]

        # 尝试不同的下载策略
        for strategy in strategies:
            try:
                result = await self.download_file(url, strategy, method=method)
                if result:
                    return result
            except SourceDownloadError as e:
                # 继续尝试下一个策略(如直连失败则尝试代理)
                self.logger.warning(f'下载失败 [{strategy["type"]}]: {url} - {e}')
            except Exception as e:
                self.logger.warning(f'下载失败 [{strategy["type"]}]: {url} - {e}')

        self.logger.error(f'所有下载策略均失败: {url}')
        return None

    async def download_file(self, url: str, strategy: dict, method: str = 'raw') -> str | None:
        """
        下载单个文件 - 增强超时处理和错误处理

        Args:
            url: 下载URL
            strategy: 下载策略配置
            method: 下载方式 (raw/api/proxy/mirror)

        Returns:
            Optional[str]: 成功下载的文件路径,失败返回None
        """
        session = None
        try:
            self.logger.info(f'尝试下载 [{strategy["type"]}] (method={method}): {url}')

            # 为GitHub源设置更长的超时时间
            if 'github.com' in url or 'raw.githubusercontent.com' in url:
                timeout_config = aiohttp.ClientTimeout(
                    total=120,  # 总超时120秒
                    connect=60,  # 连接超时60秒
                    sock_connect=60,  # socket连接超时60秒
                    sock_read=60,  # socket读取超时60秒
                )
            else:
                timeout_config = aiohttp.ClientTimeout(total=60, connect=30, sock_connect=30, sock_read=30)

            # M4: 使用共享session替代每次创建销毁
            session = await self.get_session(strategy['use_proxy'])

            # ---- 纪码增强:GitHub API Token注入 ----
            headers = {}
            if self.api_token and ('raw.githubusercontent.com' in url or 'github.com' in url):
                headers['Authorization'] = f'Bearer {self.api_token}'
                self.logger.debug(f'注入Authorization头到GitHub请求: {url[:60]}...')

            # api 方式：请求 GitHub API 返回 raw 内容
            if method == 'api' and 'api.github.com' in url:
                headers['Accept'] = 'application/vnd.github.v3.raw'
                self.logger.debug(f'使用 GitHub API raw content: {url[:60]}...')

            async with session.get(url, timeout=timeout_config, headers=headers) as response:
                if response.status == 200:
                    content = await response.text()

                    # 保存文件
                    filename = self.get_filename_from_url(url)
                    filepath = os.path.join(self.online_dir, filename)

                    async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
                        await f.write(content)

                    return filepath
                else:
                    raise SourceDownloadError(f'HTTP错误 {response.status}: {url}')

        except TimeoutError:
            raise SourceDownloadError(f'请求超时: {url}') from None
        except SourceDownloadError:
            raise
        except Exception as e:
            self.logger.debug(f'下载详细错误 [{strategy["type"]}]: {url} - {e}')
            raise SourceDownloadError(f'下载失败: {url} - {e}') from e
        finally:
            # M4: 不再关闭共享session,由close()方法统一管理
            pass

    def get_filename_from_url(self, url: str) -> str:
        """
        从URL提取安全的文件名

        Args:
            url: 源URL

        Returns:
            str: 安全的文件名
        """
        # 清理URL参数
        clean_url = url.split('?')[0]
        filename = clean_url.split('/')[-1]

        # 如果文件名无效,使用URL的MD5哈希
        if not filename or '.' not in filename:
            import hashlib

            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            filename = f'source_{url_hash}.txt'

        # 移除不安全的字符
        filename = re.sub(r'[^\w\-_.]', '_', filename)

        return filename

    def parse_all_files(self) -> list[dict]:
        """
        解析所有源文件

        Returns:
            List[Dict]: 解析后的源数据列表
        """
        all_sources = []

        # 解析本地文件
        local_dirs = self.config.get_sources()['local_dirs']
        for local_dir in local_dirs:
            if os.path.exists(local_dir):
                try:
                    sources = self.parse_local_files(local_dir)
                    all_sources.extend(sources)
                    self.logger.info(f'成功解析本地目录 {local_dir}: {len(sources)} 个源')
                except Exception as e:
                    self.logger.error(f'解析本地文件失败 {local_dir}: {e}')

        # 解析在线文件
        try:
            online_sources = self.parse_local_files(self.online_dir)
            all_sources.extend(online_sources)
            self.logger.info(f'成功解析在线目录: {len(online_sources)} 个源')
        except Exception as e:
            self.logger.error(f'解析在线文件失败: {e}')

        self.logger.info(f'成功解析 {len(all_sources)} 个源')
        return all_sources

    def apply_ua_settings(self, sources: list[dict]) -> list[dict]:
        """对已解析的源数据应用文件级 UA 设置和频道级 UA 覆盖。

        在 parse_all_files 之后调用，从 Web UI 配置注入 UA。
        UA 优先级：频道级覆盖 > URL内联/EXTVLCOPT/EXTINF属性 > 文件级设置
        """
        file_ua_settings = self.config.get_source_file_ua_settings()
        channel_ua_overrides = self.config.get_channel_ua_overrides()

        if not file_ua_settings and not channel_ua_overrides:
            return sources

        # 构建 source_path → UA 设置映射
        path_ua_map: dict[str, dict] = {}
        sources_config = self.config.get_sources()
        # online URL → filename 映射
        for url in sources_config.get('online_urls', []):
            key = f'online:{url}'
            if key in file_ua_settings:
                ua = file_ua_settings[key]
                if ua.get('enabled') and ua.get('ua_value'):
                    filename = self.get_filename_from_url(url)
                    path_ua_map[filename] = ua
        # local path 映射
        for path in sources_config.get('local_dirs', []):
            key = f'local:{path}'
            if key in file_ua_settings:
                ua = file_ua_settings[key]
                if ua.get('enabled') and ua.get('ua_value'):
                    path_ua_map[path] = ua
                    path_ua_map[os.path.basename(path)] = ua
        # GitHub 源：文件下载到 online_dir，其 source_path 为文件名。
        # 通过「条目→文件名」映射（采集时记录，见 download_all_sources）将文件级 UA
        # 应用到实际下载的文件名，使 GitHub 文件级 UA 精确生效。
        for entry in sources_config.get('github_sources', []):
            key = f'github:{entry}'
            if key in file_ua_settings:
                ua = file_ua_settings[key]
                if ua.get('enabled') and ua.get('ua_value'):
                    path_ua_map[f'__github__{entry}'] = ua
                    for fname in self._github_entry_map.get(entry, []):
                        path_ua_map[fname] = ua
                        path_ua_map[os.path.basename(fname)] = ua

        for source in sources:
            url = source.get('url', '')

            # 频道级覆盖（最高优先级）
            if url in channel_ua_overrides:
                override = channel_ua_overrides[url]
                if override.get('ua_value'):
                    source['user_agent'] = override['ua_value']
                    source['ua_position'] = override.get('ua_position', 'extinf')
                continue

            # 文件级 UA（仅当源没有来自 EXTVLCOPT/EXTINF/URL内联 的 UA 时填充）
            source_path = source.get('source_path', '')
            file_ua = path_ua_map.get(source_path) or path_ua_map.get(os.path.basename(source_path))
            if file_ua:
                if not source.get('user_agent'):
                    source['user_agent'] = file_ua['ua_value']
                source['ua_position'] = file_ua.get('ua_position', 'extinf')

        return sources

    def parse_local_files(self, directory: str) -> list[dict]:
        """
        解析本地目录中的所有源文件

        Args:
            directory: 目录路径

        Returns:
            List[Dict]: 解析后的源数据列表
        """
        sources = []

        # 遍历目录中的所有文件
        for root, _, files in os.walk(directory):
            for file in files:
                # 只处理支持的源文件格式
                if file.endswith(('.m3u', '.m3u8', '.txt')):
                    file_path = os.path.join(root, file)
                    try:
                        file_sources = self.parse_file(file_path)
                        sources.extend(file_sources)
                        self.logger.debug(f'成功解析文件 {file_path}: {len(file_sources)} 个源')
                    except Exception as e:
                        self.logger.error(f'解析文件失败 {file_path}: {e}')

        return sources

    def parse_file(self, file_path: str, file_ua: dict | None = None) -> list[dict]:
        """
        解析单个源文件

        Args:
            file_path: 文件路径
            file_ua: 可选的文件级 UA 配置 {"enabled": bool, "ua_value": str, "ua_position": str}
                     由 Web API 传入，优先于 self.user_agents 的文件级配置

        Returns:
            List[Dict]: 解析后的源数据列表

        Raises:
            SourceParseError: 文件不可读或格式无法解析时抛出
        """
        sources = []

        # 确定源类型(在线或本地)
        source_type = 'online' if file_path.startswith(self.online_dir) else 'local'
        # M9: 使用 os.path.relpath 代替脆弱的字符串替换
        source_path = os.path.relpath(file_path, self.online_dir) if source_type == 'online' else file_path

        # 检查UA配置
        # 优先级: file_ua 参数 (Web API 传入) > self.user_agents (Config UserAgents section)
        file_ua_value = None
        file_ua_position = None
        if file_ua and file_ua.get('enabled') and file_ua.get('ua_value'):
            file_ua_value = file_ua['ua_value']
            file_ua_position = file_ua.get('ua_position', 'extinf')
        elif self.ua_enabled:
            file_ua_value = self.user_agents.get(source_path) or self.user_agents.get(file_path)

        # 读取文件内容,支持多种编码
        try:
            content = self._read_file_with_encoding(file_path)
        except Exception as e:
            raise SourceParseError(f'无法读取文件: {file_path} - {e}') from e

        # 解析内容
        lines = content.splitlines()
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # 跳过M3U文件头
            if line.startswith('#EXTM3U'):
                i += 1
                continue

            # 处理EXTINF格式的频道信息
            if line.startswith('#EXTINF:'):
                extinf = line
                i += 1

                # 跳过 #EXTVLCOPT: 等指令行，直到找到实际 URL 行
                extvlc_referrer = None
                extvlc_user_agent = None
                while i < len(lines):
                    peek = lines[i].strip()
                    if peek.startswith('#EXTVLCOPT:'):
                        opt_str = peek[len('#EXTVLCOPT:') :]
                        if '=' in opt_str:
                            opt_key, opt_val = opt_str.split('=', 1)
                            opt_key = opt_key.strip().lower()
                            opt_val = opt_val.strip()
                            if opt_key == 'http-user-agent':
                                extvlc_user_agent = opt_val
                            elif opt_key == 'http-referrer':
                                extvlc_referrer = opt_val
                        i += 1
                    elif peek.startswith('#') and not peek.startswith('#EXTINF:'):
                        # 跳过其他 #EXT 注释行（如 #EXTGRP 等）
                        i += 1
                    else:
                        break

                if i < len(lines):
                    url = lines[i].strip()
                    if url and not url.startswith('#'):
                        # 提取频道信息
                        name = self.extract_name(extinf)
                        logo = self.extract_logo(extinf)
                        group = self.extract_group(extinf)

                        # 从 EXTINF 属性提取 http-user-agent / http-referrer
                        extinf_ua = self.extract_http_user_agent(extinf)
                        extinf_referrer = self.extract_http_referrer(extinf)

                        # 处理URL中的UA信息
                        url_parts = url.split('|')
                        stream_url = url_parts[0]
                        # UA 优先级：URL 内联 > #EXTVLCOPT > EXTINF 属性 > 文件级配置
                        url_user_agent = extvlc_user_agent or extinf_ua or file_ua_value

                        # ---- 纪码增强:URL安全审查 ----
                        safe, reason = is_safe_url(stream_url)
                        if not safe:
                            self.logger.debug(f'URL安全审查未通过,跳过: {stream_url} - {reason}')
                            i += 1
                            continue

                        if len(url_parts) > 1 and 'User-Agent=' in url_parts[1]:
                            url_user_agent = url_parts[1].replace('User-Agent=', '')

                        # 提取频道信息
                        channel_info = self.channel_rules.extract_channel_info(name, source_id=None)

                        # 构建源数据
                        source_data = {
                            'name': name,
                            'url': stream_url,
                            'logo': logo,
                            'source_type': source_type,
                            'source_path': source_path,
                            'user_agent': url_user_agent,
                            'ua_position': file_ua_position,
                            'group': group,
                            'category': self.channel_rules.determine_category(name),
                            'country': channel_info.get('country', 'CN'),
                            'region': channel_info.get('region'),
                            'language': channel_info.get('language', 'zh'),
                        }

                        # 附加 http-referrer 信息（EXTVLCOPT 优先，其次 EXTINF 属性）
                        final_referrer = extvlc_referrer or extinf_referrer
                        if final_referrer:
                            source_data['http_referrer'] = final_referrer

                        sources.append(source_data)
            else:
                # 处理简单URL格式
                if line and not line.startswith('#') and self.is_valid_url(line):
                    # ---- 纪码增强:URL安全审查 ----
                    clean_line_url = line.split('|')[0]
                    safe, reason = is_safe_url(clean_line_url)
                    if not safe:
                        self.logger.debug(f'URL安全审查未通过,跳过: {clean_line_url} - {reason}')
                        i += 1
                        continue

                    name = f'Channel from {os.path.basename(file_path)}'
                    channel_info = self.channel_rules.extract_channel_info(name, source_id=None)

                    url_parts = line.split('|')
                    stream_url = url_parts[0]
                    url_user_agent = file_ua_value

                    if len(url_parts) > 1 and 'User-Agent=' in url_parts[1]:
                        url_user_agent = url_parts[1].replace('User-Agent=', '')

                    # 构建源数据
                    source_data = {
                        'name': name,
                        'url': stream_url,
                        'logo': None,
                        'source_type': source_type,
                        'source_path': source_path,
                        'user_agent': url_user_agent,
                        'ua_position': file_ua_position,
                        'group': source_path,
                        'category': self.channel_rules.determine_category(name),
                        'country': channel_info.get('country', 'CN'),
                        'region': channel_info.get('region'),
                        'language': channel_info.get('language', 'zh'),
                    }

                    sources.append(source_data)

            i += 1

        return sources

    def _read_file_with_encoding(self, file_path: str) -> str:
        """
        使用多种编码尝试读取文件

        Args:
            file_path: 文件路径

        Returns:
            str: 文件内容

        Raises:
            UnicodeDecodeError: 所有编码尝试都失败时抛出
        """
        encodings = ['utf-8', 'gbk', 'gb2312', 'latin1', 'iso-8859-1']

        for encoding in encodings:
            try:
                with open(file_path, encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue

        # 如果所有编码都失败,使用二进制读取并忽略错误
        with open(file_path, 'rb') as f:
            content_bytes = f.read()
        return content_bytes.decode('utf-8', errors='ignore')

    def extract_name(self, extinf_line: str) -> str:
        """
        从EXTINF行提取频道名称

        Args:
            extinf_line: EXTINF行内容

        Returns:
            str: 频道名称
        """
        match = re.search(r',([^,]+)$', extinf_line)
        if match:
            name = match.group(1).strip()
            # 尝试修复编码问题
            try:
                return name.encode('latin1').decode('utf-8')
            except (UnicodeEncodeError, UnicodeDecodeError):
                return name
        return 'Unknown Channel'

    def extract_logo(self, extinf_line: str) -> str | None:
        """
        从EXTINF行提取频道图标

        Args:
            extinf_line: EXTINF行内容

        Returns:
            Optional[str]: 图标URL,未找到返回None
        """
        match = re.search(r'tvg-logo="([^"]+)"', extinf_line)
        if match:
            return match.group(1).strip()
        return None

    def extract_group(self, extinf_line: str) -> str | None:
        """
        从EXTINF行提取分组信息

        Args:
            extinf_line: EXTINF行内容

        Returns:
            Optional[str]: 分组名称,未找到返回None
        """
        match = re.search(r'group-title="([^"]+)"', extinf_line)
        if match:
            return match.group(1).strip()
        return None

    def extract_http_user_agent(self, extinf_line: str) -> str | None:
        """从EXTINF行提取 http-user-agent 属性"""
        match = re.search(r'http-user-agent="([^"]+)"', extinf_line)
        if match:
            return match.group(1).strip()
        return None

    def extract_http_referrer(self, extinf_line: str) -> str | None:
        """从EXTINF行提取 http-referrer 属性"""
        match = re.search(r'http-referrer="([^"]+)"', extinf_line)
        if match:
            return match.group(1).strip()
        return None

    def is_valid_url(self, url: str) -> bool:
        """
        检查URL是否有效

        Args:
            url: 待检查的URL

        Returns:
            bool: URL是否有效
        """
        try:
            # 清理URL参数和UA信息
            clean_url = url.split('|')[0]
            result = urlparse(clean_url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False

    async def close(self):
        """M4: 关闭共享的aiohttp会话,释放连接资源"""
        if self._session is not None and not self._session.closed:
            try:
                await self._session.close()
                self.logger.info('共享aiohttp会话已关闭')
            except Exception as e:
                self.logger.warning(f'关闭aiohttp会话失败: {e}')
        self._session = None
