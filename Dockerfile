# =============================================================
# Dockerfile — 多阶段构建 · 宿主无关 · 网络自适应
# =============================================================
# 只要宿主有 Docker，一条命令即可构建镜像：
#   docker build -t lsm:latest .
# 国内用户可使用镜像加速：
#   docker build --build-arg BASE_IMAGE=python:3.13-slim-bookworm -t lsm:latest .
# =============================================================

ARG BASE_IMAGE=python:3.13-slim-bookworm

# ===== Stage 1: 构建环境 =====
FROM ${BASE_IMAGE} AS builder

# 安装构建工具（Debian bookworm 容器内）
# 优先使用清华镜像源加速国内构建
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        xz-utils \
        && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 配置 pip 使用清华镜像（国内加速）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

# 复制并安装 Python 依赖
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --target=/opt/pylib -r /tmp/requirements.txt && \
    rm -rf /tmp/requirements.txt ~/.cache/pip



# ===== Stage 2: 运行环境 =====
FROM ${BASE_IMAGE}

ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/ \
    DEBIAN_FRONTEND=noninteractive \
    NGINX_PORT=12345 \
    WEB_PORT=23456 \
    TEST_TIMEOUT=30 \
    CONCURRENT_THREADS=10 \
    OUTPUT_FILENAME=live.m3u \
    UPDATE_CRON="0 6,12,18,22 * * *"

LABEL maintainer="Live Source Manager <admin@example.com>" \
      description="Live Source Manager with Nginx" \
      version="3.0"

# 运行时 apt：只装绝对必需的包
# 优先使用清华镜像源加速国内构建
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        tzdata \
        cron \
        nginx \
        curl \
        ca-certificates \
        procps \
        dos2unix \
        gettext-base \
        && \
    ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    dpkg-reconfigure -f noninteractive tzdata && \
    update-ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /app /config /log ./www/output /data /var/log/nginx /tmp/livesourcemanager && \
    chown -R www-data:www-data ./www/output /var/log/nginx

# 安装 FFmpeg 静态构建（包含 ffmpeg + ffprobe）
RUN cd /tmp && \
    curl -sL https://github.com/BtbN/FFmpeg-Builds/releases/download/master/ffmpeg-master-latest-linux64-gpl.tar.xz -o ffmpeg.tar.xz || \
    curl -sL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o ffmpeg.tar.xz && \
    tar -xf ffmpeg.tar.xz && \
    cp ffmpeg-*/ffmpeg /usr/local/bin/ && \
    cp ffmpeg-*/ffprobe /usr/local/bin/ && \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe && \
    rm -rf /tmp/ffmpeg* && \
    echo "FFmpeg installed: $(ffmpeg -version | head -1)"

WORKDIR /

# 从 builder 阶段复制 Python 依赖
COPY --from=builder /opt/pylib /usr/local/lib/python3.13/site-packages/

# 复制应用文件
COPY app/ /app/app/
# 分类规则种子 SQL 脚本（替代 YAML）
COPY app/data/seed_classification_rules.sql /app/data/seed_classification_rules.sql
# 所有配置走 SQLite app_config 表（无 config.ini 依赖）
COPY config/channel_rules.yml /config/channel_rules.yml
COPY web/ /app/web/
COPY start_docker.sh /start_docker.sh
COPY nginx.conf /etc/nginx/nginx.conf
COPY healthcheck.sh /healthcheck.sh

# 权限 & 初始化（单 RUN 层）
# P3-新-4: 创建 /app/config/channel_rules.yml → /config/channel_rules.yml 软链接
RUN chmod +x /start_docker.sh /healthcheck.sh && \
    find /app -name "*.py" -exec chmod 644 {} \; && \
    chown -R www-data:www-data ./www/output /var/log/nginx && \
    touch /log/cron.log /log/app.log && \
    chmod 640 /log/cron.log /log/app.log && \
    echo "healthy" > ./www/output/health && \
    echo "<html><body><h1>Live Source Manager</h1><p>Nginx serving on port $NGINX_PORT</p></body></html>" > ./www/output/index.html && \
    chmod 644 ./www/output/health ./www/output/index.html && \
    chown www-data:www-data ./www/output/health ./www/output/index.html && \
    ln -sf /dev/stdout /var/log/nginx/access.log && \
    ln -sf /dev/stderr /var/log/nginx/error.log && \
    mkdir -p /app/config && \
    ln -sf /config/channel_rules.yml /app/config/channel_rules.yml

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD /healthcheck.sh

EXPOSE ${NGINX_PORT}
EXPOSE ${WEB_PORT}

CMD ["/start_docker.sh"]
