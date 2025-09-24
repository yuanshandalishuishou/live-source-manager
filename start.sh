#!/bin/bash
# 设置错误时退出
set -e

# 设置 Python 使用 UTF-8 编码
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PYTHONIOENCODING=utf-8

# 导入环境变量（使用默认值）
UPDATE_CRON="${UPDATE_CRON:-0 12 * * *}"
TEST_TIMEOUT="${TEST_TIMEOUT:-10}"
CONCURRENT_THREADS="${CONCURRENT_THREADS:-50}"
OUTPUT_FILENAME="${OUTPUT_FILENAME:-live.m3u}"

# 如果/config/config.ini不存在，则使用默认配置
if [ ! -f /config/config.ini ]; then
    echo "警告: /config/config.ini 不存在，使用默认配置"
    cp /app/default_config.ini /config/config.ini || {
        echo "错误: 无法复制默认配置" >&2
        exit 1
    }
fi

# 如果/config/channel_rules.yml不存在，则使用默认配置
if [ ! -f /config/channel_rules.yml ]; then
    echo "警告: /config/channel_rules.yml 不存在，使用默认配置"
    cp /app/channel_rules.yml /config/channel_rules.yml || {
        echo "错误: 无法复制默认频道规则配置" >&2
        exit 1
    }
fi

# 更新配置文件
sed -i "s#^timeout = .*#timeout = $TEST_TIMEOUT#" /config/config.ini
sed -i "s#^concurrent_threads = .*#concurrent_threads = $CONCURRENT_THREADS#" /config/config.ini
sed -i "s#^filename = .*#filename = $OUTPUT_FILENAME#" /config/config.ini

# 确保日志目录存在并有正确权限
mkdir -p /log || exit 1
touch /log/app.log /log/cron.log
chmod 666 /log/app.log /log/cron.log

# 确保输出目录存在
mkdir -p /www/output
chmod 755 /www/output

# 确保在线目录存在
mkdir -p /config/online
chmod 755 /config/online

# 确保数据目录存在
mkdir -p /data
chmod 755 /data

# 设置cron任务
echo "$UPDATE_CRON /usr/local/bin/python /app/main.py >> /log/cron.log 2>&1" > /etc/cron.d/live-source-cron
chmod 0644 /etc/cron.d/live-source-cron
crontab /etc/cron.d/live-source-cron || {
    echo "错误: crontab配置失败" >&2
    exit 1
}

# 启动cron服务
service cron start || {
    echo "错误: 无法启动cron服务" >&2
    exit 1
}

# 启动Nginx（后台运行）
nginx -c /config/nginx.conf -g "daemon off;" &

# 容器启动时立即运行一次任务
echo "启动时立即运行一次任务..."
python /app/main.py || {
    echo "警告: 初始任务执行失败，继续启动容器..." >&2
}

# 保持容器运行（监控日志）
tail -f /log/app.log /log/cron.log