#!/usr/bin/env bash
# ============================================================
# build.sh — 一键构建 lsm:latest Docker 镜像
# 用法：
#   ./build.sh              # 使用默认镜像源
#   ./build.sh --proxy      # 使用镜像代理（国内加速）
#   ./build.sh --no-cache   # 强制全新构建
#
# 要求：仅需 Docker（任何 Linux / macOS / Windows WSL2）
# 输出：lsm:latest（约 460MB）
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

PROXY=""
NO_CACHE=""

for arg in "$@"; do
    case "$arg" in
        --proxy) PROXY="--build-arg BASE_IMAGE=docker.1ms.run/python:3.9-slim-bookworm" ;;
        --no-cache) NO_CACHE="--no-cache" ;;
        *) echo "❌ 未知参数: $arg"; echo "用法: ./build.sh [--proxy] [--no-cache]"; exit 1 ;;
    esac
done

echo "🚀 构建 lsm:latest Docker 镜像..."
echo "   基础镜像: ${PROXY:+docker.1ms.run/}python:3.9-slim-bookworm"
echo "   预期体积: ~460MB"
echo "   构建用时: ~2-5 分钟（取决于网络）"
echo ""

docker build $NO_CACHE $PROXY -t lsm:latest .

echo ""
echo "✅ 构建完成！"
docker images lsm:latest --format "   镜像: lsm:latest  体积: {{.Size}}"
echo ""
echo "启动容器："
echo "  docker run -d --name lsm -p 12345:12345 lsm:latest"
echo ""
echo "查看日志："
echo "  docker logs -f lsm"
