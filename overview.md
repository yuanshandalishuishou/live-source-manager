# 团队技术提升 — 第二批工程化改造总结

> 执行时间：2026-07-04
> 执行人：Senior Developer（高级开发工程师）
> 覆盖方向：webapp拆分 / 工程基建 / 测试体系 / 技术债清理 / Bug修复

---

## 一、web/webapp.py 拆分

### 改造前
- **1 个文件，3711 行，85 个路由装饰器**
- 配置代理、WebSocket管理、认证session、中间件、路由全部塞在一起

### 改造后
| 文件 | 行数 | 职责 |
|------|------|------|
| `web/core.py` | 936 | 共享基础设施（app实例/中间件/lifespan/认证/配置代理/辅助函数） |
| `web/webapp.py` | 94 | 精简入口（导入app + 挂载routers + uvicorn启动） |
| `web/routes/pages.py` | 79 | 10个HTML页面路由 |
| `web/routes/auth.py` | 298 | 13个认证+用户管理API |
| `web/routes/dashboard.py` | 145 | 5个Dashboard统计API |
| `web/routes/sources.py` | 1320 | 17个源管理API+辅助函数 |
| `web/routes/config_api.py` | 229 | 8个配置中心API |
| `web/routes/rules.py` | 670 | 22个规则+频道映射+分类字典API |
| `web/routes/system.py` | 236 | 8个系统API（测试/WS/日志/审计/GitHub） |

### 关键设计
- 路由模块用 FastAPI `APIRouter` 模式，只依赖 `web.core`，不互相依赖
- `conftest.py` 兼容：core.py 用 `_get_config_path()` / `_get_csrf_exempt_paths()` 访问器，运行时动态读取 webapp 模块覆写值
- **64 个 API 端点全部注册成功**，向后完全兼容

---

## 二、工程基建 — Ruff + mypy + pre-commit

### 新增配置文件
- **`pyproject.toml`** — 统一配置 ruff(lint+format) / mypy(类型检查) / pytest
- **`.pre-commit-config.yaml`** — git pre-commit 钩子（ruff check + format + mypy + 基础钩子）

### Ruff 配置策略
- 启用规则集：E, W, F, I, UP, B, SIM, TCH, RUF
- 豁免：E501(行长度由formatter管)、B008(FastAPI Depends标准模式)、RUF001-003(中文全角标点)
- `app/manager.py` 和 `app/logger.py` 豁免 T201(print) — 这些是合理的 fallback/CLI 输出

### 执行结果
- ruff 自动修复 **892 个 lint 问题**（导入排序、类型注解现代化、未使用导入清理等）
- ruff format 格式化 **22 个文件**
- 剩余 **84 个手动修复类问题**（B904 raise from、F401 未使用导入等）作为技术债跟踪
- mypy 检出 365 个类型问题（主要在 manager.py），已建立基线逐步修复

### 工具安装
```
ruff, mypy, pre-commit, pytest-asyncio, types-requests, types-PyYAML
```
全部安装到 `.venv`，通过 `pip install -e ".[dev]"` 可一键安装开发依赖。

---

## 三、单元测试体系

### 从零到 100 个测试

| 测试文件 | 测试数 | 覆盖模块 | 覆盖内容 |
|----------|--------|----------|----------|
| `test_exceptions.py` | 30 | app.exceptions | 异常继承关系、BaseAppException属性、ErrorStats统计、catch_exception装饰器(同步+异步)、format_error_response |
| `test_utils.py` | 22 | app.utils | atomic_write(正常/重试/备份/验证/大文件/空文件)、safe_read_file(UTF-8/多编码/BOM/二进制回退)、_backup_file |
| `test_security.py` | 28 | app.security | validate_url(合法/非法/XSS/命令注入/路径遍历/私有IP/黑名单/端口/查询参数)、sanitize_url、is_safe_url、域名黑名单管理 |
| `test_config.py` | 20 | app.config | Config初始化、配置读写、类型转换(getint/getboolean)、默认值、items/sections、UserAgents |

```
100 passed in 0.99s
```

### 发现的问题（已记录为已知限制）
1. `validate_url` 的命令注入检查会把 URL query 中的 `&` 误报（`CMD_INJECTION_PATTERNS` 含 `&`）
2. `urlparse` 把 `;` 解析为 params 不进入 path，命令注入检测覆盖不到
3. `127.0.0.1` 在 `DEFAULT_DOMAIN_BLACKLIST` 中，会被黑名单拦截而非私有IP检查

---

## 四、技术债清理

### 删除重复 venv
- `venv/` 目录（40MB）已删除 — 所有脚本引用 `.venv/`，`venv/` 是废弃的
- `.venv/`（339MB）保留为主开发环境

### print 语句分析
- 全项目 13 处 `print()` 语句
- `app/logger.py`（4处）：logger 初始化失败时的 fallback，合理保留
- `app/manager.py`（9处）：CLI 入口输出 + 错误处理降级，合理保留
- **结论：全部合理，在 ruff 配置中针对性豁免 T201 规则**

---

## 五、Bug 修复

### conftest.py viewer_password
- **问题**：`tests/conftest.py` 第 66 行传 `viewer_password=SHARED_VIEWER_PW` 给 `init_db()`，但该参数已移除（viewer 用户已废弃）
- **修复**：移除 `viewer_password` 参数、`SHARED_VIEWER_PW` 变量、`WEB_VIEWER_PASSWORD` 环境变量
- **清理**：`DELETE FROM users WHERE username NOT IN ('admin', 'viewer')` → `NOT IN ('admin')`

---

## 六、项目架构现状

```
live-source-manager/
├── app/                    # 核心业务逻辑（10个模块，5层依赖）
│   ├── exceptions.py       # L0: 异常体系 + ErrorStats
│   ├── logger.py           # L0: 日志管理
│   ├── utils.py            # L0: 文件工具
│   ├── config.py           # L1: 配置管理
│   ├── security.py         # L1: URL安全审查
│   ├── rules.py            # L2: 频道分类规则
│   ├── source_manager.py   # L2: 源采集/解析
│   ├── stream_tester.py    # L2: 流测试
│   ├── m3u_generator.py    # L3: M3U生成
│   ├── manager.py          # L4: 协调层
│   └── __init__.py         # re-export（197行，零业务逻辑）
├── web/                    # Web 管理界面
│   ├── core.py             # 共享基础设施
│   ├── webapp.py           # 精简入口（94行）
│   ├── routes/             # 7个路由模块
│   ├── models.py           # SQLite ORM
│   └── crypto_utils.py     # 加密工具
├── tests/                  # 单元测试（100个）
│   ├── conftest.py         # 共享fixture
│   ├── test_exceptions.py  # 30个测试
│   ├── test_utils.py       # 22个测试
│   ├── test_security.py    # 28个测试
│   └── test_config.py      # 20个测试
├── pyproject.toml          # 统一配置（ruff+mypy+pytest）
├── .pre-commit-config.yaml # git hooks
├── requirements.txt        # 运行时依赖
└── .gitignore
```

---

## 七、后续建议

1. **修复剩余 84 个 ruff 问题** — 主要是 B904(raise from) 和 F401(未使用导入)，可批量处理
2. **补充 web 层测试** — 用 FastAPI TestClient 测试 API 端点
3. **mypy 逐步修复** — 从 L0 模块开始，逐步消除类型错误
4. **CI/CD 流水线** — GitHub Actions / GitLab CI 配置自动化检查
5. **Code Review 规范** — 制定 PR 模板和 review checklist
