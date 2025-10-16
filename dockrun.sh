#!/bin/bash
# 直播源管理工具 Docker 运行脚本（Nginx版）
# 功能: 构建Docker镜像并启动容器，使用Nginx提供HTTP服务
# 修复内容:
# - 修复文件路径问题
# - 改进错误处理
# - 优化文件操作逻辑

set -euo pipefail

# 脚本信息
SCRIPT_NAME="dockrun.sh"
SCRIPT_VERSION="2.3"
echo "=== 直播源管理工具 Docker 运行脚本 v${SCRIPT_VERSION} (Nginx版) ==="

# 设置工作目录为脚本所在目录
cd "$(dirname "$0")"

# 停止并删除现有容器
echo "停止并清理现有容器..."
docker stop livesourcemanager 2>/dev/null || true
docker rm livesourcemanager 2>/dev/null || true

# 清理所有未使用的资源
echo "清理Docker资源..."
#docker system prune -a -f --volumes
#docker builder prune -a -f
#docker image prune -a -f

# 清理 Docker 缓存
sudo rm -rf /var/lib/docker/tmp/* 2>/dev/null || true

# 检查文件结构
echo "检查文件结构..."
if [ ! -d "app" ]; then
    echo "错误: app 目录不存在"
    echo "当前目录内容:"
    ls -la
    exit 1
fi

if [ ! -d "config" ]; then
    echo "错误: config 目录不存在"
    exit 1
fi

# 确保文件格式正确
echo "检查并修复文件编码..."
if command -v dos2unix >/dev/null 2>&1; then
    echo "转换文件格式为Unix格式..."
    # 修复：只在文件存在时进行转换
    find . -name "*.py" -exec dos2unix {} \; 2>/dev/null || true
    find . -name "*.sh" -exec dos2unix {} \; 2>/dev/null || true
    find . -name "*.ini" -exec dos2unix {} \; 2>/dev/null || true
    find . -name "*.yml" -exec dos2unix {} \; 2>/dev/null || true
    find . -name "*.txt" -exec dos2unix {} \; 2>/dev/null || true
    find . -name "*.conf" -exec dos2unix {} \; 2>/dev/null || true
fi

# 修复Python文件编码
echo "修复Python文件编码..."
for py_file in $(find . -name "*.py"); do
    if file -i "$py_file" | grep -v utf-8 >/dev/null 2>&1; then
        echo "修复编码: $py_file"
        cp "$py_file" "$py_file.backup"
        # 尝试多种编码转换
        if iconv -f $(file -b --mime-encoding "$py_file") -t UTF-8 "$py_file" > "$py_file.utf8" 2>/dev/null; then
            mv "$py_file.utf8" "$py_file"
            echo "成功转换: $py_file"
        else
            # 使用Python进行转换
            python3 -c "
import sys
try:
    with open('$py_file', 'r', encoding='gbk') as f:
        content = f.read()
    with open('$py_file', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Python转换成功: $py_file')
except:
    try:
        with open('$py_file', 'r', encoding='latin1') as f:
            content = f.read()
        with open('$py_file', 'w', encoding='utf-8') as f:
            f.write(content)
        print('Latin1转换成功: $py_file')
    except Exception as e:
        print(f'转换失败: $py_file - {e}')
        # 恢复备份
        import shutil
        shutil.copy('$py_file.backup', '$py_file')
" || true
        fi
        rm -f "$py_file.backup" "$py_file.utf8" 2>/dev/null || true
    fi
done

# 移除UTF-8 BOM标记
echo "移除UTF-8 BOM标记..."
# 修复：检查文件是否存在再操作
[ -f "start.sh" ] && sed -i '1s/^\xEF\xBB\xBF//' start.sh 2>/dev/null || true
[ -f "main.py" ] && sed -i '1s/^\xEF\xBB\xBF//' main.py 2>/dev/null || true
[ -f "config/config.ini" ] && sed -i '1s/^\xEF\xBB\xBF//' config/config.ini 2>/dev/null || true
[ -f "requirements.txt" ] && sed -i '1s/^\xEF\xBB\xBF//' requirements.txt 2>/dev/null || true
[ -f "nginx.conf" ] && sed -i '1s/^\xEF\xBB\xBF//' nginx.conf 2>/dev/null || true
[ -f "config/channel_rules.yml" ] && sed -i '1s/^\xEF\xBB\xBF//' config/channel_rules.yml 2>/dev/null || true

# 修复：只在文件存在时替换制表符
echo "替换制表符为空格..."
for file in $(find . -name "*.py" -o -name "*.sh" -o -name "*.conf"); do
    if [ -f "$file" ]; then
        sed -i 's/\t/    /g' "$file"
    fi
done

# 清理数据目录
echo "清理数据目录..."
mkdir -p data
rm -rf ./data/* 2>/dev/null || true

# 创建必要的目录
echo "创建必要的目录..."
mkdir -p ./{logs,output}

# 设置目录权限
echo "设置目录权限..."
chmod 755 ./logs ./output ./config ./data 2>/dev/null || true
chmod 644 ./*.py ./*.ini ./*.yml ./*.txt ./*.conf 2>/dev/null || true
chmod 755 ./start.sh ./dockrun.sh

# 验证主要Python文件
echo "验证Python文件语法..."
for py_file in main.py app/*.py; do
    if [ -f "$py_file" ]; then
        if python3 -m py_compile "$py_file" 2>/dev/null; then
            echo "✓ $py_file 语法正确"
        else
            echo "✗ $py_file 语法错误"
            python3 -m py_compile "$py_file" || true
        fi
    fi
done



# 重新构建镜像
echo "重新构建Docker镜像（Nginx版）..."
docker build -t livesourcemanager-nginx .

# 设置默认环境变量
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
NGINX_PORT=${NGINX_PORT:-12345}

# 运行容器
echo "启动容器（Nginx版）..."
docker run -d \
  --name livesourcemanager \
  --privileged \
  --security-opt seccomp=unconfined \
  --restart always \
  -p ${NGINX_PORT}:12345 \
  -v $(pwd)/config:/config \
  -v $(pwd)/logs:/log \
  -v $(pwd)/output:/www/output \
  -v $(pwd)/data:/data \
  -e LANG=C.UTF-8 \
  -e LC_ALL=C.UTF-8 \
  -e PYTHONIOENCODING=utf-8 \
  -e UPDATE_CRON="0 02 * * *" \
  -e TEST_TIMEOUT=10 \
  -e CONCURRENT_THREADS=50 \
  -e OUTPUT_FILENAME=live.m3u \
  -e NGINX_PORT=12345 \
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
  livesourcemanager-nginx

echo "容器启动完成，检查日志..."
sleep 10
docker logs livesourcemanager --tail 50

# 等待Nginx服务启动
echo "等待Nginx服务启动..."
sleep 20

# 测试HTTP服务
echo "测试Nginx HTTP服务..."
if curl -f http://localhost:${NGINX_PORT}/health >/dev/null 2>&1; then
    echo "✓ Nginx HTTP服务测试通过"
    
    # 测试文件访问
    if curl -f http://localhost:${NGINX_PORT}/live.m3u >/dev/null 2>&1; then
        echo "✓ M3U文件访问正常"
    else
        echo "⚠ M3U文件尚未生成，等待处理完成"
    fi
    
    # 获取本机IP
    HOST_IP=$(hostname -I | awk '{print $1}')
    if [ -z "$HOST_IP" ]; then
        HOST_IP="localhost"
    fi
    
    echo "访问地址: http://${HOST_IP}:${NGINX_PORT}/live.m3u"
    echo "健康检查: http://${HOST_IP}:${NGINX_PORT}/health"
else
    echo "✗ Nginx HTTP服务测试失败"
    echo "查看详细日志: docker logs livesourcemanager"
    exit 1
fi

# 显示最终状态
echo ""
echo "=== 部署完成 ==="
echo "容器名称: livesourcemanager"
echo "Nginx端口: ${NGINX_PORT}"
echo "输出目录: $(pwd)/output"
echo "配置目录: $(pwd)/config"
echo "日志目录: $(pwd)/logs"
echo ""
echo "常用命令:"
echo "查看日志: docker logs -f livesourcemanager"
echo "进入容器: docker exec -it livesourcemanager bash"
echo "停止容器: docker stop livesourcemanager"
echo "启动容器: docker start livesourcemanager"
echo "重启容器: docker restart livesourcemanager"