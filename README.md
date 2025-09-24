# Live Source Manager - 直播源管理工具

[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://www.docker.com/)
[![Python](https://img.shields.io/badge/Python-3.8%2B-green.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

一个功能强大的直播源管理工具，能够自动从多个来源收集、测试、过滤和生成高质量的直播源播放列表。

## 🌟 项目简介

Live Source Manager 是一个智能的直播源管理系统，它能够：

- 🔍 **自动收集** - 从本地文件、在线URL和GitHub仓库等多个来源获取直播源
- 🧪 **智能测试** - 使用多线程并发测试直播源的有效性和质量
- 🎯 **精准分类** - 基于YAML规则文件对频道进行智能分类和地理定位
- 📊 **质量评估** - 全面评估直播源的延迟、速度、分辨率等质量指标
- 🚀 **高效输出** - 生成多种格式的播放列表（M3U/TXT）

## ✨ 核心功能

### 多源采集
- **本地源**：支持从本地目录读取M3U/TXT文件
- **在线源**：支持从多个在线URL自动下载直播源
- **GitHub源**：自动扫描GitHub仓库中的直播源文件
- **代理支持**：支持HTTP/HTTPS/SOCKS5代理，确保访问稳定性

### 智能测试
- **并发测试**：多线程并发测试，提高测试效率
- **质量评估**：测试延迟、下载速度、比特率等关键指标
- **分辨率检测**：自动识别视频流的分辨率信息
- **缓存机制**：智能缓存测试结果，避免重复测试

### 频道识别与分类
- **规则驱动**：基于YAML配置文件进行频道识别
- **多级分类**：支持国家、地区、类型等多维度分类
- **智能匹配**：使用关键词匹配和优先级规则进行精准分类
- **地理定位**：自动识别频道所属的国家、省份和城市

### 过滤与优化
- **质量过滤**：基于延迟、速度、分辨率等条件过滤低质量源
- **去重优化**：自动去除重复频道，保留最优源
- **数量控制**：限制同一频道的源数量，避免列表臃肿
- **多种模式**：支持区间筛选、最低要求、最高限制等过滤模式

### 多格式输出
- **M3U格式**：标准M3U播放列表，兼容大多数播放器
- **TXT格式**：简化文本格式，便于手动导入
- **分组输出**：按分类、地区、来源等多种方式分组
- **元数据丰富**：包含频道图标、分组信息、质量指标等

### 容器化部署
- **Docker支持**：完整的Docker镜像和运行脚本
- **环境配置**：支持环境变量配置，便于容器化部署
- **持久化存储**：数据、配置、日志持久化存储
- **自动更新**：支持定时自动更新直播源

## 🛠 技术架构

### 系统组件
```
├── 配置管理 (Config)
├── 频道规则 (ChannelRules)
├── 日志系统 (Logger)
├── 数据库管理 (ChannelDB)
├── 源管理器 (SourceManager)
├── 流媒体测试器 (StreamTester)
├── M3U生成器 (M3UGenerator)
└── API接口 (LiveSourceManagerAPI)
```

### 核心技术栈
- **语言**：Python 3.8+
- **数据库**：SQLite（轻量级，高性能）
- **Web服务器**：Nginx（静态文件服务）
- **容器**：Docker（部署和运行）
- **异步处理**：asyncio/aiohttp（高性能网络请求）

## 📦 安装部署

### 环境要求
- Docker Engine 20.10+
- 或 Python 3.8+（直接运行）
- 磁盘空间：至少1G可用空间
- 内存：建议512MB以上

### 快速开始（Docker方式）

1. **克隆项目**
```bash
git clone https://github.com/yuanshandalishuishou/live-source-manager
cd live-source-manager
```

2. **配置环境变量**
```bash
# 编辑 dockrun.sh 中的环境变量
# 或直接使用默认配置
```

3. **运行容器**
```bash
chmod +x dockrun.sh
./dockrun.sh
```


## ⚙️ 配置说明

### 主要配置文件

#### `config.ini` - 主配置文件
```ini
[Sources]
local_dirs = /config/sources          # 本地源目录
online_urls = https://example.com/source.m3u  # 在线源URL
github_sources = owner/repo/path      # GitHub仓库路径

[Network]
proxy_enabled = True                  # 启用代理
proxy_type = socks5                   # 代理类型
proxy_host = 192.168.1.211            # 代理主机
proxy_port = 1800                     # 代理端口

[Testing]
timeout = 10                          # 测试超时时间(秒)
concurrent_threads = 20               # 并发线程数
enable_speed_test = True              # 启用速度测试

[Output]
filename = live.m3u                   # 输出文件名
group_by = category                   # 分组方式
enable_filter = True                  # 启用过滤

[Filter]
max_latency = 5000                    # 最大延迟(毫秒)
min_resolution = 720p                 # 最小分辨率
max_resolution = 1080p                # 最大分辨率
```

#### `channel_rules.yml` - 频道规则文件
```yaml
categories:
  - name: "央视频道"
    priority: 1
    keywords: ["CCTV", "央视", "中央"]
  
channel_types:
  卫视: ["卫视"]
  电影: ["电影", "影院", "剧场"]

geography:
  continents:
    - name: "Asia"
      countries:
        - name: "中国大陆"
          code: "CN"
          provinces:
            - name: "北京"
              keywords: ["北京", "BTV"]
```

### 环境变量配置
支持通过环境变量覆盖配置文件中的设置：

```bash
# 定时任务配置
UPDATE_CRON="0 12 * * *"        # 每天中午12点更新
TEST_TIMEOUT=10                 # 测试超时时间
CONCURRENT_THREADS=50           # 并发线程数

# 代理配置
PROXY_ENABLED=true
PROXY_TYPE=socks5
PROXY_HOST=192.168.1.211

# GitHub配置
GITHUB_API_TOKEN=your_token_here
```

## 📁 文件结构

```
live-source-manager/
├── app/
│   └── main.py                 # 主程序入口
├── config/
│   ├── config.ini             # 主配置文件
│   ├── channel_rules.yml      # 频道规则文件
│   └── nginx.conf            # Nginx配置
├── logs/                      # 日志目录
├── output/                    # 输出文件目录
├── data/                      # 数据库文件目录
├── start.sh                   # 容器启动脚本
├── dockrun.sh                 # Docker运行脚本
└── requirements.txt           # Python依赖
```

## 🚀 使用方法

### 基本操作流程

1. **初始化配置**
   - 编辑 `config.ini` 设置源地址
   - 配置 `channel_rules.yml` 定义分类规则

2. **运行采集测试**
   ```bash
   python main.py
   # 或通过Docker自动运行
   ```

3. **查看结果**
   - 访问 `http://localhost:12345/live.m3u` 查看生成的播放列表
   - 查看日志文件了解详细处理过程

### 输出文件说明

程序会生成多种格式的输出文件：

- `live.m3u` - 所有有效源的M3U播放列表
- `live.txt` - 所有有效源的TXT格式
- `qualified_live.m3u` - 合格源的M3U播放列表  
- `qualified_live.txt` - 合格源的TXT格式

### 定时任务

通过cron定时自动更新：
```bash
# 默认每天中午12点更新
UPDATE_CRON="0 12 * * *"
```

## 🔧 高级功能

### API接口
启用API功能后，可以通过RESTful接口管理直播源：

```bash
# 获取所有有效源
GET /api/sources

# 获取合格源
GET /api/sources/qualified

# 获取统计信息
GET /api/stats

# 手动刷新源
POST /api/refresh
```

### 用户代理配置
支持为不同源配置不同的User-Agent：

```ini
[UserAgents]
ua_position = extinf          # UA位置：extinf或url
ua_enabled = True
/sources/local.m3u = MyUserAgent/1.0
https://example.com/source.m3u = CustomAgent/2.0
```

### 分辨率筛选模式
支持多种分辨率筛选模式：

- **range**（区间模式）：必须同时满足最小和最大分辨率
- **min_only**（仅最低）：只检查最低分辨率要求
- **max_only**（仅最高）：只检查最高分辨率限制

## 📊 监控与日志

### 日志系统
- **日志级别**：DEBUG、INFO、WARNING、ERROR、CRITICAL
- **日志轮转**：自动分割和压缩旧日志
- **多输出**：同时输出到文件和控制台

### 统计信息
程序运行后会输出详细的统计信息：

```
===== 源质量统计信息 =====
有效源总数: 1250
平均延迟: 856.23ms, 中位数延迟: 723.45ms
平均速度: 245.67KB/s, 中位数速度: 198.34KB/s
最常见分辨率: 1920x1080

分辨率分布:
  1920x1080: 450个
  1280x720: 320个
  720x576: 180个
```

## 🔒 安全考虑

1. **网络隔离**：建议在隔离的网络环境中运行
2. **代理支持**：通过代理访问外部资源，增强隐私保护
3. **权限控制**：容器以非root用户运行，减少安全风险
4. **输入验证**：对所有输入源进行严格的格式验证

## 🐛 故障排除

### 常见问题

1. **网络连接失败**
   - 检查代理配置是否正确
   - 验证网络连通性
   - 查看防火墙设置

2. **测试速度慢**
   - 调整 `concurrent_threads` 参数
   - 检查网络带宽
   - 考虑使用更快的代理服务器

3. **分类不准确**
   - 检查 `channel_rules.yml` 规则配置
   - 添加更多关键词匹配规则
   - 调整优先级设置

### 日志分析
查看 `/log/app.log` 获取详细错误信息：

```bash
docker logs livesourcemanager
# 或
tail -f logs/app.log
```


## 🙏 致谢

感谢以下开源项目的贡献：
- [aiohttp](https://github.com/aio-libs/aiohttp) - 异步HTTP客户端/服务器
- [tqdm](https://github.com/tqdm/tqdm) - 进度条显示
- [PyYAML](https://github.com/yaml/pyyaml) - YAML解析器


---

**注意**：请确保遵守相关法律法规，仅将本工具用于学习及合法用途。使用者应对其行为负全部责任。
