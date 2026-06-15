# 代码质量审查报告

**项目**: live-source-manager  
**Commit**: 2b92c58  
**日期**: 2026-06-15  
**总代码量**: 7,290 行 (14 个核心文件 + 22 个测试文件)

---

## 一、各文件 Pyflakes 问题统计

| 文件 | 问题数 | 主要问题 |
|------|--------|---------|
| `app/__init__.py` | 0 | — |
| `app/channel_rules.py` | 2 | 未使用导入 `Optional`, `Tuple` |
| `app/config_manager.py` | 0 | ✅ |
| `app/__init__.py` | 0 | ✅ |
| `app/m3u_generator.py` | 4 | 未使用 `os`; 未使用 `LsmError`, `OutputError`; f-string无占位符 |
| `app/main.py` | **5** | 未使用 `socket`; 未使用 `LsmError`; `SourceDownloadError`未定义(实为未导入); 未使用 `total_sources`; 未使用 `bitrate` |
| `app/models.py` | 1 | 未使用 `typing.Optional` |
| `app/scripts.py` | 1 | 未使用 `time` |
| `app/source_manager.py` | 3 | 未使用 `SourceError`, `validate_url`; 未使用变量 `proxy_auth` |
| `app/stream_tester.py` | 4 | 未使用 `Any`, `BytesIO`; 未使用 `has_audio`; f-string无占位符 |
| `app/url_sanitizer.py` | 3 | 未使用 `logging`, `quote`, `BaseAppException` |
| `app/utils.py` | 2 | 未使用变量 `last_exception`, `_log` |
| `web/__init__.py` | 0 | ✅ |
| `web/auth.py` | 3 | 未使用 `web.models` (重复3次) |
| `web/crypto_utils.py` | 0 | ✅ |
| `web/models.py` | 1 | f-string无占位符 |
| `web/webapp.py` | **11** | 未使用 `uuid`, `time`, `Path`, `Fernet`, `optional_current_user`, `login_required_page`, `_csrf_tokens`; 未使用 `total`, `valid`; 2个f-string无占位符 |
| **测试文件(22个)** | 共~55+ | 多为未使用的导入 (详见下节) |
| **总计** | **~90+** | |

### 测试文件问题概要

| 文件 | 问题数 | 说明 |
|------|--------|------|
| `test_config_sqlite_r2.py` | 8 | 未使用 `json`, `patch`, `read_section`, `write_config` 等 |
| `test_web_auth.py` | 7 | 未使用 `json`, `time`, `uuid`, `asyncio`, `shutil`, `pytest` |
| `test_stream_tester.py` | 5 | 未使用 `time`, `tempfile`, `PropertyMock`, 内部重导入 |
| `test_integration_config.py` | 4 | 未使用 `json`, `_get_csrf_token`, 未用变量 |
| `test_source_manager.py` | 4 | 未使用 `AsyncMock`, 内部重导入 |
| `test_channel_rules.py` | 3 | 未使用 `patch`, `MagicMock`, `mock_open` |
| `test_error_handler.py` | 3 | 未使用 `pytest`, `catch_exception`, `LsmError` |
| `test_main.py` | 3 | 未使用 `tempfile`, `pytest`, `PropertyMock` |
| `test_test_http.py` | 3 | 未使用 `sys`, `os`, `scripts_test_http_service` |
| `test_web_api.py` | 2 | 未使用 `json`, `pytest` |
| `test_config_sqlite.py` | 2 | 未使用 `json`, `pytest` |
| 其余 | 0-2 | 零星未使用导入 |

---

## 二、Pycodestyle 风格问题统计

| 文件 | W293(空行末尾空格) | 其他问题 | 合计 |
|------|-------------------|---------|------|
| `app/main.py` | 189 | 22 | **211** |
| `app/config_manager.py` | 1 | 8 | 9 |
| `app/utils.py` | 0 | 2 | 2 |
| `app/m3u_generator.py` | 78 | 2 | 80 |
| `app/source_manager.py` | 105 | 4 | 109 |
| `app/stream_tester.py` | 194 | 23 | **217** |
| `app/url_sanitizer.py` | 40 | 0 | 40 |
| `app/channel_rules.py` | 70 | 2 | 72 |
| `app/models.py` | 0 | 0 | 0 ✅ |
| `app/scripts.py` | 0 | 3 | 3 |
| `web/webapp.py` | 1 | 67 | 68 |
| `web/auth.py` | 0 | 3 | 3 |
| `web/crypto_utils.py` | 0 | 0 | 0 ✅ |
| `web/models.py` | 0 | 2 | 2 |
| **总计** | **678** | **138** | **816** |

