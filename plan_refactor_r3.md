# 第三轮重构方案 —— 代码合并 + Docker中国源优化

> 文档版本: v1.0  
> 编写日期: 2026-06-15  
> 前置文档: `plan_test_r3.md`, `review_*_web_r2.md`

---

## 目录

1. [A. 代码合并评估](#a-代码合并评估)
   - [1.1 项目结构全景](#11-项目结构全景)
   - [1.2 依赖关系分析](#12-依赖关系分析)
   - [1.3 合并可行性评估](#13-合并可行性评估)
   - [1.4 各文件整合度评分](#14-各文件整合度评分)
   - [1.5 推荐方案](#15-推荐方案)
2. [B. Docker镜像源优化](#b-docker镜像源优化)
   - [2.1 现有方案分析](#21-现有方案分析)
   - [2.2 问题诊断](#22-问题诊断)
   - [2.3 优化方案](#23-优化方案)
3. [综合执行方案](#综合执行方案)

---

## A. 代码合并评估

### 1.1 项目结构全景

```
/opt/dev/live-source-manager/
├── Dockerfile                  # 多阶段构建 (已含部分清华源)
├── build.sh                    # 构建脚本 (支持 --proxy 参数)
├── start.sh                    # 容器入口 (bash, 810行)
├── nginx.conf                  # Nginx配置
├── requirements.txt            # Python依赖
├── .dockerignore
│
├── app/                        ★ 核心业务 (10个 .py 文件, 5462行)
│   ├── __init__.py             (1行)
│   ├── main.py                 (1122行) 核心管理器 + 入口
│   ├── config_manager.py       (426行) 配置管理
│   ├── source_manager.py       (608行) 源下载/解析
│   ├── stream_tester.py        (1126行) 流媒体测试
│   ├── m3u_generator.py        (445行) M3U/TXT 生成
│   ├── channel_rules.py        (436行) 频道分类规则
│   ├── url_sanitizer.py        (384行) URL安全审查
│   ├── file_utils.py           (245行) 原子写入工具
│   ├── error_handler.py        (309行) 统一错误处理
│   ├── exceptions.py           (116行) 异常体系
│   ├── network_test.py         (160行) 网络测试脚本
│   └── test_http.py            (68行)  HTTP测试脚本
│
├── web/                        ★ Web管理 (6个 .py 文件, 1388行)
│   ├── __init__.py             (1行) 包声明
│   ├── webapi.py               (777行) FastAPI应用+所有路由
│   ├── models.py               (303行) SQLite ORM
│   ├── auth.py                 (125行) 认证/Session/CSRF
│   ├── config_proxy.py         (222行) config.ini 安全读写
│   ├── ws_manager.py           (60行)  WebSocket连接管理
│   ├── templates/              (10个html, 1151行)
│   └── static/                 (app.css 175行 + app.js 229行)
│
├── tests/                      (19个测试文件, 不含__pycache__)
│   ├── conftest.py
│   ├── test_web_api.py         (605行)
│   ├── test_web_auth.py        (611行)
│   ├── test_channel_rules.py   ...
│   ├── ... (共19个测试文件)
│   └── __init__.py
│
├── config/
│   ├── config.ini
│   └── channel_rules.yml
│
├── data/ (状态/数据目录)
│
└── 文档/报告文件 (若干 .md)
```

**文件分类总结**：

| 位置 | 数量 | 总行数 | 性质 |
|------|------|--------|------|
| `app/` | 13 .py | 5462 | 核心业务（异步+同步混合） |
| `web/` | 6 .py | 1388 | Web管理（FastAPI + HTMX） |
| `tests/` | 19 .py | ~5000 | 单元测试 |
| `web/` 静态资源 | 2 + 10 | 175+229+1151 | HTML/CSS/JS（不可合并） |

### 1.2 依赖关系分析

#### 1.2.1 `app/` 模块导入关系

```
main.py
  ├── config_manager         ──→ exceptions
  ├── channel_rules          ──→ (yaml)
  ├── source_manager         ──→ config_manager, channel_rules, url_sanitizer, exceptions
  ├── stream_tester          ──→ config_manager, exceptions
  ├── m3u_generator          ──→ config_manager, exceptions
  └── exceptions

config_manager.py
  └── exceptions

url_sanitizer.py
  └── exceptions

file_utils.py
  └── exceptions

error_handler.py
  └── exceptions

network_test.py
  ├── config_manager, Logger*
  └── exceptions

test_http.py
  ├── config_manager, Logger*
  └── exceptions
```

**关键发现**：`exceptions.py` 是唯一的**叶节点**（被所有模块依赖），`config_manager.py` 是核心枢纽（被 6 个模块依赖）。

#### 1.2.2 `web/` 模块导入关系

```
webapi.py (FastAPI 应用)
  ├── web.models             (ORM)
  ├── web.auth               (认证)
  ├── web.config_proxy       (配置代理)
  └── web.ws_manager         (WS连接管理)

  → 运行时懒加载 app 模块：
     ├── app.source_manager
     ├── app.config_manager
     └── app.channel_rules

auth.py
  └── web.models

config_proxy.py
  └── (无子模块依赖)

ws_manager.py
  └── (无子模块依赖)
```

**关键发现**：`web/` 内部各文件之间是**单向分层依赖**（models → auth → webapi），不存在循环引用。

### 1.3 合并可行性评估

#### 1.3.1 维度分析

| 维度 | 评价 |
|------|------|
| **行数** | `web/` 合计 1388行，`app/` 合计 5462行——超大单体不推荐 |
| **依赖方向** | 均为单向，无循环引用——合并难度低 |
| **命名冲突** | 各文件中无重名函数/类/全局变量——安全 |
| **全局状态** | 仅 `ws_manager.py` 有 `manager` 全局单例；`auth.py` 有 `_csrf_tokens` 内存 dict——可合并 |
| **热加载** | FastAPI 的 `--reload` 下，拆文件越多单文件变更越少触发重启——合并后调试稍差 |
| **测试影响** | 测试直接按模块 import，合并后需调整 import |

#### 1.3.2 哪些文件天然适合合并

| 候选组 | 理由 |
|--------|------|
| **web/auth.py → webapi.py** | auth 仅被 webapi 使用，125行小文件，无独立运行需求 |
| **web/ws_manager.py → webapi.py** | 仅被 webapi 使用，60行，极简 |
| **app/file_utils.py + app/error_handler.py → app/utils.py** | 都是工具函数，各被 2-3 个模块引用 |
| **app/exceptions.py → app/error_handler.py** | 异常定义 + 异常处理天然同属 |
| **app/test_http.py + app/network_test.py → app/scripts.py** | 都是独立脚本，非核心模块 |
| **web/config_proxy.py → webapi.py** | 仅被 webapi 使用，222行中等，但职责清晰可保留分离 |

#### 1.3.3 哪些文件建议保留分离

| 文件 | 理由 |
|------|------|
| **app/config_manager.py** (426行) | 被 6 个模块依赖，是核心枢纽 |
| **app/main.py** (1122行) | 入口 + 完整组织逻辑，职责不可降 |
| **app/source_manager.py** (608行) | 独立的异步下载/解析逻辑 |
| **app/stream_tester.py** (1126行) | 极大规模，涉及 ffprobe/并发 |
| **app/m3u_generator.py** (445行) | 输出格式生成，独立职责 |
| **app/channel_rules.py** (436行) | YAML 规则引擎，独立边界 |
| **app/url_sanitizer.py** (384行) | 安全审查，独立关注点 |
| **web/models.py** (303行) | 被 auth.py + webapi.py 依赖，SQLite ORM 独立 |

#### 1.3.4 将 `web/` 全部合并为 1-2 个文件是否可行？

**结论：技术可行，但不推荐。**

- **可行理由**：6个文件共 1388 行，无命名冲突，依赖单向，无循环引用
- **不推荐理由**：
  - `models.py` (303行) + `auth.py` (125行) + `config_proxy.py` (222行) + `ws_manager.py` (60行) + `webapi.py` (777行) = 1487行
  - 合并后仍为清晰文件，但**每次修改 web 任何逻辑都触发 FastAPI `--reload` 全量重启**
  - 代码审查时无法通过文件名判断问题范围
  - 合并后的文件约 1500 行——对 Python 而言已是较大单体

### 1.4 各文件整合度评分

| 文件 | 行数 | 被引用数 | 独立执行 | 建议 |
|------|------|----------|----------|------|
| `web/ws_manager.py` | 60 | 1 | 否 | **合并** → webapi.py |
| `web/auth.py` | 125 | 1 | 否 | **合并** → webapi.py |
| `web/config_proxy.py` | 222 | 1 | 否 | **合并** → webapi.py |
| `web/__init__.py` | 1 | - | - | 保留（包声明用） |
| `web/models.py` | 303 | 2 | 否 | **保留分离**（被 auth + webapi 依赖） |
| `web/webapi.py` | 777 | 1 | 是 | 保留（主文件） |
| `app/__init__.py` | 1 | - | - | 保留 |
| `app/exceptions.py` | 116 | 全模块 | 否 | **合并** → app/error_handler.py |
| `app/file_utils.py` | 245 | 2-3 | 否 | **合并** → app/utils.py |
| `app/error_handler.py` | 309 | 2-3 | 否 | **合并** → app/utils.py |
| `app/test_http.py` | 68 | 0 | 是 | **合并** → app/scripts.py |
| `app/network_test.py` | 160 | 0 | 是 | **合并** → app/scripts.py |
| 其余核心7文件 | 436-1126 | 2-6 | 部分 | **保留分离** |

### 1.5 推荐方案

#### 方案A（保守推荐）—— 合并小文件

**变更量**：减少 5 个 `.py` 文件（13→8）

| 操作 | 说明 |
|------|------|
| `ws_manager.py + auth.py + config_proxy.py` → **并入 `webapi.py`** | 精简 web/ 从 6→3 文件 |
| `exceptions.py` → **并入 `error_handler.py`**（重命名 `core.py`） | 异常+处理合一 |
| `file_utils.py + error_handler.py` → **合并为 `utils.py`** | 工具函数归一 |
| 保留 `__init__.py`、`models.py`、7个核心 app 模块 | 不动 |

**结果**：`web/` 从 6 → 3 文件，`app/` 从 13 → 8 文件。

**优点**：
- 风险最低，约 20 分钟完成
- 不影响任何测试（仅需修正 2 处 import）
- 不影响热加载

#### 方案B（激进）—— web/ 全部合并 + app/ 精简

**变更量**：减少 9 个 `.py` 文件

| 操作 | 说明 |
|------|------|
| `web/` 全部 6 文件 → **合并为 `webapp.py`** 单文件 | web/ 从 6→1 |
| `app/exceptions.py + error_handler.py + file_utils.py` → **合并为 `app/utils.py`** | 3 合一 |
| `app/network_test.py + app/test_http.py` → **合并为 `app/scripts.py`** | 2 合一 |

**结果**：`web/` 6→1，`app/` 13→9，整体减少 9 个文件。

**优点**：
- 大幅减少文件数
- web 模块结构清晰

**风险**：
- `webapp.py` 约 1500 行，定位问题需搜索
- 所有 web 模块变更都触发 FastAPI reload
- 测试文件 import 路径需修改（`test_web_auth.py` 和 `test_web_api.py`）

#### 方案（极限不推荐）—— 全部合并为 2 个文件

不推荐。core 业务 5462 行无法单文件维护，且 import 上下文管理复杂。

---

## B. Docker镜像源优化

### 2.1 现有方案分析

**当前 Dockerfile 已含中国源优化（已完成部分）**：

```dockerfile
# ⚡ 已存在（Dockerfile 第28行）
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list  && \
    apt-get update && ...

# ⚡ 已存在（Dockerfile 第37行）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# ⚡ 已存在（Dockerfile 第47行）
# ffprobe 优先从清华源下载
"https://mirrors.tuna.tsinghua.edu.cn/ffmpeg/ffmpeg-release-amd64-static.tar.xz"

# ⚡ 已存在 build.sh
# 支持 --proxy 参数切换基础镜像为 docker.1ms.run/python:3.9-slim-bookworm
```

### 2.2 问题诊断

#### 问题 1：debian.sources 格式兼容性

```dockerfile
# 若使用 bookworm（Debian 12），sources 文件改为 .sources 格式
# 但当前 sed 命令只处理 sources.list，bookworm 默认使用 /etc/apt/sources.list.d/debian.sources
# 现有写法用 2>/dev/null 掩蔽错误
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list
```

- `debian.sources` 使用 `deb822` 格式，`sed 's/deb.debian.org/...'` **不替换** `URIs:` 字段名
- 真正需要替换的是 `URIs: http://deb.debian.org/debian` → `URIs: http://mirrors.tuna.tsinghua.edu.cn/debian`
- 当前写法仅替换了 `sources.list` 旧格式，对于 bookworm 的 `debian.sources` **实际上源未被替换**

#### 问题 2：cron 安装失败

Dockerfile 第 80 行安装 `cron` 等包，但 apt-get 已用到清华源——此部分若 debian.sources 未替换成功，bookworm 下将回退官方源

#### 问题 3：pip 源仅全局配置一次

```
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

每次 pip install 都用此源——但在 `requirements.txt` 里有个别依赖（如 `aiohttp_socks`）可能只在 PyPI 的特定镜像延迟同步

### 2.3 优化方案

#### 方案 A（最小修复，推荐）

**只修复现有方案的问题，不改动结构**：

**1. 修复 debian.sources 替换**

```dockerfile
# Debian bookworm (12+) 使用 .sources DEB822 格式
# 替换其中所有 URIs 字段
RUN sed -i 's|URIs: http://deb.debian.org|URIs: http://mirrors.tuna.tsinghua.edu.cn|g' \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null || true && \
    sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' \
        /etc/apt/sources.list 2>/dev/null || true && \
    ...
```

**2. 为 pip 增加备用源（避免单一源失败）**

```dockerfile
# 优先清华，失败回退官方
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip config set global.extra-index-url https://pypi.org/simple && \
    pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn pypi.org
```

**3. ffprobe 增加阿里云/中科大备用**

```dockerfile
RUN curl -fSL --retry 3 --retry-delay 5 \
        "https://mirrors.tuna.tsinghua.edu.cn/ffmpeg/ffmpeg-release-amd64-static.tar.xz" \
        -o /tmp/ffmpeg-static.tar.xz 2>/dev/null || \
    curl -fSL --retry 3 --retry-delay 5 \
        "https://mirrors.ustc.edu.cn/ffmpeg/ffmpeg-release-amd64-static.tar.xz" \
        -o /tmp/ffmpeg-static.tar.xz 2>/dev/null || \
    ...
```

#### 方案 B（全面增强，覆盖所有网络层）

| 层面 | 当前 | 改进后 |
|------|------|--------|
| **基础镜像** | docker.1ms.run (--proxy时) | 保留，同时增加 `--build-arg` 说明文档 |
| **apt 源** | 清华 TUNA | 修复 deb822 格式 + 增加中科大/阿里云备用 |
| **pip 源** | 清华 TUNA | 增加 extra-index-url 备用 |
| **ffprobe 下载** | 清华 → johnvansickle → GitHub | 增加阿里云/中科大作为第2顺位 |
| **npm** | 不适用 | N/A（无 node 依赖） |
| **GitHub 访问** | 直连 | 通过环境变量 `GITHUB_TOKEN` 提高速率（已实现） |
| **Docker Hub** | docker.1ms.run (--proxy时) | 文档说明更清晰 |

**具体修改**（三处 Dockerfile 的 RUN）：

```dockerfile
# ===== 修改1：builder 阶段 apt 源（第28-33行替换）=====
RUN ( \
    # Debian 12 bookworm 使用 .sources (DEB822) 格式
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i \
            's|URIs: http://deb.debian.org|URIs: http://mirrors.tuna.tsinghua.edu.cn|g; \
             s|http://mirrors.tuna.tsinghua.edu.cn|http://mirrors.ustc.edu.cn|g' \
            /etc/apt/sources.list.d/debian.sources; \
    fi; \
    # 旧格式 .list 兼容
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list; \
    fi; \
) && \
apt-get update && \
apt-get install -y --no-install-recommends curl ca-certificates xz-utils && \
apt-get clean && rm -rf /var/lib/apt/lists/*

# ===== 修改2：pip 源配置（第37行替换）=====
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip config set global.extra-index-url https://pypi.org/simple && \
    pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn pypi.org && \
    pip config set global.timeout 60

# ===== 修改3：ffprobe 下载（第47-62行替换）=====
RUN curl -fSL --retry 3 --retry-delay 5 \
        "https://mirrors.tuna.tsinghua.edu.cn/ffmpeg/ffmpeg-release-amd64-static.tar.xz" \
        -o /tmp/ffmpeg-static.tar.xz 2>/dev/null || \
    curl -fSL --retry 3 --retry-delay 5 \
        "https://mirrors.ustc.edu.cn/ffmpeg/ffmpeg-release-amd64-static.tar.xz" \
        -o /tmp/ffmpeg-static.tar.xz 2>/dev/null || \
    curl -fSL --retry 3 --retry-delay 5 \
        "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz" \
        -o /tmp/ffmpeg-static.tar.xz 2>/dev/null || \
    curl -fSL --retry 3 --retry-delay 5 \
        "https://github.com/eugeneware/ffmpeg-static/releases/download/v5.1.1/ffmpeg-linux-x64" \
        -o /tmp/ffprobe-linux-x64 2>/dev/null; \
    ...

# ===== 修改4：运行时 apt 源（第80行区域）=====
# 与修改1相同的源替换逻辑（生产阶段各包同样依赖 apt 源）
```

### 2.4 不影响现有功能验证

| 检查项 | 验证方法 |
|--------|----------|
| pip install 仍成功 | `pip install -r requirements.txt`（验证可安装到官方包） |
| apt 包完整 | 从清华源安装 cron/nginx/tzdata 等 |
| ffprobe 可用性 | `ffprobe -version` 检查 |
| 构建时间缩短 | 对比 `time docker build` |
| 构建不依赖外网 | 断外网测试清华源是否离线可用 |

---

## 综合执行方案

### 推荐路径

```
┌─────────────────────────────────────────────┐
│   第1步：Docker中国源优化（安全，无代码影响）   │
│   • 修复 builder 阶段 debian.sources 替换     │
│   • 修复 runtime 阶段 apt 源替换              │
│   • pip 增加 extra-index-url 备用             │
│   • ffprobe 增加中科大备用源                  │
│   预计耗时：12分钟                            │
└─────────────────┬───────────────────────────┘
                  │
┌─────────────────▼───────────────────────────┐
│   第2步：代码合并方案A（保守，低风险）          │
│   web/ 合并 (6→3):                           │
│   • ws_manager.py → webapi.py                │
│   • auth.py → webapi.py                      │
│   • config_proxy.py → webapi.py              │
│   app/ 合并 (13→11):                          │
│   • exceptions.py → error_handler.py + rename│
│   • file_utils.py → utils.py (新建)           │
│   预计耗时：20分钟                            │
└─────────────────┬───────────────────────────┘
                  │
┌─────────────────▼───────────────────────────┐
│   第3步：第三轮测试                            │
│   • pytest 全量运行                           │
│   • docker build 验证                        │
│   • 功能回归验证                               │
│   预计耗时：10分钟                             │
└─────────────────────────────────────────────┘
```

### 代码合并明细清单

#### Step 2a：web/ 合并（ws_manager.py → webapi.py）

**6 行代码内联**：`ws_manager.py` 仅 `manager` 单例 + `connect/disconnect/broadcast/count` → 插入到 `webapi.py` 末尾（或顶部 `# WebSocket 管理` 区域后）

**注意**：
- `ws_manager.py` 的 `manager = ConnectionManager()` 全局单例改为 `webapi.py` 中的模块级变量
- `from web.ws_manager import manager as ws_manager` → 删掉，直接引用

#### Step 2b：web/ 合并（auth.py → webapi.py）

**125 行内联**：
- `SESSION_TTL`, `IDLE_TIMEOUT` 常量 → 移入 webapi.py
- `_csrf_tokens`, `_csrf_lock` → 移入 webapi.py
- 所有函数（`_get_csrf_token`, `verify_csrf_token`, `create_session`, `get_session`, `destroy_session` → 移入 webapi.py
- FastAPI 依赖函数（`get_current_user`, `optional_current_user`, `require_admin`）→ 移入 webapi.py
- `login_required_page` 装饰器 → 移入 webapi.py

**注意**：移除 `from web import models` → 改为 `from web.models`（import 路径不变，仅文件合并）

#### Step 2c：web/ 合并（config_proxy.py → webapi.py）

**222 行内联**：
- `SECTION_SCHEMA`, `SENSITIVE_FIELDS`, `FIELD_TYPE` → 移入 webapi.py
- 所有函数 → 移入 webapi.py

**注意**：`CONFIG_PATH` 路径计算直接内联

#### Step 2d：app/ 合并（exceptions.py → error_handler.py）

`exceptions.py`（116行）全部内容追加到 `error_handler.py` 起始位置（因为 `BaseAppException` 系列类需要在 `setup_logger` 之前）

**重命名**：`error_handler.py` → `core.py`（或保留原名，取决于喜好）

#### Step 2e：app/ 合并（file_utils.py → 新建 utils.py）

新建 `app/utils.py`，内容为 `file_utils.py` 全部内容（原子写入 + 安全读取），同时 `error_handler.py` 合并后的文件也可将其 `setup_logger`/`ErrorStats` 纳入

### 测试影响分析

| 测试文件 | 是否受影响 |
|----------|-----------|
| `tests/test_web_auth.py` | **受影响**（import `web.auth` 路径需改为 `web.webapi`） |
| `tests/test_web_api.py` | **受影响**（import `web.webapi` 不变，但内部符号位置变化） |
| `tests/test_file_utils.py` | **受影响**（import `app.file_utils` → `app.utils`） |
| `tests/test_error_handler.py` | **受影响**（import `app.error_handler` → `app.utils`） |
| `tests/test_exceptions.py` | **受影响**（import `app.exceptions` → `app` 内部） |
| 其余 15 个测试 | **无影响** |

---

### 附录：合并后文件结构预览

**方案A 执行后**：
```
app/
├── __init__.py        (1行)  保留
├── main.py            (1122行)  保留
├── config_manager.py  (426行)  保留
├── source_manager.py  (608行)  保留
├── stream_tester.py   (1126行)  保留
├── m3u_generator.py   (445行)  保留
├── channel_rules.py   (436行)  保留
├── url_sanitizer.py   (384行)  保留
├── utils.py           [新建]  file_utils + error_handler + exceptions
├── scripts.py         [新建]  network_test + test_http
└── models.py          (29行)  保留 (app 数据模型)

web/
├── __init__.py        (1行)  保留
├── webapi.py          ≈1184行  (原777 + auth125 + ws60 + config_proxy222)
├── models.py          (303行)  保留
├── templates/         (不变)
└── static/            (不变)
```

**精简统计**：
- `app/` .py 文件：13 → 11（减少2个）
- `web/` .py 文件：6 → 3（减少3个）
- **总计减少 5 个 .py 文件，节省 0 行代码**

---

*End of plan_refactor_r3.md*
