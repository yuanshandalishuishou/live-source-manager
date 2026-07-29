# 直播源管理工具（live-source-manager）项目设计与编程思路完整指南

> **文档目的**：本文档基于 `E:\工作空间\live-source-manager` 源码的完整逆向分析，给出**足以让另一个 AI 按图索骥、几乎 1:1 复刻功能与代码**的设计说明 + 编程思路。
> 阅读对象：需要二次实现、接手维护、或做同类竞品（如纯 Go 版 `live-source-manager-go`）的工程师。
> 文档精度：类/函数签名级、配置默认值级、SQLite 建表级、API 端点级、部署脚本级。
> 技术栈：Python 3.13 + FastAPI + SQLite（配置/用户/审计唯一事实来源）+ 原生 Jinja2 + HTMX + 原生 JS；底层引擎为纯 Python 模块（`app/`）。

---

## 目录

1. [项目定位与技术栈](#1-项目定位与技术栈)
2. [目录结构与分层架构依赖图](#2-目录结构与分层架构依赖图)
3. [数据模型与配置体系](#3-数据模型与配置体系)
4. [后端引擎模块职责与关键函数](#4-后端引擎模块职责与关键函数)
5. [Web 管理端](#5-web-管理端)
6. [启动链路与自动建库](#6-启动链路与自动建库)
7. [部署三件套（Docker / Linux / Windows）](#7-部署三件套docker--linux--windows)
8. [关键红线（不可违反）](#8-关键红线不可违反)
9. [复刻验收清单](#9-复刻验收清单)

---

## 1. 项目定位与技术栈

### 1.1 它是做什么的

一个**直播源（IPTV / M3U）采集 → 解析 → 流媒体连通性/质量测试 → 规则化分类 → 生成可用播放列表（M3U/TXT）**的全流程管理工具，附带 Web 管理后台。

核心数据流：

```
多源采集（本地目录 / 在线 URL / GitHub 仓库）
   ↓
解析为统一结构 {name, url, source_path, ...}
   ↓
流测试（ffprobe/ffmpeg 并发）：延迟 / 分辨率 / 比特率 / 下载速度 / 广告检测 / 冻结退避
   ↓
分层筛选（valid → base → qualified）
   ↓
分类引擎（ChannelRules：6 维度 + 三层联合防御，人工覆盖写 channel_name_mapping）
   ↓
M3UGenerator 生成 base/qualified 两级播放列表 + 多分类展开
   ↓
文件发布服务（端口 12345）对外提供 /www/output/live.m3u
   ↓
Web 管理后台（端口 23456 FastAPI）查看/配置/触发测试/审计
```

### 1.2 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| 运行时 | Python 3.13+ | Dockerfile/start_docker.sh 强制 3.13 |
| Web 框架 | FastAPI + Uvicorn | 端口 23456（`HTTPServer.manager_port`） |
| 模板 | Jinja2 | `web/templates/*.html` |
| 前端交互 | HTMX + 原生 JS | `web/static/js/app.js` 暴露 `window.LSM` 组件库 |
| 配置/存储 | SQLite（单库 `web.db`） | 配置走 `app_config` 表，**无 INI 文件依赖** |
| 测试引擎 | ffprobe / ffmpeg 子进程 | 可选，缺失则测试功能降级 |
| 发布服务 | nginx（Docker）/ ThreadingHTTPServer（裸跑） | 端口 12345（`HTTPServer.fileshare_port`） |
| 部署 | Docker + docker-compose + systemd + Windows 任务计划 | 三平台脚本齐备 |
| 加密 | `cryptography.Fernet`（AES-128-CBC + HMAC-SHA256） | 敏感配置加密存储 |

### 1.3 两个端口（红线）

| 端口 | 角色 | 提供者 |
|---|---|---|
| **12345** | 文件发布（对外提供 M3U/TXT） | 裸跑：`web/core.py._start_fileshare_server`（后台线程）；Docker：nginx |
| **23456** | Web 管理后台（FastAPI） | `uvicorn web.webapp:app` |

> ⚠️ **同机端口红线**：Python 版与 Go 版（`live-source-manager-go`）默认都占 12345/23456。**同一台机器同一时间只能跑一个**，否则后者因端口绑定失败退出。

---

## 2. 目录结构与分层架构依赖图

### 2.1 顶层目录

```
live-source-manager/
├── app/                      # 后端引擎（纯 Python，无 Web 依赖）
│   ├── __init__.py           # 包入口，暴露 SourceManager/StreamTester/ChannelRules/Config/Logger
│   ├── config.py             # Config 类：读取 app_config 表，get_*_config()
│   ├── manager.py            # EnhancedLiveSourceManager：主流程编排（采集→测试→筛选→生成）
│   ├── source_manager.py     # SourceManager：多源下载 + parse_all_files()
│   ├── stream_tester.py      # StreamTester：ffprobe 并发测试 + 看门狗 + 冻结
│   ├── rules.py              # ChannelRules：DB 驱动分类引擎（6 维度 + 三层防御）
│   ├── security.py           # SSRF 窄门禁 is_static_safe() + 完整校验版
│   ├── m3u_generator.py      # M3UGenerator：生成 M3U/TXT + 多分类展开
│   ├── exceptions.py         # 异常体系 + ErrorStats + catch_exception 装饰器
│   ├── logger.py             # Logger.setup_logging()
│   └── utils.py              # atomic_write / safe_read_file / force_remove
├── web/                      # Web 管理端
│   ├── webapp.py             # 挂载 7 个 router + main() 端口探测
│   ├── core.py               # 共享基础设施（app 实例 / lifespan / 中间件 / Session / CSRF / RBAC / _render）
│   ├── models.py             # SQLite 建表 + 所有 DB 读写函数
│   ├── crypto_utils.py       # Fernet 加密 + 机器绑定加密
│   ├── routes/               # 7 个路由模块
│   │   ├── pages.py          # 页面渲染（GET）
│   │   ├── auth.py           # 登录/用户/密码/加密密钥
│   │   ├── config_api.py     # 配置读写
│   │   ├── dashboard.py      # 仪表盘统计
│   │   ├── sources.py        # 源文件/频道管理
│   │   ├── rules.py          # 分类规则/映射/字典
│   │   └── system.py         # 测试触发/日志/审计/WS
│   ├── templates/            # Jinja2 模板
│   ├── static/js/            # app.js / audit-components.js / lsm-components.js
│   └── static/css/           # app.css
├── config/
│   ├── config-defaults.yaml  # 配置默认值外部化（与 SECTION_SCHEMA/_DEFAULT_VALUES 同步）
│   ├── channel_rules.yml     # 频道分类规则（首次运行自动生成默认）
│   ├── online/               # 在线源下载落盘（gitignore，运行期生成）
│   └── sources/              # 本地源目录（gitignore，运行期生成）
├── deploy/
│   ├── live-source-web.service   # Linux systemd 模板
│   └── windows/                 # Windows 自启动脚本
├── tests/                    # pytest 测试套件（12+ 文件）
├── www/output/               # 生成产物（live.m3u / qualified_live.m3u / *.txt）
├── data/status/              # 状态文件（source_summary.json / auto_scan_state.json）
├── Dockerfile / docker-compose.yml / nginx.conf / start_docker.sh
└── requirements.txt
```

### 2.2 分层依赖（单向无环）

```
L0  异常/日志/工具:  app/exceptions.py  app/logger.py  app/utils.py
  ↑
L1  配置/安全:       app/config.py  app/security.py
  ↑
L2  业务引擎:        app/rules.py  app/source_manager.py  app/stream_tester.py
  ↑
L3  生成器:          app/m3u_generator.py
  ↑
L4  编排:            app/manager.py (EnhancedLiveSourceManager)

Web 依赖:
  web/core.py  →  app/*（仅 engine 层，不反向依赖 web）
  web/models.py →  SQLite（独立，被所有 web 路由调用）
  web/routes/*  →  core + models + crypto_utils + app.engine（路由之间互不依赖）
  web/webapp.py →  汇总挂载 routes
```

**铁律**：`web/` 可以 import `app/`，`app/` **绝不** import `web/`。配置读写唯一入口是 `app_config` 表（`web/core.py.read_config` → `web/models.py.get_all_config`）。

---

## 3. 数据模型与配置体系

### 3.1 SQLite 全表结构

数据库路径：`WEB_DATA_DIR` 环境变量（Docker 为 `/data/web.db`）或 `./data/web.db`。单库 `web.db`。

| 表名 | 用途 | 关键列 |
|---|---|---|
| `users` | 用户 | id, username UNIQUE, password_hash, role(`admin`/`viewer`), display_name, is_active, created_at, updated_at |
| `audit_logs` | 审计日志 | id, user_id FK, username, action, target, detail, ip_address, created_at + 索引 |
| `app_config` | **配置唯一事实来源** | key PK(`Section.key`), value, updated_at |
| `sessions` | 登录会话 | id PK, user_id FK CASCADE, username, role, created_at REAL, last_active REAL |
| `classification_dimensions` | 分类维度 | id, dim_key UNIQUE, dim_name, sort_order, is_active |
| `classification_rules` | 分类规则（含 content/region/…） | id, rule_type, name, keywords, priority, sort_order, is_active |
| `province_exclusion_map` | 省际排除映射 | id, province_keyword, excluded_keyword, note, UNIQUE |
| `stream_source_categories` | 频道维度分类（⚠️ FK 悬挂，见下） | id, source_id, dim_key, dim_value, is_manual, UNIQUE(source_id,dim_key) |
| `channel_name_mapping` | **频道全名权威分类（人工覆盖主表）** | channel_name PK, content, region, language, quality, media_type, genre, is_manual |
| `category_dictionary` | 分类受控词表（UI 下拉源） | id, dimension, value, label, sort_order, UNIQUE(dimension,value) |
| `github_download_cache` | GitHub 下载缓存 | repo_key, filename, file_size, downloaded_at, PK(repo_key,filename) |
| `password_change_required` | 首次登录强制改密 | username PK, required, created_at |
| `login_lockout` | 登录失败锁定 | username PK, attempts, lockout_until |

#### ⚠️ `stream_sources` 表不存在——这是有意为之

本项目**不创建** `stream_sources` 表。直播源数据**全部来自运行时解析 M3U/TXT 文件**（`SourceManager.parse_all_files()`），不持久化到 DB。

- `stream_source_categories.source_id` 定义上有 `REFERENCES stream_sources(id)`，但该表不存在 → FK 实质悬挂（SQLite 默认不强制 FK 检查）。运行时 `source_id` 存的是前端传来的 **MD5(name|url)[:12] 字符串**，而非 INT。
- **真正的频道分类权威存储是 `channel_name_mapping`**（主键 `channel_name`），人工覆盖以 `is_manual=1` 标记。
- 复刻要点：不要去建 `stream_sources` 表；分类读写以 `channel_name_mapping` 为主，`stream_source_categories` 仅作历史兼容冗余。

#### 关键建表 SQL（复刻参考）

```sql
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT DEFAULT 'viewer',
  display_name TEXT,
  is_active INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS app_config (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS channel_name_mapping (
  channel_name TEXT PRIMARY KEY,
  content TEXT, region TEXT, language TEXT, quality TEXT,
  media_type TEXT, genre TEXT,
  is_manual INTEGER DEFAULT 0,
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, username TEXT, action TEXT, target TEXT,
  detail TEXT, ip_address TEXT, created_at TEXT DEFAULT (datetime('now'))
);
-- 其余表结构见 web/models.py 全文（CREATE TABLE IF NOT EXISTS 语句）
```

### 3.2 配置体系与默认值清单

配置读取链：
1. `app/config.py` `Config._DEFAULT_VALUES`（种子，64 键）
2. `config/config-defaults.yaml`（外部化，运行时合并进 `SECTION_SCHEMA`）
3. `web/core.py.SECTION_SCHEMA`（UI 字段定义，61 键，含类型/默认/标签/帮助）
4. 运行时写入：`app_config` 表（SQLite 唯一事实来源）

> 维护同步规则：改默认值必须同时改 **`_DEFAULT_VALUES`**（种子）+ `SECTION_SCHEMA` + `config-defaults.yaml` 三处，否则 UI 会丢字段。

#### 完整默认值表

| Section | Key | 类型 | 默认值 |
|---|---|---|---|
| HTTPServer | enabled | bool | True |
| | host | str | 0.0.0.0 |
| | fileshare_port | int | **12345** |
| | manager_port | int | **23456** |
| | document_root | str | ./www/output |
| Output | filename | str | live.m3u |
| | group_by | str | category |
| | include_failed | bool | False |
| | max_sources_per_channel | int | 8 |
| | enable_filter | bool | False |
| | whitelist_force_keep | bool | False |
| Filter | max_latency | int | 4000 |
| | min_bitrate | int | 80 |
| | must_hd | bool | False |
| | must_4k | bool | False |
| | min_speed | int | 50 |
| | min_resolution | str | 360p |
| | max_resolution | str | 4k |
| | resolution_filter_mode | str | range |
| Testing | timeout | int | 10 |
| | concurrent_threads | int | 40 |
| | max_concurrent_ffprobe | int | 16 |
| | cache_ttl | int | 120 |
| | enable_speed_test | bool | True |
| | speed_test_duration | int | 6 |
| | auto_scan_enabled | bool | False |
| | auto_scan_mode | str | interval |
| | auto_scan_interval_hours | int | 24 |
| | auto_scan_daily_time | str | 03:00 |
| | enable_host_speed_share | bool | True |
| | enable_source_freeze | bool | True |
| | freeze_fail_threshold | int | 3 |
| | freeze_base_seconds | int | 60 |
| | freeze_max_hours | int | 24 |
| | enable_ad_detect | bool | True |
| | ad_keywords | str | no_signal,/ad/,advertisement,测试卡,无信号,test_pattern,colorbar,broadcast_test,signal_lost |
| | ad_max_duration | int | 90 |
| | global_blacklist | str | （空） |
| | global_whitelist | str | （空） |
| | output_sort_by | str | speed |
| | max_test_attempts | int | 1 |
| Network | proxy_enabled | bool | False |
| | proxy_type | str | socks5 |
| | proxy_host | str | 192.168.1.46 |
| | proxy_port | int | 1800 |
| | proxy_username | str | （空，敏感） |
| | proxy_password | str | （空，敏感） |
| | github_mirror | str | https://ghproxy.com/ |
| | ipv6_enabled | bool | True |
| Sources | local_dirs | str | ./config/sources |
| | online_urls | textarea | 20 个公开 IPTV 源 URL（见 SECTION_SCHEMA） |
| | github_sources | textarea | 9 个 owner/repo |
| GitHub | api_url | str | https://api.github.com |
| | api_token | str | （空，敏感，机器绑定加密） |
| | rate_limit | int | 5000 |
| UserAgents | ua_enabled | bool | False |
| | ua_position | str | extinf |
| Logging | level | str | INFO |
| | file | str | ./log/app.log |
| | max_size | int | 10 |
| | backup_count | int | 5 |

---

## 4. 后端引擎模块职责与关键函数

### 4.1 `app/config.py` — Config

```python
class Config:
    # 种子默认值 _DEFAULT_VALUES: dict[str, Any]  # 约 64 键，与 SECTION_SCHEMA/yaml 同步
    def __init__(self): ...
    def get_logging_config(self) -> dict          # level/file/max_size/backup_count
    def get_network_config(self) -> dict          # proxy_*/github_mirror/ipv6_enabled
    def get_github_config(self) -> dict           # api_url/api_token/rate_limit
    def get_testing_params(self) -> dict          # timeout/concurrent_threads/...（见上表）
    def get_filter_params(self) -> dict           # max_latency/min_bitrate/...
    def get_output_params(self) -> dict           # filename/group_by/max_sources_per_channel/...
    def get_http_server_config(self) -> dict      # enabled/host/fileshare_port/manager_port/document_root
    def get_ua_position(self) -> str              # 'extinf' | 'header'
    def get_user_agents(self) -> dict
    def get_source_file_ua_settings(self) -> dict
    def get_channel_ua_overrides(self) -> dict
    def get_sources(self) -> dict                 # local_dirs(→list)/online_urls(→list)/github_sources(→list)
```

> 注意：`get_sources()` 把逗号/换行分隔字符串拆成列表。`local_dirs` 支持多个目录；`online_dir` 从 `local_dirs` 同级 `online/` 派生（不硬编码路径，跨平台用 `./` 相对路径）。

### 4.2 `app/logger.py` — Logger

```python
UNIFIED_LOG_FORMAT = '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s'

class Logger:
    @staticmethod
    def setup_logging(config) -> 'logging.Logger':
        # RotatingFileHandler(maxBytes=max_size*1024*1024, backupCount)
        # console handler；clear_on_startup 可选
```

### 4.3 `app/utils.py` — 原子写入与文件安全

```python
def atomic_write(filepath, content, encoding='utf-8', retries=3, ...) -> None:
    # 建目录 → 备份 → _do_atomic_write(tempfile.mkstemp(suffix='.tmp', prefix='.atomic_') → 写 → fsync → os.replace)
    # _verify_write 字节数比对

def safe_read_file(path) -> str:
    # utf-8/gbk/gb2312/latin1/utf-8-sig 回退，去 BOM

def force_remove(path) -> None:
    # Windows 调 ctypes.windll.kernel32.DeleteFileW 绕过回收站 monkeypatch（沙箱兼容）
```

### 4.4 `app/exceptions.py` — 异常体系

```
BaseAppException(error_code, message, suggestion, details, original)
├── LsmError
├── ConfigError(1001)
├── SourceError(3001)
│   ├── SourceDownloadError(3002)
│   └── SourceParseError(3003)
├── StreamTestError(4001)
├── FileException(5002)
└── OutputError(5001)
```

- `ErrorStats`：窗口统计单例 `global_error_stats`。
- `catch_exception(logger, module_name, raise_original, fallback_return, capture_stats)`：sync + async 双实现装饰器，捕获异常转结构化结果。
- `setup_global_exception_hook()`：全局兜底。

### 4.5 `app/security.py` — SSRF 窄门禁（**红线模块**）

**核心函数 `is_static_safe(url) -> tuple[bool, str, str]`（只做解析阶段的窄门禁）：**

```python
ALLOWED_SCHEMES = {'http', 'https', 'rtmp', 'rtsp', 'rtp'}
BLOCKED_SCHEMES = {'file', 'data', 'javascript', ...}

def is_static_safe(url) -> tuple[bool, str, str]:
    # 仅校验：
    #  1) scheme ∈ ALLOWED_SCHEMES
    #  2) host 格式合法
    #  3) 非内网/回环 IP（_is_private_ip / localhost / .local / .internal）
    # 故意【不做】：DNS 解析 / XSS 检测 / 命令注入 / 路径遍历 / 境外流媒体拦截 / 域名黑白名单
    # 返回 (safe, reason, category)  category ∈ {'ok','scheme','host','ssrf'}
```

**为什么窄门禁**：解析阶段若做 DNS/境外拦截，会把「服务器不可达但用户本地可看」的源静默丢弃。真正的可达性判定交给 `StreamTester`（发起真实连接测试）。

**完整版校验（非解析阶段用）**：`validate_url` / `sanitize_url` / `is_safe_url` 含 DNS 解析（`ThreadPoolExecutor(max_workers=4)` 5s 超时）、境外域名拦截、黑名单。

辅助常量：
- `KNOWN_OVERSEAS_STREAMING_DOMAINS`：youtube/netflix/hbomax/disneyplus/primevideo/twitch…
- `XSS_PATTERNS`：谨慎匹配真 HTML 事件处理器（避免误伤含 `on` 的 query 参数）
- `CMD_INJECTION_PATTERNS`：仅拦 `${}` / 反引号 / `$( )` / 管道接命令
- `PATH_TRAVERSAL_PATTERNS`

### 4.6 `app/source_manager.py` — SourceManager

```python
class SourceManager:
    def __init__(self, config: Config, logger, rules: ChannelRules): ...
    def parse_all_files(self) -> list[dict]:
        # 遍历 local_dirs + online_dir，递归读 .m3u/.m3u8/.txt
        # 每条 → {name, url, source_path, ...}
    # 多源下载：
    def download_local(...)   # 本地目录
    def download_online(...)  # 在线 URL（共享 aiohttp session）
    def download_github(...)  # GitHub 仓库发现：raw/api/proxy/mirror，SOCKS5 代理
```

> **GitHub 下载方式**：`raw` / `api` / `proxy`（github_mirror）/ `mirror`。`github_download_cache` 表缓存，避免重复下载。直连不通时走 `socks5://127.0.0.1:10808`。

### 4.7 `app/stream_tester.py` — StreamTester（测试引擎核心）

```python
class StreamTester:
    _ffprobe_verified = None     # 类级缓存 ffprobe 可用性（避免每次实例化跑子进程）
    _ffprobe_path = None
    _ffmpeg_path = None

    @classmethod
    def _find_executable(cls, name) -> str | None:
        # 搜索顺序：tools/ffmpeg/ 目录 → 系统 PATH

    def __init__(self, config: Config, logger):
        # 读取 testing_params；初始化 Semaphore(max_concurrent_ffprobe)
        # _ffprobe_semaphore / _active_futures / _watchdog_timer

    def test_all_sources(self, sources) -> list[dict]:
        # 1) cleanup_cache()
        # 2) _start_watchdog()（超时强制取消所有 future + 释放 Semaphore）
        # 3) max_workers = _calculate_optimal_workers()
        #    若 max_workers > ffprobe_max*2 → 调整为 min(max_workers, ffprobe_max*1.5)
        # 4) ThreadPoolExecutor(max_workers) 提交 test_single_stream
        # 5) as_completed：future.result(timeout=timeout+15)
        #    - success → check_if_qualified()
        #    - frozen → 计入失败但不刷 error 日志（预期冷却）
        #    - TimeoutError → status='timeout'
        # 6) 统计 成功/合格/失败 百分比
        # 7) 若启用冻结 → _save_frozen_map()（跨进程持久化）
        # 8) _stop_watchdog()

    def _calculate_optimal_workers(self) -> int:
        # 基于 concurrent_threads 配置与 cpu_count() 动态计算

    def test_single_stream(self, source) -> dict:
        # 调用 test_stream_url()（ffprobe/ffmpeg 子进程）
        # 提取元数据 → check_if_qualified() → 返回 {status, response_time, resolution, bitrate, ...}

    def test_stream_url(self, url, user_agent=None, ...) -> dict:
        # 实际子进程调用；捕获 stderr 交给 _classify_stream_error()

    def extract_metadata(self, data) -> dict
    def _extract_video_stream_info(self, stream) -> dict
    def _extract_audio_stream_info(self, stream) -> dict
    def _determine_media_type(self, metadata) -> str   # 'radio'/'audio'/'tv'...

    def test_download_speed(self, url, user_agent=None) -> float  # KB/s

    def check_if_qualified(self, result) -> bool:
        # 白名单强制保留 → 音频仅查延迟 → 视频查 延迟/分辨率(range/min_only/max_only)/比特率/must_hd/must_4k/速度

    def is_resolution_meet_min(self, resolution, min_resolution) -> bool
    def is_resolution_meet_max(self, resolution, max_resolution) -> bool

    # 同 Host 测速复用（enable_host_speed_share）
    def _get_host_cached_result(self, host) -> dict | None
    def _cache_host_result(self, host, result)

    # 冻结退避（enable_source_freeze）
    def _load_frozen_map(self) / _check_frozen(url_norm) -> float | None
    def _record_failure(self, url_norm) / _record_success(self, url_norm)
    # 冻结时长 = 2^失败次数 × freeze_base_seconds，封顶 freeze_max_hours

    def _detect_ad_playlist(self, url, user_agent, metadata) -> bool
    # 含 #EXT-X-ENDLIST 且累计时长 ≤ ad_max_duration → 判循环占位；命中 ad_keywords → 广告

    def _start_watchdog(self) / _watchdog_timeout_handler(self) / _stop_watchdog(self)
    # 看门狗超时：cancel() 所有活跃 future + release() Semaphore（防挂起）
```

**错误分类 `_classify_stream_error(msg) -> str`**：

| 返回类别 | 触发特征 |
|---|---|
| `connection_refused` | connection refused / error -111 |
| `connection_failed` | timed out / network unreachable / no route / host is down / could not connect |
| `dns_failed` | name or service not known / could not resolve / getaddrinfo |
| `auth_blocked` | 403/401/forbidden/unauthorized/expired/txSecret/txtime |
| `not_found` | 404 / not found / no such |
| `ffprobe_failed_no_output` | 空输出 |
| `ffprobe_error` | 兜底 |

### 4.8 `app/rules.py` — ChannelRules（分类引擎）

```python
DIMENSIONS = ['content', 'region', 'language', 'quality', 'media_type', 'genre']

class ChannelRules:
    def __init__(self, rules_path='/config/channel_rules.yml'):
        # 优先从 DB 加载（classification_rules / classification_dimensions / province_exclusion_map）
        # YAML 作为 fallback；_seed_category_dictionary() 导入 category_dictionary

    def determine_categories(self, channel_name) -> dict[str, str]:
        # 返回 6 维度最佳分类
        # ① 优先查 channel_name_mapping（人工修正权威）→ 直接返回
        # ② 否则按维度 _match_dimension() 匹配
        # ③ content 维度额外走 _apply_defense_layers()（三层联合防御）

    def determine_category(self, channel_name) -> str:
        # 向后兼容，返回 content 维度

    def _apply_defense_layers(self, channel_name, channel_upper, matches) -> str:
        # 三层联合防御（仅 content 维度）：
        #  第一层：高优先级（priority<=5，如 CCTV/港澳台）不允许被排除 → 直接返回
        #  第二层：负向排除（命中负向排词且唯一匹配 → 其他频道）
        #  第三层：普通优先级 + 最长匹配 + 省际排除映射
        #    - 长短词覆盖：长关键词（如"湖南卫视"）覆盖短词（如"湖南"）
        #    - 省际排除：province_exclusion_map 决定 best 是否让位 other

    def _is_excluded(self, candidate_kw, other_kw, best_name, other_name) -> bool
    def test_classification(self, test_cases) -> None
```

**DB 驱动函数（`app/rules.py` 顶层）**：
- `get_active_classification_rules_for_app()` → 读 `classification_rules`
- `check_exclusion_for_app(kw1, kw2)` → 读 `province_exclusion_map`
- `get_channel_name_mapping_for_app(name)` / `save_source_categories_for_app(source_id, cats)` / `get_source_categories_for_app(source_id)`
- `get_all_exclusions_for_app()`

### 4.9 `app/m3u_generator.py` — M3UGenerator

```python
class M3UGenerator:
    def __init__(self, config, logger):
        # 读 output_params / filter_params / ua_position / ua_enabled / whitelist_force_keep

    def generate_enhanced_m3u(self, sources, level='base') -> str:
        # base 级：用全部源；qualified 级：先 enhanced_filter_sources()
        # 多分类展开：content 含逗号 → 复制多个频道条目
        # 每条按 get_source_categories_for_app(source_id) 读 DB 人工修正覆盖
        # 构建 EXTINF + URL（UA 按 ua_position 注入 |User-Agent= 或 #User-Agent=）

    def enhanced_filter_sources(self, sources) -> list:
        # 白名单强制保留 → status=success → 音频仅查延迟 → 视频查延迟/分辨率/比特率/速度

    def enhanced_group_and_sort_sources(self, sources) -> list:
        # 先按 media_type 预分组（radio→收音机 / audio→在线音频）
        # 再按 group_by 分组；sort_by ∈ {speed, name, resolution}

    def build_enhanced_extinf(self, source, categories) -> str:
        # #EXTINF:-1 tvg-id= tvg-name= tvg-logo= group-title= media-type=
        #   tvg-country/region/province= user-agent= resolution= bitrate=
        #   response-time=/download-speed=(qualified 级)
```

生成产物（默认 `live.m3u`）：
- `www/output/live.m3u` — base 级（全部源）
- `www/output/qualified_live.m3u` — qualified 级（过滤后）
- 同目录 `.txt` 列表与多分类展开文件

### 4.10 `app/manager.py` — EnhancedLiveSourceManager（主流程编排）

```python
class EnhancedLiveSourceManager:
    def run(self):
        # 1) SourceManager 采集 + 解析（parse_all_files）
        # 2) StreamTester.test_all_sources() 并发测试
        # 3) hierarchical_filtering(sources) → valid → base → qualified
        # 4) ChannelRules 分类（determine_categories）
        # 5) M3UGenerator 生成 base/qualified M3U + TXT
        # 6) 写入 www/output + data/status/source_summary.json

    def hierarchical_filtering(self, sources) -> dict:
        # valid：测试 status=success
        # base：valid 全部
        # qualified：通过 enhanced_filter_sources 质量阈值
```

---

## 5. Web 管理端

### 5.1 `web/core.py` — 共享基础设施

**职责**：app 实例、lifespan、中间件、异常处理器、静态/模板、配置代理（纯 SQLite）、Session/CSRF/RBAC、WebSocket 连接管理、认证辅助、`_render`/`_get_source_summary`/`_get_system_info`、SourceManager 懒加载缓存。

**关键对象**：
- `SECTION_SCHEMA`：UI 字段定义（见 §3.2），含 YAML 覆盖逻辑（`_load_defaults_from_yaml`）。
- `ConnectionManager`（WebSocket）：`MAX_CONNECTIONS=50`，`connect/disconnect/broadcast/count`。
- `_write_lock`：配置写入全局锁。
- `PROJECT_ROOT`：`web/` 的父目录。
- `_get_csrf_exempt_paths()`：运行时读取 `webapp.CSRF_EXEMPT_PATHS`（conftest 可覆写）。

**Session / CSRF / RBAC**：
```python
_auth_sessions: dict[str, dict]              # session_id → data（内存 + SQLite 双写）
SESSION_TTL = 24*3600; IDLE_TIMEOUT = 2*3600; CSRF_TTL = 1*3600
CSRF_EXEMPT_PATHS = frozenset({'/api/auth/login', '/login'})

def create_session(user) -> str               # uuid4 + 写 sessions 表
def get_session(session_id) -> dict | None    # 内存优先，SQLite 回退
def destroy_session(session_id)
async def get_current_user(request) -> dict   # 依赖注入，读 Cookie 'session'
async def require_admin(current_user)         # 依赖注入，role=='admin' 否则 403

def _get_csrf_token(session_id, user_agent) -> str   # 1h 复用，绑定 UA
def verify_csrf_token(session_id, token, user_agent) -> bool  # hmac.compare_digest
```

**登录失败锁定**（依据《网络安全法》第24条）：
```python
LOGIN_LOCKOUT_MAX_ATTEMPTS = 5
check_login_lockout(username) -> (bool, int)   # 委托 models
record_login_failure(username)
reset_login_lockout(username)
```

**`_render(request, template, **kwargs)`**：
- 注入 `user`（username/role/user_id）、`csrf_token`（**内联到页面** `<script>window.__csrf_token=...</script>`，D-8 修复：不再依赖异步 fetch 时序）。
- `templates.TemplateResponse(request=..., name=..., context={request, user, csrf_token, **kwargs})`。

**SourceManager 懒加载与缓存**（高并发关键）：
```python
_sm_instance = None                            # SourceManager 单例
_parse_cache / _parse_cache_fingerprint        # 基于源文件集合指纹（路径|size|mtime 哈希）
_file_channel_counts                           # 文件路径→频道数 预构建映射

def _compute_source_fingerprint(sm) -> str     # 任一文件增删都改变指纹 → 缓存正确失效
def parse_all_files_cached(sm) -> list         # 指纹未变→秒回；变化→to_thread 重解析；
                                               #   并发刷新期间 stale-while-revalidate 返回旧缓存
def invalidate_parse_cache()                    # 源变更时调用
def get_file_channel_counts() -> dict
def _load_source_manager() -> SourceManager | None
def get_source_by_id(source_id) -> dict | None # MD5(name|url)[:12] 定位
def reset_source_manager_cache()               # 配置变更后调用
```

### 5.2 启动链路（lifespan）

```python
@app.on_event 等效 → @asynccontextmanager lifespan(app):
    # startup:
    # 1) 校验 WEB_ADMIN_PASSWORD 复杂度（GB/T 39786-2021：≥8 位 + 3 类字符；不合规直接 RuntimeError 拒绝启动）
    # 2) models.init_db(admin_password) → 自动建库 + 自动生成强密码（首次部署零配置）
    # 3) cleanup_expired_sessions / cleanup_audit_logs(max_days=180)
    # 4) set_password_change_required('admin', True)（首次登录强制改密）
    # 5) init_login_lockout_table()
    # 6) crypto_utils.ensure_key_initialized()（加密密钥就绪，必须在读密前）
    # 7) 若 app_config 空 → seed_app_config_defaults()；始终 fill_missing_app_config_defaults()（幂等补全新键）
    # 8) 若无 channel_rules.yml → 写默认规则
    # 9) 后台任务：_periodic_cleanup（每 24h 清 session/审计）、_auto_scan_scheduler（按配置定时触发测试）、_prewarm_parse_cache（预热解析缓存）
    # 10) _start_fileshare_server()（裸跑 12345 文件发布）
    # yield 进入运行
    # shutdown: _stop_fileshare_server() + cancel 各后台任务
```

### 5.3 `web/crypto_utils.py` — 加密

```python
# Fernet = AES-128-CBC + HMAC-SHA256
# 密钥派生：PBKDF2HMAC(SHA256, 600000 iters, salt=_BUILTIN_SALT=b'liv3_s0urc3_m4n4g')

SENSITIVE_KEYS = {Network.proxy_username, Network.proxy_password, GitHub.api_token}
MACHINE_BOUND_KEYS = {GitHub.api_token}        # 机器绑定加密，'MENC:' 前缀

def ensure_key_initialized()                   # 双重检查锁，线程安全
def is_custom_key() -> bool
def get_machine_id() -> str                     # Win 注册表 MachineGuid / Linux /etc/machine-id / macOS
def encrypt_machine_bound(val) / decrypt_machine_bound(val)   # 'MENC:'
def encrypt_value(val) / decrypt_value(val)     # 'ENC:'
def re_encrypt_all(new_key)                     # 两阶段提交原子重加密
```

**密钥来源优先级**：环境变量 `CONFIG_ENCRYPT_KEY` > SQLite `System.encrypt_key` > **自动生成**（首次运行打印醒目边框警告，建议设自定义 env）。

### 5.4 路由端点全清单

挂载（`web/webapp.py`）：`pages / auth / dashboard / sources / config / rules / system` 共 7 个 router。

> 通用约定：所有写操作（POST/PUT/DELETE/PATCH）必须带 `X-CSRF-Token` 头（CSRF 中间件校验，豁免 `/api/auth/login`、`/login`、WS）。页面请求 401 → 重定向 `/login`。source_id 用 `MD5(name|url)[:12]`。

#### 页面（pages.py，均为 GET + `_render`）
| 路由 | 模板 |
|---|---|
| `/login` | login.html |
| `/` | dashboard.html |
| `/sources` | sources.html |
| `/sources/add` | source_form.html |
| `/sources/{id}/edit` | source_form.html |
| `/config` | config.html |
| `/test` | livetest.html |
| `/logs` | logs.html |
| `/users` | users.html |
| `/rules` | rules.html |

#### 认证（auth.py）
| 方法 | 路由 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 登录（bcrypt 校验，写 session cookie，审计） |
| POST | `/api/auth/logout` | 登出 |
| GET | `/api/auth/me` | 当前用户 |
| GET | `/api/auth/csrf-token` | 取 CSRF token |
| PUT | `/api/auth/password` | 改密码 |
| PUT | `/api/auth/encrypt-key` | 重设加密密钥 |
| GET | `/api/auth/encrypt-key-status` | 密钥状态（手动/自动） |
| GET/POST | `/api/users` | 用户列表/创建 |
| PUT/DELETE/PATCH | `/api/users/{user_id}` | 改/删/局部改用户 |
| PUT | `/api/users/{user_id}/password` | 改指定用户密码 |

#### 配置（config_api.py）
| 方法 | 路由 |
|---|---|
| GET | `/api/config` |
| GET | `/api/config/fields` |
| GET | `/api/config/history` |
| GET | `/api/config/{section}` |
| PUT | `/api/config` |
| PUT | `/api/config/{section}` |
| POST | `/api/config/validate` |
| POST | `/api/config/reload` |

写后 `add_audit_log`；经 `validate_and_coerce` + `_validate_config_values` 结构化校验；写入 `app_config` 表；调用 `reset_source_manager_cache()`。

#### 仪表盘（dashboard.py）
| 方法 | 路由 | 说明 |
|---|---|---|
| GET | `/api/dashboard/stats` | 总览统计 |
| GET | `/api/dashboard/test-info` | 测试信息 |
| GET | `/api/dashboard/channel-stats` | 频道统计（`asyncio.to_thread(parse_all_files_cached, sm)`，兜底读 live.m3u 的 `#EXTGRP:`/`group-title=`） |
| GET | `/api/dashboard/status` | 状态 |
| GET | `/api/dashboard/system` | 系统信息（`_get_system_info`：psutil 或 /proc 兜底） |

#### 源管理（sources.py）
| 方法 | 路由 | 说明 |
|---|---|---|
| GET/POST/PUT/DELETE | `/api/sources` | 源 CRUD |
| GET/POST/DELETE/PUT | `/api/source-files` | 源文件管理 |
| GET | `/api/source-files/{id}/channels` | 文件内频道列表 |
| PUT/DELETE | `/api/source-files/{id}/ua` | 文件级 UA |
| PUT/DELETE | `/api/source-files/{id}/channel-ua` | 频道级 UA 覆盖 |
| POST | `/api/sources/collect` | 触发采集 |
| GET | `/api/sources/{id}/categories` | 频道多维分类 |
| PUT | `/api/sources/{id}/categories/{dim_key}` | 人工修正单维度 |

#### 分类规则（rules.py）
| 方法 | 路由 |
|---|---|
| GET/POST/PUT/DELETE | `/api/rules`（含 batch-order） |
| GET/POST/DELETE | `/api/rules/dimensions` |
| GET/POST/DELETE | `/api/rules/exclusions` |
| POST | `/api/rules/test-classification` |
| POST | `/api/rules/reimport` |
| POST | `/api/rules/reset-defaults` |
| GET/PUT/DELETE | `/api/channel-mapping/{name}` |
| GET/batch-import | `/api/channel-mappings` |
| reset-defaults/GET/POST/DELETE/PUT | `/api/category-dictionary` |

#### 系统（system.py）
| 方法 | 路由 | 说明 |
|---|---|---|
| POST | `/api/github/test-token` | 测 GitHub token |
| GET | `/api/test/status` | 测试状态 |
| POST | `/api/test/trigger` | 触发测试（真跑 StreamTester，默认 300，可选 300/500/1000/all） |
| POST | `/api/test/pause` / `resume` / `cancel` | 暂停/恢复/取消 |
| WS | `/ws/test` | 实时进度（WebSocket + 轮询兜底） |
| GET | `/api/logs` | 日志 |
| GET | `/api/logs/download` | 下载日志 |
| GET | `/api/audit` | 审计日志 |
| GET | `/api/audit/actions` | 审计动作枚举 |

#### 健康检查（core.py）
| 方法 | 路由 | 说明 |
|---|---|---|
| GET | `/api/health` | 探活（无需认证） |

### 5.5 前端页面与交互

- **模板**：`web/templates/`（base/config/dashboard/login/logs/livetest/rules/source_form/sources/users/audit.html）。`base.html` 注入 `csrf_token` 与导航。
- **`web/static/js/app.js`**（601 行，IIFE 暴露 `window.LSM`）：
  - HTMX 401 → 重定向 `/login`
  - `formatSourceList` / `formatUserList`：JSON → 表格（分页 + 管理员操作按钮）
  - `Toast` / `Modal` / `Tabs` / `Toggle` 组件
  - **CSRF Token 自动注入**所有 HTMX 写请求头 `X-CSRF-Token`
  - `escapeHtml` / `escapeJs` 防 XSS
  - `showSourceCategories` 弹窗：调 `/api/sources/{id}/categories` + PUT `/api/channel-mapping/{name}`
- **`lsm-components.js` / `audit-components.js`**：分类/审计专用组件。
- **`app.css`**：基础样式（可叠加 premium 风，但本项目偏实用）。

---

## 6. 启动链路与自动建库

入口：`web/webapp.py.main()`
```python
def check_port(host, port) -> bool:   # socket bind 探测
def main():
    host = os.environ.get('WEB_HOST', '0.0.0.0')
    port = int(os.environ.get('WEB_PORT', 23456))
    if not check_port(host, port):
        sys.exit(1)                    # 端口占用直接退出（不抢占）
    uvicorn.run(app, host=host, port=port)
```

`app` 实例在 `web/core.py` 定义，挂载 lifespan（见 §5.2）→ 自动建库（`models.init_db` 幂等）→ 启动文件发布服务 → 启动后台任务。

**自动建库幂等**：
- `has_app_config_data()` 为空 → `seed_app_config_defaults()`（约 61 键）
- 始终 `fill_missing_app_config_defaults()`（补全 schema 新增键，不覆盖已有值）
- `init_db(admin_password)`：首次生成强随机 admin 密码并写日志（`ADMIN_PASSWORD_INITIALIZED=...`），后续仅校验。
- 输出文件（`Output.output_dir/Output.filename`）默认加入 `local_dirs`（`init_db` 幂等 ensure：过滤空串 + 去重，仅值变化才写回）。

---

## 7. 部署三件套（Docker / Linux / Windows）

### 7.1 Docker（Dockerfile + docker-compose + nginx.conf）

**Dockerfile**（多阶段）：
- builder：`curl`/`ca-certificates`/`xz-utils`
- runtime：`nginx`/`cron`/`tzdata`/`ffmpeg`（可选，BtbN 或 johnvansickle，失败仅告警）
- venv 建在 `/app/.venv`，用清华 pip 源
- COPY `app/ config/channel_rules.yml web/ start_docker.sh nginx.conf healthcheck.sh`
- 软链 `/config/channel_rules.yml`
- `HEALTHCHECK` → `/healthcheck.sh`
- `EXPOSE 12345 23456`；`CMD ["/start_docker.sh"]`

**docker-compose.yml**：
- service `live-source-manager`
- ports：`${NGINX_PORT:-12345}` / `${WEB_PORT:-23456}`
- volumes：`config` / `logs` / `output`/`data` / `sources`
- env：`TZ` / `NGINX_PORT` / `WEB_PORT` / `TEST_TIMEOUT` / `CONCURRENT_THREADS` / `OUTPUT_FILENAME` / `UPDATE_CRON` / `WEB_ADMIN_PASSWORD` / `CONFIG_ENCRYPT_KEY`

**nginx.conf**：
- `listen ${NGINX_PORT}`；`root /www/output`
- 安全头：`X-Frame-Options` / `X-Content-Type-Options` / HSTS / `CSP default-src 'self'; script-src 'none'`
- `.m3u/.m3u8/.txt` CORS `*`
- `/health` 返回 200；`/status` stub_status 限内网
- `limit_req_zone` api 10m rate=10r/s

### 7.2 `start_docker.sh` 编排（核心入口）

Docker 容器 CMD，也供 `setup_linux.sh` source 复用（有守卫不重复 main）：

1. **环境检测 + 自动安装**：`detect_os` / `detect_package_manager` / `check_python`(强制 3.13) / `install_python` / `check_pip` / `check_venv` / `create_venv` / `check_python_deps` / `install_python_deps`（清华镜像）/ `check_ffmpeg` / `install_nginx`
2. **环境变量**：`UPDATE_CRON=0 2 * * *` / `TEST_TIMEOUT=10` / `CONCURRENT_THREADS=50` / `OUTPUT_FILENAME=live.m3u` / `NGINX_PORT=12345` / `WEB_PORT=23456` / `WEB_DATA_DIR=/data`
3. **`init_sqlite_db()`**：校验表完整性（`users/audit_logs/app_config/sessions`）；缺失则备份旧库 + 重建；`init_db(admin_pw)`；捕获自动生成密码醒目提示；把环境变量写入 `app_config`
4. **`setup_config_files()`**：复用 `/config/channel_rules.yml`；复制 nginx.conf 到 `/etc/nginx/`
5. **`setup_file_permissions()`**：DB 权限 `chmod 600`（P0 安全）
6. **`setup_nginx()`**：`envsubst` 注入 `NGINX_PORT`；`nginx -t` 校验；写 `/www/output/health`；`nginx -g "daemon off;" &`
7. **启动 Web**：`cd /app && PYTHONPATH=/app /app/.venv/bin/python -m uvicorn web.webapp:app --host 0.0.0.0 --port ${WEB_PORT} &`，PID 写 `/var/run/web.pid`
8. **`setup_cron_jobs()`**：`/etc/cron.d/live-source-cron` 定时跑 `python -m app`
9. **`start_main_program()`**：启动时立即跑一次 `python -m app`
10. **`monitor_processes()`**：每 30s 检查 nginx/web PID，死亡自动重启（nginx 最多 3 次）

### 7.3 Linux（setup_linux.sh + live-source-web.service）

- `setup_linux.sh`：渲染 systemd 模板（含 `__PROJECT_DIR__` 占位），enable 服务
- `live-source-web.service`：`User=www-data`，`WorkingDirectory=$PROJECT_DIR`，`ExecStart=$PROJECT_DIR/.venv/bin/python -m uvicorn web.webapp:app --host 0.0.0.0 --port 23456`
- venv 路径：`$PROJECT_DIR/.venv`

### 7.4 Windows（setup_windows.ps1 / install-autostart.ps1）

- 注册任务计划 `LiveSourceManagerWeb`：`SYSTEM` 账户 `AtStartup` 触发
- 参数名：**`-DontStopIfGoingOnBatteries`**（带 s，勿拼错）
- 注意：Windows 路径脚本勿用非 ASCII 路径（编码损坏）

### 7.5 仓库红线（git）

- `config/online`、`config/sources` 已 gitignore（运行期下载，勿入库）
- 推送前 `git ls-files | grep config/online` 确认无跟踪
- GitHub token 仅内联 `git -c "url.https://<TOKEN>@github.com/..."` 使用，**不落盘**
- pre-commit hook entry 全用托管 venv 绝对路径 + `-m`（勿改回裸 `ruff`/`mypy`）

---

## 8. 关键红线（不可违反）

1. **SSRF 窄门禁**（`security.is_static_safe`）：解析阶段**只**做协议白名单 + 主机格式 + 内网/回环拒绝。**禁止**在解析阶段做 DNS 解析 / 境外流媒体拦截 / 域名黑白名单 —— 会让「服务器不可达但用户可看」的源蒸发。可达性交给 `StreamTester`。
2. **被排除 URL 必须记录**：解析阶段被 `is_static_safe` 排除的 URL，必须 `logger.info` + 收集 `exclusion_summary`，**禁止静默 `continue`**。
3. **分类三层架构不可合并/删除**（2026-07-10 李总确认）：
   - 分类字典 `category_dictionary`：受控词表，仅 UI 下拉源，不被引擎消费
   - 分类规则 `classification_rules`：关键词自动分类
   - 频道映射 `channel_name_mapping`：两 UI 共用，写同表同行（key=频道名）
   - 优先级：手动频道映射 > 规则引擎自动；维度级增量合并
   - ⚠️ 勿删分类字典（会让下拉空白、破坏手动覆盖）
4. **CSRF 内联注入**：token 在 `_render()` 内联 `<script>window.__csrf_token=...</script>`，不再依赖异步 fetch 时序。原生 fetch POST 必须带 `X-CSRF-Token`。豁免：`/api/auth/login`、`/api/auth/logout`、`/login`。
5. **加密密钥机器绑定**：`GitHub.api_token` 用 `MENC:` 前缀机器绑定加密；密钥来源 env > SQLite > 自动生成，自动生成必须告警。
6. **原子写入**：配置文件用 `atomic_write`（`.tmp` + `os.replace`），禁止直接覆盖。
7. **端口独占**：12345/23456 同机单实例；Python 与 Go 版互斥。
8. **配置唯一来源**：所有配置读写走 `app_config` 表；改默认值须同步 `_DEFAULT_VALUES` + `SECTION_SCHEMA` + `config-defaults.yaml` 三处。
9. **登录锁定**：5 次失败锁定 15 分钟（依据《网络安全法》第24条）。
10. **首登强改密**：admin 首次登录必须改密码（`password_change_required`）。

---

## 9. 复刻验收清单

让另一个 AI 1:1 实现时，按以下清单逐项核对：

- [ ] **目录结构**符合 §2.1（app/ 9 模块 + web/ 7 路由 + config/ + deploy/）
- [ ] **分层依赖**单向无环（web→app，app 内部 L0→L4）
- [ ] **SQLite 表**全部建表（注意 stream_sources 不建，以 channel_name_mapping 为权威）
- [ ] **配置默认值**与 §3.2 表逐项一致（含 61 键 SECTION_SCHEMA）
- [ ] **is_static_safe** 窄门禁实现（不做 DNS/境外/黑白名单）
- [ ] **StreamTester** 含 ThreadPoolExecutor + Semaphore + 看门狗 + 冻结退避 + host 缓存 + 错误分类
- [ ] **ChannelRules** 6 维度 + 三层联合防御（高优先级/负向排除/最长匹配+省际排除）
- [ ] **M3UGenerator** base/qualified 两级 + 多分类展开 + UA 注入
- [ ] **FastAPI 端点**与 §5.4 全清单一致（含 CSRF 豁免、MD5 source_id）
- [ ] **认证** Session Cookie(httponly,samesite=lax) + RBAC + CSRF(绑定 UA) + 审计日志
- [ ] **加密** Fernet + 机器绑定(MENC:) + 密钥三来源
- [ ] **lifespan** 启动链路与 §5.2 一致（含零配置自动生成 admin 密码）
- [ ] **前端** Jinja2 + HTMX + app.js(window.LSM) + CSRF 自动注入
- [ ] **部署** Dockerfile/compose/nginx.conf/start_docker.sh/setup_linux.sh/setup_windows.ps1 五件套
- [ ] **红线** §8 全部遵守
- [ ] **测试** pytest 套件（209 用例通过）：`.venv/Scripts/python.exe -m pytest`

### 复刻时最容易踩的坑

1. **stream_sources 表**：不要新建它；分类用 channel_name_mapping。
2. **解析阶段门禁**：别手痒加 DNS/境外拦截，否则源会异常消失。
3. **CSRF token 时序**：必须用 `_render` 内联，否则前端偶发「token 为空」。
4. **配置三处同步**：改一处忘改两处，UI 会丢字段。
5. **端口冲突**：同机先停 Go 版再起 Python 版（或反之）。
6. **ffprobe 路径**：先查 `tools/ffmpeg/` 再查 PATH；缺失时测试降级而非崩溃。
7. **Windows 脚本参数**：`-DontStopIfGoingOnBatteries`（带 s）。

---

> **文档版本**：基于 2026-07-20 源码快照逆向生成。如后续代码演进，以 `app/`、`web/`、`config/` 实际源码为准，本指南作为架构与复刻基准。