**其他问题（非W293）分布**:
- `app/main.py` (22): E402(模块级导入不在文件顶) ×6, E302/E305(空行数) ×8, 其余空行空格
- `app/stream_tester.py` (23): W291(行尾空格)多处, E303(多余空行)
- `web/webapp.py` (67): 大量E302(类/函数前后空行数错误), E402, E501(行超长)
- `app/config_manager.py` (8): E501(6处超长120字符)
- `app/scripts.py` (3): E501(3处超长)

---

## 三、硬编码问题清单

### 3.1 硬编码路径
| 文件 | 行号 | 内容 | 问题 |
|------|------|------|------|
| `app/config_manager.py` | 337 | `'output_dir': '/www/output'` | `get_output_params()` 中硬编码返回 `/www/output`，**不从配置读取** |
| `app/config_manager.py` | 48 | `'HTTPServer.document_root': '/www/output'` | 默认值硬编码 |
| `app/scripts.py` | 96-101 | `os.path.exists('/www/output')` → `os.access('/www/output', ...)` | 硬编码路径 |
| `app/scripts.py` | 116-118 | `http://localhost:12345/health` 等 | 硬编码端口 |

### 3.2 硬编码端口
| 文件 | 行号 | 端口 | 说明 |
|------|------|------|------|
| `web/webapp.py` | 66 | 12345 | 默认监听端口（有配置覆盖） |
| `web/webapp.py` | 58 | 1800 | 默认代理端口（有配置覆盖） |
| `web/webapp.py` | 1020 | 23455 | `check_port()` 函数参数默认值（与12345不一致） |
| `web/webapp.py` | 1035 | 23455 | WEB_PORT 环境变量回退值（与12345不一致） |
| `app/config_manager.py` | 41 | 1800 | 默认代理端口 |
| `app/config_manager.py` | 47 | 12345 | 默认HTTP端口 |
| `app/scripts.py` | 92 | 12345 | 硬编码健康检查URL |

### 3.3 配置不一致问题
- `web/webapp.py`中 `check_port()` 和 `main()` 的默认端口为 **23455**，但 `config_manager.py` 中默认值为 **12345**。两者不一致，且 `webapp.py:66` schema中也定义默认12345。存在矛盾的默认值。

### 3.4 硬编码密钥/盐/数据库路径
- 无硬编码密钥或盐——密码读取自环境变量(`WEB_ADMIN_PASSWORD`, `WEB_VIEWER_PASSWORD`)
- 加密密钥通过 `crypto_utils.ensure_key_initialized()` 动态初始化
- 数据库路径未检出硬编码（SQLite路径通过配置读取）

---

## 四、废弃代码 / TODO / 调试打印清单

### 4.1 `print()` 语句（生产代码）
多数 `print()` 在 `app/main.py`, `app/config_manager.py`, `app/scripts.py` 中用于启动日志 / CLI输出，尚可接受。但有争议的包括:

| 文件 | 行号 | 内容 | 评价 |
|------|------|------|------|
| `app/main.py` | 1060-1074 | `print(f"INFO: {message}")` 等 | logger_info/error/warning 用 print 实现而非 logging 模块 |
| `app/config_manager.py` | 507-534 | `print(f"...)` 多处 | 日志初始化失败时的降级处理，可接受 |
| `app/scripts.py` | 25-186 | 大量 `print()` | CLI诊断工具，合理 |

### 4.2 TODO / FIXME / HACK / XXX
- **未发现**任何 `TODO`/`FIXME`/`HACK`/`XXX` 残留注释

### 4.3 注释标记
- `app/main.py:1080` — `# 调试信息不输出到控制台`（正常注释）
- 未发现 `# 测试用` / `# debug` 等临时标记

---

## 五、安全风险扫描

### 5.1 危险函数
| 函数 | 出现 | 风险 |
|------|------|------|
| `eval()` | ❌ 未使用 | 安全 |
| `exec()` | ❌ 未使用 | 安全 |
| `pickle.loads()` | ❌ 未使用 | 安全 |
| `subprocess.run()` | ✅ 使用（2处） | **中等风险** — 见下文 |
| `os.system()` | ❌ 未使用 | 安全 |

