# 架构重构总结：app 包模块拆分

## 执行日期
2026-07-03

## 背景
`app/__init__.py` 是一个 6326 行的 God Object（18个类、190个函数），所有核心逻辑塞在单文件中。
任何改动都有连锁风险，不可维护、不可测试。

## 拆分结果

### 拆分前
| 文件 | 行数 | 问题 |
|------|------|------|
| `app/__init__.py` | 6326 | God Object，18个类190个函数 |

### 拆分后
| 模块 | 行数 | 层级 | 职责 |
|------|------|------|------|
| `__init__.py` | 197 | - | re-export 全部公开接口（向后兼容） |
| `exceptions.py` | 287 | L0 | 异常体系 + ErrorStats + catch_exception 装饰器 |
| `logger.py` | 117 | L0 | Logger 类 + setup_logger |
| `utils.py` | 133 | L0 | atomic_write / safe_read_file 等文件工具 |
| `config.py` | 494 | L1 | Config 类（全SQLite版配置管理） |
| `security.py` | 323 | L1 | URL安全审查 + SourceData TypedDict |
| `rules.py` | 912 | L2 | ChannelRules + 7个DB访问函数 |
| `source_manager.py` | 959 | L2 | SourceManager（源采集/解析） |
| `stream_tester.py` | 1297 | L2 | StreamTester（流测试/FFprobe） |
| `m3u_generator.py` | 519 | L3 | M3UGenerator（M3U/TXT生成） |
| `manager.py` | 1156 | L4 | EnhancedLiveSourceManager（协调层）+ main() |

### 依赖方向（无循环）
```
L0: exceptions / logger / utils
 ↓
L1: config / security
 ↓
L2: rules / source_manager / stream_tester
 ↓
L3: m3u_generator
 ↓
L4: manager (EnhancedLiveSourceManager)
 ↓
__init__.py (re-export)
```

## 验证结果
- [x] 全部公开接口导入测试通过（from app import XXX）
- [x] 全部 10 个子模块独立导入测试通过
- [x] web 层导入测试通过（web.models, web.webapp）
- [x] `python -m app` 入口正常
- [x] 外部代码零修改（完全向后兼容）

## 技术决策
1. **__init__.py 保持 re-export**：外部代码 `from app import Config` 等无需修改
2. **循环依赖修复**：`get_source_categories_for_app` / `get_channel_name_mapping_for_app` 从 `from app import` 改为 `from app.rules import`
3. **ChannelRules 运行时导入**：source_manager.py 中从 TYPE_CHECKING 改为直接导入（方法签名需要运行时可用）
4. **全局常量保留**：AIOHTTP_AVAILABLE / TQDM_AVAILABLE / YAML_AVAILABLE 在 __init__.py 中保留

## 已知问题（非本次引入）
- `tests/conftest.py:66` 传了 `viewer_password` 参数，但 `init_db()` 已移除该参数（viewer 用户已移除）
- 这是项目已有 bug，与本次拆分无关

## 后续建议
1. **阶段一：工程基建** — 配置 Ruff + mypy + pre-commit
2. **阶段三：测试体系** — 为拆分后的模块编写单元测试
3. **web/webapp.py 拆分** — 3711行/85路由按领域分组到 web/routes/
4. **清理重复 venv** — .venv (330M) + venv (40M) 合并为一个
