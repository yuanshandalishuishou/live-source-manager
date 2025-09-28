#!/bin/bash
# 直播源管理工具启动脚本（Nginx版）
# 版本: 2.3
# 功能: 容器环境初始化、配置检查、服务启动、进程监控
# 增强内容: 
# - 修复Nginx配置路径问题
# - 增强错误处理和日志记录
# - 改进健康检查机制

# 设置严格的错误处理
set -euo pipefail

# 脚本信息
SCRIPT_NAME="start.sh"
SCRIPT_VERSION="2.3"
echo "=== 直播源管理工具启动脚本 v${SCRIPT_VERSION} (Nginx版) ==="

# 设置环境变量（使用默认值）
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PYTHONIOENCODING=utf-8
export PYTHONPATH=/app

# 可配置的环境变量（支持docker run -e参数覆盖）
UPDATE_CRON="${UPDATE_CRON:-0 2 * * *}"           # 定时任务时间（默认每天凌晨2点）
TEST_TIMEOUT="${TEST_TIMEOUT:-10}"                # 测试超时时间（秒）
CONCURRENT_THREADS="${CONCURRENT_THREADS:-50}"    # 并发线程数
OUTPUT_FILENAME="${OUTPUT_FILENAME:-live.m3u}"    # 输出文件名
NGINX_PORT="${NGINX_PORT:-12345}"                 # Nginx端口（关键修复：使用环境变量）

# 日志函数
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log_info() {
    log "INFO: $1"
}

log_warn() {
    log "WARN: $1" >&2
}

log_error() {
    log "ERROR: $1" >&2
}

# 检查目录权限函数
check_directory() {
    local dir=$1
    local description=$2
    
    if [ ! -d "$dir" ]; then
        log_warn "$description 目录不存在: $dir"
        if mkdir -p "$dir" 2>/dev/null; then
            log_info "创建目录: $dir"
        else
            log_error "无法创建目录: $dir"
            return 1
        fi
    fi
    
    if [ ! -w "$dir" ]; then
        log_error "$description 目录不可写: $dir"
        return 1
    fi
    
    log_info "$description 目录检查通过: $dir"
    return 0
}

# 检查必要目录函数
check_required_directories() {
    log_info "开始检查必要目录..."
    
    # 检查配置目录
    check_directory "/config" "配置" || {
        log_error "配置目录检查失败"
        return 1
    }
    
    # 检查日志目录
    check_directory "/log" "日志" || {
        log_error "日志目录检查失败"
        return 1
    }
    
    # 检查输出目录（Nginx服务目录）
    check_directory "/www/output" "Nginx输出" || {
        log_error "输出目录检查失败"
        return 1
    }
    
    # 检查数据目录
    check_directory "/data" "数据" || {
        log_error "数据目录检查失败"
        return 1
    }
    
    # 检查在线源目录
    check_directory "/config/online" "在线源" || {
        log_error "在线源目录检查失败"
        return 1
    }
    
    # 检查Nginx日志目录
    check_directory "/var/log/nginx" "Nginx日志" || {
        log_error "Nginx日志目录检查失败"
        return 1
    }
    
    log_info "所有目录检查完成"
}

# 配置文件初始化函数
setup_config_files() {
    log_info "开始初始化配置文件..."
    
    # 主配置文件检查与初始化
    if [ ! -f "/config/config.ini" ]; then
        log_warn "主配置文件不存在: /config/config.ini"
        if [ -f "/app/config.ini" ]; then
            if cp "/app/config.ini" "/config/config.ini"; then
                log_info "已创建默认主配置文件"
                
                # 更新环境变量到配置文件
                sed -i "s#^timeout = .*#timeout = $TEST_TIMEOUT#" /config/config.ini
                sed -i "s#^concurrent_threads = .*#concurrent_threads = $CONCURRENT_THREADS#" /config/config.ini
                sed -i "s#^filename = .*#filename = $OUTPUT_FILENAME#" /config/config.ini
                
                log_info "已更新配置文件中的环境变量"
            else
                log_error "无法创建默认主配置文件"
                return 1
            fi
        else
            log_error "默认配置文件不存在: /app/config.ini"
            return 1
        fi
    else
        log_info "主配置文件已存在"
    fi
    
    # 频道规则文件检查与初始化
    if [ ! -f "/config/channel_rules.yml" ]; then
        log_warn "频道规则文件不存在: /config/channel_rules.yml"
        if [ -f "/app/channel_rules.yml" ]; then
            if cp "/app/channel_rules.yml" "/config/channel_rules.yml"; then
                log_info "已创建默认频道规则文件"
            else
                log_error "无法创建默认频道规则文件"
                return 1
            fi
        else
            log_error "默认频道规则文件不存在: /app/channel_rules.yml"
            return 1
        fi
    else
        log_info "频道规则文件已存在"
    fi
    
    # 检查Nginx配置文件
    if [ ! -f "/etc/nginx/nginx.conf" ]; then
        log_warn "Nginx配置文件不存在: /etc/nginx/nginx.conf"
        if [ -f "/nginx.conf" ]; then
            if cp "/nginx.conf" "/etc/nginx/nginx.conf"; then
                log_info "已复制Nginx配置文件"
            else
                log_error "无法复制Nginx配置文件"
                return 1
            fi
        else
            log_error "Nginx配置文件不存在: /nginx.conf"
            return 1
        fi
    fi
    
    log_info "配置文件初始化完成"
}

