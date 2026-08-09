# 国内网络部署 Live Source Manager（Docker）

> 本文专讲**把服务跑在国内网络、让 IPTV 源测试通过率正常**的部署要点。
> 通用安装/运行/卷挂载请先看 [`DOCKER_RUN.md`](./DOCKER_RUN.md)，本文只补充"国内网络"特有的坑。

---

## 〇、为什么需要单独说"国内网络"

一份线上运行日志里：容器解析出 **17084 个源**，最终**只有 7 个有效**（通过率 0.08%）。
失败原因几乎全是 `ffprobe_error` / `connection_failed` / `dns_failed` / `auth_blocked` / `not_found` / `connection_refused`。

**根因不是代码 bug，而是容器的出网环境连不上国内 IPTV CDN**（咪咕、芒果TV、移动 `cmvideo.cn`、CNTV 等）。
这正是项目设计意图——解析阶段只做窄门禁（`app/security.is_static_safe` 协议白名单 + SSRF 检查），**流的可达性完全交给 `StreamTester` 判定**——所以"服务器不可达但用户可看"的源被判定失败。

**结论：想拿到可用的播放列表，必须把容器部署在能直连国内 IPTV CDN 的网络里。代码不需要改，网络环境决定一切。**

---

## 一、选对网络环境（最关键）

| 环境 | 源测试预期 | 说明 |
|---|---|---|
| 国内云主机（阿里云/腾讯云/华为云 **国内地域**） | ✅ 正常 | 出站直连国内 CDN，首选 |
| 国内家庭宽带下的 NAS / 软路由 / 小主机 | ✅ 正常 | 真实家庭网络，源可用率最高 |
| 有国内直连出口的 VPS | ✅ 正常 | 需确认出口未被限 |
| 纯海外 VPS / 被墙网络 | ❌ 几乎全失败 | 连不上国内 CDN，只有个位数源 |

> 避免把生产服务放在纯海外节点。日志里 0.08% 通过率就是典型案例。

---

## 二、容器出网要求（出站，不是入站）

流测试是 ffprobe **主动向外连**，所以必须放行**出站**：

- **TCP 80 / 443**：HTTP/HTTPS 源
- **RTMP / RTSP / RTP**：直播流协议（部分源用这些端口）
- **UDP**：部分组播 / HTTP-FLV 走 UDP

常见坑：云厂商**安全组只配了入站**，出站默认放行；但如果你用了自建 `iptables`/`ufw`/透明代理，务必确认**出站链也放行**，否则 ffprobe 全 `connection_failed`。

> 不要给容器套"需要账号登录的透明代理"——会破坏 ffprobe 对源的直连判定。

---

## 三、DNS 解析（容器要能解析国内域名）

默认 `8.8.8.8` / `1.1.1.1` 在部分国内网络下解析慢或不稳，会导致 `dns_failed`。
建议给容器显式指定国内公共 DNS：

```bash
docker run -d \
  --name live-source-manager \
  --restart unless-stopped \
  --dns 223.5.5.5 \          # 阿里公共 DNS
  --dns 119.29.29.29 \       # 腾讯公共 DNS
  -p 12345:12345 \
  -p 23456:23456 \
  -e TZ=Asia/Shanghai \
  -e WEB_ADMIN_PASSWORD='你的强密码' \
  -e CONFIG_ENCRYPT_KEY='你的Fernet密钥' \
  -v ./data:/data \
  -v ./config:/config \
  -v ./output:/www/output \
  -v ./logs:/log \
  lsm:latest
```

或永久在 `/etc/docker/daemon.json` 配 `"dns": ["223.5.5.5","119.29.29.29"]` 后 `systemctl restart docker`。

---

## 四、拉取镜像（GHCR 国内加速）

镜像发布在 `ghcr.io/yuanshandalishuishou/live-source-manager:latest`，**国内直接 pull 可能慢/不稳**。三选一：

1. **镜像加速器**（推荐）：在 `/etc/docker/daemon.json` 加 `registry-mirrors`（阿里云 ACR 加速器、中科大等），再 `docker pull`。
2. **转存到阿里云 ACR 个人版**：把 GHCR 镜像同步到 ACR，再从 ACR pull（国内稳定）。
3. **国内机器自行构建**：`git clone` 后 `docker build`（已内置清华源加速，见下文）。

---

## 五、构建加速（已支持）

项目 `Dockerfile` 支持国内 pip 源，构建时自动切清华源：

```bash
git clone https://github.com/yuanshandalishuishou/live-source-manager
cd live-source-manager
docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t lsm:latest .
```

> 镜像内通过 `apt-get install ffmpeg` 预装了 FFmpeg/FFprobe，**流测试开箱即用，无需运行时下载**。

---

## 六、GitHub 源列表可达性（解析阶段要下载 GitHub 上的 iptv 列表）

解析阶段要从 GitHub 下载源列表（如 `wcb1969/iptv` 等公开仓库）。GitHub 在国内偶尔不稳或被限流。

⚠️ **关键坑：代理不要误伤流测试**

- 如果设了全局 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量，Python 的 `aiohttp`（下载源列表）**和** ffprobe 子进程（测流）**都会走代理**。
- 后果：源测试走到的是**代理出口**的可达性，而不是你服务器本身的可达性 → 测试结果失真，甚至把"代理那头能连、你家连不上"的源判成有效。
- **正确做法**：只为"下载 GitHub 源列表"走代理，流测试保持直连。两种方式：
  1. **不设全局代理**，仅在 Web 后台的"源配置"里用**国内可访问的源列表镜像地址**（如把列表镜像到 Gitee / 国内对象存储），让下载直连国内；或
  2. 若必须全局代理，则在调用 ffprobe 的环境里 `unset` 掉 `http_proxy` / `https_proxy`（程序侧 `subprocess` 调用时清掉代理环境变量），确保测流直连。

> 日志里出现的 `GitHub ... HTTP 451` 是 `wcb1969/iptv` 返回 **451（法律不可用）**，程序已 WARNING + 自动跳过，不影响其他源，属正常降级。

---

## 七、健康检查与验证

启动后跑一次手动测试，看日志确认通过率是否回升：

```bash
# 看处理流程完成与有效源数
docker logs live-source-manager 2>&1 | grep -E "增强版处理流程完成|有效性测试完成"

# 看 Web 后台是否起来
curl -I http://localhost:23456/

# 看生成的播放列表
curl -s http://localhost:12345/live.m3u | head -5
```

国内网络正常时，有效源数应从"个位数"提升到几十~几百（取决于源质量与你的出口带宽）。

---

## 八、排错清单（还是接近 0 通过？）

按顺序查：

1. **出站防火墙**是否放行 80/443/RTMP/RTSP/UDP（见第二节）。
2. **DNS** 是否指定国内公共 DNS（见第三节）。
3. **是否在海外节点**跑（见第一节）——这是 99% 的根因。
4. **是否误设全局代理**导致流测试走代理出口（见第六节）。
5. 看日志失败原因分布：`grep "最后原因" <日志> | sort | uniq -c`，按占比最高项对症。
6. `docker logs` 里 `ffprobe_error` 居多 → 多半是出站/协议端口被拦；`dns_failed` 居多 → DNS 问题；`connection_refused` → 源本身失效或出口被封。

---

## 九、时区

已默认 `TZ=Asia/Shanghai`，生成的播放列表、EPG、日志时间均为北京时间，无需额外配置。
