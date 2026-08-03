# =============================================================
# Dockerfile — 多阶段构建 · 宿主无关 · 网络自适应
# =============================================================
# 只要宿主有 Docker，一条命令即可构建镜像：
#   docker build -t lsm:latest .
# 国内用户可使用镜像加速：
#   docker build --build-arg BASE_IMAGE=python:3.13-slim-bookworm -t lsm:latest .
# =============================================================

ARG BASE_IMAGE=python:3.13-slim-bookworm

# ===== Stage 1: 构建环境（仅准备构建工具）=====
FROM ${BASE_IMAGE} AS builder

# 安装构建工具（Debian bookworm 容器内）
# 优先使用清华镜像源加速国内构建
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list && \
    apt-get -o Acquire::Retries=3 update && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        xz-utils \
        && \
    apt-get clean && rm -rf /var/lib/apt/lists/*


# ===== Stage 2: 运行环境 =====
FROM ${BASE_IMAGE}

ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/ \
    PROJECT_DIR=/app \
    WEB_DATA_DIR=/data \
    DEBIAN_FRONTEND=noninteractive \
    NGINX_PORT=12345 \
    WEB_PORT=23456 \
    TEST_TIMEOUT=30 \
    CONCURRENT_THREADS=50 \
    OUTPUT_FILENAME=live.m3u \
    UPDATE_CRON="0 6,12,18,22 * * *"

LABEL maintainer="Live Source Manager <admin@example.com>" \
      description="Live Source Manager with Nginx" \
      version="3.0"

# 运行时 apt：只装绝对必需的包
# 优先使用清华镜像源加速国内构建
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list && \
    apt-get -o Acquire::Retries=3 update && \
    apt-get install -y --no-install-recommends \
        tzdata \
        cron \
        nginx \
        curl \
        ca-certificates \
        procps \
        dos2unix \
        gettext-base \
        python3-venv \
        ffmpeg \
        && \
    ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    dpkg-reconfigure -f noninteractive tzdata && \
    update-ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /app /config /log /www/output /data /var/log/nginx /tmp/livesourcemanager && \
    chown -R www-data:www-data /www/output /var/log/nginx

# FFmpeg 已通过上方 apt-get install 安装（Debian 官方仓库 ffmpeg 包，稳定，无需 GitHub 下载）。
# 双保险：软链 /app/tools/ffmpeg/{ffmpeg,ffprobe} -> /usr/bin
# 兼容程序「项目内 tools/ffmpeg 目录」查找逻辑，也方便用户挂载宿主二进制到该目录。
RUN mkdir -p /app/tools/ffmpeg \
    && ln -sf /usr/bin/ffmpeg /app/tools/ffmpeg/ffmpeg \
    && ln -sf /usr/bin/ffprobe /app/tools/ffmpeg/ffprobe \
    && echo "FFmpeg ready: $(ffmpeg -version | head -1)"

# ── 构建期创建带全部依赖的虚拟环境 ──────────────
# start_docker.sh 默认以 /app/.venv/bin/python 启动 Web 服务；
# 必须在镜像内预先建好「带依赖」的 venv，否则容器首启时 start_docker.sh 会尝试
# 联网 pip install（离线/弱网环境下会失败，导致 Web 服务起不来）。
# 注意：venv 必须构建在运行阶段（路径固定为 /app/.venv），不能从 builder 阶段
# COPY 过来——否则 venv 内部 shebang 仍指向 builder 路径而失效。
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /app/.venv && \
    /app/.venv/bin/pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    /app/.venv/bin/pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn && \
    /app/.venv/bin/pip install --no-cache-dir --retries 5 --timeout 60 -r /tmp/requirements.txt && \
    rm -rf /tmp/requirements.txt ~/.cache/pip
# 复制 requirements.txt 供容器运行时依赖自检（check_python_deps 命中即跳过安装）
COPY requirements.txt /app/requirements.txt

WORKDIR /

# 复制应用文件
COPY app/ /app/app/
# 分类规则种子 SQL 脚本（替代 YAML）
# 注意：web.models 中 PROJECT_ROOT=/app，种子路径为 /app/app/data/seed_classification_rules.sql
COPY app/data/seed_classification_rules.sql /app/app/data/seed_classification_rules.sql
# 所有配置走 SQLite app_config 表（无 config.ini 依赖）
COPY config/channel_rules.yml /config/channel_rules.yml
COPY web/ /app/web/
COPY start_docker.sh /start_docker.sh
COPY nginx.conf /etc/nginx/nginx.conf
COPY healthcheck.sh /healthcheck.sh

# 权限 & 初始化（单 RUN 层）
# 将应用运行时目录软链到卷目录，使 docker-compose 的 ./data ./config ./output ./logs 挂载生效：
#   /app/www/output → /www/output（nginx 服务目录，M3U 由此发布）
#   /app/config     → /config    （channel_rules / online / sources）
#   /app/log        → /log       （应用日志）
RUN chmod +x /start_docker.sh /healthcheck.sh && \
    find /app -name "*.py" -exec chmod 644 {} \; && \
    chown -R www-data:www-data /www/output /var/log/nginx && \
    touch /log/cron.log /log/app.log && \
    chmod 640 /log/cron.log /log/app.log && \
    echo "healthy" > /www/output/health && \
    echo "<html><body><h1>Live Source Manager</h1><p>Nginx serving on port $NGINX_PORT</p></body></html>" > /www/output/index.html && \
    chmod 644 /www/output/health /www/output/index.html && \
    chown www-data:www-data /www/output/health /www/output/index.html && \
    ln -sf /dev/stdout /var/log/nginx/access.log && \
    ln -sf /dev/stderr /var/log/nginx/error.log && \
    mkdir -p /app/www && \
    ln -sfn /www/output /app/www/output && \
    rm -rf /app/config && ln -sfn /config /app/config && \
    ln -sfn /log /app/log

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD /healthcheck.sh

EXPOSE ${NGINX_PORT}
EXPOSE ${WEB_PORT}

CMD ["/start_docker.sh"]
