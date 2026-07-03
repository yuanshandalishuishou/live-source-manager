#!/usr/bin/env python3
"""Dashboard 统计 API"""

import json
import os

from fastapi import APIRouter, Depends

from web.core import (
    PROJECT_ROOT,
    _get_source_summary,
    _get_system_info,
    _load_source_manager,
    get_current_user,
    require_admin,
)

router = APIRouter()


@router.get('/api/dashboard/stats')
async def api_dashboard_stats(current_user: dict = Depends(get_current_user)):
    return _get_source_summary()


@router.get('/api/dashboard/test-info')
async def api_dashboard_test_info(current_user: dict = Depends(get_current_user)):
    """仪表盘 - 最后测试时间信息"""
    status_file = os.path.join(PROJECT_ROOT, 'data', 'status', 'latest_test.json')
    if not os.path.exists(status_file):
        return '<div class="test-details">暂无测试记录</div>'
    try:
        with open(status_file) as f:
            data = json.load(f)
        started = data.get('started_at', '未知')
        status = data.get('status', 'idle')
        total = data.get('total', 0)
        passed = data.get('passed', 0)
        failed = data.get('failed', 0)
        pct = f'{(passed / total * 100):.1f}%' if total > 0 else '-'
        status_map = {'running': '运行中', 'completed': '已完成', 'idle': '空闲'}
        return f"""
        <div class="test-details">
            <p>最后测试时间: {started}</p>
            <p>状态: {status_map.get(status, status)}</p>
            <p>通过 <strong>{passed}</strong> / 失败 <strong>{failed}</strong> / 有效率 <strong>{pct}</strong></p>
        </div>
        """
    except Exception as e:
        from web.core import logger

        logger.warning(f'读取 latest_test.json 失败: {e}')
        return '<div class="test-details">读取测试状态失败</div>'


@router.get('/api/dashboard/channel-stats')
async def api_dashboard_channel_stats(current_user: dict = Depends(get_current_user)):
    """仪表盘 - 各频道分组统计"""
    try:
        sm = _load_source_manager()
        if sm:
            sources = sm.parse_all_files()
            from collections import Counter

            groups = Counter()
            for s in sources:
                g = s.get('group', s.get('tvg_group', '未分类'))
                groups[g] += 1
            channels = [{'name': name, 'count': count} for name, count in groups.most_common()]
            return {'channels': channels, 'total': len(channels)}
    except Exception as e:
        from web.core import logger

        logger.warning(f'SourceManager 读取频道统计失败, 回退至 M3U: {e}')

    # 兜底：从 m3u 文件解析分组
    try:
        m3u_paths = [
            './www/output/live.m3u',
            os.path.join(PROJECT_ROOT, 'www', 'output', 'live.m3u'),
        ]
        for m3u in m3u_paths:
            if os.path.exists(m3u):
                from collections import Counter

                groups = Counter()
                with open(m3u) as f:
                    for line in f:
                        if line.startswith('#EXTGRP:'):
                            group = line[len('#EXTGRP:') :].strip()
                            groups[group] += 1
                        elif line.startswith('#EXTINF:'):
                            import re

                            m = re.search(r'group-title="([^"]+)"', line)
                            if m:
                                groups[m.group(1)] += 1
                channels = [{'name': name, 'count': count} for name, count in groups.most_common()]
                if channels:
                    return {'channels': channels, 'total': len(channels)}
    except Exception as e:
        from web.core import logger

        logger.warning(f'M3U 频道统计回退失败: {e}')

    return {'channels': [], 'total': 0}


@router.get('/api/dashboard/status')
async def api_dashboard_status(current_user: dict = Depends(get_current_user)):
    """仪表盘 - 系统运行状态"""
    stats = _get_source_summary()
    sysinfo = _get_system_info()

    # 获取最近测试状态
    test_status = {'status': 'idle', 'last_run': None}
    test_file = os.path.join(PROJECT_ROOT, 'data', 'status', 'latest_test.json')
    if os.path.exists(test_file):
        try:
            with open(test_file) as f:
                td = json.load(f)
                test_status = {
                    'status': td.get('status', 'idle'),
                    'last_run': td.get('started_at'),
                    'passed': td.get('passed', 0),
                    'failed': td.get('failed', 0),
                    'total': td.get('total', 0),
                }
        except Exception as e:
            from web.core import logger

            logger.warning(f'读取最新测试状态文件失败: {e}')

    return {
        'sources': {
            'total': stats.get('total_sources', 0),
            'valid': stats.get('valid', 0),
            'invalid': stats.get('invalid', 0),
            'rate': stats.get('rate', '0%'),
        },
        'system': sysinfo,
        'test': test_status,
        'version': '1.0.0',
    }


@router.get('/api/dashboard/system')
async def api_dashboard_system(current_user: dict = Depends(require_admin)):
    return _get_system_info()
