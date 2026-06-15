# 加密密钥显示与登录后提示修改 — 架构设计方案

> 编制：architect-expert  
> 日期：2026-06-15  
> 关联提交：`feat: 全量配置SQLite化 + 敏感字段加密`

---

## 目录

1. [Part 1: 密钥生成与日志显示](#part-1-密钥生成与日志显示)
2. [Part 2: Docker日志显示](#part-2-docker日志显示)
3. [Part 3: 登录后提示修改密钥](#part-3-登录后提示修改密钥)
4. [Part 4: 密钥轮换（修改）](#part-4-密钥轮换修改)
5. [Part 5: 变更影响清单](#part-5-变更影响清单)
6. [Part 6: 安全注意事项](#part-6-安全注意事项)

---

## Part 1: 密钥生成与日志显示

### 现状分析

当前 `web/crypto_utils.py` 的密钥获取逻辑：

```python
def _get_fernet() -> Fernet:
    env_key = os.environ.get('CONFIG_ENCRYPT_KEY', '')
    if env_key:
        # 从环境变量读取，hex解码
        key_bytes = bytes.fromhex(env_key)
        ...
    else:
        # 回退到内置固定密钥——安全风险！
        fernet_key = _derive_fallback_key()
```

**问题**：未设置 `CONFIG_ENCRYPT_KEY` 时使用硬编码的 `_FALLBACK_KEY`，所有实例共享同一密钥，加密形同虚设。

### 改造方案

#### 1.1 检测时机

在 `web/webapp.py` 的 `lifespan` startup 函数中，**在 crypto_utils 被首次调用之前**完成密钥检测与生成。

#### 1.2 流程图

```
lifespan startup 开始
    │
    ├─ 检查 os.environ.get('CONFIG_ENCRYPT_KEY')
    │     │
    │     ├─ 已设置 → 跳过生成，标记 has_custom_key=True
    │     │
    │     └─ 未设置 → 生成随机32字节密钥
    │           ├─ base64(urlsafe)编码 → 构造 CONFIG_ENCRYPT_KEY=xxx 格式
    │           ├─ 写入 SQLite app_config (key='System.encrypt_key')
    │           ├─ 设置 os.environ['CONFIG_ENCRYPT_KEY'] = xxx
    │           └─ 标记 has_custom_key=False
    │
    ├─ 打印显眼日志（带边框的钥匙箱图样）
    │
    └─ 继续后续初始化…
```

#### 1.3 关键代码设计

**在 `web/webapp.py` 的 `lifespan` startup 中新增**：

```python
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """应用生命周期 startup + shutdown"""

    # ═══════════════════════════════════════════════════════
    # 0. 加密密钥检测与自动生成（必须在 crypto_utils 调用前执行）
    # ═══════════════════════════════════════════════════════

    # 🔐 密钥检测状态
    encrypt_key_info = _init_encryption_key()

    # ── 原有 startup 逻辑继续 ──────────────────
    import secrets
    import string
    # ...（后续代码不变）
```

**新增辅助函数 `_init_encryption_key()`**：

```python
def _init_encryption_key() -> dict:
    """检测并初始化加密密钥。返回 {'has_custom_key': bool, 'key_display': str}"""
    import base64
    import secrets
    import logging

    logger = logging.getLogger('web.webapp')

    has_custom_key = True
    key_value = os.environ.get('CONFIG_ENCRYPT_KEY', '')

    if not key_value:
        # 自动生成 32 字节（256位）随机密钥
        logger.warning("⚠️  CONFIG_ENCRYPT_KEY 未设置，正在生成随机加密密钥...")
        raw_key = secrets.token_bytes(32)
        key_b64 = base64.urlsafe_b64encode(raw_key).decode('ascii')
        key_value = key_b64
        has_custom_key = False

        # 写入 SQLite 持久化（供检索/审计）
        try:
            from . import models
            models.set_app_config('System.encrypt_key', key_value)
            logger.info("加密密钥已持久化到 SQLite (System.encrypt_key)")
        except Exception as e:
            logger.warning(f"密钥持久化失败（不影响运行）: {e}")

        # 设置到环境变量，供 crypto_utils._get_fernet() 使用
        os.environ['CONFIG_ENCRYPT_KEY'] = key_value
    else:
        logger.info("CONFIG_ENCRYPT_KEY 已从环境变量读取")

    # ── 显眼日志输出 ──────────────────────────
    _log_encrypt_key(key_value, has_custom_key)

    return {'has_custom_key': has_custom_key, 'key_value': key_value}
```

**新增日志格式化函数 `_log_encrypt_key()`**：

```python
def _log_encrypt_key(key_value: str, has_custom_key: bool):
    """以醒目边框格式打印密钥（方便从 docker logs 中复制）"""
    logger_func = logger.info if has_custom_key else logger.warning

    box_width = 66
    key_line = f"  CONFIG_ENCRYPT_KEY={key_value}  "
    # 确保不超过边框宽度
    if len(key_line) > box_width - 4:  # 4 for ║  + padding
        key_line = key_line[:box_width - 7] + "…  "

    title = "  加密密钥（请保存至安全位置，丢失后加密数据无法恢复）" if not has_custom_key     else "  加密密钥（用户自定义，启动已读取）"

    logger_func("")
    logger_func("╔" + "═" * (box_width - 2) + "╗")
    logger_func("║" + title.ljust(box_width - 4) + "  ║")
    logger_func("║" + " " * (box_width - 4) + "  ║")
    logger_func("║" + key_line.ljust(box_width - 4) + "  ║")
    logger_func("╚" + "═" * (box_width - 2) + "╝")
    logger_func("")

    if not has_custom_key:
        logger_func(
            "💡 建议：设置环境变量 CONFIG_ENCRYPT_KEY 使用自定义密钥\n"
            "   例如: docker run -e CONFIG_ENCRYPT_KEY=<您的密钥> ..."
        )
```

#### 1.4 密钥格式说明

| 属性 | 值 |
|------|-----|
| 长度 | 32字节（256位） |
| 编码 | URL-safe Base64 |
| 示例 | `dGhpcyBpcyBhIDMyLWJ5dGUgcmFuZG9tIGtleSBmb3IgZGVtby4=` |
| 生成方式 | `secrets.token_bytes(32)` → `base64.urlsafe_b64encode()` |

> **注意**：Fernet 本身要求 32 字节 base64 编码的密钥。当前 `crypto_utils.py` 已将环境变量值通过 `bytes.fromhex()` 解码，再通过 PBKDF2 派生成 Fernet 密钥。为保证兼容性，需调整 `_get_fernet()` 使其能同时识别 **hex 编码** 和 **base64 编码** 的密钥。

#### 1.5 对 `crypto_utils.py` 的调整

在 `_get_fernet()` 中增加自动检测密钥编码格式的逻辑：

```python
def _get_fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance

    env_key = os.environ.get('CONFIG_ENCRYPT_KEY', '')
    if env_key:
        try:
            # 尝试 base64 解码（自动生成的密钥格式）
            key_bytes = base64.urlsafe_b64decode(env_key)
            if len(key_bytes) == 32:
                fernet_key = env_key.encode('ascii')  # 已经是合法的 Fernet key
                _fernet_instance = Fernet(fernet_key)
                return _fernet_instance
        except Exception:
            pass

        try:
            # 兼容旧版 hex 编码
            key_bytes = bytes.fromhex(env_key)
            if len(key_bytes) >= 16:
                if len(key_bytes) != 32:
                    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                                     salt=_SALT, iterations=100000)
                    key_bytes = kdf.derive(key_bytes)
                fernet_key = base64.urlsafe_b64encode(key_bytes)
                _fernet_instance = Fernet(fernet_key)
                return _fernet_instance
        except Exception as e:
            logger.error(f"CONFIG_ENCRYPT_KEY 格式解析失败: {e}，将使用内置密钥")

    # 回退逻辑——lifespan 已保证此时环境变量必然设置
    logger.warning("CONFIG_ENCRYPT_KEY 未设置，使用内置固定密钥（仅开发环境）")
    fernet_key = _derive_fallback_key()
    _fernet_instance = Fernet(fernet_key)
    return _fernet_instance
```

---

## Part 2: Docker日志显示

### 方案

由于 Part 1 的日志打印发生在 Python `lifespan` startup 中，Docker 容器启动时标准输出会被 `docker logs` 捕获，密钥日志自然可见。

### 额外增强：start.sh 提示

在 `start.sh` 的 `main()` 函数中，Nginx 和 Web 服务启动成功之后、进入守护模式之前，增加以下提示：

```bash
# 在 start.sh main() 中，setup_nginx+Web启动之后、monitor_processes之前
echo ""
echo "============================================"
echo "  Live Source Manager 启动中..."
echo "  查看加密密钥: docker logs <container> | grep CONFIG_ENCRYPT_KEY"
echo "  首次运行自动生成随机密钥，建议设置自定义环境变量"
echo "  设置方式: docker run -e CONFIG_ENCRYPT_KEY=your_key_here ..."
echo "============================================"
echo ""
```

这样即使容器已经启动，用户也可以通过一条命令快速定位密钥。

---

## Part 3: 登录后提示修改密钥

### 3.1 API 设计

#### `GET /api/auth/encrypt-key-status`

```python
@app.get('/api/auth/encrypt-key-status')
async def api_encrypt_key_status(current_user: dict = Depends(get_current_user)):
    """返回加密密钥是否为用户自定义"""
    # 检测方式：lifespan 启动时将状态写入 app_config
    custom = models.get_app_config('System.encrypt_key_custom')
    return {'has_custom_key': custom == '1'}
```

**实现说明**：在 `lifespan` 中初始化密钥时，同步写入 `models.set_app_config('System.encrypt_key_custom', '1' if has_custom_key else '0')`。

#### `POST /api/auth/login` 返回体增强

在现有返回体中增加 `encrypt_key_hint` 字段：

```python
@app.post('/api/auth/login')
async def api_login(request: Request, username: str = Form(...), password: str = Form(...)):
    # ... 原有认证逻辑不变 ...
    
    # 新增：判断是否建议修改密钥
    custom_status = models.get_app_config('System.encrypt_key_custom')
    encrypt_key_hint = (custom_status == '0')  # 自动生成密钥 → True
    
    resp = JSONResponse({
        'status': 'ok',
        'role': user['role'],
        'encrypt_key_hint': encrypt_key_hint,        # ← 新增
    })
    resp.set_cookie(...)
    return resp
```

### 3.2 前端实现

#### 方案选择

项目使用 **HTMX + Jinja2 模板**，没有独立的前端框架。因此提示条通过以下方式实现：

**在 `base.html` 中嵌入 JavaScript 逻辑**（登录后检查 `encrypt_key_hint`）：

```javascript
// 在 base.html 的 <script> 中追加
document.addEventListener('DOMContentLoaded', function() {
    // 检查是否已关闭提示
    if (localStorage.getItem('encrypt_key_dismissed')) return;
    
    // 从登录响应中检测 encrypt_key_hint
    // 登录使用 htmx 的 hx-post，响应 JSON 在 htmx 处理中不可直接访问
    // 方案：通过 /api/auth/encrypt-key-status 接口获取
    fetch('/api/auth/encrypt-key-status', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
            if (!data.has_custom_key) {
                showEncryptKeyBanner();
            }
        })
        .catch(() => {});
});

function showEncryptKeyBanner() {
    const banner = document.createElement('div');
    banner.id = 'encrypt-key-banner';
    banner.style.cssText = `
        background: #fef3c7; border: 1px solid #fde68a;
        border-radius: 8px; padding: 12px 16px; margin-bottom: 16px;
        display: flex; align-items: center; justify-content: space-between;
        gap: 12px; font-size: 14px;
    `;
    banner.innerHTML = `
        <div style="display:flex;align-items:center;gap:8px;flex:1">
            <span style="font-size:20px">🔐</span>
            <span>
                系统使用<strong>自动生成的加密密钥</strong>，
                建议设置自定义环境变量 <code>CONFIG_ENCRYPT_KEY</code> 以增强安全性。
                <a href="/config" style="color:var(--primary);text-decoration:underline">前往配置页</a>
            </span>
        </div>
        <button onclick="dismissEncryptKeyBanner()" style="
            background:none;border:none;cursor:pointer;font-size:18px;padding:4px;
            color:#92400e;flex-shrink:0;
        " title="我知道了，不再提示">✕</button>
    `;
    
    // 插入到 .content-body 最前面
    const content = document.querySelector('.content-body');
    if (content) {
        content.insertBefore(banner, content.firstChild);
    }
}

function dismissEncryptKeyBanner() {
    localStorage.setItem('encrypt_key_dismissed', '1');
    const banner = document.getElementById('encrypt-key-banner');
    if (banner) banner.remove();
}
```

**说明**：虽然没有使用登录 API 返回的 `encrypt_key_hint` 直接触发（因为 HTMX 表单提交不暴露 JSON 给页面 JS），但通过 `GET /api/auth/encrypt-key-status` 在页面加载后异步查询可以实现同样效果。

### 3.3 行为总结

| 场景 | 行为 |
|------|------|
| 首次登录（自动生成密钥） | 页面顶部显示黄色提示条 + localStorage 记录"已关闭" |
| 关闭提示后 | 不再显示（localStorage 键 `encrypt_key_dismissed`） |
| 用户已设置 `CONFIG_ENCRYPT_KEY` | `has_custom_key=True`，无提示 |
| 登录API返回 | `encrypt_key_hint` 字段已存在但前端暂未直接使用 |

---

## Part 4: 密钥轮换（修改）

### 4.1 API 设计

```python
@app.put('/api/auth/encrypt-key')
async def api_rotate_encrypt_key(
    data: dict,
    request: Request,
    current_user: dict = Depends(require_admin)
):
    """管理员修改加密密钥——重新加密所有已存储的敏感配置"""
    from web.crypto_utils import (
        encrypt_value, decrypt_value,
        is_sensitive_key, is_encrypted,
        _get_fernet, SENSITIVE_KEYS
    )
    from cryptography.fernet import Fernet, InvalidToken

    new_key = (data.get('new_key') or '').strip()
    if not new_key:
        raise HTTPException(status_code=400, detail="新密钥不能为空")
    if len(new_key) < 16:
        raise HTTPException(status_code=400, detail="新密钥长度至少需要16字节（建议32字节）")

    # 1. 备份旧密钥
    old_key_value = os.environ.get('CONFIG_ENCRYPT_KEY', '')

    # 2. 验证新密钥是否可用
    try:
        # 尝试 base64
        test_bytes = base64.urlsafe_b64decode(new_key)
    except Exception:
        try:
            # 尝试 hex
            test_bytes = bytes.fromhex(new_key)
        except Exception:
            raise HTTPException(status_code=400, detail="新密钥格式无效（需 base64 或 hex 编码）")

    if len(test_bytes) < 16:
        raise HTTPException(status_code=400, detail="新密钥解码后不足16字节")

    # 3. 获取所有已加密的 app_config 值
    conn = models.get_conn()
    rows = conn.execute(
        "SELECT key, value FROM app_config WHERE key IN ({}) AND value LIKE 'ENC:%'".format(
            ','.join('?' for _ in SENSITIVE_KEYS)
        ),
        list(SENSITIVE_KEYS)
    ).fetchall()

    if not rows:
        logger.info("无已加密配置需要轮换")

    # 4. 逐条解密 → 暂存明文
    plaintexts = {}
    for row in rows:
        try:
            plain = decrypt_value(row['value'])
            plaintexts[row['key']] = plain
        except InvalidToken:
            raise HTTPException(
                status_code=500,
                detail=f"配置 {row['key']} 解密失败（密钥不匹配），轮换已中止"
            )

    # 5. 替换环境变量为新密钥
    os.environ['CONFIG_ENCRYPT_KEY'] = new_key

    # 6. 重置 Fernet 实例，用新密钥重新加密
    import web.crypto_utils as cu
    cu._fernet_instance = None  # 强制下次调用时重新创建
    # 触发新实例创建
    cu._get_fernet()

    # 7. 用新密钥重新加密并写入
    for config_key, plaintext in plaintexts.items():
        new_encrypted = encrypt_value(plaintext)
        models.set_app_config(config_key, new_encrypted)

    # 8. 更新 SQLite 中的 System.encrypt_key
    models.set_app_config('System.encrypt_key', new_key)
    # 标记为自定义
    models.set_app_config('System.encrypt_key_custom', '1')

    # 9. 记录审计日志
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='encrypt_key_rotate', target='',
        detail='加密密钥已轮换（{} 条配置重新加密）'.format(len(plaintexts)),
        ip_address=request.client.host if request.client else '',
    )

    logger.info(f"加密密钥轮换完成，{len(plaintexts)} 条配置已重新加密")

    return {
        'status': 'ok',
        'message': f'密钥轮换完成，{len(plaintexts)} 条敏感配置已重新加密',
        're_encrypted_count': len(plaintexts),
    }
```

### 4.2 安全约束

| 项 | 说明 |
|----|------|
| 权限 | 仅 `admin` 角色可操作 |
| 新密钥长度 | ≥16 字节（推荐 32 字节） |
| 格式 | 支持 base64(urlsafe) 或 hex 编码 |
| 事务语义 | 单条配置失败时会抛出 500，**不自动回滚**已写入的新加密值（见注意事项） |
| 幂等性 | 全量重新加密，不依赖旧密钥状态 |

---

## Part 5: 变更影响清单

### 5.1 文件修改汇总

| # | 文件 | 修改内容 | 影响范围 |
|---|------|----------|----------|
| 1 | `web/webapp.py` | `lifespan` 中新增 `_init_encryption_key()` + `_log_encrypt_key()`；新增 API `GET /api/auth/encrypt-key-status` 和 `PUT /api/auth/encrypt-key`；登录API增加 `encrypt_key_hint` 字段 | 核心启动流、认证API、新API端点 |
| 2 | `web/crypto_utils.py` | `_get_fernet()` 增加 base64 密钥格式检测（自动生成密钥格式）；保持 hex 向后兼容；新增 `reload_fernet()` 函数 | 加密/解密核心逻辑 |
| 3 | `web/auth.py` | 无直接修改（`encrypt_key_hint` 在 `webapp.py` 中处理） | — |
| 4 | `web/templates/base.html` | 在 `<script>` 中嵌入密钥提示 banner 的 JS 代码（含 localStorage 关闭逻辑） | 所有登录后的页面 |
| 5 | `web/templates/login.html` | 无修改（登录API返回的 `encrypt_key_hint` 字段由 htmx 自动传递，前端另查 `/api/auth/encrypt-key-status`） | — |
| 6 | `web/static/js/app.js` | 新增 `fetchEncryptKeyStatus()` 和 `dismissEncryptKeyBanner()` 等前端函数 | 前端交互 |
| 7 | `start.sh` | 在启动成功提示中增加密钥查看指引 | Docker 启动日志 |
| 8 | `Dockerfile` | 无修改（start.sh 修改已涵盖） | — |

### 5.2 新增文件

无。所有改动均在现有文件内完成。

### 5.3 数据库变更

| 变更 | SQLite 表/键 | 说明 |
|------|-------------|------|
| 新配置项 | `app_config` 表 `key='System.encrypt_key'` | 存储自动生成的密钥（首次运行） |
| 新配置项 | `app_config` 表 `key='System.encrypt_key_custom'` | 存储 `'1'` 或 `'0'` 表示是否用户自定义 |

### 5.4 新增配置环境变量

无新增。复用了已有的 `CONFIG_ENCRYPT_KEY`。

### 5.5 新增 API

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| `GET` | `/api/auth/encrypt-key-status` | 返回 `{has_custom_key: bool}` | 登录用户 |
| `PUT` | `/api/auth/encrypt-key` | 修改加密密钥并重新加密配置 | 仅管理员 |

### 5.6 修改 API

| 方法 | 路径 | 变更 |
|------|------|------|
| `POST` | `/api/auth/login` | 返回体新增 `encrypt_key_hint: bool` |

---

## Part 6: 安全注意事项

### 6.1 密钥丢失风险

```
⚠️  关键警告（需在日志和提示中强调）：
┌─────────────────────────────────────────────────────┐
│  加密密钥一旦丢失，已加密的敏感配置将永久无法解密！    │
│  - SQLite app_config 中 Network.proxy_password、    │
│    GitHub.api_token 等 ENC: 前缀数据变为不可恢复      │
│  - 即使有完整的 SQLite 文件备份也无济于事              │
│  - 必须使用完全相同的 CONFIG_ENCRYPT_KEY 才能解密     │
└─────────────────────────────────────────────────────┘
```

### 6.2 密钥轮换中断风险

密钥轮换操作设计上存在**原子性缺陷**（无法跨 SQLite 行实现事务）：

1. 先解密所有配置 → 暂存明文 → 切换密钥 → 新密钥加密 → 写入
2. 若步骤"写入"过程中进程崩溃，部分配置使用**新密钥**、部分使用**旧密钥**
3. **建议**：轮换前自动备份当前密钥到 `System.encrypt_key_backup`

```python
# 在轮换开始时备份旧密钥
models.set_app_config('System.encrypt_key_backup', old_key_value)
models.set_app_config('System.encrypt_key_custom', '1')
```

若轮换后重启应用报解密错误，管理员可：
1. 回退 `CONFIG_ENCRYPT_KEY` 为备份值
2. 重新轮换

### 6.3 密钥存储策略建议

| 存储位置 | 用途 | 安全性 |
|----------|------|--------|
| 环境变量 `CONFIG_ENCRYPT_KEY` | **生产推荐**，通过 `docker run -e` 或 Kubernetes Secret | 高 |
| SQLite `System.encrypt_key` | 首次运行自动生成时的**持久化备份** | 中（SQLite 文件本身是加密保护的？否！） |
| 内置固定密钥 | **仅开发环境**回退 | 低（所有实例相同） |

> **重要**：SQLite 中存储的密钥本身未被加密（"谁加密密钥？"的经典问题）。建议生产环境中**始终通过环境变量传递**，SQLite 中的备份仅作为辅助查看手段。

### 6.4 Docker 日志安全

密钥在启动日志中输出，意味着：
- `docker logs` 命令可以查看到密钥
- 容器重启日志仍会重新显示
- 对已授权的运维人员可接受，但需注意日志**不**应被公开
- 可考虑增加 `ENCRYPT_KEY_LOG_DISABLE=1` 环境变量来禁用日志显示（预留）

### 6.5 密钥强度

| 参数 | 值 |
|------|-----|
| 算法 | `secrets.token_bytes(32)` → URL-safe Base64 |
| 熵 | 256 位 |
| 预期安全性 | 暴力穷举不可行（2^256 空间） |
| 存储格式 | 48 字符 base64 字符串 |

### 6.6 向后兼容

- **hex 格式密钥**：旧版用户设置的 hex 编码 `CONFIG_ENCRYPT_KEY` 继续支持
- **base64 格式密钥**：新自动生成的格式
- 两者在 `_get_fernet()` 中通过 try-except 自动检测
- 已加密的 `ENC:` 前缀数据不受影响

---

## 附：实现顺序建议

```
实现顺序（按依赖关系）：

Step 1: web/crypto_utils.py — 支持 base64 密钥格式 + reload_fernet()
Step 2: web/webapp.py — lifespan 中 _init_encryption_key() + 日志打印
Step 3: web/webapp.py — 新增 encrypt-key-status API + 登录 API 增强
Step 4: web/webapp.py — 新增 encrypt-key 轮换 API
Step 5: web/templates/base.html — 前端提示条脚本
Step 6: start.sh — Docker 日志提示
Step 7: 测试 & git commit

测试要点：
  a) 未设置 CONFIG_ENCRYPT_KEY → 自动生成+日志打印
  b) 设置 CONFIG_ENCRYPT_KEY(hex) → 正常解密已存数据
  c) 设置 CONFIG_ENCRYPT_KEY(base64) → 正常解密已存数据
  d) 登录API返回 encrypt_key_hint
  e) 密钥轮换API → 旧加密数据可重新加密+解密
  f) Docker 容器中 docker logs | grep CONFIG_ENCRYPT_KEY 可见
```
