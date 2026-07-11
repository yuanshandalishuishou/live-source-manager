#!/bin/bash
# 直播源管理工具 Docker 启动脚本（Nginx版）
# 版本: 3.0 (增加OS检测和自动安装功能)
# 功能: OS检测、依赖检查、自动安装、容器环境初始化、配置检查、服务启动、进程监控
# 用途: Docker 容器入口点（CMD ["/start_docker.sh"]）
#       setup_linux.sh 会 source 此文件复用检测函数（有守卫不执行 main）

# 设置严格的错误处理
set -euo pipefail

# 脚本信息
SCRIPT_NAME="start_docker.sh"
SCRIPT_VERSION="3.0"
echo "=== 直播源管理工具启动脚本 v${SCRIPT_VERSION} (Nginx版 / SQLite) ==="

# ============================================================================
# OS 检测和组件自动安装函数
# ============================================================================

# 检测操作系统类型
detect_os() {
    if [ -f /.dockerenv ] || grep -q 'docker\|lxc' /proc/1/cgroup 2>/dev/null; then
        echo "docker"
    elif [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "$ID"
    elif [ "$(uname -s)" = "Linux" ]; then
        echo "linux"
    else
        echo "unknown"
    fi
}

# 检测包管理器
detect_package_manager() {
    if command -v apt-get >/dev/null 2>&1; then
        echo "apt"
    elif command -v yum >/dev/null 2>&1; then
        echo "yum"
    elif command -v dnf >/dev/null 2>&1; then
        echo "dnf"
    elif command -v pacman >/dev/null 2>&1; then
        echo "pacman"
    elif command -v apk >/dev/null 2>&1; then
        echo "apk"
    else
        echo "unknown"
    fi
}

# 检查 Python 版本
check_python() {
    local required_version="3.13"
    local python_cmd=""
    
    # 查找 Python 命令
    for cmd in python3.13 python3 python; do
        if command -v "$cmd" >/dev/null 2>&1; then
            local version=$($cmd --version 2>&1 | grep -oP '[0-9]+\.[0-9]+' | head -1)
            if [ -n "$version" ]; then
                local major=$(echo "$version" | cut -d. -f1)
                local minor=$(echo "$version" | cut -d. -f2)
                if [ "$major" -ge 3 ] && [ "$minor" -ge 13 ]; then
                    python_cmd="$cmd"
                    break
                fi
            fi
        fi
    done
    
    if [ -n "$python_cmd" ]; then
        log_info "Python 已安装: $($python_cmd --version 2>&1)"
        echo "$python_cmd"
        return 0
    else
        log_warn "Python 3.13+ 未安装"
        return 1
    fi
}

# 自动安装 Python 3.13
install_python() {
    local os=$(detect_os)
    local pkg_mgr=$(detect_package_manager)
    
    log_info "开始安装 Python 3.13..."
    
    if [ "$pkg_mgr" = "apt" ]; then
        # Debian/Ubuntu - 使用 deadsnakes PPA
        if [ "$os" = "debian" ]; then
            # Debian - 从源码编译或使用 backports
            apt-get update
            apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev \
                libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev \
                wget libbz2-dev liblzma-dev
            
            cd /tmp
            wget https://mirrors.tuna.tsinghua.edu.cn/python/3.13.1/Python-3.13.1.tar.xz
            tar -xf Python-3.13.1.tar.xz
            cd Python-3.13.1
            ./configure --enable-optimizations --prefix=/usr/local
            make -j$(nproc)
            make altinstall
            cd /
            rm -rf /tmp/Python-3.13.1*
            
            log_info "Python 3.13 安装完成"
        else
            # Ubuntu - 使用 deadsnakes PPA
            apt-get update
            apt-get install -y software-properties-common
            add-apt-repository -y ppa:deadsnakes/ppa
            apt-get update
            apt-get install -y python3.13 python3.13-venv python3.13-dev
            
            log_info "Python 3.13 安装完成"
        fi
    elif [ "$pkg_mgr" = "yum" ] || [ "$pkg_mgr" = "dnf" ]; then
        # RHEL/CentOS/Fedora
        if [ "$pkg_mgr" = "dnf" ]; then
            dnf install -y python3.13
        else
            yum install -y python3.13
        fi
    else
        log_error "不支持的包管理器: $pkg_mgr"
        log_info "请手动安装 Python 3.13+"
        return 1
    fi
    
    # 验证安装
    if check_python >/dev/null 2>&1; then
        log_info "✓ Python 3.13 安装成功"
        return 0
    else
        log_error "✗ Python 3.13 安装失败"
        return 1
    fi
}

# 检查 pip
check_pip() {
    local python_cmd="${1:-python3}"
    
    if "$python_cmd" -m pip --version >/dev/null 2>&1; then
        log_info "pip 已安装: $("$python_cmd" -m pip --version 2>&1 | head -1)"
        return 0
    else
        log_warn "pip 未安装"
        return 1
    fi
}

# 安装 pip
install_pip() {
    local python_cmd="${1:-python3}"
    
    log_info "安装 pip..."
    
    # 尝试使用 ensurepip
    if "$python_cmd" -m ensurepip >/dev/null 2>&1; then
        log_info "✓ pip 安装成功 (ensurepip)"
        return 0
    fi
    
    # 尝试使用 get-pip.py
    local get_pip_url="https://mirrors.tuna.tsinghua.edu.cn/pypi/get-pip.py"
    if command -v curl >/dev/null 2>&1; then
        curl -sSL "$get_pip_url" | "$python_cmd"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- "$get_pip_url" | "$python_cmd"
    else
        log_error "无法安装 pip：需要 curl 或 wget"
        return 1
    fi
    
    if check_pip "$python_cmd"; then
        log_info "✓ pip 安装成功"
        return 0
    else
        log_error "✗ pip 安装失败"
        return 1
    fi
}

# 检查虚拟环境
check_venv() {
    local project_dir="${1:-/app}"
    local venv_dir="$project_dir/.venv"
    
    if [ -d "$venv_dir" ] && [ -f "$venv_dir/bin/activate" ]; then
        log_info "虚拟环境已存在: $venv_dir"
        echo "$venv_dir"
        return 0
    else
        log_warn "虚拟环境不存在: $venv_dir"
        return 1
    fi
}

# 创建虚拟环境
create_venv() {
    local project_dir="${1:-/app}"
    local venv_dir="$project_dir/.venv"
    local python_cmd="${2:-python3}"
    
    log_info "创建虚拟环境: $venv_dir"
    
    # 确保 python-venv 已安装
    if ! "$python_cmd" -m venv --help >/dev/null 2>&1; then
        log_info "安装 python3-venv..."
        local pkg_mgr=$(detect_package_manager)
        if [ "$pkg_mgr" = "apt" ]; then
            apt-get update
            apt-get install -y python3-venv
        fi
    fi
    
    "$python_cmd" -m venv "$venv_dir"
    
    if [ -f "$venv_dir/bin/activate" ]; then
        log_info "✓ 虚拟环境创建成功"
        echo "$venv_dir"
        return 0
    else
        log_error "✗ 虚拟环境创建失败"
        return 1
    fi
}

# 检查 Python 依赖包
check_python_deps() {
    local venv_dir="${1:-/app/.venv}"
    local req_file="${2:-/app/requirements.txt}"
    
    if [ ! -f "$req_file" ]; then
        log_warn "requirements.txt 不存在: $req_file"
        return 0
    fi
    
    log_info "检查 Python 依赖包..."
    
    local missing_deps=()
    while IFS= read -r line; do
        # 跳过注释和空行
        [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
        
        # 提取包名
        local pkg=$(echo "$line" | sed 's/[<=>=!].*//' | tr -d ' ')
        
        if ! "$venv_dir/bin/python" -c "import $pkg" 2>/dev/null; then
            missing_deps+=("$pkg")
        fi
    done < "$req_file"
    
    if [ ${#missing_deps[@]} -eq 0 ]; then
        log_info "✓ 所有 Python 依赖包已安装"
        return 0
    else
        log_warn "缺失的依赖包: ${missing_deps[*]}"
        return 1
    fi
}

# 安装 Python 依赖包
install_python_deps() {
    local venv_dir="${1:-/app/.venv}"
    local req_file="${2:-/app/requirements.txt}"
    local use_mirror="${3:-true}"
    
    log_info "安装 Python 依赖包..."
    
    # 配置镜像源（国内加速）
    if [ "$use_mirror" = "true" ]; then
        log_info "配置 PyPI 镜像源（清华/华为云）..."
        "$venv_dir/bin/pip" config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
        "$venv_dir/bin/pip" config set global.trusted-host pypi.tuna.tsinghua.edu.cn
    fi
    
    # 安装依赖
    if "$venv_dir/bin/pip" install -r "$req_file"; then
        log_info "✓ Python 依赖包安装成功"
        return 0
    else
        log_warn "PyPI 镜像安装失败，尝试使用默认源..."
        "$venv_dir/bin/pip" config unset global.index-url
        "$venv_dir/bin/pip" config unset global.trusted-host
        
        if "$venv_dir/bin/pip" install -r "$req_file"; then
            log_info "✓ Python 依赖包安装成功（默认源）"
            return 0
        else
            log_error "✗ Python 依赖包安装失败"
            return 1
        fi
    fi
}

# 检查 FFmpeg/FFprobe
check_ffmpeg() {
    if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
        log_info "FFmpeg 已安装: $(ffmpeg -version 2>&1 | head -1)"
        return 0
    else
        log_warn "FFmpeg/FFprobe 未安装"
        return 1
    fi
}

# 安装 FFmpeg
install_ffmpeg() {
    local pkg_mgr=$(detect_package_manager)
    
    log_info "安装 FFmpeg..."
    
    if [ "$pkg_mgr" = "apt" ]; then
        apt-get update
        apt-get install -y ffmpeg
    elif [ "$pkg_mgr" = "yum" ]; then
        yum install -y ffmpeg
    elif [ "$pkg_mgr" = "dnf" ]; then
        dnf install -y ffmpeg
    elif [ "$pkg_mgr" = "apk" ]; then
        apk add ffmpeg
    else
        # 从静态构建安装
        log_info "从静态构建安装 FFmpeg..."
        cd /tmp
        wget -O ffmpeg.tar.xz https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
        tar -xf ffmpeg.tar.xz
        cp ffmpeg-*/ffmpeg /usr/local/bin/
        cp ffmpeg-*/ffprobe /usr/local/bin/
        chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe
        rm -rf /tmp/ffmpeg*
    fi
    
    if check_ffmpeg; then
        log_info "✓ FFmpeg 安装成功"
        return 0
    else
        log_error "✗ FFmpeg 安装失败"
        return 1
    fi
}

# 检查 Nginx
check_nginx() {
    if command -v nginx >/dev/null 2>&1; then
        log_info "Nginx 已安装: $(nginx -v 2>&1)"
        return 0
    else
        log_warn "Nginx 未安装"
        return 1
    fi
}

# 安装 Nginx
install_nginx() {
    local pkg_mgr=$(detect_package_manager)
    
    log_info "安装 Nginx..."
    
    if [ "$pkg_mgr" = "apt" ]; then
        apt-get update
        apt-get install -y nginx
    elif [ "$pkg_mgr" = "yum" ]; then
        yum install -y nginx
    elif [ "$pkg_mgr" = "dnf" ]; then
        dnf install -y nginx
    elif [ "$pkg_mgr" = "apk" ]; then
        apk add nginx
    else
        log_error "不支持的包管理器: $pkg_mgr"
        return 1
    fi
    
    if check_nginx; then
        log_info "✓ Nginx 安装成功"
        return 0
    else
        log_error "✗ Nginx 安装失败"
        return 1
    fi
}

# 主检测和安装函数
setup_environment() {
    log_info "开始环境检测 and 自动安装..."
    
    # 1. 检测 Python
    local python_cmd=""
    if ! python_cmd=$(check_python 2>/dev/null); then
        log_warn "Python 3.13+ 未安装，尝试自动安装..."
        if install_python; then
            python_cmd=$(check_python 2>/dev/null)
        else
            log_error "Python 安装失败，请手动安装 Python 3.13+"
            return 1
        fi
    fi
    
    # 2. 检查/安装 pip
    if ! check_pip "$python_cmd" 2>/dev/null; then
        log_warn "pip 未安装，尝试自动安装..."
        if ! install_pip "$python_cmd"; then
            log_error "pip 安装失败"
            return 1
        fi
    fi
    
    # 3. 检查/创建虚拟环境
    local project_dir="${PROJECT_DIR:-/app}"
    local venv_dir=""
    if ! venv_dir=$(check_venv "$project_dir" 2>/dev/null); then
        log_warn "虚拟环境不存在，正在创建..."
        if ! venv_dir=$(create_venv "$project_dir" "$python_cmd" 2>/dev/null); then
            log_error "虚拟环境创建失败"
            return 1
        fi
    fi
    
    # 4. 检查/安装 Python 依赖
    if ! check_python_deps "$venv_dir" "$project_dir/requirements.txt" 2>/dev/null; then
        log_warn "Python 依赖包缺失，正在安装..."
        if ! install_python_deps "$venv_dir" "$project_dir/requirements.txt"; then
            log_error "Python 依赖包安装失败"
            return 1
        fi
    fi
    
    # 5. 检查/安装 FFmpeg（可选，失败时仅警告）
    if ! check_ffmpeg 2>/dev/null; then
        log_warn "FFmpeg 未安装，尝试自动安装..."
        if ! install_ffmpeg; then
            log_warn "FFmpeg 自动安装失败，流媒体测试功能将受限"
        fi
    fi
    
    # 6. 检查/安装 Nginx（仅 Linux 非 Docker 环境）
    local os=$(detect_os)
    if [ "$os" != "docker" ]; then
        if ! check_nginx 2>/dev/null; then
            log_warn "Nginx 未安装，尝试自动安装..."
            if ! install_nginx; then
                log_error "Nginx 安装失败"
                return 1
            fi
        fi
    fi
    
    log_info "✓ 环境检测和安装完成"
    return 0
}

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
NGINX_PORT="${NGINX_PORT:-12345}"                 # Nginx端口
WEB_PORT="${WEB_PORT:-23456}"                        # Web管理界面端口（HTTPServer.manager_port）

# SQLite 数据库路径：持久化到 /data 卷（docker-compose 将宿主机 ./data 挂载到容器 /data），容器重建不丢库
export WEB_DATA_DIR="/data"
mkdir -p "$WEB_DATA_DIR"
DB_PATH="$WEB_DATA_DIR/web.db"

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
    
    # 检查本地源目录
    check_directory "/config/sources" "本地源" || {
        log_info "本地源目录 /config/sources 不存在，自动创建"
        mkdir -p /config/sources
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

# SQLite 数据库初始化函数
init_sqlite_db() {
    log_info "开始检查 SQLite 数据库..."

    # 确保 data 目录存在
    mkdir -p "$WEB_DATA_DIR" 2>/dev/null || {
        log_error "无法创建 SQLite 数据目录 $WEB_DATA_DIR"
        return 1
    }

    if [ -f "$DB_PATH" ]; then
        log_info "SQLite 数据库已存在: $DB_PATH"

        # 检查表结构完整性
        local tables_ok
        tables_ok=$(cd /app && /app/.venv/bin/python -c "
from web.models import get_conn
try:
    conn = get_conn()
    expected = {'users', 'audit_logs', 'app_config', 'sessions'}
    rows = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
    actual = {r[0] for r in rows}
    conn.close()
    if expected.issubset(actual):
        print('OK')
    else:
        missing = expected - actual
        print('MISSING: ' + ','.join(missing))
except Exception as e:
    print('ERROR: ' + str(e))
" 2>&1)

        if [ "$tables_ok" = "OK" ]; then
            log_info "✓ SQLite 表结构完整，无需重建"
            return 0
        elif [[ "$tables_ok" == MISSING:* ]]; then
            log_warn "SQLite 表结构不完整 ($tables_ok)，将重建数据库"
            # 备份原有数据库
            local backup_path="${DB_PATH}.bak.$(date +%Y%m%d%H%M%S)"
            cp "$DB_PATH" "$backup_path" && log_info "已备份原数据库至: $backup_path"
            rm -f "$DB_PATH"
            log_info "已删除旧数据库，准备重新初始化"
        else
            log_warn "SQLite 检查异常: $tables_ok，将重新初始化"
            rm -f "$DB_PATH"
        fi
    else
        log_info "SQLite 数据库不存在，将创建新数据库"
    fi

    # 初始化数据库：建表 + 创建默认用户
    log_info "正在初始化 SQLite 数据库表结构..."
    local init_output
    # 从环境变量读取管理员密码；未设置时由 init_db 自动生成强随机密码（零配置首次部署）
    local ADMIN_PW="${WEB_ADMIN_PASSWORD:-}"

    if [ -z "$ADMIN_PW" ]; then
        log_info "环境变量 WEB_ADMIN_PASSWORD 未设置，首次部署将由 init_db 自动生成强随机管理员密码"
    else
        log_info "使用环境变量 WEB_ADMIN_PASSWORD 作为管理员密码"
        # 复杂度提示（不强制阻断，兼容历史部署；建议 ≥8 位且含字母与数字）
        if ! printf '%s' "$ADMIN_PW" | grep -qE '^.{8,}$' \
            || ! printf '%s' "$ADMIN_PW" | grep -qE '[A-Za-z]' \
            || ! printf '%s' "$ADMIN_PW" | grep -qE '[0-9]'; then
            log_warn "⚠️  WEB_ADMIN_PASSWORD 长度不足 8 位或缺少字母/数字，建议设置为更强的密码"
        fi
    fi

    init_output=$(cd /app && /app/.venv/bin/python <<PYEOF 2>&1
from web.models import init_db
# 留空则传 None，由 init_db 自动生成强密码（项目仅保留 admin 用户，无 viewer）
init_db('$ADMIN_PW' if '$ADMIN_PW' else None)
print('DB_INIT_OK')
PYEOF
    ) || {
        log_error "SQLite 数据库初始化失败: $init_output"
        return 1
    }

    if echo "$init_output" | grep -q "DB_INIT_OK"; then
        log_info "✓ SQLite 数据库初始化成功"

        # 捕获首次部署自动生成的密码并醒目提示（init_db 仅在创建用户时打印该行）
        local gen_pw
        gen_pw=$(echo "$init_output" | grep '^ADMIN_PASSWORD_INITIALIZED=' | head -1 | cut -d= -f2-) || true
        if [ -n "$gen_pw" ]; then
            log_warn "============================================================"
            log_warn "⚠️  首次部署已自动生成管理员密码，请立即记录并尽快修改！"
            log_warn "    管理员账号: admin"
            log_warn "    管理员密码: $gen_pw"
            log_warn "============================================================"
        fi

        # 将环境变量配置写入 SQLite app_config 表
        log_info "将环境变量配置写入 SQLite..."
        cd /app && /app/.venv/bin/python -c "
from web.models import set_app_config_raw
set_app_config_raw('Testing.timeout', '$TEST_TIMEOUT')
set_app_config_raw('Testing.concurrent_threads', '$CONCURRENT_THREADS')
set_app_config_raw('Output.filename', '$OUTPUT_FILENAME')
print('ENV_CONFIG_IMPORTED')
" 2>&1 | grep -v "^$"
        log_info "✓ 环境变量已写入 SQLite app_config 表"
    else
        log_warn "SQLite 初始化输出: $init_output"
    fi

    return 0
}

# 配置文件初始化函数
setup_config_files() {
    log_info "开始初始化配置文件..."

    # 1. SQLite 数据库初始化
    if ! init_sqlite_db; then
        log_error "SQLite 数据库初始化失败，启动中止"
        return 1
    fi

    # 2. 频道规则文件（可选，不阻断启动）
    if [ ! -f "/config/channel_rules.yml" ]; then
        log_warn "频道规则文件不存在: /config/channel_rules.yml"
        if [ -f "/app/channel_rules.yml" ]; then
            if cp "/app/channel_rules.yml" "/config/channel_rules.yml"; then
                log_info "已创建默认频道规则文件"
            else
                log_warn "无法复制默认频道规则文件，跳过（不影响启动）"
            fi
        else
            log_info "默认频道规则文件也不存在，跳过（不影响启动）"
        fi
    else
        log_info "频道规则文件已存在: /config/channel_rules.yml"
    fi

    # 3. 检查Nginx配置文件（必要，无配置则退出）
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
    chmod 640 /log/app.log /log/cron.log 2>/dev/null || {
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
    
    # 设置数据库文件权限（仅拥有者可读写——P0安全修复）
    if [ -f "$DB_PATH" ]; then
        chmod 600 "$DB_PATH" 2>/dev/null || true
    fi
    
    log_info "文件权限设置完成"
}

# Nginx服务管理函数
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
    
    # 注入NGINX_PORT环境变量到nginx配置（使用 envsubst）
    if command -v envsubst >/dev/null 2>&1; then
        log_info "使用 envsubst 注入 Nginx 端口: ${NGINX_PORT}"
        envsubst '${NGINX_PORT}' < /etc/nginx/nginx.conf > /tmp/nginx.conf.tmp \
            && mv /tmp/nginx.conf.tmp /etc/nginx/nginx.conf
    else
        # 降级方案：直接用 sed 替换（以防 envsubst 不可用）
        log_info "envsubst 不可用，使用 sed 注入 Nginx 端口: ${NGINX_PORT}"
        sed -i "s/listen \[::\]:.*/listen [::]:${NGINX_PORT};/g" /etc/nginx/nginx.conf
        sed -i "s/listen .* default_server/listen ${NGINX_PORT} default_server/g" /etc/nginx/nginx.conf
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
    
    # 启动Nginx（后台运行）
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
    
    local python_path="/app/.venv/bin/python"
    if [ ! -f "$python_path" ]; then
        python_path=$(command -v python3 || command -v python || echo "/usr/local/bin/python")
    fi
    
    # 创建定时任务
    echo "# 直播源管理工具定时任务" > "$cron_file"
    echo "# 自动生成于: $(date)" >> "$cron_file"
    echo "$UPDATE_CRON cd /app && PYTHONPATH=/app $python_path -m app >> /log/cron.log 2>&1" >> "$cron_file"
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
    if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
        log_error "Python不可用"
        return 1
    fi
    log_info "✓ Python检查通过"
    
    # 检查FFmpeg/FFprobe是否可用（仅警告，不自动安装）
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
    
    # 执行主程序（app 包的 __main__ 入口）
    if PYTHONPATH=/app /app/.venv/bin/python -m app; then
        log_info "✓ 主程序执行成功"
        # 写入健康检查标记，记录最近成功执行时间
        echo "healthy" > /www/output/health
        chmod 644 /www/output/health
        return 0
    else
        log_error "✗ 主程序执行失败，退出码: $?"
        return 1
    fi
}

# 进程监控函数
monitor_processes() {
    log_info "启动进程监控..."
    
    local check_interval=30
    local nginx_restart_count=0
    local max_restarts=3
    local WEB_PID_FILE="/var/run/web.pid"
    
    while true; do
        # 检查Nginx
        if [ -f /var/run/nginx.pid ]; then
            local npid=$(cat /var/run/nginx.pid 2>/dev/null)
            if [ -z "$npid" ] || ! kill -0 "$npid" 2>/dev/null; then
                log_error "Nginx已停止，尝试重启... ($((nginx_restart_count + 1))/$max_restarts)"
                if [ $nginx_restart_count -lt $max_restarts ]; then
                    setup_nginx && nginx_restart_count=0 || nginx_restart_count=$((nginx_restart_count + 1))
                else
                    log_error "Nginx重启次数超限，停止重试"
                    return 1
                fi
            fi
        fi
        
        # 检查Web管理进程（从PID文件读取）
        if [ -f "$WEB_PID_FILE" ]; then
            local web_pid=$(cat "$WEB_PID_FILE" 2>/dev/null)
            if [ -z "$web_pid" ] || ! kill -0 "$web_pid" 2>/dev/null; then
                log_warn "Web管理进程已退出，重启中..."
                cd /app && PYTHONPATH=/app /app/.venv/bin/python -m uvicorn web.webapp:app --host 0.0.0.0 --port ${WEB_PORT} &
                local new_pid=$!
                echo "$new_pid" > "$WEB_PID_FILE"
                log_info "Web管理已重启 (PID: $new_pid)"
            fi
        fi
        
        # 健康端点
        curl -sf http://localhost:${NGINX_PORT}/health >/dev/null 2>&1 ||             log_warn "Nginx健康检查失败"
        
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
    log_info "直播源管理工具（Nginx版 / SQLite）启动中..."
    
    # 第一步：环境检测和自动安装
    if ! setup_environment; then
        log_warn "环境检测/安装失败，尝试继续启动（部分功能可能不可用）..."
    fi
    
    # 设置信号处理
    setup_signal_handlers
    
    # 执行健康检查
    if ! health_check; then
        log_error "健康检查失败，启动中止"
        exit 1
    fi
    
    # 检查必要目录（不再要求 /config 目录存在）
    if ! check_required_directories; then
        log_error "目录检查失败，启动中止"
        exit 1
    fi
    
    # 初始化配置文件（含 SQLite 数据库初始化）
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
    
    # 启动Web管理界面（后台守护）
    log_info "启动Web管理界面 (端口 ${WEB_PORT})..."
    cd /app && PYTHONPATH=/app /app/.venv/bin/python -m uvicorn web.webapp:app --host 0.0.0.0 --port ${WEB_PORT} &
    local WEB_PID=$!
    echo "$WEB_PID" > /var/run/web.pid
    sleep 1
    if kill -0 $WEB_PID 2>/dev/null; then
        log_info "✓ Web管理界面已启动 (PID: $WEB_PID, 端口: ${WEB_PORT})"
    else
        log_warn "Web管理界面启动可能异常，继续运行..."
    fi
    
    # 设置定时任务
    setup_cron_jobs
    
    # 启动时立即运行一次主程序（前台执行，便于查看日志）
    log_info "启动时立即执行一次直播源处理..."
    if start_main_program; then
        log_info "✓ 主程序执行成功"
    else
        # 不能 exit 1 导致容器退出，继续保持运行等待定时任务重试
        log_error "✗ 主程序执行失败，将继续等待定时任务重试"
    fi
    
    log_info "直播源管理工具（Nginx版 / SQLite）启动完成"
    log_info "Nginx服务访问地址: http://<容器IP>:${NGINX_PORT}/"
    log_info "查看日志: docker logs -f <容器名>"
    log_info "定时任务配置: $UPDATE_CRON"
    log_info "加密密钥查看: docker logs <container> | grep CONFIG_ENCRYPT_KEY"
    log_info "首次运行自动生成随机密钥，建议设置自定义环境变量"
    log_info "设置方式: docker run -e CONFIG_ENCRYPT_KEY=<您的密钥> ..."
    
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
