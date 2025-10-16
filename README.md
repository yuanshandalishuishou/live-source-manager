# 📺 直播源管理工具 (Live Source Manager)

**一个智能、高效的直播源收集、测试和分发系统**  
*自动聚合多源直播流，智能过滤优质频道，通过Nginx提供稳定可靠的M3U播放列表服务*

---

## ✨ 核心特色

### 🚀 全自动流程
- **一键部署** - Docker容器化，开箱即用
- **智能聚合** - 自动从多个在线源和本地文件收集直播源
- **质量检测** - 实时测试流媒体可用性、延迟和分辨率
- **智能分类** - 基于YAML规则的频道分类和地理位置识别

### 🎯 智能过滤
- **多维度筛选** - 按延迟、分辨率、比特率、下载速度等条件过滤
- **质量优先** - 自动选择每个频道的最佳源（最多保留3个）
- **双重输出** - 同时生成包含所有有效源的完整版和仅合格源的精选版

### 🌐 高效服务
- **Nginx集成** - 高性能静态文件服务，支持大量并发访问
- **多格式支持** - 同步输出M3U和TXT两种播放列表格式
- **跨平台兼容** - 支持IPTV播放器、Kodi、VLC等主流播放软件

### ⚙️ 灵活配置
- **代理支持** - 内置SOCKS5/HTTP代理，突破网络限制
- **定时更新** - 可配置的定时任务，自动保持直播源新鲜度
- **详细日志** - 完整的处理日志和统计信息，便于监控和调试

---

## 🛠 快速开始

### 系统要求
- Docker & Docker Compose
- 至少2GB可用内存
- 10GB可用磁盘空间

### 一键部署
```bash
# 克隆项目
git clone https://github.com/yuanshandalishuishou/live-source-manager.git
cd live-source-manager

# 运行部署脚本（自动构建镜像并启动服务）
chmod +x dockrun.sh
./dockrun.sh
```

### 手动部署
```bash
# 构建Docker镜像
docker build -t livesourcemanager-nginx .

# 运行容器
docker run -d \
  --name livesourcemanager \
  -p 12345:12345 \
  -v $(pwd)/config:/config \
  -v $(pwd)/logs:/log \
  -v $(pwd)/output:/www/output \
  livesourcemanager-nginx
```
### 特别提醒！特别提醒！在部署项目之前，请一定修改config.ini中视频来源，屏蔽一些在线视频源或者本地视频源文件，切记不要选太多，否则系统运行以小时计。
### 环境变量配置
```bash
# 代理设置
-e PROXY_ENABLED=true
-e PROXY_TYPE=socks5
-e PROXY_HOST=192.168.1.211
-e PROXY_PORT=1800

# 更新频率
-e UPDATE_CRON="0 2 * * *"  # 每天凌晨2点更新

# 性能调优
-e CONCURRENT_THREADS=50
-e TEST_TIMEOUT=10
```

---

## 📁 项目结构

```
live-source-manager/
├── app/                           # 核心应用代码
│   ├── main.py                   # 主程序入口
│   ├── config_manager.py         # 配置管理
│   ├── channel_rules.py          # 频道规则管理
│   ├── source_manager.py         # 源文件管理
│   ├── stream_tester.py          # 流媒体测试
│   └── m3u_generator.py          # 播放列表生成
├── config/                       # 配置文件目录
│   ├── config.ini               # 主配置文件
│   └── channel_rules.yml        # 频道分类规则
├── nginx.conf                   # Nginx配置文件
├── requirements.txt             # Python依赖
├── dockrun.sh                  # Docker部署脚本
├── start.sh                    # 容器启动脚本
└── output/                     # 生成的播放列表文件
```

---

## ⚙️ 详细配置

### 直播源配置 (`config/config.ini`)
```ini
[Sources]
# 本地源目录
local_dirs = /config/sources

# 在线源URL列表（每行一个）
online_urls = 
    https://live.zbds.org/tv/iptv4.m3u
    https://raw.githubusercontent.com/YueChan/Live/main/APTV.m3u
```

### 频道规则配置 (`config/channel_rules.yml`)
```yaml
categories:
  - name: "央视频道"
    priority: 1
    keywords: ["CCTV", "央视", "中央"]
  - name: "卫视频道"  
    priority: 10
    keywords: ["卫视"]
```

