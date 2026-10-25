# Live Source Manager

直播源管理工具：多源自动采集 → 规则分类 → 并发测速 → 质量筛选 → 生成 M3U 播放列表与 XMLTV 节目单，配 Web 管理后台与定时更新。

## 功能清单

- **多源采集**：在线 URL 列表、GitHub 仓库（raw/api/proxy/mirror 多种下载方式）、本地目录，支持 IPv4/IPv6 与 SOCKS5/HTTP 代理
- **规则分类**：可扩展分类规则（种子 SQL + Web 可编辑），频道名映射、负向关键词过滤
- **并发测速**：ffprobe/aiohttp 双引擎，冻结退避、失效自动停用、Host 级测速共享
- **质量筛选**：分辨率/码率/延迟/速度分层过滤，候选池择优，白名单强制保留
- **EPG 电子节目单**：预置 7 个稳定源，订阅源 `url-tvg`/`x-tvg-url` 自动提取注册，生成 `epg.xml.gz`
- **Web 管理**：源/配置/测速/EPG/用户全功能后台，RBAC 与 CSRF 防护
- **定时更新**：cron 调度自动采集测速发布，支持 Docker/Windows 任务计划/Linux cron

## 端口

| 端口 | 用途 |
|---|---|
| `12345` | 文件发布（Nginx）：`live.m3u`、`epg.xml.gz` 等 |
| `23456` | Web 管理后台 |

## 快速开始

### Docker（一条命令）

```bash
docker run -d \
  --name live-source-manager \
  --restart unless-stopped \
  -p 12345:12345 -p 23456:23456 \
  -e WEB_ADMIN_PASSWORD='你的强密码(至少8位且含字母和数字)' \
  -e CONFIG_ENCRYPT_KEY='你的Fernet密钥' \
  -v ./data:/data -v ./config:/config \
  -v ./output:/www/output -v ./logs:/log \
  ghcr.io/yuanshandalishuishou/live-source-manager:latest
```

镜像支持 `linux/amd64` 与 `linux/arm64`。启动后访问 <http://localhost:23456>，账号 `admin`
（未设 `WEB_ADMIN_PASSWORD` 时为默认初始密码，首次登录强制改密；生产务必显式设置）。
更多 Docker 细节（数据卷、环境变量全表、EPG、国内网络部署）见 [DOCKER_RUN.md](DOCKER_RUN.md)
与 [DOCKER_DEPLOY_CN.md](DOCKER_DEPLOY_CN.md)。

### Windows

```powershell
# 1) 创建 venv 并安装依赖（Python 3.11+）
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2) 启动 Web 后台（默认 23456，可用 WEB_PORT 环境变量覆盖）
.venv\Scripts\python -m uvicorn web.webapp:app --host 0.0.0.0 --port 23456

# 或使用开机自启包装脚本（任务计划程序调用）：
#   deploy\windows\start-web.bat
```

### Linux（裸机 / Docker）

```bash
# Docker Compose（推荐）
docker compose up -d

# 或裸机：安装依赖后由 start_docker.sh 统一编排
# （内置文件服务 12345 + Web 后台 23456 + cron 定时更新）
bash start_docker.sh
```

> 裸机部署注意：文件发布服务（12345）与 Web 后台（23456）不要与其它服务冲突；
> 定时更新依赖 cron（Docker 镜像内已含，裸机需自行安装 cron 并启用 UPDATE_CRON 条目）。

## EPG 说明

- **默认启用**：首启自动写入 7 个预置 EPG 源，后台按调度抓取并生成 `epg.xml.gz`（与 `live.m3u` 同目录发布）
- **订阅源自动提取**：解析 m3u 文件头 `#EXTM3U url-tvg="..."` / `x-tvg-url="..."` 自动注册为 EPG 源
  （开关 `EPG.open_subscribe_epg`，默认开启）
- **播放器直接用**：生成的 `live.m3u` 首行自带 `url-tvg` 指向，每条频道带 `tvg-id`，
  TiviMate / DIYP / Kodi / PotPlayer 等导入即可看到节目单，无需额外配置
- 多网卡/Docker 端口映射场景需显式设置 `EPG.web_base_url`，详见 [DOCKER_RUN.md](DOCKER_RUN.md)

## 配置

所有配置项（含环境变量映射）见 **[CONFIG.md](CONFIG.md)**；默认值单一事实来源为
`app/config.py` 的 `Config._DEFAULT_VALUES`（可由 `config/config-defaults.yaml` 覆盖），
Web 后台「配置中心」可视化编辑。本期有意遗留事项见 [docs/BACKLOG.md](docs/BACKLOG.md)。

## 开发与测试

```bash
# 安装开发依赖（含测试链）
pip install -r requirements-dev.txt

# 全量测试
PYTHONPATH=. pytest tests/ -q

# 代码检查（pre-commit）
pre-commit run --all-files
```

## 目录结构

```
app/            核心业务（采集/解析/测速/分类/EPG/M3U 生成）
web/            FastAPI Web 管理后台（路由/模板/SQLite 模型）
config/         默认配置与分类规则
tools/          ffmpeg/ffprobe 与配置文档生成器
tests/          全量测试套件
deploy/         Windows/Linux 部署脚本
docs/           架构文档、BACKLOG、历史归档
```

## License

MIT
