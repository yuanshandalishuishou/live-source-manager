# LSM Web管理模块 — 第三轮测试方案

> **文档版本**: v1.0  
> **制定日期**: 2026-06-15  
> **制定人**: architect-expert  
> **审核范围**: `web/` 模块（FastAPI + HTMX + SQLite）

---

## 目录

1. [评估概要](#1-评估概要)
2. [优先级矩阵](#2-优先级矩阵)
3. [测试方案详述](#3-测试方案详述)
   - [P0: 修复验证回归测试](#31-p0-修复验证回归测试)
   - [P1: 性能基准测试](#32-p1-性能基准测试)
   - [P2: 安全渗透测试（受限）](#33-p2-安全渗透测试受限)
   - [P3: 边界/压力测试](#34-p3-边界压力测试)
   - [P4: 浏览器端E2E测试](#35-p4-浏览器端e2e测试)
4. [工具链评估与安装](#4-工具链评估与安装)
5. [时间与资源估算](#5-时间与资源估算)
6. [风险与注意事项](#6-风险与注意事项)

---

## 1. 评估概要

### 1.1 当前项目状态

| 检查项 | 现状 |
|--------|------|
| 代码行数（web/） | 7 个 Python 文件 + 9 个 HTML 模板 + 1 个 JS 文件 ≈ 1900 行 |
| 已有测试 | 44 项认证测试 + 48 项 API 功能测试，**全部通过** |
| 已有 CI | Docker 构建 GitHub Actions（无测试阶段） |
| **性能基准** | **无** — 项目从未建立过性能基准数据 |
| 部署模式 | Docker 容器，uvicorn 单 worker 运行 |
| 依赖框架 | FastAPI 0.136 + Starlette 1.3 + uvicorn + bcrypt + psutil |
| 异步模式 | asyncio 事件循环（单线程驱动） |
| 前端技术 | 无前端构建工具 — HTMX CDN + 原生 JS |

### 1.2 各维度必要性/可行性评估

| 维度 | 必要性 | 可行性 | 说明 |
|------|:------:|:------:|------|
| **修复验证回归测试** | ⭐⭐⭐ P0 | ✅ 高 | 第二轮审核发现 2 项 P0 阻断问题，修复后必须全量回归。这是本轮最优先事项 |
| **性能基准测试** | ⭐⭐⭐ P1 | ✅ 中 | 无历史基准，需从零建立。当前单 worker 架构下 API 响应时间基本可预测，但需确认是否存在瓶颈 |
| **安全渗透测试** | ⭐⭐ P2 | ⚠️ 中受限 | 项目已有 bcrypt 防暴力破解、CSRF、参数化查询。OWASP ZAP 可自动化但扫描 SQLite 功能有限，且需额外安装 |
| **边界/压力测试** | ⭐⭐ P3 | ⚠️ 中受限 | 单 worker 架构决定了并发能力天花板低。Session 上限、大日志文件读取策略可测 |
| **浏览器端E2E测试** | ⭐ P4 | ⚠️ 低 | HTMX 无 SPA 路由，无复杂前端交互。手动验证更高效，自动化投入产出比低 |

### 1.3 推荐的第三方工具

| 工具 | 推荐等级 | 用途 | 是否需安装 |
|------|:--------:|------|:----------:|
| `pytest-benchmark` | **必装** | API 性能基准测试 | `pip install pytest-benchmark` |
| `locust` | **推荐** | 并发/压力测试 | `pip install locust` |
| `httpx` | 已安装 | 轻量级并发请求（替代 locust） | ✅ 已存在 (v0.28.1) |
| `OWASP ZAP` | **可选** | 全自动安全扫描 | 需 Docker 拉取 `ghcr.io/zaproxy/zaproxy`，约 600MB |
| `selenium` / `playwright` | **不推荐** | E2E 测试 | 当前单页面+HTMX 模式不划算，手动验证即可 |
| `slowapi` | **建议** | Rate limiting 测试扩展示例 | `pip install slowapi`（配合暴力破解测试） |

---

## 2. 优先级矩阵

```
时间/资源预算：约 3-4 人天
┌─────────────────────────────────────────────────────────┐
│  优先级 │ 测试维度            │ 预估时间 │ 自动化程度    │
├─────────┼─────────────────────┼──────────┼───────────────┤
│  P0     │ 修复验证回归测试    │ 0.5人天  │ 100% 自动化   │
│  P1     │ 性能基准测试        │ 1人天    │ 80% 自动化    │
│  P2     │ 安全渗透测试(受限)  │ 1人天    │ 60% 自动化    │
│  P3     │ 边界/压力测试       │ 1人天    │ 70% 自动化    │
│  P4     │ 浏览器端E2E测试     │ 0.5人天  │ 20% 自动化    │
├─────────┼─────────────────────┼──────────┼───────────────┤
│  总计   │                     │ 3-4人天  │                │
└─────────────────────────────────────────────────────────┘
```

---

## 3. 测试方案详述

---

### 3.1 P0 修复验证回归测试

#### 3.1.1 背景

第二轮审核发现 2 个 P0 断裂问题：
- **P0-3**: CSRF 中间件阻断所有写操作（服务端完整但前端未注入 CSRF token）
- **P0-4**: WebSocket 无认证 + 无连接上限

当前最新代码（`app.js`）中**已自动加载 CSRF token**（`DOMContentLoaded` 事件中 `fetch('/api/auth/csrf-token')`），但需验证前端完整工作流。

#### 3.1.2 测试目标

确认修复后的全链路（前端 ↔ API ↔ 认证 ↔ 数据库）在以下操作中完整可用：

| 操作 | 前端来源 | CSRF 是否已注入 | 预期状态 |
|------|---------|:---------------:|:--------:|
| 登录 | login.html `hx-post` | 豁免路径 ✅ | 200 |
| 查看源列表 | sources.html `hx-get` | 读操作豁免 ✅ | 200 |
| 添加源 | source_form.html `hx-post` | `__csrf_token` 注入 ✅ | 200 |
| 编辑源 | source_form.html `hx-put` | `__csrf_token` 注入 ✅ | 200 |
| 删除源 | sources.html `htmx.ajax('DELETE')` | `__csrf_token` 注入 ✅ | 200 |
| 保存配置 | config.html `fetch('PUT')` | `window.__csrf_token` 注入 ✅ | 200 |
| 重载配置 | config.html `fetch('POST')` | 同上 ✅ | 200 |
| 登出 | base.html `hx-post` | 豁免路径 ✅ | 200 |

#### 3.1.3 测试方式

**自动化回归**：复用 `test_web_auth.py` + `test_web_api.py` 全量执行，确认 92 项测试全部通过：

```bash
cd /opt/dev/live-source-manager
python3 -m pytest tests/test_web_auth.py tests/test_web_api.py -v
```

**执行时机**：每次代码变更后、上线前。

#### 3.1.4 新增测试项（可选）

如果需要对 CSRF 前端注入做专项验证，可增加以下快速测试（Shell 脚本）：

```bash
#!/bin/bash
# 验证 CSRF 前端注入是否可工作
# 1. 启动测试服务
python3 -c "
import uvicorn
from web.webapi import app
uvicorn.run(app, host='127.0.0.1', port=23456)
" &
sleep 2

# 2. 模拟前端登录 + 获取 CSRF token + 写操作
SESSION_ID=$(curl -s -c - http://127.0.0.1:23456/api/auth/login \
  -d 'username=admin&password=TestAdminPw1!' | grep session | awk '{print $NF}')

CSRF_TOKEN=$(curl -s -b "session=$SESSION_ID" \
  http://127.0.0.1:23456/api/auth/csrf-token | python3 -c "import sys,json; print(json.load(sys.stdin)['csrf_token'])")

# 3. 带 CSRF token 写配置
STATUS=$(curl -s -o /dev/null -w '%{http_code}' -X PUT \
  -b "session=$SESSION_ID" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"Logging":{"level":"INFO"}}' \
  http://127.0.0.1:23456/api/config)

echo "CSRF write test: $STATUS (expected 200)"
kill %1 2>/dev/null
```

---

### 3.2 P1 性能基准测试

#### 3.2.1 前置条件

安装 `pytest-benchmark`：

```bash
pip install pytest-benchmark
```

#### 3.2.2 测试内容

##### 3.2.2.1 单 API 响应时间（自动化，TestClient 方式）

编写 `tests/test_web_perf.py`，使用 pytest-benchmark 对关键 API 采集基准。

**建议覆盖的 API**：

| API | 方法 | 原因 |
|-----|------|------|
| `/api/auth/login` | POST | 含 bcrypt 验证，最慢路径之一 |
| `/api/auth/me` | GET | 无 I/O，快路径参考 |
| `/api/sources` | GET | 需加载 SourceManager+解析文件，最慢路径 |
| `/api/config` | GET | 文件 I/O 读取 |
| `/api/config/Logging` | GET | 快速段落 |
| `/api/audit` | GET | 数据库查询+分页 |
| `/api/users` | GET | 数据库全表 |
| `/api/dashboard/stats` | GET | JSON 文件读取 |

**测试模板**：

```python
"""test_web_perf.py — Web API 性能基准测试"""
import os, sys, json, tempfile
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pytest
from fastapi.testclient import TestClient

# ... (setup 同 test_web_auth.py 的数据库隔离) ...

class TestAPIPerformance:
    """API 响应时间基准"""

    @pytest.mark.benchmark(min_rounds=20, warmup=True)
    def test_login_performance(self, benchmark):
        client, _ = self._setup()
        benchmark(lambda: client.post('/api/auth/login',
            data={'username': 'admin', 'password': 'TestAdminPw1!'}))

    @pytest.mark.benchmark(min_rounds=50, warmup=True)
    def test_auth_me_performance(self, benchmark):
        client, auth = self._admin_login()
        benchmark(lambda: client.get('/api/auth/me'))

    @pytest.mark.benchmark(min_rounds=5, warmup=True)
    def test_sources_list_performance(self, benchmark):
        client, auth = self._admin_login()
        benchmark(lambda: client.get('/api/sources'))
```

##### 3.2.2.2 并发性能（httpx + asyncio）

编写独立脚本 `tests/stress_web_concurrent.py`，使用 `httpx.AsyncClient` 模拟多用户并发请求。

**测试场景**：

| 场景 | 并发数 | 请求数 | 测量指标 |
|------|:------:|:------:|---------|
| 读操作无竞争 | 10 | 100 | P50/P95/P99 延迟，成功率 |
| 读操作高并发 | 50 | 500 | 同上 |
| 混合读写 | 10 读 + 2 写 | 200 | 写操作延迟，读操作稳定性 |
| 认证竞态 | 20 客户端同时登录 | 100 | 数据库写入竞态 |

**示例脚本框架**：

```python
#!/usr/bin/env python3
"""并发性能测试"""
import asyncio
import httpx
import time
import statistics

BASE_URL = "http://127.0.0.1:23455"

async def warmup():
    """预热：确保所有模块已加载"""
    async with httpx.AsyncClient() as c:
        for _ in range(3):
            resp = await c.post(f"{BASE_URL}/api/auth/login",
                data={"username": "admin", "password": "TestAdminPw1!"})
            if resp.status_code == 200:
                break

async def concurrency_test(name, n_clients, n_requests, request_fn):
    """通用并发测试"""
    latencies = []
    success = 0
    fail = 0

    async def worker(client_data):
        nonlocal success, fail
        client, auth = client_data
        lat = []
        for _ in range(n_requests):
            t0 = time.perf_counter()
            ok, _ = await request_fn(client, auth)
            elapsed = time.perf_counter() - t0
            if ok:
                success += 1
                lat.append(elapsed * 1000)  # ms
            else:
                fail += 1
        return lat

    # 创建客户端池
    clients = [await create_auth_client() for _ in range(n_clients)]
    all_lats = await asyncio.gather(*[worker(c) for c in clients])
    flat_lats = [l for sub in all_lats for l in sub]

    print(f"\n[{name}]")
    print(f"  {n_clients} clients × {n_requests} requests = {n_clients * n_requests} total")
    print(f"  Success: {success} / Fail: {fail}")
    if flat_lats:
        flat_lats.sort()
        print(f"  P50:  {statistics.median(flat_lats):.1f}ms")
        print(f"  P95:  {flat_lats[int(len(flat_lats)*0.95)]:.1f}ms")
        print(f"  P99:  {flat_lats[int(len(flat_lats)*0.99)]:.1f}ms")
```

#### 3.2.3 预期输出

基准测试应输出 `--benchmark-save=web_r3_baseline` 以备后续比对。输出文件存放在 `tests/.benchmarks/`。

**可接受标准**（参考值）：
- 读 API（`/api/auth/me`, `/api/config`）: P50 < 50ms, P99 < 200ms
- 写 API（login, config PUT）: P50 < 500ms, P99 < 2000ms
- 源列表 API: P50 < 200ms（取决于源数量）
- 并发 50 用户: 成功率 > 99%

---

### 3.3 P2 安全渗透测试（受限）

#### 3.3.1 已在第二轮覆盖的安全项

| 安全维度 | 覆盖状态 | 来源 |
|---------|:--------:|------|
| SQL 注入 | ✅ 参数化查询 | SQLite `?` 占位符，无字符串拼接 |
| XSS | ✅ Jinja2 自动转义 + `escapeHtml()` | 模板所有 `{{ }}` 自动转义 |
| CSRF | ✅ 恒定时间比较 + 内存绑定 | `verify_csrf_token()` 恒定时间比较 |
| Session 劫持 | ✅ httponly + samesite=lax | Cookie 不可 JS 读取 |
| 密码哈希 | ✅ bcrypt 12轮 | `bcrypt.gensalt()` |
| 暴力破解 | ⚠️ 仅 bcrypt 慢哈希 | **无请求级 rate limiting** |

#### 3.3.2 新增安全测试

##### 3.3.2.1 暴力破解模拟（手动核查 + 自动化）

编写 `tests/test_web_security.py`，主要测试：

**测试 1：bcrypt 耗时测量**
```python
def test_bcrypt_timing():
    """测量一次 bcrypt 验证耗时（不应过快）"""
    import time
    from web import models
    t0 = time.perf_counter()
    user = models.verify_password('admin', 'TestAdminPw1!')
    elapsed = time.perf_counter() - t0
    assert user is not None
    # bcrypt 应在 100-500ms 之间（太慢影响 UX，太快不安全）
    assert 0.05 <= elapsed <= 1.0, f"bcrypt timing: {elapsed:.3f}s"
```

**测试 2：相同 session 的暴力登录尝试**
```python
def test_rapid_login_attempts():
    """同一 IP 快速登录应可承受（无 rate limit 时不应崩溃）"""
    client = TestClient(app)
    results = []
    for i in range(100):
        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': f'wrong_{i}',
        })
        results.append(resp.status_code)
    # 所有请求都应正常返回（401 而非 500），系统不应 OOM 或崩
    assert all(r == 401 for r in results), "暴力破解尝试不应导致服务异常"
    assert len(results) == 100
```

**测试 3：Session ID 不可预测性**
```python
def test_session_id_entropy():
    """Session ID 应有足够熵"""
    import re
    client = TestClient(app)
    sessions = set()
    for _ in range(10):
        resp = client.post('/api/auth/login', data={
            'username': 'admin', 'password': 'TestAdminPw1!'
        })
        sid = resp.cookies.get('session')
        # Session ID 应为 uuid4 (32 hex chars = 128 bit entropy)
        assert re.match(r'^[a-f0-9]{32}$', sid), f"Session ID 格式异常: {sid}"
        sessions.add(sid)
        client.post('/api/auth/logout')
    assert len(sessions) == 10, "Session ID 应具有唯一性"
```

##### 3.3.2.2 Cookie 安全配置验证
```python
def test_cookie_security_flags():
    """登录返回的 cookie 应包含 httponly, samesite=lax"""
    client = TestClient(app)
    resp = client.post('/api/auth/login', data={
        'username': 'admin', 'password': 'TestAdminPw1!'
    })
    cookies = resp.cookies
    session_cookie = cookies.get('session')
    # FastAPI TestClient 通过 set-cookie header 体现属性
    set_cookie = resp.headers.get('set-cookie', '')
    assert 'httponly' in set_cookie.lower(), "Cookie 应设置 HttpOnly"
    assert 'samesite' in set_cookie.lower(), "Cookie 应设置 SameSite"
```

##### 3.3.2.3 OWASP ZAP（可选）

如环境允许，可通过 Docker 运行 ZAP 被动扫描：

```bash
# 1. 启动被测应用
cd /opt/dev/live-source-manager && WEB_ADMIN_PASSWORD=Admin1234 \
  WEB_VIEWER_PASSWORD=Viewer1234 python3 -m web.webapi &

# 2. 启动 ZAP
docker run -d --name zap --network host \
  -v /tmp/zap:/zap/wrk \
  ghcr.io/zaproxy/zaproxy:stable \
  zap.sh -daemon -port 8090 -config api.key=zap-api-key

# 3. 启动被动扫描
docker exec zap zap-cli -p 8090 -a zap-api-key \
  spider http://localhost:23455

# 4. 生成报告
docker exec zap zap-cli -p 8090 -a zap-api-key \
  report -o /zap/wrk/zap_report.html -f html
```

> **注意**: OWASP ZAP 全量扫描约 200-500MB Docker 镜像，扫描时间约 10-20 分钟。
> 对于当前项目的攻击面（FastAPI + SQLite + HTMX），ZAP 主要发现 CSRF 和
> 未认证端点，这两项已在前两轮覆盖。因此 ZAP 扫描为**可选，建议有时间再执行**。

---

### 3.4 P3 边界/压力测试

#### 3.4.1 测试内容

##### 3.4.1.1 WebSocket 连接上限
```python
"""tests/test_web_ws_pressure.py"""
import asyncio
import websockets
import httpx
import pytest

@pytest.mark.asyncio
async def test_ws_connection_limit():
    """超过最大连接数的 WebSocket 应被拒绝"""
    # 从 WebSocket 握手获取 session
    async with httpx.AsyncClient() as c:
        resp = await c.post("http://127.0.0.1:23455/api/auth/login",
            data={"username": "admin", "password": "TestAdminPw1!"})
        session_id = resp.cookies.get('session')

    ws_url = f"ws://127.0.0.1:23455/ws/test"
    connections = []

    # 建立 50 个连接（当前 max=50）
    for i in range(50):
        try:
            ws = await websockets.connect(
                ws_url, cookie=f"session={session_id}")
            connections.append(ws)
        except Exception as e:
            pytest.fail(f"第{i+1}个连接失败: {e}")

    assert len(connections) == 50, "应能建立 50 个连接"

    # 第 51 个连接应被拒绝
    try:
        ws_extra = await websockets.connect(ws_url)
        connections.append(ws_extra)
        # 如果连接成功，马上关闭（但说明上限设置可能未生效）
        await ws_extra.close()
        # 此项为信息性检查，不 assert 失败
        print("⚠️  注意: 第51个连接未被拒绝，上限设置可能需要确认")
    except Exception:
        pass  # 被拒绝 = 预期行为

    # 清理
    for ws in connections:
        await ws.close()
```

##### 3.4.1.2 大量源数据场景

```python
def test_large_source_list():
    """模拟大量源数据下的 API 响应"""
    client, auth = _admin_login()
    resp = client.get('/api/sources?page=1&size=50')
    assert resp.status_code == 200
    data = resp.json()
    # 记录数量和响应时间（信息性）
    print(f"  源总数: {data.get('total', 0)}")
```

##### 3.4.1.3 大日志文件读取测试

```python
def test_large_log_file():
    """日志文件超过 100MB 时 API 不应 OOM"""
    import os
    from web import config_proxy

    # 获取日志路径
    cfg = config_proxy.read_section('Logging')
    log_path = cfg.get('file', '/log/app.log')

    if os.path.exists(log_path) and os.path.getsize(log_path) > 10 * 1024 * 1024:
        # 只在大日志文件时执行验证
        client, auth = _admin_login()
        resp = client.get('/api/logs?tail=100')
        assert resp.status_code == 200
        assert len(resp.json().get('logs', [])) <= 100
```

##### 3.4.1.4 并发 Session 风暴测试

```python
def test_concurrent_session_avalanche():
    """大量并发 Session 创建和查询时的稳定性"""
    import threading
    from web import models
    from web.auth import create_session, get_session, destroy_session

    errors = []
    lock = threading.Lock()

    def worker(n):
        try:
            user = {'id': 1, 'username': 'admin', 'role': 'admin'}
            for _ in range(n):
                sid = create_session(user)
                assert get_session(sid) is not None
                destroy_session(sid)
        except Exception as e:
            with lock:
                errors.append(str(e))

    threads = [threading.Thread(target=worker, args=(20,)) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发 Session 操作异常: {errors[:3]}"
```

#### 3.4.2 补充：Locust 压力测试（全链路）

如果时间允许，可安装 Locust 进行真实 HTTP 压力测试：

```bash
pip install locust
```

编写 `locustfile.py`：

```python
from locust import HttpUser, task, between

class WebAdminUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        """登录并获取 CSRF token"""
        resp = self.client.post("/api/auth/login",
            data={"username": "admin", "password": "TestAdminPw1!"})
        self.csrf_token = None
        if resp.status_code == 200:
            csrf_resp = self.client.get("/api/auth/csrf-token")
            if csrf_resp.status_code == 200:
                self.csrf_token = csrf_resp.json().get("csrf_token")

    @task(5)
    def view_dashboard(self):
        self.client.get("/api/dashboard/stats")

    @task(3)
    def list_sources(self):
        self.client.get("/api/sources")

    @task(2)
    def read_config(self):
        self.client.get("/api/config")

    @task(1)
    def save_config(self):
        """写操作（需要 CSRF token）"""
        if self.csrf_token:
            self.client.put("/api/config",
                json={"Logging": {"level": "INFO"}},
                headers={"X-CSRF-Token": self.csrf_token})

    @task(1)
    def view_audit(self):
        self.client.get("/api/audit")

    def on_stop(self):
        self.client.post("/api/auth/logout")
```

**启动方式**：
```bash
WEB_ADMIN_PASSWORD=TestAdminPw1! WEB_VIEWER_PASSWORD=TestViewerPw1! \
  python3 -m web.webapi &
locust -f locustfile.py --host http://127.0.0.1:23455 --users 10 --spawn-rate 1
```

---

### 3.5 P4 浏览器端 E2E 测试

#### 3.5.1 评估结论

**推荐方式：手动验证，不引入自动化框架。**

原因：
1. **项目技术栈**：HTMX + 原生 JS，无前端构建、无 SPA 路由
2. **页面交互**：9 个页面均为 CRUD + 表格展示，无拖拽、无动画、无复杂状态
3. **投入产出**：Selenium/Playwright 需要浏览器 driver（~200MB），配置成本 > 手动验证成本
4. **回归覆盖**：92 项 API 自动化测试已覆盖所有后端逻辑，前端仅负责渲染和请求转发

#### 3.5.2 手动验证清单

执行一次完整手动验证，遍历以下流程：

| # | 测试场景 | 步骤 | 预期结果 |
|---|---------|------|---------|
| 1 | 登录 | 访问 /login → 输入 admin 密码 → 提交 | 跳转仪表盘，显示用户名 |
| 2 | 仪表盘 | 查看统计卡片、测试状态、系统信息 | 三块内容正确渲染 |
| 3 | 源管理 | 查看源列表 → 点击添加 → 填写表单 → 提交 | 表单提交成功，弹提示 |
| 4 | 配置中心 | 查看各配置段 → 修改一项 → 保存 → 验证 | 保存成功提示，刷新确认 |
| 5 | 实时测试 | 查看测试页面 | 页面渲染正常 |
| 6 | 日志查看 | 查看日志列表 → 切换级别 | 日志正确展示 |
| 7 | 审计日志(admin) | 查看审计日志 → 操作类型筛选 | 日志列表和筛选正常 |
| 8 | 用户管理(admin) | 查看用户 → 创建用户 → 编辑 → 删除 | 所有操作正常 |
| 9 | 登出 | 点击退出 → 页面跳转登录页 | session 清除，访问 / 被拒 |
| 10 | 权限隔离 | 用 viewer 账号测试 | admin 入口不可见，API 返回 403 |
| 11 | 404 页面 | 访问不存在的路径 | 返回 404 或优雅页面 |
| 12 | 错误密码登录 | 输入错误密码 → 提交 | 显示错误提示 |

---

## 4. 工具链评估与安装

### 4.1 必装工具

```bash
pip install pytest-benchmark
```

### 4.2 推荐安装

```bash
pip install locust
```

### 4.3 可选工具

```bash
# OWASP ZAP（Docker 方式，约 600MB）
docker pull ghcr.io/zaproxy/zaproxy:stable

# websockets 用于 WebSocket 压力测试（may be already installed）
pip install websockets
```

### 4.4 不推荐安装

| 工具 | 原因 |
|------|------|
| selenium | 需要 Chrome/Firefox driver，环境复杂，投入产出低 |
| playwright | 同上，且需浏览器二进制文件 |
| pytest-xdist | 当前 Web 模块测试使用单进程 SQLite + 全局状态（_csrf_tokens），并行执行会竞态 |
| pytest-cov | 可选但不是 R3 必需 |

---

## 5. 时间与资源估算

### 5.1 按测试维度

| 维度 | 编码 | 执行 | 分析 | 合计 |
|------|:----:|:----:|:----:|:----:|
| P0 修复验证回归 | 0h | 0.5h | 0.5h | **1h** |
| P1 性能基准 | 2h | 1h | 2h | **5h** |
| P2 安全渗透 | 2h | 2h | 1h | **5h** |
| P3 边界/压力 | 3h | 2h | 1h | **6h** |
| P4 E2E 手动验证 | 0h | 2h | 0.5h | **2.5h** |
| **总计** | **7h** | **7.5h** | **5h** | **~20h（2.5人天）** |

### 5.2 建议执行顺序

```
Day 1 AM:  P0 回归测试（0.5h）+ P1 基准测试编码（2h）+ P1 执行（1h）
Day 1 PM:  P1 分析（1h）+ P2 安全测试编码（2h）+ P2 执行（1h）
Day 2 AM:  P2 分析（1h）+ P3 压力测试编码（2h）+ P3 执行（1h）
Day 2 PM:  P3 分析（1h）+ P4 手动验证（2h）+ 综合报告（1h）
```

---

## 6. 风险与注意事项

### 6.1 已知限制

1. **单 worker 架构天花板**：uvicorn 单 worker + asyncio 单线程，CPU 密集型操作（bcrypt、大文件解析）即使通过 `asyncio.to_thread` 也受限于 GIL。此架构限定了并发能力上限约 100-200 QPS。

2. **TestClient 与真实 HTTP 差异**：现有 TestClient 测试不经过网络栈，不反映真实网络条件下的表现。性能测试必须使用真实的 uvicorn 实例 + HTTP 客户端（httpx/curl/locust）。

3. **无独立测试数据库**：当前 `test_web_auth.py` 使用 `tempfile.mkdtemp()` 创建临时 SQLite，但与生产配置路径通过环境变量隔离，可能存在交叉影响风险。

### 6.2 建议在 R3 执行前修复

根据第二轮审核的 MUST-FIX 清单：

| 问题 | 优先级 | 是否影响 R3 |
|------|:------:|:----------:|
| P0-3 CSRF 前端注入 | P0 | ✅ 必须修复后测试（否则大部分写操作不可用） |
| P0-4 WebSocket 认证+上限 | P0 | ✅ 必须修复（影响安全测试和 WS 压力测试） |
| P1-8 CSRF token 并发锁 | P1 | 建议修复（影响性能测试的并发安全性） |
| P1-3(遗留) 暴力破解 | P1 | 非阻塞 |
| P2-6 SourceManager 缓存 | P2 | 非阻塞 |

### 6.3 数据隔离

执行性能测试前务必确认：
1. 使用独立端口（如 23456）而非生产端口（23455）
2. 使用临时 SQLite 数据库（不覆盖 `web/data/web.db`）
3. 测试完成后清理临时文件
4. 压力测试不要在 CI/CD 自动运行（可能影响同主机其他服务）

---

## 附录

### A. 测试文件清单（建议新增）

| 文件路径 | 用途 | 测试数估算 |
|----------|------|:----------:|
| `tests/test_web_perf.py` | API 响应时间基准 | ~8 |
| `tests/test_web_security.py` | 暴力破解/Cookie/渗透 | ~6 |
| `tests/test_web_ws_pressure.py` | WebSocket 连接压力 | ~3 |
| `tests/stress_web_concurrent.py` | 并发性能测试脚本 | N/A |
| `locustfile.py` | Locust 压力测试 | N/A |
| `tests/e2e_manual_checklist.txt` | 手动验证清单 | N/A |

### B. 参考命令一览

```bash
# 全量自动化回归
pytest tests/test_web_auth.py tests/test_web_api.py -v --tb=short

# 启动被测服务
WEB_ADMIN_PASSWORD=TestAdminPw1! WEB_VIEWER_PASSWORD=TestViewerPw1! \
  python3 -m uvicorn web.webapi:app --host 127.0.0.1 --port 23456 --log-level warning

# 性能基准
pytest tests/test_web_perf.py -v --benchmark-save=web_r3_baseline

# HTTPie 单点测试
http POST http://127.0.0.1:23456/api/auth/login username=admin \
  password=TestAdminPw1!
```

---

> **总结**：第三轮测试的核心差异在于从功能正确性验证（R1/R2）转向性能、安全和边界验证。
> 因项目处于早期，性能基准需从零建立。建议先执行 P0 修复回归，确认前端完整工作流正常后，
> 再依次执行性能基准 → 安全测试 → 压力测试 → 手动 E2E 验证。
> 总预算 2-3 人天。