### 过滤规则配置
```ini
[Filter]
max_latency = 5000        # 最大延迟(ms)
min_bitrate = 100         # 最小比特率(kbps)  
min_resolution = 720p     # 最低分辨率
max_resolution = 4k       # 最高分辨率
```

---

## 📊 使用指南

### 访问播放列表
服务启动后，通过以下地址访问播放列表：

| 文件类型 | 访问地址 | 说明 |
|---------|---------|------|
| 主播放列表 | `http://你的IP:12345/live.m3u` | 包含所有有效源 |
| 精选播放列表 | `http://你的IP:12345/qualified_live.m3u` | 仅包含合格源 |
| 文本格式 | `http://你的IP:12345/live.txt` | 兼容简单播放器 |
| 健康检查 | `http://你的IP:12345/health` | 服务状态检查 |

### 在播放器中使用
**VLC Media Player:**
1. 打开VLC → 媒体 → 打开网络串流
2. 输入: `http://你的IP:12345/live.m3u`

**Kodi:**
1. 安装PVR IPTV Simple Client插件
2. 设置M3U播放列表URL
3. 输入上述地址

**智能电视/手机APP:**
- 支持M3U格式的任何IPTV播放器
- 直接输入播放列表URL即可
- 强烈建议使用https://github.com/yaoxieyoulei/mytv-android 这个软件仓库最新的安卓app（需要一定设置，选择经典界面，导入在线地址，如http://81.68.248.64:12345/live.m3u）

---

## 🔄 定时任务

系统内置定时更新功能，默认配置为每天凌晨2点自动更新：

```bash
# 修改更新频率（cron表达式）
-e UPDATE_CRON="0 */6 * * *"  # 每6小时更新一次

# 手动立即更新
docker exec livesourcemanager python /app/main.py
```

### 自定义更新策略
```ini
[Testing]
cache_ttl = 120          # 缓存有效期(分钟)
concurrent_threads = 30  # 并发测试线程数
enable_speed_test = True # 启用速度测试
```

---

## 📈 监控与日志

### 查看实时日志
```bash
# 查看容器日志
docker logs -f livesourcemanager

# 查看应用日志
tail -f logs/app.log

# 查看定时任务日志  
tail -f logs/cron.log
```

### 健康状态检查
```bash
# 检查服务状态
curl http://localhost:12345/health

# 检查Nginx状态
docker exec livesourcemanager nginx -t

# 检查文件生成情况
ls -la output/
```

### 统计信息示例
```
[2024-01-20 10:30:45] 测试完成: 2856 个有效源, 1924 个合格源
[2024-01-20 10:30:45] 合格率: 67.4%
[2024-01-20 10:30:45] 文件统计:
[2024-01-20 10:30:45]   live.m3u: 2856 个频道, 1.2MB
[2024-01-20 10:30:45]   qualified_live.m3u: 1924 个频道, 856KB
```

---

## 🐛 故障排除

### 常见问题解决

**Q: 容器启动失败**
```bash
# 检查端口占用
netstat -tulpn | grep 12345

# 重新构建镜像
docker system prune -a
./dockrun.sh
```

**Q: 直播源测试大量失败**
```bash
# 检查网络连接
docker exec livesourcemanager ping 8.8.8.8

# 调整代理设置
修改 config/config.ini 中的代理配置
```

**Q: 播放列表无法访问**
```bash
# 检查Nginx服务
docker exec livesourcemanager nginx -t

# 检查文件权限
docker exec livesourcemanager ls -la /www/output/
```

### 性能优化建议

1. **增加并发数** - 对于高性能服务器，可增加 `CONCURRENT_THREADS`
2. **调整超时时间** - 网络环境差时适当增加 `TEST_TIMEOUT`  
3. **启用代理** - 国内环境建议配置代理访问GitHub源
4. **定期清理** - 每月清理一次Docker缓存和日志文件

---


## 🙏 致谢

感谢以下开源项目提供的灵感和技术支持：
- [FFmpeg](https://ffmpeg.org/) - 流媒体测试核心
- [aiohttp](https://github.com/aio-libs/aiohttp) - 高性能异步HTTP客户端
- [Nginx](https://nginx.org/) - 高性能Web服务器