# 设置文件权限函数
setup_file_permissions() {
    log_info "开始设置文件权限..."
    
    # 设置日志文件权限（允许写入）
    touch /log/app.log /log/cron.log 2>/dev/null || true
    chmod 666 /log/app.log /log/cron.log 2>/dev/null || {
        log_warn "无法设置日志文件权限，继续执行..."
    }
    
    # 设置输出目录权限（Nginx需要读写权限）
    chmod 755 /www/output 2>/dev/null || {
        log_warn "无法设置输出目录权限，继续执行..."
    }
    
    # 设置Nginx用户对输出目录的所有权
    chown -R www-data:www-data /www/output 2>/dev/null || {
        log_warn "无法更改输出目录所有者，继续执行..."
    }
    
    # 设置配置目录权限
    chmod 755 /config 2>/dev/null || {
        log_warn "无法设置配置目录权限，继续执行..."
    }
    
    # 设置数据目录权限
    chmod 755 /data 2>/dev/null || {
        log_warn "无法设置数据目录权限，继续执行..."
    }
    
    log_info "文件权限设置完成"
}

# Nginx服务管理函数 - 关键修复
setup_nginx() {
    log_info "开始设置Nginx..."
    
    # 检查Nginx是否安装
    if ! command -v nginx >/dev/null 2>&1; then
        log_error "Nginx未安装"
        return 1
    fi
    
    # 检查Nginx配置文件是否存在
    if [ ! -f "/etc/nginx/nginx.conf" ]; then
        log_error "Nginx配置文件不存在: /etc/nginx/nginx.conf"
        return 1
    fi
    
    # 测试Nginx配置
    log_info "测试Nginx配置..."
    if nginx -t >/dev/null 2>&1; then
        log_info "✓ Nginx配置测试通过"
    else
        log_error "✗ Nginx配置测试失败"
        nginx -t  # 显示详细错误信息
        return 1
    fi
    
    # 创建健康检查文件
    echo "healthy" > /www/output/health
    chmod 644 /www/output/health
    chown www-data:www-data /www/output/health
    
    # 启动Nginx（前台运行，便于容器管理）
    log_info "启动Nginx服务（端口: ${NGINX_PORT}）..."
    nginx -g "daemon off;" &
    NGINX_PID=$!
    
    # 记录Nginx进程ID
    echo $NGINX_PID > /var/run/nginx.pid
    log_info "Nginx进程启动，PID: $NGINX_PID"
    
    # 等待Nginx启动
    local max_wait=15
    local waited=0
    while [ $waited -lt $max_wait ]; do
        if curl -f http://localhost:${NGINX_PORT}/health >/dev/null 2>&1; then
            log_info "✓ Nginx服务验证成功，端口: ${NGINX_PORT}"
            return 0
        fi
        log_info "等待Nginx启动... ($((waited + 1))/${max_wait})"
        sleep 1
        waited=$((waited + 1))
    done
    
    log_error "✗ Nginx启动超时"
    # 尝试获取Nginx错误日志
    if [ -f "/var/log/nginx/error.log" ]; then
        log_error "Nginx错误日志:"
        tail -20 /var/log/nginx/error.log >&2
    fi
    return 1
}

# 定时任务设置函数
setup_cron_jobs() {
    log_info "开始设置定时任务..."
    
    # 创建定时任务文件
    local cron_file="/etc/cron.d/live-source-cron"
    
    # 检查cron服务是否可用
    if ! command -v crontab >/dev/null 2>&1; then
        log_warn "cron服务不可用，跳过定时任务设置"
        return 0
    fi
    
    # 创建定时任务
    echo "# 直播源管理工具定时任务" > "$cron_file"
    echo "# 自动生成于: $(date)" >> "$cron_file"
    echo "$UPDATE_CRON /usr/local/bin/python /app/main.py >> /log/cron.log 2>&1" >> "$cron_file"
    echo "# 结束" >> "$cron_file"
    
    # 设置正确的权限
    chmod 0644 "$cron_file"
    
    # 加载定时任务
    if crontab "$cron_file"; then
        log_info "定时任务设置成功: $UPDATE_CRON"
    else
        log_error "定时任务设置失败"
        return 1
    fi
    
    # 启动cron服务
    if service cron start >/dev/null 2>&1; then
        log_info "cron服务启动成功"
    else
        # 尝试直接启动cron守护进程
        if /usr/sbin/cron; then
            log_info "cron守护进程启动成功"
        else
            log_warn "无法启动cron服务，定时任务可能无法执行"
        fi
    fi
    
    log_info "定时任务设置完成"
}

