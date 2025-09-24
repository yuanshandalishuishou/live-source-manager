#!/bin/bash
# 停止并删除现有容器
docker stop livesourcemanager 2>/dev/null || true
docker rm livesourcemanager 2>/dev/null || true

# 清理所有未使用的资源
docker system prune -a -f --volumes
docker builder prune -a -f
docker image prune -a -f

# 清理 Docker 缓存
sudo rm -rf /var/lib/docker/tmp/*

# 确保文件格式正确
if command -v dos2unix >/dev/null 2>&1; then
    echo "转换文件格式为Unix格式..."
    dos2unix start.sh
    dos2unix ./app/main.py
    dos2unix config.ini
    dos2unix ./config/channel_rules.yml
    find . -name "*.sh" -exec dos2unix {} \;
    find . -name "*.py" -exec dos2unix {} \;
    find . -name "*.ini" -exec dos2unix {} \;
    find . -name "*.txt" -exec dos2unix {} \;
    find . -name "*.yml" -exec dos2unix {} \;
fi

# 移除UTF-8 BOM标记（如果存在）
echo "移除UTF-8 BOM标记..."
sed -i '1s/^\xEF\xBB\xBF//' start.sh 2>/dev/null || true
sed -i '1s/^\xEF\xBB\xBF//' ./app/main.py 2>/dev/null || true
sed -i '1s/^\xEF\xBB\xBF//' config.ini 2>/dev/null || true
sed -i '1s/^\xEF\xBB\xBF//' requirements.txt 2>/dev/null || true
sed -i '1s/^\xEF\xBB\xBF//' ./config/channel_rules.yml 2>/dev/null || true

# 替换所有制表符为空格
sed -i 's/\t/    /g' ./app/main.py

rm -rf ./data/* #清除所有以往的数据库残留

# 创建必要的目录
mkdir -p ./{app,logs,config,output,data}

# 设置目录权限
chmod 777 ./logs ./output ./config ./data

chmod 644 ./app/main.py ./config/channel_rules.yml
chmod 755 ./start.sh

# 重新构建镜像
docker build -t livesourcemanager .

# 设置默认环境变量，如果不设置，则从config.ini中读取
PROXY_ENABLED=${PROXY_ENABLED:-true}
PROXY_TYPE=${PROXY_TYPE:-socks5}
PROXY_HOST=${PROXY_HOST:-192.168.1.211}
PROXY_PORT=${PROXY_PORT:-1800}
PROXY_USERNAME=${PROXY_USERNAME:-}
PROXY_PASSWORD=${PROXY_PASSWORD:-}
GITHUB_API_URL=${GITHUB_API_URL:-https://api.github.com}
GITHUB_API_TOKEN=${GITHUB_API_TOKEN:-}
GITHUB_MIRROR_ENABLED=${GITHUB_MIRROR_ENABLED:-true}
GITHUB_MIRROR_URL=${GITHUB_MIRROR_URL:-https://ghproxy.com/,https://ghproxy.net/}

# 运行容器
docker run -d \
  --name livesourcemanager \
  --privileged \
  --security-opt seccomp=unconfined \
  --restart always \
  -p 12345:12345 \
  -p 35455:35455 \
  -v $(pwd)/config:/config \
  -v $(pwd)/logs:/log \
  -v $(pwd)/output:/www/output \
  -v $(pwd)/data:/data \
  -e LANG=C.UTF-8 \
  -e LC_ALL=C.UTF-8 \
  -e PYTHONIOENCODING=utf-8 \
  -e UPDATE_CRON="0 12 * * *" \
  -e TEST_TIMEOUT=10 \
  -e CONCURRENT_THREADS=50 \
  -e OUTPUT_FILENAME=live.m3u \
  -e PROXY_ENABLED=$PROXY_ENABLED \
  -e PROXY_TYPE=$PROXY_TYPE \
  -e PROXY_HOST=$PROXY_HOST \
  -e PROXY_PORT=$PROXY_PORT \
  -e PROXY_USERNAME=$PROXY_USERNAME \
  -e PROXY_PASSWORD=$PROXY_PASSWORD \
  -e GITHUB_API_URL=$GITHUB_API_URL \
  -e GITHUB_API_TOKEN=$GITHUB_API_TOKEN \
  -e GITHUB_MIRROR_ENABLED=$GITHUB_MIRROR_ENABLED \
  -e GITHUB_MIRROR_URL=$GITHUB_MIRROR_URL \
  livesourcemanager