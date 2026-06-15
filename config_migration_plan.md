# 配置迁移方案 — SQLite + 首次运行初始化

> 版本: v1.0  
> 日期: 2026-06-15  
> 设计者: Architect Expert  
> 涉及Commit: 2f943c9

---

## 目录

1. [PART 1 — SQLite 配置表设计](#part-1--sqlite-配置表设计)
2. [PART 2 — 配置读写接口重构](#part-2--配置读写接口重构)
3. [PART 3 — 首次运行初始化（first-run detection）](#part-3--首次运行初始化first-run-detection)
4. [PART 4 — 变更影响分析](#part-4--变更影响分析)
5. [PART 5 — 迁移数据安全](#part-5--迁移数据安全)
6. [附录 — 实现顺序与测试策略](#附录--实现顺序与测试策略)

---

## PART 1 — SQLite 配置表设计

### 1.1 表结构

在 `web/models.py` 中新增 `app_config` 表。该表独立于现有的 `users` / `audit_logs` / `sessions` 表，使用**点分命名**的 key-value 模式存储配置。

```sql
CREATE TABLE IF NOT EXISTS app_config (
    key         TEXT PRIMARY KEY,         -- 点分命名: "Sources.local_dirs"
    value       TEXT NOT NULL,            -- 字符串值
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**点分命名规则**:

| INI Section | INI Key | SQLite key |
|-------------|---------|------------|
| `[Sources]` | `local_dirs` | `Sources.local_dirs` |
| `[Network]` | `proxy_enabled` | `Network.proxy_enabled` |
| `[Logging]` | `level` | `Logging.level` |

多行值（如 `online_urls`、`github_sources`）保留原始字符串格式原样存入 `value` 字段。

### 1.2 迁移函数

**`web/models.py` 新增的函数**:

| 函数 | 用途 |
|------|------|
| `get_app_config(key: str) -> Optional[str]` | 按点分key读取单个配置项 |
| `set_app_config(key: str, value: str)` | 写入/覆盖单个配置项（使用 INSERT OR REPLACE） |
| `get_all_config() -> Dict[str, Dict[str, str]]` | 返回 `{section: {key: value}}` 格式的全量配置（兼容现有 `read_config()` 返回值格式） |
| `import_from_ini_file(path: str) -> int` | 将 `config.ini` 文件导入 SQLite `app_config` 表，返回导入的记录数 |
| `has_app_config_data() -> bool` | 检查 `app_config` 表是否有数据，用于首次运行判断 |
| `delete_app_config_by_section(section: str)` | 删除指定 section 下的所有 key（支持部分更新） |

### 1.3 建表时机

在 `init_db()` 中追加建表 DDL。现有 `init_db()` 使用 `conn.executescript()` 批量执行 DDL，直接将新表定义追加到末尾即可。

```python
# 在 init_db() 的 conn.executescript("""...""") 末尾追加:
CREATE TABLE IF NOT EXISTS app_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 1.4 写锁保护

`app_config` 表的写入也通过 `_execute()` 函数（现有 `_write_lock` 锁）保护，无需额外锁机制。该函数已经具备重试 + WAL 模式能力，写入安全。

---

## PART 2 — 配置读写接口重构

### 2.1 整体架构

重构前:

```
前端 PUT /api/config → webapp.write_config() → 直接写 config.ini (文件锁)
前端 GET /api/config  → webapp.read_config()  → 直接读 config.ini
app/main.py           → Config(config_path)   → 读 config.ini
```

重构后:

```
前端 PUT /api/config → webapp.write_config() → models.set_app_config() → SQLite
                     └─ 同时写入 config.ini (可选同步)
前端 GET /api/config  → webapp.read_config()  → models.get_all_config() → SQLite
                     └─ 后备读取 config.ini (SQLite无数据时)
app/main.py           → Config(config_path)   → 读 config.ini (保持不动)
```

### 2.2 `web/webapp.py` 修改方案

**2.2.1 `read_config()` — 改为 SQLite 优先**

```python
def read_config() -> Dict[str, Dict[str, str]]:
    """读取全量配置，优先从 SQLite 读取，回退到 config.ini"""
    sqldata = models.get_all_config()
    if sqldata:
        return sqldata
    # 回退: config.ini
    cp = _read_raw()
    result = {}
    for section in cp.sections():
        result[section] = dict(cp.items(section))
    return result
```

**2.2.2 `read_section()` — 追加 SQLite 查询**

```python
def read_section(section: str) -> Dict[str, str]:
    """读取指定段配置，优先 SQLite"""
    sqldata = models.get_all_config()
    if sqldata and section in sqldata:
        return sqldata[section]
    # 回退
    cp = _read_raw()
    if section in cp:
        return dict(cp.items(section))
    return {}
```

**2.2.3 `write_config()` — 写入 SQLite + 同步写入 config.ini**

```python
def write_config(data: Dict[str, Dict[str, str]]) -> Tuple[bool, str]:
    """
    写入配置：写入 SQLite + 同步写入 config.ini 作为备份/兼容源
    """
    success, msg = _validate_and_coerce_config(data)
    if not success:
        return False, msg

    try:
        # 1. 写入 SQLite
        for section, fields in data.items():
            for key, value in fields.items():
                models.set_app_config(f"{section}.{key}", str(value))

        # 2. 同步写入 config.ini（保持向后兼容）
        _sync_sqlite_to_ini()

        return True, "配置已保存（SQLite + config.ini）"
    except Exception as e:
        return False, f"写入失败: {e}"
```

**`_sync_sqlite_to_ini()`** — 新辅助函数:

```python
def _sync_sqlite_to_ini():
    """将 SQLite app_config 数据同步写回 config.ini（原子写入 + 文件锁）"""
    sqldata = models.get_all_config()
    if not sqldata:
        return
    cp = configparser.ConfigParser()
    for section, fields in sqldata.items():
        cp[section] = fields
    # 原子写入（复用原有 CONFIG_PATH + 文件锁逻辑）
    _write_ini_atomic(cp)
```

其中 `_write_ini_atomic(cp)` 复用现有 `write_config()` 中步骤 4–6 的临时文件 + rename 逻辑，提取为独立函数。

**2.2.4 `_write_ini_atomic()` — 提取独立函数**

从现有 `write_config()` 中提取原子写入 ini 文件的逻辑:

```python
def _write_ini_atomic(cp: configparser.ConfigParser) -> None:
    """原子写入 config.ini（带备份）"""
    config_dir = os.path.dirname(CONFIG_PATH)
    os.makedirs(config_dir, exist_ok=True)
    lock_path = CONFIG_PATH + '.lock'

    with _write_lock:
        with open(lock_path, 'w') as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            bak_path = CONFIG_PATH + '.bak'
            if os.path.exists(CONFIG_PATH):
                shutil.copy2(CONFIG_PATH, bak_path)
            fd, tmp_path = tempfile.mkstemp(dir=config_dir, prefix='config_', suffix='.tmp')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as tmpf:
                    cp.write(tmpf)
                    tmpf.flush()
                    os.fsync(fd)
                os.rename(tmp_path, CONFIG_PATH)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
            fcntl.flock(lock_f, fcntl.LOCK_UN)
```

重构后的 `write_config()` 简化:

```python
def write_config(data: Dict[str, Dict[str, str]]) -> Tuple[bool, str]:
    """写入配置（SQLite 为主，同步回写 config.ini 做备份）"""
    config_dir = os.path.dirname(CONFIG_PATH)
    os.makedirs(config_dir, exist_ok=True)

    with _write_lock:
        try:
            # 校验
            for section, fields in data.items():
                for key, value in fields.items():
                    schema = SECTION_SCHEMA.get(section, {})
                    if key in schema:
                        _, err = validate_and_coerce(section, key, value, schema[key])
                        if err:
                            return False, f"[{section}] {key}: {err}"

            # 写入 SQLite
            for section, fields in data.items():
                for key, value in fields.items():
                    models.set_app_config(f"{section}.{key}", str(value))

            # 同步写入 config.ini（无校验分支）
            cp = _read_raw()
            for section, fields in data.items():
                if section not in cp:
                    cp.add_section(section)
                for key, value in fields.items():
                    cp.set(section, key, str(value))
            _write_ini_atomic(cp)

            return True, "配置已保存"

        except Exception as e:
            return False, f"写入失败: {e}"
```

### 2.3 `app/config_manager.py` 兼容方案

**Config 类保持不动**。理由:

1. `app/config_manager.py` 的 `Config` 类被 `app/main.py`、`app/source_manager.py`、`app/stream_tester.py` 等后台任务组件使用，它们部署在与 Web 进程**相同或不同的容器**中，不一定能访问 SQLite。
2. `Config` 类读 `config.ini` 的逻辑是静态的 `configparser.ConfigParser` 直接解析，没有文件锁，效率高。
3. **同步机制**: 每次 `write_config()` 写入 SQLite 后也会回写 `config.ini`，因此 `Config` 类始终能通过 `config.ini` 获取到最新配置。
4. 如果未来需要 `Config` 类支持 SQLite，可增加 `db_path` 构造参数，但**不在本方案范围**。

**可选增强**: 在 `Config` 类中增加一个 `use_fallback_db` 标记，如果未来后台任务也需要 SQLite 读取：

```python
class Config:
    def __init__(self, config_path: str = ..., reload_interval: int = 60, db_path: str = None):
        self.db_path = db_path  # None 表示纯文件模式
        ...
```

但**本方案不做此变动**,保持最小变更。

### 2.4 API 响应格式兼容

- `GET /api/config` → 调用 `read_config()` → 返回 `{section: {key: value}}`。
- `PUT /api/config` → 调用 `write_config()` → 返回 `{status, message}`。
- `GET /api/config/{section}` → 调用 `read_section()` → 返回 `{key: value}`。

**返回值格式与重构前完全一致**。前端无需修改。

---

## PART 3 — 首次运行初始化（first-run detection）

### 3.1 初始化流程

在 `web/webapp.py` 的 `lifespan` startup 中，按以下顺序执行:

```
lifespan startup 开始
│
├─ 1. 初始化数据库 (models.init_db) —— 已有
│     └─ 建表（含新 app_config 表）
│
├─ 2. 检查 config/config.ini 是否存在
│     ├─ 不存在 → 调用 Config.create_default_config() 生成默认
│     └─ 存在   → 跳过
│
├─ 3. 检查 SQLite app_config 表是否有数据
│     ├─ 无数据 → 调用 models.import_from_ini_file(CONFIG_PATH) 导入
│     │         └─ 从 config.ini 导入到 SQLite
│     └─ 有数据 → 跳过
│
├─ 4. 检查 config/channel_rules.yml 是否存在
│     ├─ 不存在 → 生成默认 channel_rules.yml
│     └─ 存在   → 跳过
│
└─ ...继续后续初始化（清理审计日志等）
```

### 3.2 实现代码（lifespan 新增部分）

```python
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    # ── 原有: 数据库初始化 ──
    import secrets, string
    admin_pw = os.environ.get('WEB_ADMIN_PASSWORD') or \
        ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    viewer_pw = os.environ.get('WEB_VIEWER_PASSWORD') or \
        ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    await asyncio.to_thread(models.init_db, admin_password=admin_pw, viewer_password=viewer_pw)

    # ── 新增: 首次运行初始化 ──
    first_run_detected = False

    # 2. 检查 config.ini
    if not os.path.exists(CONFIG_PATH):
        logger.info("首次运行: config.ini 不存在，创建默认配置")
        from app.config_manager import Config as CfgMgr
        temp_cfg = CfgMgr(CONFIG_PATH)  # 构造函数内调用 create_default_config()
        # 注意: 如果 Config.__init__ 的 autoload 行为已创建文件，则此处自动完成
        # 如果需要显式调用: CfgMgr.create_default_config(CONFIG_PATH)
        # 建议: 提取 static method CfgMgr.create_default_at(path) 供外部调用
        first_run_detected = True

    # 3. 检查 SQLite app_config 是否有数据
    if not models.has_app_config_data():
        logger.info("首次运行: app_config 表无数据，从 config.ini 导入")
        count = models.import_from_ini_file(CONFIG_PATH)
        logger.info(f"已从 {CONFIG_PATH} 导入 {count} 条配置记录到 SQLite")
        first_run_detected = True

    # 4. 检查 channel_rules.yml
    rules_path = os.path.join(PROJECT_ROOT, 'config', 'channel_rules.yml')
    if not os.path.exists(rules_path):
        logger.info("首次运行: channel_rules.yml 不存在，创建默认文件")
        _create_default_channel_rules(rules_path)
        first_run_detected = True

    if first_run_detected:
        logger.info("首次运行初始化完成")
    else:
        logger.info("系统已初始化，跳过首次运行检查")

    # ── 原有: 清理审计日志 ──
    ...
```

### 3.3 默认 channel_rules.yml 生成函数

```python
def _create_default_channel_rules(path: str):
    """生成默认的频道规则 YAML 文件（最小占位）"""
    import yaml
    default_rules = {
        'version': 1,
        'categories': {
            '央视': {'keywords': ['CCTV', '央视', '中央']},
            '卫视': {'keywords': ['卫视', '东南', '东方', '湖南', '浙江']},
        },
        'provinces': {},
        'countries': {}
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(default_rules, f, allow_unicode=True, default_flow_style=False)
    logger.info(f"默认频道规则已创建: {path}")
```

### 3.4 Config.create_default_config() 改为可复用

`app/config_manager.py` 的 `create_default_config()` 目前是实例方法，静态调用不方便。建议增加一个类方法或独立函数:

```python
@staticmethod
def create_default_at(path: str):
    """在指定路径创建默认配置"""
    config = configparser.ConfigParser()
    config['Sources'] = { ... }
    config['Network'] = { ... }
    # ... 保留现有默认值
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        config.write(f)
```

然后在 `Config.__init__` 中调用 `create_default_at` 替换内联创建代码。

---

## PART 4 — 变更影响分析

### 4.1 需修改的文件一览

| # | 文件 | 变更类型 | 变更内容 |
|---|------|---------|---------|
| 1 | `web/models.py` | **新增代码** | 新增 `app_config` 表 DDL、CRUD 函数、import/export/has_data 函数 |
| 2 | `web/webapp.py` | **修改** | 重构 `read_config()/write_config()/read_section()`；新增 `_write_ini_atomic()`；修改 lifespan 增加 first-run 检测；新增 `_create_default_channel_rules()` |
| 3 | `app/config_manager.py` | **小修改** | 提取 `create_default_at(path)` 静态方法；保留原有实例方法不动 |
| 4 | `tests/conftest.py` | **修改** | 新增 `app_config` 表初始化（在 init_db 调用后）或在测试启动时无视（SQLite 首次查询无数据 = 触发 ini 回退，符合预期） |

### 4.2 各文件具体修改详情

#### 4.2.1 `web/models.py`

**新增函数清单**:

```python
# ── app_config CRUD ──

def init_db(admin_password, viewer_password):
    """现有函数 - 在 execscript 末尾追加 app_config 建表 DDL"""
    # ... 已有代码 ...
    # 在 execscript 中追加:
    """
    CREATE TABLE IF NOT EXISTS app_config (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """

def get_app_config(key: str) -> Optional[str]:
    """按点分 key 获取单个配置值（如 'Logging.level'）"""
    conn = get_conn()
    row = conn.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
    return row['value'] if row else None

def set_app_config(key: str, value: str):
    """写入或覆盖配置项"""
    _execute(
        "INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, value)
    )

def get_all_config() -> Dict[str, Dict[str, str]]:
    """获取全量配置，返回 {section: {key: value}} 格式"""
    conn = get_conn()
    rows = conn.execute("SELECT key, value FROM app_config ORDER BY key").fetchall()
    if not rows:
        return {}
    result: Dict[str, Dict[str, str]] = {}
    for row in rows:
        key = row['key']
        parts = key.split('.', 1)
        section = parts[0]
        field = parts[1] if len(parts) > 1 else key
        if section not in result:
            result[section] = {}
        result[section][field] = row['value']
    return result

def has_app_config_data() -> bool:
    """检查 app_config 表是否有数据"""
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) as cnt FROM app_config").fetchone()
    return row['cnt'] > 0

def import_from_ini_file(ini_path: str) -> int:
    """从 config.ini 文件中导入所有配置到 app_config 表，返回导入记录数"""
    import configparser
    cp = configparser.ConfigParser()
    if not os.path.exists(ini_path):
        logger.warning(f"config.ini 不存在: {ini_path}，跳过导入")
        return 0
    cp.read(ini_path, encoding='utf-8')
    count = 0
    for section in cp.sections():
        for key, value in cp.items(section):
            set_app_config(f"{section}.{key}", value)
            count += 1
    logger.info(f"从 {ini_path} 导入 {count} 条配置到 SQLite")
    return count

def delete_app_config_by_section(section: str):
    """删除指定 section 所有 key"""
    _execute("DELETE FROM app_config WHERE key LIKE ?", (f"{section}.%",))
```

#### 4.2.2 `web/webapp.py` 修改

**修改 `read_config()`**:
```python
def read_config() -> Dict[str, Dict[str, str]]:
    """读取全量配置，优先 SQLite"""
    sqldata = models.get_all_config()
    if sqldata:
        return sqldata
    # 回退到 config.ini
    cp = _read_raw()
    return {section: dict(cp.items(section)) for section in cp.sections()}
```

**修改 `read_section()`**:
```python
def read_section(section: str) -> Dict[str, str]:
    """读取指定段配置，优先 SQLite"""
    sqldata = models.get_all_config()
    if sqldata and section in sqldata:
        return sqldata[section]
    cp = _read_raw()
    return dict(cp.items(section)) if section in cp else {}
```

**提取 `_write_ini_atomic()`** (上述 2.2.4 节)。

**重构 `write_config()`** (上述 2.2.4 节)。

**修改 lifespan 函数** (上述 3.2 节)。

**新增 `_create_default_channel_rules()`** (上述 3.3 节)。

#### 4.2.3 `app/config_manager.py` 修改

```python
@staticmethod
def create_default_at(path: str):
    """在指定路径创建默认配置（静态方法，可在外部调用）"""
    config = configparser.ConfigParser()
    config['Sources'] = { ... }   # 同现有 create_default_config() 内容
    config['Network'] = { ... }
    config['HTTPServer'] = { ... }
    config['GitHub'] = { ... }
    config['Testing'] = { ... }
    config['Output'] = { ... }
    config['Logging'] = { ... }
    config['Filter'] = { ... }
    config['UserAgents'] = { ... }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        config.write(f)
```

现有 `create_default_config()` 实例方法改为调用 `create_default_at(self.config_path)`:

```python
def create_default_config(self):
    """创建默认配置文件"""
    Config.create_default_at(self.config_path)
    self.load_config()  # 重新加载
```

#### 4.2.4 `tests/conftest.py` 修改

当前测试已覆写 `models.DATA_DIR` 和 `web.webapp.CONFIG_PATH`，使用临时目录。

- `models.init_db()` 在测试启动时调用，新 `app_config` 建表 DDL 已包含其中。
- 测试文件中 `test_put_config_admin` 使用 `write_config()` 写入配置 → 验证读取。
- 迁移后 `write_config()` 写入 SQLite + 同步回 config.ini。
- 测试验证 `read_config()` 仍返回正确数据 → 测试通过。

**无需修改测试代码**，因为:
1. API 返回值格式不变
2. 测试临时 SQLite 中 `app_config` 表为空 → `read_config()` 触发 ini 回退 → 行为与重构前一致
3. 写入后 `app_config` 表有数据 → 下次 `read_config()` 从 SQLite 读取

### 4.3 无需修改的文件

| 文件 | 原因 |
|------|------|
| `app/main.py` | 使用 `Config` 类读 `config.ini`，不受影响 |
| `app/source_manager.py` | 同 |
| `app/stream_tester.py` | 同 |
| `app/m3u_generator.py` | 同 |
| `web/auth.py` | 只关心 session/csrf，不涉及配置 |
| `frontend templates` | API 格式不变，前端无感知 |
| `app/channel_rules.py` | YAML 文件处理逻辑不变 |

---

## PART 5 — 迁移数据安全

### 5.1 数据不丢失保证

| 场景 | 行为 | 数据安全 |
|------|------|---------|
| 已有 config.ini + 首次启动 | `import_from_ini_file()` 读取写入 SQLite | INI 保留，SQLite 为新副本 |
| 已有 SQLite 数据 | `has_app_config_data()` 返回 True，跳过导入 | 无写操作 |
| config.ini 不存在 | lifespan 调用 `Config.create_default_at()` 创建默认 | 不影响已有 SQLite |
| SQLite 写入失败 | `set_app_config()` 抛异常，`write_config()` 捕获返回错误 | config.ini 由 `_write_ini_atomic()` 写入，完整性由原子 rename 保障 |
| 回退方案 | `read_config()` / `read_section()` 检测 SQLite 无数据时回退 INI | 后台任务一直使用 INI，双轨正常 |

### 5.2 config.ini 保留方案

- **INI 文件始终保留**，即使是迁移完成后也不删除。
- 每次 `write_config()` 写入 SQLite 后，同步回写 INI（作为备份 + Config 类的数据源）。
- 回退路径: 如果 SQLite 的 `app_config` 表为空（数据库损坏、重置等），`read_config()` 自动回退到 INI，系统功能不受影响。

### 5.3 原子写入保证

- `_write_ini_atomic()` 采用**临时文件 + os.rename** 的原子写入模式，替换前的文件备份为 `.bak`。
- SQLite 写入使用 `INSERT OR REPLACE` + WAL 模式 + `_write_lock` 保护，事务由 `_execute()` 内的 `conn.commit()` 完成。

### 5.4 回退/降级方案

如果需要完全回退到纯 INI 模式:
1. 将 `read_config()` 和 `read_section()` 中的 SQLite 优先逻辑注释掉
2. 将 `write_config()` 中的 SQLite 写入逻辑注释掉，恢复为纯 `_read_raw()` + `_write_ini_atomic()`
3. **SQLite 表 `app_config` 的数据不受影响**，可作为冗余保留

### 5.5 写锁简化

重构前:
```
_write_lock (threading.Lock) + fcntl.flock .lock 文件 → 两层保护
```

重构后 SQLite 路径:
```
_write_lock (threading.Lock) → 保护 SQLite 写入
```

`_write_ini_atomic()` 内部仍使用 `fcntl.flock` 保护 INI 写入（因为多个进程可能同时写 INI）。

**总结**: 文件锁的复杂度从主路径（`write_config()` 全量代码）降低到仅 `_write_ini_atomic()` 内部，整体复杂度下降。

---

## 附录 — 实现顺序与测试策略

### 实现顺序（建议）

```
Step 1: web/models.py — 新增 app_config 表 + CRUD 函数
Step 2: app/config_manager.py — 提取 create_default_at() 静态方法
Step 3: web/webapp.py — 重构 read_config() / read_section()
Step 4: web/webapp.py — 提取 _write_ini_atomic()，重构 write_config()
Step 5: web/webapp.py — 修改 lifespan 增加 first-run 检测
Step 6: 运行完整测试套件验证
```

### 测试策略

| 测试场景 | 验证方式 |
|---------|---------|
| **单元**: SQLite CRUD | 新建 `tests/test_app_config.py`，测试 `get/set/get_all/has_data` |
| **单元**: INI 导入 | 使用临时 INI 文件调用 `import_from_ini_file()`，验证 SQLite 数据正确 |
| **集成**: API 读取 | 运行已有 `test_web_api.py` 全部用例，确保 `test_get_config_authenticated` etc 通过 |
| **集成**: API 写入 | `test_put_config_admin` — 写入后验证读取到新值 |
| **集成**: 首次运行 | 清空 app_config 表 → 重启 → 验证已导入 |
| **回归**: 后台任务 | 使用 Config 类读取 config.ini，确保同步写入生效 |
| **回归**: 测试 conftest | 临时目录模式下 app_config 为空 → 回退 INI → 行为不变 |

---

*— End of Plan —*