# 健康检查函数
health_check() {
    log_info "执行健康检查..."
    
    # 检查Python是否可用
    if ! command -v python3 >/dev/null 2>&1; then
        log_error "Python3不可用"
        return 1
    fi
    log_info "✓ Python3检查通过"
    
    # 检查主要Python模块
    if ! python3 -c "import aiohttp, aiofiles, yaml" >/dev/null 2>&1; then
        log_error "必要的Python模块缺失"
        return 1
    fi
    log_info "✓ Python模块检查通过"
    
    # 检查FFmpeg/FFprobe是否可用（用于流媒体测试）
    if ! command -v ffprobe >/dev/null 2>&1; then
        log_warn "FFprobe不可用，流媒体测试功能将受限"
    else
        log_info "✓ FFprobe检查通过"
    fi
    
    # 检查Nginx是否可用
    if ! command -v nginx >/dev/null 2>&1; then
        log_error "Nginx不可用"
        return 1
    fi
    log_info "✓ Nginx检查通过"
    
    # 检查Nginx配置文件
    if [ ! -f "/etc/nginx/nginx.conf" ]; then
        log_error "Nginx配置文件不存在"
        return 1
    fi
    log_info "✓ Nginx配置文件检查通过"
    
    log_info "健康检查完成"
}

# 主程序启动函数
start_main_program() {
    log_info "启动主程序..."
    
    # 切换到应用目录
    cd /app
    
    # 执行主程序
    if python main.py; then
        log_info "✓ 主程序执行成功"
        return 0
    else
        log_error "✗ 主程序执行失败，退出码: $?"
        return 1
    fi
}

# 进程监控函数
monitor_processes() {
    log_info "启动进程监控..."
    
    local check_interval=30  # 检查间隔（秒）
    local nginx_restart_count=0
    local max_restarts=3
    
    while true; do
        # 检查Nginx是否存活
        if ! ps -p $(cat /var/run/nginx.pid 2>/dev/null) >/dev/null 2>&1; then
            log_error "Nginx进程已停止，尝试重启... (重启次数: $((nginx_restart_count + 1))/$max_restarts)"
            
            if [ $nginx_restart_count -lt $max_restarts ]; then
                if setup_nginx; then
                    log_info "✓ Nginx重启成功"
                    nginx_restart_count=0
                else
                    nginx_restart_count=$((nginx_restart_count + 1))
                    log_error "Nginx重启失败"
                fi
            else
                log_error "Nginx重启次数超过限制，停止重启尝试"
                return 1
            fi
        fi
        
        # 检查端口是否监听
        if ! netstat -tuln 2>/dev/null | grep -q ":${NGINX_PORT} "; then
            log_warn "Nginx端口${NGINX_PORT}未监听，等待恢复..."
        fi
        
        # 检查健康端点
        if ! curl -f http://localhost:${NGINX_PORT}/health >/dev/null 2>&1; then
            log_warn "Nginx健康检查失败，服务可能不可用"
        fi
        
        sleep $check_interval
    done
}

# 信号处理函数
setup_signal_handlers() {
    trap 'log_info "接收到终止信号，正在退出..."; kill $(cat /var/run/nginx.pid 2>/dev/null) 2>/dev/null; exit 0' TERM INT
    log_info "信号处理器已设置"
}

# 主执行函数
main() {
    log_info "直播源管理工具（Nginx版）启动中..."
    
    # 设置信号处理
    setup_signal_handlers
    
    # 执行健康检查
    if ! health_check; then
        log_error "健康检查失败，启动中止"
        exit 1
    fi
    
    # 检查必要目录
    if ! check_required_directories; then
        log_error "目录检查失败，启动中止"
        exit 1
    fi
    
    # 初始化配置文件
    if ! setup_config_files; then
        log_error "配置文件初始化失败，启动中止"
        exit 1
    fi
    
    # 设置文件权限
    setup_file_permissions
    
    # 启动Nginx
    if ! setup_nginx; then
        log_error "Nginx启动失败，启动中止"
        exit 1
    fi
    
    # 设置定时任务
    setup_cron_jobs
    
    # 启动时立即运行一次主程序（前台执行，便于查看日志）
    log_info "启动时立即执行一次直播源处理..."
    if start_main_program; then
        log_info "✓ 主程序执行成功"
    else
        log_error "✗ 主程序执行失败"
        exit 1
    fi
    
    log_info "直播源管理工具（Nginx版）启动完成"
    log_info "Nginx服务访问地址: http://<容器IP>:${NGINX_PORT}/"
    log_info "查看日志: docker logs -f <容器名>"
    log_info "定时任务配置: $UPDATE_CRON"
    
    # 输出当前文件列表
    log_info "当前可访问的文件:"
    ls -la /www/output/ | while read line; do log_info "  $line"; done
    
    # 保持容器运行，监控进程
    log_info "容器进入守护模式，监控Nginx和服务状态..."
    monitor_processes
}

# 脚本入口点
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi