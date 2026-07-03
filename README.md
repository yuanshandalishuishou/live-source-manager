# 直播源管理工具 - 安装指南

## 项目概述

直播源管理工具是一个用于管理和测试直播源的工具，支持自动分类、健康检查和播放列表生成。

## 安装方式

本项目支持三种安装方式，请根据您的运行环境选择合适的方式：

### 1. Windows 下运行

#### 方式一：使用 PowerShell 脚本（推荐）

1. 右键点击 `setup_windows.ps1`
2. 选择 "使用 PowerShell 运行"
3. 按提示完成安装

#### 方式二：使用批处理启动器

1. 右键点击 `setup_windows.bat`
2. 选择 "以管理员身份运行"
3. 按提示完成安装

#### 安装内容

- Python 3.13（如未安装）
- Python 虚拟环境（`.venv`）
- Python 依赖包（使用清华镜像加速）
- FFmpeg（包含 ffmpeg.exe 和 ffprobe.exe）
- SQLite 数据库初始化

#### 启动服务

安装完成后，可以通过以下命令启动 Web 服务：

```cmd
cd /d E:\工作空间\live-source-manager
.venv\Scripts\python.exe -m web.webapp
```

访问地址：<ADDRESS_REMOVED>

默认账号：
- 管理员：`admin` / `admin123`
- 查看者：`viewer` / `viewer123`

---

### 2. Linux (Debian/Ubuntu) 下运行

#### 使用安装脚本

```bash
sudo bash setup_linux.sh
```

#### 安装内容

- 系统依赖（nginx, cron, procps 等）
- Python 3.13（如未安装）
- Python 虚拟环境（`.venv`）
- Python 依赖包（使用清华镜像加速）
- FFmpeg（包含 ffmpeg 和 ffprobe）
- SQLite 数据库初始化
- Nginx 配置
- systemd 服务

#### 启动服务

安装完成后，通过 systemd 启动：

```bash
sudo systemctl start live-source-web
sudo systemctl status live-source-web
```

访问地址：<ADDRESS_REMOVED>

默认账号：
- 管理员：`admin` / `admin123`
- 查看者：`viewer` / `viewer123`

---

### 3. Docker 方式运行

#### 前置条件

- 安装 Docker
- 安装 docker-compose

#### 快速启动

```bash
# 1. 复制 .env.example 为 .env 并修改密码
cp .env.example .env
# 编辑 .env 文件，设置强密码

# 2. 构建并启动容器
docker-compose up -d --build

# 3. 查看日志
docker-compose logs -f

# 4. 停止服务
docker-compose down
```

#### 国内网络优化

如果 Docker Hub 拉取镜像较慢，可以使用国内镜像加速：

```bash
# 腾讯云镜像
export DOCKER_MIRROR=https://mirror.ccs.tencentyun.com
docker-compose up -d --build

# 或使用阿里云镜像（需要登录阿里云容器镜像服务获取加速地址）
# https://cr.console.aliyun.com/cn-hangzhou/instances/mirrors
```

#### 数据存储

以下数据通过卷映射持久化：
- `./config`: 配置文件
- `./logs`: 日志文件
- `./output`: 输出的 M3U 文件
- `./data`: SQLite 数据库

---

## 配置说明

### Web 界面配置

安装完成后，可以通过 Web 界面配置：
1. 访问 `http://localhost:23455`
2. 使用管理员账号登录
3. 进入"配置"页面修改参数

### 主要配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| Testing.timeout | 测试超时时间（秒） | 10 |
| Testing.concurrent_threads | 并发线程数 | 50 |
| Output.filename | 输出文件名 | live.m3u |
| UPDATE_CRON | 定时任务表达式 | `0 6,12,18,22 * * *` |

---

## 常见问题

### 1. FFmpeg 不可用

**Windows**: 确保 `tools\ffmpeg\bin` 已添加到 PATH

**Linux**: 运行 `sudo apt-get install -y ffmpeg` 或手动下载静态构建

**Docker**: 镜像已包含 FFmpeg，无需额外操作

### 2. 数据库初始化失败

删除现有数据库文件并重新初始化：

```bash
# Windows
del /f /q data\web.db

# Linux/Docker
rm -f data/web.db
```

然后重新启动服务，系统会自动重新初始化数据库。

### 3. Web 服务无法访问

- 检查端口是否被占用（默认 23455）
- 检查防火墙设置
- 查看日志文件：`logs/app.log`

---

## 项目结构

```
live-source-manager/
├── app/                    # 核心应用逻辑
│   ├── main.py           # 主程序入口
│   ├── classifier.py      # 频道分类引擎
│   ├── crawler.py         # 源数据采集
│   ├── testing.py        # 流测试
│   └── config_utils.py   # 配置管理
├── web/                    # Web 管理界面
│   ├── webapp.py         # FastAPI 应用
│   ├── models.py          # 数据库模型
│   └── crypto_utils.py   # 加密工具
├── config/                  # 配置文件
│   └── channel_rules.yml # 频道规则
├── data/                    # 数据目录
│   └── seed_*.sql        # 种子数据
├── requirements.txt         # Python 依赖
├── setup_windows.ps1      # Windows 安装脚本
├── setup_windows.bat       # Windows 批处理启动器
├── setup_linux.sh          # Linux 安装脚本
├── docker-compose.yml      # Docker Compose 配置
├── Dockerfile               # Docker 镜像构建文件
└── README.md               # 本文件
```

---

## 技术支持

如有问题，请通过以下方式获取支持：
1. 查看日志文件：`logs/app.log`
2. 提交 Issue：项目仓库
3. 联系开发者

---

**注意**：生产环境请务必修改默认密码和加密密钥！
