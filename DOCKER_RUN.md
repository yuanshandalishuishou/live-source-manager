# 使用 Docker 运行 Live Source Manager

本项目提供完整的 Docker 支持。你可以直接构建镜像，或用我们已发布的镜像一键 `docker run` 启动，无需在本地编译。

---

## 一、镜像来源

- **自己构建**：在项目根目录执行 `docker build`（见第三节）。
- **直接拉取（推荐给他人）**：我们已发布到 GitHub Container Registry（GHCR）：

  ```
  ghcr.io/yuanshandalishuishou/live-source-manager:latest
  ```

---

## 二、直接用 `docker run` 运行（标准命令）

无论镜像是自己构建的还是从 GHCR 拉取的，运行命令都一样：

```bash
docker run -d \
  --name live-source-manager \
  --restart unless-stopped \
  -p 12345:12345 \
  -p 23456:23456 \
  -e WEB_ADMIN_PASSWORD='你的强密码(至少8位且含字母和数字)' \
  -e CONFIG_ENCRYPT_KEY='你的Fernet密钥' \
  -v ./data:/data \
  -v ./config:/config \
  -v ./output:/www/output \
  -v ./logs:/log \
  -v ./sources:/config/sources:ro \
  lsm:latest
```

> 使用 GHCR 镜像时，把上面最后一行的 `lsm:latest` 换成
> `ghcr.io/yuanshandalishuishou/live-source-manager:latest`。

### 端口说明

| 宿主机端口 | 容器端口 | 用途 |
|---|---|---|
| `12345` | `12345` | Nginx 文件服务（对外分发 `live.m3u`） |
| `23456` | `23456` | Web 管理后台 |

### 数据卷说明（不挂载则重启丢数据）

| 容器路径 | 作用 | 必挂 |
|---|---|---|
| `/data` | SQLite 数据库 `web.db`（所有配置都在这里） | ✅ 必挂 |
| `/config` | 配置 / 分类规则 / 在线源 | ✅ 必挂 |
| `/www/output` | 生成的 `live.m3u`（Nginx 服务目录） | ✅ 必挂 |
| `/log` | 运行日志 | 建议 |
| `/config/sources` | 本地源文件（只读） | 可选 |

---

## 三、自己构建镜像

在项目根目录（含 `Dockerfile` 的目录）执行：

```bash
# 标准构建
docker build -t lsm:latest .

# 国内网络加速（自动切清华源，基础镜像不变）
docker build --build-arg BASE_IMAGE=python:3.13-slim-bookworm -t lsm:latest .
```

镜像内已预装：全部 Python 依赖（独立 venv）、Nginx、cron。
FFmpeg/ffprobe 为**可选组件**——构建时从 GitHub 下载静态构建，若网络不可达则自动跳过（流测试功能受限，Web/Nginx/SQLite 正常运行）。
入口脚本 `start_docker.sh` 会在首次启动时自动建库、建表、灌入默认值。

> 提示：仓库根目录已含 `.dockerignore`，可避免把 `.venv`、`log`、`config/online` 等运行期产物打进构建上下文，加快构建速度。

---

## 四、首次启动后获取管理员密码

- 若启动时**未设置** `WEB_ADMIN_PASSWORD`，容器会随机生成一个强密码并打印到日志，账号固定为 `admin`：

  ```bash
  docker logs live-source-manager | grep ADMIN_PASSWORD_INITIALIZED
  ```

- 若已设置 `WEB_ADMIN_PASSWORD`，则使用该密码登录（账号 `admin`）。

### 健康检查

```bash
curl -I http://localhost:23456/    # Web 后台：返回 303（未登录重定向，正常）
curl -I http://localhost:12345/    # 文件服务：返回 200
```

---

## 五、获取 GHCR 镜像（他人直接使用）

本项目已配置 GitHub Actions（`.github/workflows/docker.yml`）：每次推送到 `master` 分支时自动构建镜像并发布到 GHCR，无需手动操作。

### 他人使用（推荐）

```bash
# 直接拉取（公开镜像无需登录）
docker pull ghcr.io/yuanshandalishuishou/live-source-manager:latest
# 然后执行「第二节」的 docker run 命令（把镜像名换成上面的地址）
```

> 若 pull 报 `denied`，说明镜像尚未构建完成或包可见性为 private。
> 前往 https://github.com/yuanshandalishuishou/live-source-manager/actions 查看构建状态；
> 构建成功后可在 https://github.com/users/yuanshandalishuishou/packages 把镜像设为 Public。

### 手动推送（备选，需本机有 Docker）

如果需要在本地构建并手动推送：

```bash
# 登录（用带 write:packages 权限的 GitHub Token）
echo $GITHUB_TOKEN | docker login ghcr.io -u yuanshandalishuishou --password-stdin

# 打标签并推送
docker tag lsm:latest ghcr.io/yuanshandalishuishou/live-source-manager:latest
docker push ghcr.io/yuanshandalishuishou/live-source-manager:latest

# 推送完成后登出
docker logout ghcr.io
```

---

## 六、⚠️ 两个关键安全 / 稳定注意事项

1. **`WEB_ADMIN_PASSWORD`**：不设置则每次全新部署随机生成（日志可见）。要可复现就显式设一个 ≥8 位、含字母+数字的强密码。
2. **`CONFIG_ENCRYPT_KEY`**：不设置则每次启动随机生成 → 重启后**无法解密之前加密过的配置**。稳定部署必须固定一个 Fernet 密钥。生成方式：

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   > 该密钥属于敏感信息，请用 `-e` 或 Docker Secret 传入，**不要写进镜像或提交到仓库**。

---

## 七、环境变量一览

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WEB_ADMIN_PASSWORD` | 空（自动生成） | 管理员密码 |
| `CONFIG_ENCRYPT_KEY` | 空（自动生成） | 配置加密密钥（务必固定） |
| `NGINX_PORT` | `12345` | Nginx 文件服务端口 |
| `WEB_PORT` | `23456` | Web 管理端口 |
| `TEST_TIMEOUT` | `10` | 流测试超时（秒） |
| `CONCURRENT_THREADS` | `50` | 并发线程数 |
| `OUTPUT_FILENAME` | `live.m3u` | 输出文件名 |
| `UPDATE_CRON` | `0 6,12,18,22 * * *` | 定时更新 Cron 表达式 |
| `TZ` | `Asia/Shanghai` | 时区 |

> 注意：若通过环境变量修改 `NGINX_PORT` / `WEB_PORT`，`docker run -p` 的宿主机:容器映射需与容器实际监听端口保持一致。
