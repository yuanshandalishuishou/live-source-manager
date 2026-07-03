#!/usr/bin/env python3
"""系统类 API — /api/test/*, /ws/test, /api/logs/*, /api/audit/*, /api/github/*"""

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from web import models
from web.core import (
    PROJECT_ROOT,
    get_current_user,
    get_session,
    logger,
    manager,
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
            body_text = ''
            try:
                body_text = e.read().decode('utf-8')
            except Exception:
                pass
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
    return {'status': 'idle', 'progress': 0, 'message': '暂无测试数据'}


@router.post('/api/test/trigger')
async def api_trigger_test(current_user: dict = Depends(require_admin)):
    """触发采集任务（预留，当前仅记录）"""
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='test_trigger',
        target='stream_test',
        ip_address='',
    )
    return {'status': 'triggered', 'task_id': 'test_' + str(os.getpid())}


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
async def api_logs(level: str = 'INFO', tail: int = 100, page: int = 1, current_user: dict = Depends(get_current_user)):
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
                    filtered = [l.rstrip('\n\r') for l in all_tail_lines if level.upper() in l.upper()]
                else:
                    filtered = [l.rstrip('\n\r') for l in all_tail_lines]
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
async def api_audit(page: int = 1, size: int = 50, action: str = '', current_user: dict = Depends(require_admin)):
    return models.list_audit_logs(page, size, action_filter=action)


@router.get('/api/audit/actions')
async def api_audit_actions(current_user: dict = Depends(require_admin)):
    """返回所有出现的操作类型列表"""
    return models.list_audit_actions()
