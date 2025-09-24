# 使用Python 3.9作为基础镜像
#FROM docker.1ms.run/python:3.9-bookworm
FROM docker.1ms.run/python:3.9-slim-bookworm


# 设置时区和语言环境
ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive


# 配置 APT 使用清华大学 Debian 源并安装基础包
RUN echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm main contrib non-free" > /etc/apt/sources.list && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-updates main contrib non-free" >> /etc/apt/sources.list && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-backports main contrib non-free" >> /etc/apt/sources.list && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian-security bookworm-security main contrib non-free" >> /etc/apt/sources.list && \
    rm -rf /etc/apt/sources.list.d/* && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        tzdata \
        nginx \
        cron \
        ffmpeg \
        curl \
        dos2unix \
        sudo \
        procps \
        libssl-dev \
        ca-certificates \
        libpq5 \
        libpq-dev \
        iproute2 && \
    ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    dpkg-reconfigure -f noninteractive tzdata && \
    update-ca-certificates && \
    groupadd -r nginx && useradd -r -g nginx nginx && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 创建必要的目录
RUN rm -rf /data &&mkdir -p /config /log /www/output /app /data

# 设置工作目录
WORKDIR /

# 配置 pip 使用清华大学镜像源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn && \
    pip install --upgrade pip

# 复制 requirements.txt 并安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir aiohttp_socks && \
    rm -rf ~/.cache/pip  # 清理 pip 缓存
	

# 复制应用文件
COPY . .



# 设置 cron 日志文件并设置权限
RUN touch /log/cron.log /log/app.log && chmod 666 /log/cron.log /log/app.log

# 暴露端口
EXPOSE 12345

# 启动应用
CMD ["/start.sh"]