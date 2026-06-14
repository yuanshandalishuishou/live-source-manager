#!/bin/sh
# 直播源管理工具健康检查脚本
# 检查Nginx存活和最近一次main.py执行时间

# 检查Nginx是否存活（nginx.conf已配置 /health 端点）
NGINX_PORT="${NGINX_PORT:-12345}"
if ! curl -sf "http://localhost:${NGINX_PORT}/health" > /dev/null 2>&1; then
    echo "Nginx not running"
    exit 1
fi

# 通过Python检查最后一次处理时间（mtime < 2小时），避免stat跨平台兼容问题
if [ -f /www/output/health ]; then
    if ! python3 -c "
import os, time
mtime = os.path.getmtime('/www/output/health')
age = time.time() - mtime
exit(0 if age < 7200 else 1)
" 2>/dev/null; then
        echo "Last run too old or check failed"
        exit 1
    fi
fi

echo "healthy"
exit 0