### 5.2 subprocess 调用分析
- **`app/stream_tester.py:95`**: `subprocess.run(['ffprobe', '-version'], ...)` — 固定命令，安全
- **`app/stream_tester.py:424`**: `subprocess.run(cmd, ...)` — **潜在风险**: `cmd` 由参数构建，命令部分为固定 `ffprobe`，参数由 `_build_ffprobe_cmd()` 生成。需确认 `url` 没有被注入到命令行参数中。

### 5.3 SQL 注入风险
- `web/models.py:165-169` — 使用 `str.format()` 构建 `WHERE key IN ({})` 但使用 `?` 占位符参数化，**基本安全**
- 其余所有 `conn.execute()` 调用均使用参数化查询 (`?` 占位符 + 参数元组)，**安全**

### 5.4 XSS / 输出转义
- Web 输出使用 HTML 模板，需确认模板引擎是否自动转义
- CSRF 保护已实现（`csrf_middleware` + 令牌验证）
- Session 管理使用加密随机 token（`secrets` 模块）
- 敏感字段 `proxy_password`, `api_token` 在配置读取时被屏蔽为 `***`

### 5.5 其他安全考量
- 密码在启动时生成随机值并存 SQLite，日志中打印密码明文（`web/webapp.py:320`）— 低风险（本地日志）
- 默认 /www/output 路径对容器内 Nginx 暴露 — 依赖容器隔离

---

## 六、导入完整性

**结论**: 除 `app/main.py` 缺少 `SourceDownloadError` 导入外，其他文件的导入均可正确解析。

| 文件 | 状态 | 备注 |
|------|------|------|
| `app/__init__.py` | ✅ | |
| `app/main.py` | ⚠️ | 缺少 `SourceDownloadError` 导入（line 723 使用但未 import） |
| `app/config_manager.py` | ✅ | |
| `app/utils.py` | ✅ | |
| `app/m3u_generator.py` | ✅ | |
| `app/source_manager.py` | ✅ | |
| `app/stream_tester.py` | ✅ | |
| `app/url_sanitizer.py` | ✅ | |
| `app/channel_rules.py` | ✅ | |
| `web/webapp.py` | ✅ | |
| `web/auth.py` | ✅ | |
| `web/crypto_utils.py` | ✅ | |
| `web/models.py` | ✅ | |

修复后全部可导入:
```
python3 -c "from app.main import EnhancedLiveSourceManager" → OK (需正确设置 sys.path)
```

---

## 七、综合评分

| 维度 | 评分 | 依据 |
|------|------|------|
| **代码风格** | **C** | pycodestyle 问题816处，核心文件大量 W293 行尾空格; webapp.py 大量E302/E402 |
| **代码正确性** | **C** | 1处明确的运行时 Bug (SourceDownloadError 未导入); 多处未使用变量/导入 |
| **硬编码** | **C** | 路径/端口硬编码，且存在默认值不一致(12345 vs 23455) |
| **废弃代码** | **A** | 无明显废弃代码残留; 无 TODO/FIXME |
| **安全性** | **B** | 无高危风险; CSRF/参数化查询/敏感字段屏蔽均实现; subprocess调用基本安全 |
| **测试代码** | **B** | 测试覆盖较好但存在较多未使用的导入 |

### 综合评分: **C**

（A=优秀, B=良好, C=及格, D=不及格）

---

## 八、优先级修复建议

### P0 — 必须修复（运行时风险）
1. **`app/main.py:40` 缺少 `SourceDownloadError` 导入**
   - 添加 `SourceDownloadError` 到 import 语句；否则 `except SourceDownloadError` 会在运行时抛出 `NameError`

### P1 — 高优先级
2. **`app/config_manager.py:337` `get_output_params()` 硬编码 `output_dir`**
   - 应改为 `self.get('Output', 'output_dir', ...)` 从配置文件读取
3. **端口默认值不一致**: `web/webapp.py:1020` (23455) vs `web/webapp.py:66` (12345) vs `config_manager.py:47` (12345)
   - 统一为从 config 读取，消除环境变量回退分支的冲突值

### P2 — 一般建议
4. 全局清理行尾空格（W293 — 可用 `sed -i 's/[[:space:]]*$//'` 批量修复）
5. 清理所有未使用的 import
6. web/webapp.py 中 11 处未使用导入和变量清理
7. 考虑用 `logging` 模块替代 `print()` 实现 logger 方法群集

### P3 — 建议改进
8. `web/webapp.py:282` 模块级 import 放文件中部（E402），可重构到顶部
9. 测试文件中大量未使用导入（55+处），建议启用 `pyflakes` 在 CI 中检查
10. `web/webapp.py` 函数定义间距不规范（67处E302/E305）
