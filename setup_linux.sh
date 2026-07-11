#!/bin/bash
# 直播源管理工具 - Linux (Debian/Ubuntu) 安装脚本
# 版本: 2.0 (调用 start.sh 的检测和安装函数)
# 功能: 一次性系统级安装（Python、Nginx、虚拟环境、依赖包、数据库初始化、systemd 服务）
# 使用: sudo bash setup_linux.sh

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1" >&2
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

echo -e "${CYAN}================================================${NC}"
echo -e "${CYAN}  直播源管理工具 - Linux 安装脚本${NC}"
echo -e "${CYAN}  版本: 2.0${NC}"
echo -e "${CYAN}================================================${NC}"
echo ""

# 检查是否以 root 运行
if [ "$EUID" -ne 0 ]; then
    log_warn "建议以 root 权限运行此脚本（sudo bash setup_linux.sh）"
    read -p "是否继续? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 获取项目目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$SCRIPT_DIR}"

log_info "项目目录: $PROJECT_DIR"

# 导出项目目录（供 start.sh 使用）
export PROJECT_DIR

# 检查 start_docker.sh 是否存在（复用其中的环境检测和安装函数）
if [ ! -f "$PROJECT_DIR/start_docker.sh" ]; then
    log_error "start_docker.sh 不存在: $PROJECT_DIR/start_docker.sh"
    exit 1
fi

# 源 start_docker.sh 以获取检测/安装函数（有 BASH_SOURCE 守卫，不会执行 main）
source "$PROJECT_DIR/start_docker.sh" || true

# 执行环境检测和安装
log_info "开始环境检测和自动安装..."
if ! setup_environment; then
    log_error "环境检测和安装失败"
    exit 1
fi

# 初始化数据库
log_info "初始化 SQLite 数据库..."
cd "$PROJECT_DIR"
DB_INIT_OUTPUT=$("$PROJECT_DIR/.venv/bin/python" -c "
from web.models import init_db
init_db(None)  # 传 None：未设置自定义密码时自动生成强随机密码（零配置首启）
print('DB_INIT_OK')
" 2>&1 || true)
echo "$DB_INIT_OUTPUT" | grep -q "DB_INIT_OK" && {
    log_info "✓ 数据库初始化成功"
    # 捕获自动生成的密码并提示
    GEN_PW=$(echo "$DB_INIT_OUTPUT" | grep '^ADMIN_PASSWORD_INITIALIZED=' | head -1 | cut -d= -f2-)
    if [ -n "$GEN_PW" ]; then
        log_warn "============================================================"
        log_warn "⚠️  已自动生成管理员密码，请立即记录并尽快在「配置中心」修改！"
        log_warn "    管理员账号: admin"
        log_warn "    管理员密码: $GEN_PW"
        log_warn "============================================================"
    else
        log_info "  管理员账号: admin（使用既有密码或自定义环境变量 WEB_ADMIN_PASSWORD）"
    fi
} || {
    log_error "✗ 数据库初始化失败"
    exit 1
}

# 配置 Nginx（如果 /etc/nginx/sites-available 存在）
if [ -d /etc/nginx/sites-available ] && [ -f "$PROJECT_DIR/nginx.conf" ]; then
    log_info "配置 Nginx..."
    cp "$PROJECT_DIR/nginx.conf" /etc/nginx/sites-available/live-source-manager
    ln -sf /etc/nginx/sites-available/live-source-manager /etc/nginx/sites-enabled/
    rm -f /etc/nginx/sites-enabled/default
    
    # 测试 Nginx 配置
    if nginx -t >/dev/null 2>&1; then
        log_info "✓ Nginx 配置成功"
    else
        log_warn "Nginx 配置测试失败，请手动检查"
    fi
fi

# 创建 systemd 服务
log_info "创建 systemd 服务..."
cat > /etc/systemd/system/live-source-web.service <<EOF
[Unit]
Description=Live Source Manager Web Interface
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$PROJECT_DIR/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=$PROJECT_DIR/.venv/bin/python -m web.webapp
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 运行时可写目录归属 www-data（避免 systemd 以 www-data 用户运行、首启写库/下载源/生成播放列表时 PermissionError）
log_info "修复运行时目录权限 (www-data)..."
mkdir -p "$PROJECT_DIR/web/data" "$PROJECT_DIR/config/online" "$PROJECT_DIR/www/output"
chown -R www-data:www-data \
    "$PROJECT_DIR/web/data" \
    "$PROJECT_DIR/config/online" \
    "$PROJECT_DIR/www/output" 2>/dev/null || {
    log_warn "无法 chown 运行时目录，请确认 www-data 用户对 web/data、config/online、www/output 有写权限"
}

systemctl daemon-reload
systemctl enable live-source-web.service

log_info "✓ systemd 服务创建完成"

echo ""
echo -e "${GREEN}================================================${NC}"
echo -e "${GREEN}  安装完成!${NC}"
echo -e "${GREEN}================================================${NC}"
echo ""
log_info "启动 Web 服务:"
log_info "  systemctl start live-source-web"
echo ""
log_info "查看服务状态:"
log_info "  systemctl status live-source-web"
echo ""
log_info "Web 访问地址:"
log_info "  http://localhost:23456"
echo ""
log_info "查看日志:"
log_info "  journalctl -u live-source-web -f"
echo ""
