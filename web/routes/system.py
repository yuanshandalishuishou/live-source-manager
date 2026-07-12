#!/usr/bin/env python3
"""系统类 API — /api/test/*, /ws/test, /api/logs/*, /api/audit/*, /api/github/*"""

import asyncio
import concurrent.futures
import contextlib
import json
import os
import threading
import time

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse

from web import models
from web.core import (
    PROJECT_ROOT,
    _load_source_manager,
    get_current_user,
    get_session,
    logger,
    manager,
    parse_all_files_cached,
    read_config,
    read_section,
    require_admin,
)

router = APIRouter()


# ══════════════════════════════════════════════════
# GitHub Token 测试 API
# ══════════════════════════════════════════════════


@router.post('/api/github/test-token')
async def api_test_github_token(request: Request, current_user: dict = Depends(require_admin)):
    """测试 GitHub API Token 是否有效，返回当前速率限制信息"""
    import urllib.error
    import urllib.request

    try:
        body = await request.json()
    except Exception:
        body = {}

    token = (body.get('token') or '').strip()
    # 如果前端没传 token，则从配置读取
    if not token:
        token = read_config().get('GitHub', {}).get('api_token', '')

    api_url = read_config().get('GitHub', {}).get('api_url', 'https://api.github.com').rstrip('/')

    if not token:
        return JSONResponse(
            status_code=200,
            content={
                'valid': False,
                'message': '未配置 Token，当前为匿名访问（60次/时）',
                'rate_limit': {'limit': 60, 'remaining': 0, 'reset': 0},
            },
        )

    try:
        req = urllib.request.Request(
            f'{api_url}/rate_limit',
            headers={
                'Authorization': f'Bearer {token}',
                'Accept': 'application/vnd.github+json',
                'User-Agent': 'LiveSourceManager/1.0',
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            core = data.get('resources', {}).get('core', {})
            return {
                'valid': True,
                'message': f'Token 有效！认证用户：{data.get("rate", {}).get("limit", "unknown")}次/时',
                'rate_limit': {
                    'limit': core.get('limit', 0),
                    'remaining': core.get('remaining', 0),
                    'reset': core.get('reset', 0),
                },
            }
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return JSONResponse(
                status_code=200,
                content={
                    'valid': False,
                    'message': 'Token 无效或已过期（HTTP 401）',
                    'rate_limit': {'limit': 0, 'remaining': 0, 'reset': 0},
                },
            )
        elif e.code == 403:
            with contextlib.suppress(Exception):
                e.read().decode('utf-8')
            remaining = e.headers.get('X-RateLimit-Remaining', '0')
            reset_ts = e.headers.get('X-RateLimit-Reset', '0')
            return JSONResponse(
                status_code=200,
                content={
                    'valid': False,
                    'message': f'已被限流（HTTP 403），剩余 {remaining} 次。{"Token 可能无效。" if token else "匿名访问已达上限。"}',
                    'rate_limit': {
                        'limit': int(e.headers.get('X-RateLimit-Limit', 0)),
                        'remaining': int(remaining),
                        'reset': int(reset_ts),
                    },
                },
            )
        else:
            return JSONResponse(
                status_code=200,
                content={
                    'valid': False,
                    'message': f'GitHub API 返回 HTTP {e.code}',
                    'rate_limit': {'limit': 0, 'remaining': 0, 'reset': 0},
                },
            )
    except Exception as e:
        return JSONResponse(
            status_code=200,
            content={
                'valid': False,
                'message': f'请求失败: {e}',
                'rate_limit': {'limit': 0, 'remaining': 0, 'reset': 0},
            },
        )


# ══════════════════════════════════════════════════
# 测试状态 API
# ══════════════════════════════════════════════════


@router.get('/api/test/status')
async def api_test_status(current_user: dict = Depends(get_current_user)):
    """读取最新测试状态"""
    status_file = os.path.join(PROJECT_ROOT, 'data', 'status', 'latest_test.json')
    if os.path.exists(status_file):
        try:
            with open(status_file) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f'读取 latest_test.json 失败: {e}')
    return {
        'status': 'idle',
        'progress': 0,
        'completed': 0,
        'total': 0,
        'passed': 0,
        'failed': 0,
        'error_breakdown': {},
        'results': [],
        'message': '暂无测试数据',
    }


@router.post('/api/test/trigger')
async def api_trigger_test(request: Request, current_user: dict = Depends(require_admin)):
    """触发流测试：启动后台线程并发测试所有源，实时通过 WS / 状态文件上报进度。
    支持自定义测试规模：请求体 {"limit": 300|500|1000|"all"}，all=全量（默认 300）。"""
    global _app_loop, _test_thread, _test_cancel, _test_pause, _test_tester, _test_starting, _test_gen
    _app_loop = asyncio.get_running_loop()
    # 并发保护：覆盖 to_thread 解析阶段（可能耗时数秒），防止重复触发产生双线程覆盖状态
    if _test_starting.is_set() or (_test_thread is not None and _test_thread.is_alive()):
        logger.warning(
            '[TEST] 触发被拒：已有测试运行中（_test_starting=%s, thread_alive=%s）',
            _test_starting.is_set(),
            _test_thread.is_alive() if _test_thread else None,
        )
        return {
            'status': 'running',
            'message': '测试已在进行中',
            'task_id': 'test_running',
        }
    _test_starting.set()
    _test_gen += 1
    my_gen = _test_gen
    logger.warning(
        '[TEST] 触发接受：gen=%d（上一线程 alive=%s）',
        my_gen,
        _test_thread.is_alive() if _test_thread else None,
    )
    try:
        # 每次触发前重置控制信令（避免上次取消/暂停残留影响本次）
        _test_cancel.clear()
        _test_pause.clear()

        # 解析请求体：支持 {"limit": 300|500|1000|"all"}（规模测试）或 {"file_id": "<源文件ID>"}（按文件测试）
        limit = MAX_TEST_SOURCES
        file_id = None
        try:
            body = await request.body()
            if body:
                payload = json.loads(body) or {}
                raw_limit = payload.get('limit', MAX_TEST_SOURCES)
                if raw_limit in ('all', 'ALL', None, -1):
                    limit = None  # 全量
                elif isinstance(raw_limit, bool):
                    limit = MAX_TEST_SOURCES
                elif isinstance(raw_limit, int):
                    limit = raw_limit if raw_limit > 0 else None
                elif isinstance(raw_limit, str) and raw_limit.strip().isdigit():
                    limit = int(raw_limit)
                else:
                    limit = MAX_TEST_SOURCES
                file_id = payload.get('file_id') or None
        except Exception:
            limit = MAX_TEST_SOURCES

        sm = _load_source_manager()
        if not sm:
            return JSONResponse(
                status_code=503,
                content={
                    'status': 'error',
                    'detail': 'SourceManager 不可用，无法启动测试',
                },
            )

        if file_id:
            # ── 按文件测试模式：只测该文件（在线/本地）内的频道 ──
            sources, err = await asyncio.to_thread(_collect_file_sources, sm, file_id)
            if err:
                return JSONResponse(
                    status_code=400,
                    content={'status': 'error', 'detail': err},
                )
            # 按 url 去重
            total_before_dedup = len(sources)
            seen = set()
            uniq = []
            for s in sources:
                u = s.get('url')
                if u and u not in seen:
                    seen.add(u)
                    uniq.append(s)
            sources = uniq
            total_unique = len(sources)
            truncated = False
            mode = 'file'
        else:
            # ── 规模测试模式：解析全量源并根据 limit 截断 ──
            # parse_all_files_cached 是同步重 IO（首次解析可能数分钟），用 to_thread 卸到线程避免阻塞事件循环
            sources = await asyncio.to_thread(parse_all_files_cached, sm)

            # 按 url 去重（测试前先去重，避免重复测试同一源浪费资源）
            total_before_dedup = len(sources)
            seen = set()
            uniq = []
            for s in sources:
                u = s.get('url')
                if u and u not in seen:
                    seen.add(u)
                    uniq.append(s)
            sources = uniq

            total_unique = len(sources)
            # 自定义上限：limit=None 表示全量（all）；否则按所选数量截断
            truncated = (limit is not None) and (total_unique > limit)
            if truncated:
                sources = sources[:limit]
            mode = 'limit'

        if _test_thread is not None and _test_thread.is_alive():
            return {
                'status': 'running',
                'message': '测试已在进行中',
                'task_id': 'test_running',
            }

        # 文件模式不限制上限（limit 仅用于规模测试截断），传 None 避免 _run_test_task 误报"已限制上限"
        thread_limit = None if mode == 'file' else limit
        _test_thread = threading.Thread(target=_run_test_task, args=(sources, thread_limit, my_gen), daemon=True)
        _test_thread.start()

        models.add_audit_log(
            user_id=current_user['user_id'],
            username=current_user['username'],
            action='test_trigger',
            target='stream_test_file' if mode == 'file' else 'stream_test',
            ip_address='',
        )
        return {
            'status': 'triggered',
            'task_id': 'test_' + str(os.getpid()),
            'total': len(sources),
            'total_unique': total_unique,
            'total_before_dedup': total_before_dedup,
            'dedup_removed': total_before_dedup - total_unique,
            'limit': limit if limit is not None else 'all',
            'truncated': truncated,
            'mode': mode,
        }
    finally:
        _test_starting.clear()


@router.post('/api/test/pause')
async def api_test_pause(current_user: dict = Depends(require_admin)):
    """暂停正在进行的测试（立即终止当前在跑的源，恢复后重测被中断的源）。

    若后台线程已意外终止但状态仍卡在 running/paused，强制复位为已取消，
    避免前端永久显示「进行中」假象（已停止的任务无法真正暂停）。
    """
    global _test_pause, _test_tester
    # 线程已死但状态仍卡在 running/paused（僵尸任务）→ 强制复位
    if _test_thread is None or not _test_thread.is_alive():
        with _test_lock:
            if _test_state.get('status') in ('running', 'paused'):
                for res in _test_state.get('results', []):
                    if res.get('status') in ('waiting', 'running', 'testing'):
                        res['status'] = 'cancelled'
                _test_state['status'] = 'cancelled'
                _test_state['note'] = (_test_state.get('note') or '') + '（已强制复位）'
        _test_pause.clear()
        _publish_test_state()
        return {'status': 'cancelled', 'message': '已强制复位测试状态（原任务已停止）'}
    _test_pause.set()
    # 立即终止当前所有在跑的 ffprobe/ffmpeg 子进程，使暂停即时生效
    if _test_tester is not None:
        with contextlib.suppress(Exception):
            _test_tester.abort()
    return {'status': 'paused', 'message': '已发送暂停指令'}


@router.post('/api/test/resume')
async def api_test_resume(current_user: dict = Depends(require_admin)):
    """恢复被暂停的测试"""
    global _test_pause, _test_tester
    if _test_thread is None or not _test_thread.is_alive():
        return {'status': 'error', 'detail': '当前没有运行中的测试'}
    # 清除中断标志，让被中断的源可以重新正常测试
    if _test_tester is not None:
        with contextlib.suppress(Exception):
            _test_tester.clear_abort()
    _test_pause.clear()
    return {'status': 'resumed', 'message': '已恢复测试'}


@router.post('/api/test/cancel')
async def api_test_cancel(current_user: dict = Depends(require_admin)):
    """取消正在进行的测试（立即终止所有在跑源，未开始的源标为已取消）。

    若后台线程已意外终止（崩溃/卡死后被回收）但状态仍卡在 running/paused，
    仍强制复位状态，避免前端永久显示「进行中」假象。
    """
    global _test_cancel, _test_pause, _test_tester
    # 线程已死但状态仍卡在 running/paused（僵尸任务）→ 强制复位，避免永久假「进行中」
    if _test_thread is None or not _test_thread.is_alive():
        with _test_lock:
            if _test_state.get('status') in ('running', 'paused'):
                for res in _test_state.get('results', []):
                    if res.get('status') in ('waiting', 'running', 'testing'):
                        res['status'] = 'cancelled'
                _test_state['status'] = 'cancelled'
                _test_state['note'] = (_test_state.get('note') or '') + '（已强制复位）'
        _test_cancel.clear()
        _test_pause.clear()
        _publish_test_state()
        return {'status': 'cancelled', 'message': '已强制复位测试状态（原任务已停止）'}
    _test_cancel.set()
    _test_pause.clear()
    # 立即终止当前所有在跑的 ffprobe/ffmpeg 子进程，使取消即时生效
    if _test_tester is not None:
        with contextlib.suppress(Exception):
            _test_tester.abort()
    return {'status': 'cancelled', 'message': '已发送取消指令'}


# ══════════════════════════════════════════════════
# WebSocket 端点（实时测试推送）
# ══════════════════════════════════════════════════


@router.websocket('/ws/test')
async def websocket_test_endpoint(ws: WebSocket):
    # 纪码修复 P0-1: 先验证认证（session 在内存和 SQLite 中都有效），再 accept。
    # 使用 get_session() 同时检查内存和 SQLite 中的 session。
    session_id = ws.cookies.get('session')
    session = get_session(session_id) if session_id else None
    if not session:
        await ws.close(code=4001, reason='unauthorized')
        return
    await ws.accept()

    connected = await manager.connect(ws)
    if not connected:
        # 连接数已达上限
        return
    try:
        while True:
            data = await ws.receive_text()
            if data == 'ping':
                await ws.send_json({'type': 'pong'})
    except WebSocketDisconnect:
        logger.info('WebSocket 客户端正常断开')
    except Exception as e:
        logger.warning(f'WebSocket 通信异常: {e}')
    finally:
        await manager.disconnect(ws)


# ══════════════════════════════════════════════════
# 日志 API
# ══════════════════════════════════════════════════


@router.get('/api/logs')
async def api_logs(
    level: str = 'INFO',
    tail: int = 100,
    page: int = 1,
    current_user: dict = Depends(get_current_user),
):
    """读取应用日志文件，支持分页"""
    config_data = read_section('Logging')
    log_file = config_data.get('file', './log/app.log')
    logs = []
    total_lines = 0
    if os.path.exists(log_file):
        try:
            total_lines = 0
            filtered = []
            # 使用从尾读取策略，避免 OOM
            block_size = 8192
            with open(log_file, 'rb') as f:
                f.seek(0, 2)  # 尾端
                file_size = f.tell()
                total_lines = 0
                # 先统计总行数
                f.seek(0)
                for _ in f:
                    total_lines += 1
                # 从尾端读取最后 block_size*4 字节（足够取 tail 行）
                read_size = min(file_size, block_size * (tail // 10 + 4))
                f.seek(file_size - read_size)
                tail_bytes = f.read(read_size)
                # 解码
                tail_text = tail_bytes.decode('utf-8', errors='replace')
                all_tail_lines = tail_text.split('\n')
                # 按级别筛选
                if level.upper() != 'ALL':
                    filtered = [line.rstrip('\n\r') for line in all_tail_lines if level.upper() in line.upper()]
                else:
                    filtered = [line.rstrip('\n\r') for line in all_tail_lines]
                # 取最后 tail 行
                logs = filtered[-tail:]
        except Exception as e:
            logs = [f'读取日志失败: {e}']
    return {'logs': logs, 'total': len(logs), 'file_lines': total_lines}


@router.get('/api/logs/download')
async def api_logs_download(current_user: dict = Depends(require_admin)):
    """下载日志文件（返回 JSON 路径，实际文件通过静态路径处理）"""
    config_data = read_section('Logging')
    log_file = config_data.get('file', './log/app.log')
    if os.path.exists(log_file):
        return JSONResponse({'path': log_file, 'filename': os.path.basename(log_file)})
    raise HTTPException(status_code=404, detail='日志文件不存在')


# ══════════════════════════════════════════════════
# 审计日志 API
# ══════════════════════════════════════════════════


@router.get('/api/audit')
async def api_audit(
    page: int = 1,
    size: int = 50,
    action: str = '',
    current_user: dict = Depends(require_admin),
):
    return models.list_audit_logs(page, size, action_filter=action)


@router.get('/api/audit/actions')
async def api_audit_actions(current_user: dict = Depends(require_admin)):
    """返回所有出现的操作类型列表"""
    return models.list_audit_actions()


# ══════════════════════════════════════════════════
# 实时测试后台任务（触发后并发测试所有源，实时上报进度）
# ══════════════════════════════════════════════════


def _collect_file_sources(sm, file_id: str):
    """按源文件 ID 收集该文件（在线/本地）内的所有频道。

    返回 (sources, error_msg)：
      - sources: 解析出的频道列表（dict，含 'url'）
      - error_msg: 出错时的中文说明；成功为 None
    GitHub 源暂不支持按文件测试（需先采集），会返回可读错误。
    """
    from web.routes import sources as _src_routes  # 局部导入避免循环依赖

    found = _src_routes._find_source_file_by_id(file_id)
    if not found:
        return None, '未找到对应的源文件（file_id 无效，请刷新文件列表重试）'
    src_type, value = found

    if src_type == 'online':
        file_path = _src_routes._get_online_file_path(value)
        if not os.path.isfile(file_path):
            return None, '该在线文件尚未下载，请先在源管理页点击"采集所有源"后再试'
        file_ua = _src_routes._get_source_file_ua('online', value)
        try:
            channels = sm.parse_file(file_path, file_ua=file_ua if file_ua else None)
        except Exception as e:
            return None, f'解析在线文件失败: {e}'
        return channels, None

    if src_type == 'local':
        abs_path = _src_routes._resolve_local_path(value)
        if not os.path.exists(abs_path):
            return None, f'本地路径不存在: {abs_path}'
        channels = []
        try:
            if os.path.isfile(abs_path):
                channels = sm.parse_file(abs_path)
            elif os.path.isdir(abs_path):
                for fn in os.listdir(abs_path):
                    if fn.endswith(('.m3u', '.m3u8', '.txt')):
                        channels.extend(sm.parse_file(os.path.join(abs_path, fn)))
        except Exception as e:
            return None, f'解析本地文件失败: {e}'
        return channels, None

    if src_type == 'github':
        return None, 'GitHub 源暂不支持按文件测试，请先采集后再选择对应的在线/本地文件'

    return None, '未知源类型，无法按文件测试'


# 防护：单次测试最多测这么多源。ffprobe 并发受 Testing.max_concurrent_ffprobe 限制（默认 16），
# 全量 16220 源仍需数小时，默认限制单次规模以防把机器跑瘫。需要全量测试时调大此值即可。
MAX_TEST_SOURCES = 300

# 当前测试运行状态（内存态，供 /api/test/status 与 WS 推送共享）
_test_state: dict = {
    'status': 'idle',
    'total': 0,
    'completed': 0,
    'passed': 0,
    'failed': 0,
    'error_breakdown': {},
    'results': [],
    'note': '',
}


# 失败原因 → 聚合类别映射（供「实时测试结果」页按错误类别聚合统计）。
# 覆盖 StreamTester.test_single_stream 产生的所有 error_reason 形态：
#  - 带 'cat: raw' 前缀（如 connection_failed: ... Error number -138）
#  - 无前缀特殊原因（network_incompatible / ad_playlist / timeout / frozen until / ...）
def categorize_reason(reason):
    if not reason:
        return 'unknown'
    r = reason.lower()
    # 剥离 'after_N_retries: ' 外层包裹（真实 reason 形如 after_2_retries: connection_failed: ...）
    if r.startswith('after_') and ':' in r:
        reason = reason.split(':', 1)[1].strip()
        r = reason.lower()
    # 无 'cat:' 前缀的特殊原因
    if r.startswith('timeout') or '单源测试超过' in r:
        return 'timeout'
    if r.startswith('network_incompatible'):
        return 'network_incompatible'
    if r.startswith('ad_playlist'):
        return 'ad_playlist'
    if r.startswith('no_valid_streams'):
        return 'no_valid_streams'
    if r.startswith('no_probe_tool_available'):
        return 'no_probe_tool_available'
    if r.startswith('json_parse_error'):
        return 'json_parse_error'
    if r.startswith('global_blacklist'):
        return 'global_blacklist'
    if r.startswith('frozen until'):
        return 'frozen'
    if r.startswith('aborted'):
        return 'aborted'
    if r.startswith('exception:'):
        return 'exception'
    # 带 'cat: raw' 前缀：提取 cat 部分
    if ':' in reason:
        head = reason.split(':', 1)[0].strip().lower()
        _known = {
            'connection_failed',
            'connection_refused',
            'dns_failed',
            'auth_blocked',
            'not_found',
            'ffprobe_error',
            'ffprobe_failed_no_output',
        }
        if head in _known:
            return head
    # 兜底（极少触发）：按关键词粗分
    if 'error number -138' in r or 'connection failed' in r or 'could not connect' in r:
        return 'connection_failed'
    if 'name or service not known' in r or 'resolve' in r or 'getaddrinfo' in r:
        return 'dns_failed'
    if '403' in r or '401' in r or 'forbidden' in r or 'unauthorized' in r or 'expired' in r:
        return 'auth_blocked'
    if '404' in r or 'not found' in r:
        return 'not_found'
    return 'ffprobe_error'


_test_lock = threading.Lock()
_test_thread: threading.Thread | None = None
_app_loop: asyncio.AbstractEventLoop | None = None
# 控制信令：暂停 / 取消（set = 生效）。每次触发前由 api_trigger_test 重置。
_test_pause = threading.Event()
_test_cancel = threading.Event()
# 当前测试任务的 StreamTester 实例（供暂停/取消 API 立即终止在跑子进程）
_test_tester = None
# 触发准备锁：覆盖 to_thread 解析阶段，防止并发重复触发导致双线程覆盖状态
_test_starting = threading.Event()
# 测试任务世代：每次触发 +1，过期（被新触发取代）的任务自动退出，防止双任务并发写 _test_state 互相覆盖
_test_gen = 0
# 单源测试阻塞兜底超时（秒）：防止个别源的无界网络 I/O（如 DNS/连接被黑洞）拖垮整轮测试
TEST_SOURCE_TIMEOUT = 60


def schedule_auto_test() -> bool:
    """供自动扫描调度器（core.py _auto_scan_scheduler）调用的无 CSRF 测试触发器。

    复用与 api_trigger_test 完全相同的并发保护（_test_starting + _test_thread 探查 + 世代锁），
    触发**全量**测试（不截断，等效 limit='all'）。返回 True 表示已成功触发；
    False 表示已有测试运行中（调度器据此跳过本次触发，避免重复跑）。

    设计要点：
    - 无 CSRF：由进程内调度协程直接调用，不走 HTTP，无需令牌。
    - 解析重 IO（parse_all_files_cached 首次可能数分钟）放到独立准备线程，
      不阻塞调度协程的 60s 轮询循环；准备完成后启动真正的测试线程。
    - _test_starting 仅保护「准备阶段」，测试运行期靠 _test_thread.is_alive() 防护，
      与 api_trigger_test 语义一致。
    """
    global _test_thread, _test_starting, _test_gen
    # 并发保护：与 api_trigger_test 共用 _test_starting，防止重复触发
    if _test_starting.is_set() or (_test_thread is not None and _test_thread.is_alive()):
        logger.warning(
            '[AUTO-SCAN] 触发被拒：已有测试运行中（_test_starting=%s, thread_alive=%s）',
            _test_starting.is_set(),
            _test_thread.is_alive() if _test_thread else None,
        )
        return False
    _test_starting.set()
    _test_gen += 1
    my_gen = _test_gen
    # 每次触发前重置控制信令（避免上次取消/暂停残留影响本次）
    _test_cancel.clear()
    _test_pause.clear()
    logger.warning('[AUTO-SCAN] 接受自动测试触发：gen=%d', my_gen)

    def _prep_and_run() -> None:
        try:
            sm = _load_source_manager()
            if not sm:
                logger.error('[AUTO-SCAN] SourceManager 不可用，放弃本次自动测试')
                return
            # 全量解析（命中预热缓存后近乎瞬时；冷缓存时在此线程内等待，不阻塞调度协程）
            sources = parse_all_files_cached(sm)
            # 测试前先去重，避免重复测试同一源浪费资源
            seen = set()
            uniq: list[dict] = []
            for s in sources:
                u = s.get('url')
                if u and u not in seen:
                    seen.add(u)
                    uniq.append(s)
            sources = uniq
            logger.info('[AUTO-SCAN] 准备完成：去重后共 %d 个源，启动测试线程', len(sources))
            global _test_thread
            _test_thread = threading.Thread(target=_run_test_task, args=(sources, None, my_gen), daemon=True)
            _test_thread.start()
            try:
                # 审计日志（user_id=-1 表示系统自动触发；失败不影响测试）
                models.add_audit_log(
                    user_id=-1,
                    username='auto_scan',
                    action='test_trigger',
                    target='stream_test_auto',
                    ip_address='',
                )
            except Exception as e:
                logger.debug('[AUTO-SCAN] 写入审计日志失败（忽略）: %s', e)
        except Exception as e:
            logger.exception('[AUTO-SCAN] 准备/启动自动测试失败: %s', e)
        finally:
            # 准备阶段结束，释放 _test_starting（运行期交予 _test_thread.is_alive() 防护）
            _test_starting.clear()

    threading.Thread(target=_prep_and_run, daemon=True).start()
    return True


def _publish_test_state() -> None:
    """把当前测试状态写入 latest_test.json（前端轮询兜底）并尝试通过 WS 广播（线程安全）"""
    with _test_lock:
        snap = json.loads(json.dumps(_test_state))
    try:
        status_dir = os.path.join(PROJECT_ROOT, 'data', 'status')
        os.makedirs(status_dir, exist_ok=True)
        # P2-④ 加固：原子写（先写临时文件再 os.replace），避免并发/崩溃写半截导致前端解析失败
        target = os.path.join(status_dir, 'latest_test.json')
        tmp = target + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(snap, f, ensure_ascii=False)
        os.replace(tmp, target)
    except Exception as e:
        logger.warning(f'写入 latest_test.json 失败: {e}')
    if _app_loop is not None:
        try:
            asyncio.run_coroutine_threadsafe(manager.broadcast(snap), _app_loop)
        except Exception as e:
            logger.warning(f'WS 广播测试状态失败: {e}')


def _run_test_task(sources: list[dict], limit: int | None = None, gen: int = 0) -> None:
    """后台线程：分批并发测试所有源并实时上报进度，支持暂停/取消控制。

    采用分批提交（每批约 max_ff*1.5 个），批次之间与每完成一个源时检查
    _test_pause（暂停）/ _test_cancel（取消）信令，使控制指令能在当前批次
    结束或下一个源开始前及时响应，避免一次性全量提交导致无法中途停止。

    gen: 本次任务世代（由 api_trigger_test 分配）。若启动时已不是当前世代，
    说明被更新的触发取代，直接退出，避免并发双任务写同一份 _test_state 互相覆盖。
    """
    global _test_state, _test_pause, _test_cancel, _test_tester, _test_gen
    # 世代校验：过期任务立即退出，且不触碰 _test_state（防止覆盖正在进行的测试进度）
    if gen != _test_gen:
        logger.warning('[TEST] 丢弃过期任务 gen=%d（当前=%d），不覆盖状态', gen, _test_gen)
        return
    logger.warning(
        '[TEST] 任务启动 gen=%d tid=%s 源数=%d',
        gen,
        threading.get_ident(),
        len(sources),
    )
    sm = _load_source_manager()
    if not sm:
        with _test_lock:
            _test_state = {
                'status': 'error',
                'message': 'SourceManager 不可用',
                'total': 0,
                'completed': 0,
                'passed': 0,
                'failed': 0,
                'error_breakdown': {},
                'results': [],
                'note': '',
            }
        _publish_test_state()
        return

    try:
        from app import StreamTester

        tester = StreamTester(sm.config, sm.logger)
        # 记录实例并清除上次残留的中断标志，供暂停/取消立即终止子进程
        _test_tester = tester
        tester.clear_abort()
    except Exception as e:
        with _test_lock:
            _test_state = {
                'status': 'error',
                'message': f'StreamTester 初始化失败: {e}',
                'total': 0,
                'completed': 0,
                'passed': 0,
                'failed': 0,
                'error_breakdown': {},
                'results': [],
                'note': '',
            }
        _publish_test_state()
        return

    total = len(sources)

    # 预构建「所在源展示地址」映射：
    #   - online 源：下载文件名 → 原始在线 URL
    #   - github 源：下载文件名 → 仓库条目（源管理地址，如 owner/repo）
    #   - local  源：完整文件路径 → 导入的本地地址（源管理地址）
    # 来源：① 在线 URL 配置 ② GitHub 采集 discover 还原（优先仓库条目）③ 本地目录配置
    online_url_map: dict[str, str] = {}  # 下载文件名 -> 原始地址（URL 或 repo entry）
    _local_addr_map: dict[str, str] = {}  # 归一化本地路径 -> 导入的本地地址

    def _norm_local(p: str) -> str:
        try:
            return os.path.normpath(os.path.abspath(p))
        except Exception:
            return p

    try:
        from web.routes import sources as _src_routes

        # ① 在线 URL（config + DB）
        _online_urls = sm.config.get_sources().get('online_urls') or []
        if isinstance(_online_urls, str):
            _online_urls = [u.strip() for u in _online_urls.replace('\r', '').split('\n') if u.strip()]
        with contextlib.suppress(Exception):
            _online_urls = list(_online_urls) + list(_src_routes._read_online_urls_from_db() or [])
        for _u in _online_urls:
            if not _u:
                continue
            try:
                _fn = sm.get_filename_from_url(_u)
            except Exception:
                _fn = _u.split('?')[0].rstrip('/').split('/')[-1]
            if _fn:
                online_url_map.setdefault(_fn, _u)
        # ② GitHub 源：优先用采集时落盘的 entry→文件名 映射（离线可靠），反查 文件名→仓库条目
        #    该映射随 SourceManager 启动从 config/online/.github_entry_map.json 加载
        try:
            for _entry, _fns in (getattr(sm, '_github_entry_map', None) or {}).items():
                if not _entry or not isinstance(_fns, list):
                    continue
                for _fn in _fns:
                    if _fn:
                        online_url_map.setdefault(_fn, _entry)
        except Exception as e:
            logger.warning('[TEST] 读取 GitHub 条目映射失败(忽略): %s', e)
        # ③ 持久化的 github 文件映射（上次 discover 落盘，网络不可达时兜底，避免暴露下载文件名）
        _gh_map_path = os.path.join(PROJECT_ROOT, 'data', 'status', 'github_file_map.json')

        def _load_gh_map() -> dict:
            try:
                if os.path.isfile(_gh_map_path):
                    with open(_gh_map_path, encoding='utf-8') as _f:
                        return json.load(_f)
            except Exception:
                pass
            return {}

        def _save_gh_map(m: dict) -> None:
            try:
                os.makedirs(os.path.dirname(_gh_map_path), exist_ok=True)
                with open(_gh_map_path, 'w', encoding='utf-8') as _f:
                    json.dump(m, _f, ensure_ascii=False)
            except Exception:
                pass

        for _fn, _addr in _load_gh_map().items():
            if _fn and _addr:
                online_url_map.setdefault(_fn, _addr)
        # ④ 实时 discover 还原仓库条目（联网，优先覆盖），并回写两处持久化映射：
        #    - data/status/github_file_map.json（本函数下次兜底）
        #    - config/online/.github_entry_map.json（供 SourceManager 离线反查，最可靠）
        _gh = []
        with contextlib.suppress(Exception):
            _gh = _src_routes._read_github_sources_from_db() or []
        if _gh and sm and hasattr(sm, '_discover_github_source_urls'):
            try:
                _loop = asyncio.new_event_loop()
                try:
                    _discovered = _loop.run_until_complete(
                        asyncio.wait_for(sm._discover_github_source_urls(_gh), timeout=30)
                    )
                finally:
                    _loop.close()
                _fresh: dict[str, str] = {}
                _entry_map: dict[str, list[str]] = {}
                for _d in _discovered or []:
                    if not isinstance(_d, dict):
                        continue
                    _u = _d.get('url')
                    _entry = _d.get('entry')
                    if not _u:
                        continue
                    try:
                        _fn = sm.get_filename_from_url(_u)
                    except Exception:
                        _fn = _u.split('?')[0].rstrip('/').split('/')[-1]
                    if _fn:
                        _addr = _entry or _u
                        online_url_map[_fn] = _addr  # 仓库条目优先
                        _fresh[_fn] = _addr
                        if _entry:
                            _entry_map.setdefault(_entry, []).append(_fn)
                if _fresh:
                    _save_gh_map(_fresh)
                    try:
                        # 回写 config/online/.github_entry_map.json（entry→[filenames]）供离线反查
                        sm._github_entry_map.update(_entry_map)
                        sm._save_github_entry_map()
                    except Exception as e:
                        logger.warning('[TEST] 回写 GitHub 条目映射失败(忽略): %s', e)
            except Exception as e:
                logger.warning('[TEST] GitHub 源 discover 失败，使用已有映射: %s', e)
        # ⑤ 本地目录/文件：完整路径 → 导入的本地地址（源管理地址）
        _local_dirs = sm.config.get_sources().get('local_dirs') or []
        if isinstance(_local_dirs, str):
            _local_dirs = [p.strip() for p in _local_dirs.replace('\r', '').split(',') if p.strip()]
        with contextlib.suppress(Exception):
            _local_dirs = list(_local_dirs) + list(_src_routes._read_local_dirs_from_db() or [])
        for _p in _local_dirs:
            if not _p:
                continue
            _local_addr_map[_norm_local(_src_routes._resolve_local_path(_p))] = _p
    except Exception as e:
        logger.warning('[TEST] 构建所在源地址映射异常: %s', e)
        online_url_map = online_url_map or {}

    def _resolve_source_label(src_type: str, src_path: str) -> str:
        """所在源展示：online/github 还原为原始地址（在线 URL 或 GitHub 仓库条目）；
        local 还原为导入的本地地址（源管理地址）。绝不暴露 config/online/ 下载路径。"""
        if src_type == 'online':
            _fn = os.path.basename(src_path)
            return online_url_map.get(_fn) or _fn
        # local：用完整路径前缀匹配导入的本地地址
        _norm = _norm_local(src_path)
        if _norm in _local_addr_map:
            return _local_addr_map[_norm]
        _best = None
        for _k, _v in _local_addr_map.items():
            if (_norm == _k or _norm.startswith(_k + os.sep)) and (_best is None or len(_k) > len(_best)):
                _best = _k
        if _best:
            return _local_addr_map[_best]
        return src_path

    # 解析可能耗时（首次数十秒）；解析完成后再次校验世代，若已被更新的触发取代则退出
    if gen != _test_gen:
        logger.warning('[TEST] 解析完成后任务已过期 gen=%d（当前=%d），退出', gen, _test_gen)
        return

    # 初始化状态：全部为 waiting
    with _test_lock:
        _test_state = {
            'status': 'running',
            'total': total,
            'completed': 0,
            'passed': 0,
            'failed': 0,
            'error_breakdown': {},
            'results': [
                {
                    'name': s.get('name', s.get('url', '?')),
                    'status': 'waiting',
                    'url': s.get('url', ''),
                    'user_agent': s.get('user_agent', '') or '',
                    'source_type': s.get('source_type', ''),
                    'source': _resolve_source_label(s.get('source_type', ''), s.get('source_path', '')),
                }
                for s in sources
            ],
            'note': f'本次测试 {total} 个源' + (f'（已限制上限 {limit}）' if limit and total >= limit else ''),
        }
    _publish_test_state()

    # 并发度跟随 ffprobe Semaphore 上限（与 StreamTester 内部一致）
    try:
        max_ff = int((getattr(tester, '_ffprobe_semaphore', None) and tester._ffprobe_semaphore._value) or 16)
    except Exception:
        max_ff = 16
    batch_size = max(2, int(max_ff * 1.5))

    done = set()  # 已完成（成功/失败）的源索引；被中断的源不在此集合，恢复后重测
    cancelled = False
    while True:
        # ── 取消优先 ──
        if _test_cancel.is_set():
            cancelled = True
            break

        # ── 世代过期（被新触发取代）：礼貌退出，不覆盖新任务状态 ──
        if gen != _test_gen:
            logger.warning('[TEST] 任务 gen=%d 循环中已过期（当前=%d），退出', gen, _test_gen)
            return

        # ── 暂停：进入新批次前若已暂停，立即阻塞等待恢复 ──
        if _test_pause.is_set():
            if gen == _test_gen:
                with _test_lock:
                    _test_state['status'] = 'paused'
                _publish_test_state()
            while _test_pause.is_set() and not _test_cancel.is_set():
                time.sleep(0.3)
            if _test_cancel.is_set():
                cancelled = True
                break
            with _test_lock:
                _test_state['status'] = 'running'
            _publish_test_state()
            continue

        # ── 取本批（跳过已完成；被中断的源仍留在 pending 以便恢复后重测）──
        batch_idx = [i for i in range(total) if i not in done]
        if not batch_idx:
            break
        batch_idx = batch_idx[:batch_size]
        batch = [sources[i] for i in batch_idx]
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=len(batch))
        try:
            futs = {ex.submit(tester.test_single_stream, s): bi for s, bi in zip(batch, batch_idx, strict=False)}
            for fut in concurrent.futures.as_completed(futs):
                bi = futs[fut]
                # 取消信号：立即终止在跑子进程（后续 fut 会快速返回 interrupted）
                if _test_cancel.is_set():
                    with contextlib.suppress(Exception):
                        tester.abort()
                try:
                    r = fut.result(timeout=TEST_SOURCE_TIMEOUT)
                except concurrent.futures.TimeoutError:
                    # 单源测试无界阻塞（如 DNS/连接被黑洞）兜底：超时不拖垮整轮，
                    # 标记失败并尽量终止其在跑的 ffprobe，随后继续后续源
                    with contextlib.suppress(Exception):
                        tester.abort()
                    r = {
                        **sources[bi],
                        'status': 'failed',
                        'response_time': None,
                        'error_reason': f'timeout: 单源测试超过 {TEST_SOURCE_TIMEOUT}s 无响应',
                    }
                except Exception as e:
                    r = {
                        **sources[bi],
                        'status': 'failed',
                        'response_time': None,
                        'error_reason': f'exception:{e}',
                    }

                raw_status = r.get('status')

                # ── 被中断（暂停/取消打断）：保持 waiting，不入 done，恢复后重测 ──
                if raw_status == 'interrupted':
                    if gen != _test_gen:
                        return
                    with _test_lock:
                        if _test_state['results'] and len(_test_state['results']) > bi:
                            _test_state['results'][bi] = {
                                'name': sources[bi].get('name', sources[bi].get('url', '?')),
                                'status': 'waiting',
                                'url': sources[bi].get('url', ''),
                                'user_agent': sources[bi].get('user_agent', '') or '',
                                'source_type': sources[bi].get('source_type', ''),
                                'source': _resolve_source_label(
                                    sources[bi].get('source_type', ''),
                                    sources[bi].get('source_path', ''),
                                ),
                            }
                    continue

                disp_status = 'passed' if raw_status == 'success' else 'failed'
                entry = {
                    'name': r.get('name', sources[bi].get('name', '?')),
                    'status': disp_status,
                }
                # 展示字段：真实地址、生效 UA、所在源
                entry['url'] = r.get('url') or sources[bi].get('url', '')
                _ua = r.get('user_agent') or sources[bi].get('user_agent') or ''
                if not _ua:
                    try:
                        _uas = read_config().get('UserAgents', {}).get('user_agents', '') or ''
                        _ua = _uas.splitlines()[0].strip() if _uas.strip() else ''
                    except Exception:
                        _ua = ''
                entry['user_agent'] = _ua
                entry['source_type'] = r.get('source_type') or sources[bi].get('source_type', '')
                entry['source'] = _resolve_source_label(
                    entry['source_type'],
                    r.get('source_path') or sources[bi].get('source_path', ''),
                )
                if r.get('response_time') is not None:
                    rt = r['response_time']
                    entry['response_time'] = round(rt / 1000, 2) if rt > 1000 else rt
                if r.get('error_reason'):
                    entry['reason'] = r['error_reason']
                if r.get('resolution'):
                    entry['resolution'] = r['resolution']

                with _test_lock:
                    if gen == _test_gen and _test_state['results'] and len(_test_state['results']) > bi:
                        _test_state['results'][bi] = entry
                        _test_state['completed'] += 1
                        done.add(bi)
                        if disp_status == 'passed':
                            _test_state['passed'] += 1
                        else:
                            _test_state['failed'] += 1
                            _cat = categorize_reason(entry.get('reason'))
                            entry['category'] = _cat
                            _test_state['error_breakdown'][_cat] = _test_state['error_breakdown'].get(_cat, 0) + 1
                if gen == _test_gen:
                    _publish_test_state()
                else:
                    return

                # ── 暂停：每完成一个源即响应（立即终止在跑子进程，进入阻塞等待）──
                if _test_pause.is_set():
                    with contextlib.suppress(Exception):
                        tester.abort()
                    if gen == _test_gen:
                        with _test_lock:
                            _test_state['status'] = 'paused'
                        _publish_test_state()
                    while _test_pause.is_set() and not _test_cancel.is_set():
                        time.sleep(0.3)
                    if _test_cancel.is_set():
                        cancelled = True
                        break
                    with _test_lock:
                        _test_state['status'] = 'running'
                    _publish_test_state()
        finally:
            # 不阻塞等待：个别卡在网络 I/O 的 worker 可能在后台继续（泄漏线程），
            # 但避免 shutdown(wait=True) 被其阻塞导致整轮停滞；每批新建线程池，
            # 已完成 worker 由 GC 回收，卡住 worker 在自身 I/O 超时后自然结束
            ex.shutdown(wait=False, cancel_futures=True)
        if cancelled:
            break

    # 收尾
    with _test_lock:
        if cancelled:
            # 未完成的源（waiting / 被中断）统一标记为已取消
            for res in _test_state['results']:
                if res.get('status') in ('waiting',):
                    res['status'] = 'cancelled'
            _test_state['status'] = 'cancelled'
            _test_state['note'] = f'已手动取消（完成 {_test_state["completed"]}/{total}）'
        else:
            _test_state['status'] = 'completed'
    _publish_test_state()
