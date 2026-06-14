# Live Source Manager（lsm）——直播源管理工具

> **一站式直播源自动下载、解析、分类、测试、筛选与输出工具**
>
> 镜像体积 **460MB** | 多阶段构建 | 一键部署 | 零依赖

---

## 📋 目录

- [快速开始](#-快速开始)
- [功能概述](#-功能概述)
- [部署方式](#-部署方式)
- [配置说明](#-配置说明)
- [使用指南](#-使用指南)
- [项目架构](#-项目架构)
- [API 说明](#-api-说明)
- [运维管理](#-运维管理)
- [开发指南](#-开发指南)
- [常见问题](#-常见问题)
- [技术栈](#-技术栈)

---

## 🚀 快速开始

### 前提条件

仅需 **Docker**（支持 Linux / macOS / Windows WSL2），无需预装 Python、Node.js 等任何运行时环境。

### 一条命令启动

```bash
# 海外机器
docker build -t lsm:latest .
docker run -d --name lsm -p 12345:12345 lsm:latest

# 国内机器（使用镜像代理加速）
docker build --build-arg BASE_IMAGE=docker.1ms.run/python:3.9-slim-bookworm -t lsm:latest .
docker run -d --name lsm -p 12345:12345 lsm:latest
```

### 构建参数说明

| 参数 | 默认值 | 说明 |
|:----|:------|:-----|
| `BASE_IMAGE` | `python:3.9-slim-bookworm` | 基础镜像。国内环境可使用 `docker.1ms.run/python:3.9-slim-bookworm` |

### 一键部署脚本

```bash
git clone <仓库URL> && cd live-source-manager-main

# 海外
./build.sh

# 国内
./build.sh --proxy

# 启动
docker run -d --name lsm -p 12345:12345 lsm:latest
```

---

## 📖 功能概述

### 核心流程

```
[在线源/本地文件] → 下载 → 解析 URL → 分类频道 → 流测试 → 分层筛选 → 输出 M3U
```

### 功能清单

| 功能 | 说明 |
|:----|:------|
| **多源下载** | 支持在线 URL、GitHub RAW、本地文件等多种数据源 |
| **智能解析** | 自动解析 M3U/TXT 格式直播源列表 |
| **频道分类** | 基于 YAML 规则的自动分类（央视、卫视、体育、电影等） |
| **流可用性测试** | 实测每个源的响应时间、分辨率、是否为音频流 |
| **分层筛选** | 按分辨率（4K/1080p/720p 等）、延迟、可用性分层过滤 |
| **M3U 输出** | 生成分类目录的多级播放列表，通过 Nginx 发布 |
| **定时更新** | 内置 cron，支持每日多次自动更新直播源 |
| **Web 服务** | Nginx 提供 HTTP 访问，支持跨域请求 |

### 应用场景

- IPTV 直播源聚合与管理
- 家庭/企业内网直播服务
- 直播源可用性监控与自动切换
- 多端播放器（VLC/PotPlayer/Kodi）直播源统一入口

---

## 🐳 部署方式

### Docker 部署（推荐）

```bash
# 构建
docker build --build-arg BASE_IMAGE=docker.1ms.run/python:3.9-slim-bookworm -t lsm:latest .

# 运行（前台）
docker run -d --name lsm \
  -p 12345:12345 \
  -v /host/config:/config \
  -v /host/output:/www/output \
  -e NGINX_PORT=12345 \
  -e TEST_TIMEOUT=30 \
  -e UPDATE_CRON="0 6,12,18,22 * * *" \
  --restart unless-stopped \
  lsm:latest
```

### 环境变量

| 变量 | 默认值 | 说明 |
|:----|:------|:-----|
| `NGINX_PORT` | `12345` | Nginx 监听端口 |
| `TEST_TIMEOUT` | `30` | 单流测试超时时间（秒） |
| `CONCURRENT_THREADS` | `10` | 并发测试数量 |
| `OUTPUT_FILENAME` | `live.m3u` | 输出文件名称 |
| `UPDATE_CRON` | `0 6,12,18,22 * * *` | 定时更新 cron 表达式 |

### 数据持久化

```bash
# 配置文件持久化（推荐）
-v /host/config:/config

# 输出文件持久化
-v /host/output:/www/output
```

### docker-compose（推荐）

```yaml
version: '3.8'
services:
  lsm:
    build:
      context: .
      args:
        BASE_IMAGE: docker.1ms.run/python:3.9-slim-bookworm
    ports:
      - "12345:12345"
    volumes:
      - ./config:/config
      - ./output:/www/output
    environment:
      - NGINX_PORT=12345
      - TEST_TIMEOUT=30
      - CONCURRENT_THREADS=10
      - UPDATE_CRON=0 6,12,18,22 * * *
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "/healthcheck.sh"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
```

---

## ⚙️ 配置说明

### 主配置文件 `config/config.ini`

```ini
[Network]
timeout = 30
concurrent_threads = 10
proxy_type =
proxy_host =
proxy_port =
proxy_username =
proxy_password =

[Source]
online_urls =
    https://example.com/live.m3u
    https://raw.githubusercontent.com/owner/repo/path

[Output]
filename = live.m3u
output_dir = /www/output
```

### 频道规则文件 `config/channel_rules.yml`

```yaml
rules:
  - name: "央视"
    keywords: ["CCTV", "CCTV-", "中央"]
    category: "央视"
    country: "CN"
  - name: "卫视"
    keywords: ["卫视", "湖南", "浙江", "江苏", "东方"]
    category: "卫视"
    country: "CN"
  # ……更多频道规则
```

### 输出文件结构

定时更新后，Nginx 将发布以下文件：

```
/www/output/
├── live.m3u              # 综合播放列表
├── 央视/
│   ├── CCTV-1.m3u
│   ├── CCTV-2.m3u
│   └── ……
├── 卫视/
│   ├── 湖南卫视.m3u
│   └── ……
├── 体育/
├── 电影/
├── 少儿/
├── 4K/
└── health                 # 健康检查文件
```

---

## 🎯 使用指南

### 访问播放列表

```
http://<服务器IP>:12345/live.m3u
http://<服务器IP>:12345/央视/CCTV-1.m3u
```

### 在播放器中打开

**VLC**：媒体 → 打开网络串流 → `http://<IP>:12345/live.m3u`

**PotPlayer**：右键 → 打开 → 打开链接 → `http://<IP>:12345/live.m3u`

**Kodi**：添加视频源 → 网络位置 → `http://<IP>:12345/live.m3u`

### 健康检查

```
http://<服务器IP>:12345/health
# 返回：healthy
```

### Nginx 状态页

```
http://<服务器IP>:12345/status
# 仅限内网访问
```

---

## 🏗 项目架构

```
lsm/
├── app/                        # Python 核心模块（13 个）
│   ├── __init__.py             # 包初始化
│   ├── main.py                 # 主入口及 EnhancedLiveSourceManager
│   ├── config_manager.py       # 配置管理与热加载
│   ├── source_manager.py       # 直播源下载与连接池管理
│   ├── stream_tester.py        # 流媒体测试（ffprobe + 下载速度）
│   ├── channel_rules.py        # 频道规则与自动分类
│   ├── m3u_generator.py        # M3U 文件生成
│   ├── url_sanitizer.py        # URL 安全审查（抵御 6 种攻击向量）
│   ├── error_handler.py        # 统一错误处理
│   ├── exceptions.py           # 异常体系（6 层继承）
│   ├── file_utils.py           # 文件工具
│   ├── models.py               # 数据模型（TypedDict）
│   ├── helpers.py              # 辅助函数
│   ├── network_test.py         # 网络诊断脚本（运维用）
│   └── test_http.py            # HTTP 服务测试脚本（运维用）
├── tests/                      # 测试文件（17 个，207 个用例）
│   ├── test_main.py
│   ├── test_source_manager.py
│   ├── test_stream_tester.py
│   ├── test_channel_rules.py
│   ├── test_m3u_generator.py
│   ├── test_config_manager.py
│   ├── test_url_sanitizer.py
│   ├── test_network_test.py
│   ├── test_test_http.py
│   └── ……
├── config/
│   ├── config.ini              # 主配置
│   └── channel_rules.yml       # 频道规则
├── Dockerfile                  # 多阶段构建
├── build.sh                    # 一键构建脚本
├── start.sh                    # 容器启动脚本
├── healthcheck.sh              # 健康检查
├── nginx.conf                  # Nginx 配置
├── requirements.txt            # Python 依赖
├── pytest.ini                  # 测试配置
└── .dockerignore               # 构建排除规则
```

### 模块依赖关系

```
main.py
  ├── config_manager.py     ← 配置加载与热加载
  ├── source_manager.py     ← 源下载（依赖 aiohttp）
  │     └── url_sanitizer.py ← URL 安全审查
  ├── stream_tester.py      ← 流测试（依赖 ffprobe）
  │     └── file_utils.py   ← 文件操作
  ├── channel_rules.py      ← 频道分类
  └── m3u_generator.py      ← M3U 输出
```

---

## 🔌 API 说明

| 端点 | 方法 | 说明 |
|:----|:----|:------|
| `/health` | GET | 健康检查，返回 `healthy` |
| `/status` | GET | Nginx 状态（仅限内网） |
| `/*.m3u` | GET | M3U 播放列表 |
| `/*.txt` | GET | TXT 格式源列表 |

---

## 🔧 运维管理

### 查看日志

```bash
# 容器日志
docker logs -f lsm

# 应用日志
docker exec lsm cat /log/app.log

# Cron 日志
docker exec lsm cat /log/cron.log
```

### 手动触发更新

```bash
docker exec lsm python3 /app/main.py
```

### 修改配置

```bash
# 编辑配置文件（需挂载卷）
vim /host/config/config.ini
docker restart lsm
```

### 性能监控

```bash
# 资源占用
docker stats lsm

# Nginx 连接数
docker exec lsm curl -s http://localhost:12345/status
```

### 备份

```bash
# 备份配置和输出
docker cp lsm:/config ./config-backup
docker cp lsm:/www/output ./output-backup
```

---

## 🧪 开发指南

### 本地开发环境

```bash
# 安装 Python 3.9+
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 安装测试依赖
pip install pytest pytest-asyncio

# 运行测试
PYTHONPATH=app python3 -m pytest tests/ -v
```

### 运行测试

```bash
# 全量测试（207 个用例）
PYTHONPATH=app python3 -m pytest tests/ -v --tb=short

# 指定模块
PYTHONPATH=app python3 -m pytest tests/test_main.py -v

# 查看覆盖率
PYTHONPATH=app python3 -m pytest tests/ --cov=app
```

### 构建镜像（开发调试）

```bash
# 快速构建
docker build --build-arg BASE_IMAGE=docker.1ms.run/python:3.9-slim-bookworm -t lsm:dev .

# 进入容器调试
docker run -it --rm --entrypoint=/bin/bash lsm:dev
```

---

## ❓ 常见问题

### Q：构建时提示 `python:3.9-slim-bookworm` 拉取超时？

国内网络可能需要使用镜像代理：

```bash
docker build --build-arg BASE_IMAGE=docker.1ms.run/python:3.9-slim-bookworm -t lsm:latest .
```

### Q：镜像体积多大？

**460MB**（通过多阶段构建与静态 ffprobe/ffmpeg 优化，相较于 apt 安装 ffmpeg 减少 51.3%）。

### Q：容器启动后未生成直播源？

请检查配置中的 `online_urls` 是否包含有效的直播源 URL，并确认网络连通性：

```bash
docker exec lsm python3 /app/network_test.py
```

### Q：如何定时更新直播源？

通过 `UPDATE_CRON` 环境变量控制，默认于每日 6:00、12:00、18:00、22:00 执行更新。

### Q：ffprobe/ffmpeg 功能正常吗？

静态 ffprobe 版本为 **7.0.2**，支持流媒体探测的全部功能（分辨率、编码格式、音频/视频判断等）。启动时若静态二进制缺失，将自动通过 `apt-get` 安装。

### Q：如何修改频道分类规则？

编辑 `config/channel_rules.yml` 文件后重启容器即可。规则支持关键词匹配、正则表达式、分类及地区标签。

---

## 📊 技术栈

| 组件 | 技术选型 | 版本 |
|:----|:---------|:----:|
| 运行时 | Python 3.9 | 3.9-slim-bookworm |
| Web 服务器 | Nginx | 1.22.1 |
| 流探测 | ffprobe（静态） | 7.0.2 |
| HTTP 框架 | aiohttp | 3.9.5 |
| 配置解析 | PyYAML | 6.0.3 |
| 网络请求 | requests | 2.32.5 |
| 容器基础镜像 | python:3.9-slim-bookworm | ~120MB |
| 最终镜像体积 | 多阶段构建 | **460MB** |

---

## 📄 许可证

本项目仅供学习与个人使用。直播源版权归各自所有者所有，请遵守当地法律法规。

---

> **项目交付状态**
>
> ✅ 三位专家终审通过（架构/代码/测试）
> ✅ 207 个测试用例全部通过
> ✅ Docker 镜像 460MB，一键部署
> ✅ 所有历史问题已修复（3 轮审核，33 项修复）
> ✅ 最终报告：`REPORT.md`
