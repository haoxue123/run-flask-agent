# 部署知识清单：从第一性原理看今天的卡点与原因

> 用途：复盘今天把「马拉松训练计划 Flask Agent」部署到 Railway 的全过程。
> 目标：把每一处卡点拆成「现象 → 根因 → 解决」，形成可复用的知识，而不是只记操作步骤。

---

## 0. 一句话底层逻辑

**部署 = 把代码送到一台能运行它的服务器，并让它持续对外提供服务。**

今天所有问题，都能归到这条链路的四环里：

```
传输代码 → 构建镜像 → 运行服务 → 持久化数据 → 暴露公网
   ↑           ↑            ↑            ↑            ↑
  Git push   Dockerfile   $PORT/变量   Volume     域名+口令
```

任何一环断了，部署就失败。下面按这四环展开。

---

## 1. 第一性原理：部署到底在做什么

| 环节 | 在解决什么 | 今天的对应动作 |
|---|---|---|
| **传输** | 代码怎么到服务器 | `git push` 到 GitHub，Railway 再拉 |
| **构建** | 服务器怎么跑代码 | Dockerfile（装系统依赖 + pip 装包） |
| **运行** | 服务怎么被访问 | gunicorn 监听 `$PORT` |
| **持久化** | 数据怎么不丢 | Railway Volume 挂 `/app/data` |
| **暴露** | 外界怎么访问 | Generate Domain + 访问口令 |

**有状态 vs 无状态**（今天选型的核心）：
- 无状态（如静态站、纯函数）：代码丢了重发就行，数据在外面（数据库/对象存储）。
- 有状态（我们的 Flask + ChromaDB + 用户文件）：**数据在服务器本地磁盘**，必须用持久卷，否则每次重启/重建都丢。
- 所以 Vercel / Netlify 的 Serverless 跑不了我们（临时磁盘 + 休眠），Railway / 轻量云才行。

---

## 2. Railway 平台机制（今天踩坑最多的地方）

### 2.1 构建器检测：默认不是 Docker
Railway 连接 GitHub 后，会基于当时的文件结构**自动生成一个默认构建计划（Nixpacks / Railpack）**。它默认用 `python` 构建器（日志里显示 `python@3.13.14`），执行 `pip install -r requirements.txt && python app.py`。

**关键认知**：之后你再加 Dockerfile，它**不会自动重扫切换**。这是今天反复失败的根源。

### 2.2 强制用 Dockerfile 的正确开关
不是 `railway.json` 的 `builder` 字段（新版已不认），而是一个**环境变量**：

```
RAILWAY_DOCKERFILE_PATH = Dockerfile
```

设在 Service Variables 里，Railway 才会走你的 Dockerfile。日志出现 `Using detected Dockerfile!` 即生效。

### 2.3 Dockerfile 位置
Railway 对 Dockerfile 的检测依赖明确路径。放在子目录（`deploy/Dockerfile`）不被自动识别，所以后来复制到了**仓库根目录**的 `Dockerfile`。

### 2.4 Volume：Dockerfile 里的 VOLUME 不支持
Railway 报错：`docker VOLUME at Line 23 is not supported, use Railway Volumes`。
- Dockerfile 里的 `VOLUME ["/app/data"]` 必须删掉。
- 改为在 Railway 控制台 **Volumes** 页面手动挂：`/app/data`。
- 不挂的后果：应用能跑，但上传文件 / ChromaDB / 记忆会在每次重建时丢失。

### 2.5 端口：必须监听 `$PORT`
Railway 会注入 `$PORT` 环境变量，服务必须监听它。Dockerfile 里用：
```
gunicorn -b 0.0.0.0:${PORT:-5000} app:app
```
`${PORT:-5000}` 表示：有 `$PORT` 用 Railway 的，没有（本地 docker）回落 5000。

### 2.6 环境变量 vs Build Secret（隐藏坑）
Railway 会把**带 `TOKEN` 后缀的变量名**当成 Docker build secret 去解析。
- 今天出现重复变量 `COACH_ACCESS_TOKEN\t`（末尾 tab），值被设成 `${{COACH_ACCESS_TOKEN}}`（自引用），导致 `failed to solve: secret COACH_ACCESS_TOKEN not found`。
- 这是 Railway 自动生成的脏变量，点它的 "Fix" 按钮即可清除。

---

## 3. Docker 知识

### 3.1 基础镜像版本漂移
`python:3.11-slim` 是个**浮动标签**，今天实际指向 Debian trixie（不稳定测试版）。trixie 里包名/可用性与旧版不同，导致 apt 安装失败。

**教训**：生产镜像要**固定版本标签**：
```
FROM python:3.11-slim-bookworm   # Debian 12 稳定版
```

### 3.2 包名随发行版变化
`libgl1-mesa-glx` 在 trixie 里已不存在。改为通用包名 `libgl1`。
- 原本装它是因为 matplotlib 某些后端需要 GL。但我们设了 `MPLBACKEND=Agg`（无头后端），其实不依赖完整 Mesa OpenGL。

### 3.3 构建失败的两种层级
- **Build 失败**（镜像都没造出来）：Dockerfile 语法 / apt / pip 错误。今天大部分卡在这。
- **Runtime 崩溃**（镜像好了但起不来）：`SECRET_KEY` 无效、端口没绑对、依赖导入失败。

---

## 4. Git、网络与凭据

### 4.1 沙箱环境出不了公网
本对话的 Bash 工具跑在隔离沙箱，连 `github.com:443` 直接超时（21 秒）。即使关闭沙箱走宿主，同样连不上。所以**在这里 push 永远失败**——不是仓库/凭证问题，是环境无出网权限。

### 4.2 Git 凭据缓存
你自己在电脑 CMD 里能 push 成功，是因为系统有 **Git Credential Manager**，缓存了你的 GitHub 登录态。所以免交互。

### 4.3 HTTPS remote 禁提示时的报错
```
fatal: could not read Username for 'https://github.com': terminal prompts disabled
```
含义：需要输账号密码，但终端不允许交互提示。发生在「无凭据 + 非交互环境」。

### 4.4 SSH 是绕开 HTTPS 限制的备选
若 HTTPS 被网络重置（RST），可改 remote 为 SSH：
```
git remote set-url origin git@github.com:haoxue123/run-flask-agent.git
```
SSH 走 22 端口，封锁策略常与 443 不同。

---

## 5. 安全

### 5.1 公网裸奔风险
应用本身**没有登录系统**。直接暴露公网，别人能无限调用你的 DeepSeek API（刷钱）。

### 5.2 轻量访问口令（我们的方案）
在 `app.py` 的 `create_app` 里加中间件：
- 首屏带 `?token=XXX` 打开 → 验证通过 → 种会话 cookie。
- 之后所有请求（含 POST 发消息、生成计划）自动放行，**前端 JS 不改**。
- 由环境变量 `COACH_ACCESS_TOKEN` 控制，不设则行为不变（本地宽松）。
- 给面试官的链接：`https://<domain>?token=<口令>`。

---

## 6. 今天的卡点时间线（因果链）

| # | 现象 | 根因 | 解决 |
|---|---|---|---|
| 1 | push 连不上 GitHub | 沙箱无出网 | 用户本地 CMD push（多次成功） |
| 2 | Railway 不用我们的 Dockerfile | 默认 Railpack 构建器 | 加 `RAILWAY_DOCKERFILE_PATH` |
| 3 | 根目录加 Dockerfile 仍无效 | Railpack 不重扫已生成的计划 | 同上变量强制切换 |
| 4 | `railway.json` 的 builder 字段无效 | 新版 Railpack 不认该字段 | 弃用，改用环境变量 |
| 5 | `VOLUME` 指令报错 | Railway 不支持 Dockerfile VOLUME | 删掉，控制台手动挂 Volume |
| 6 | `SECRET_KEY` 占位符 → 崩溃 | Flask 拒绝弱密钥 | 生成真随机串 `secrets.token_hex(32)` |
| 7 | 缺 `COACH_ACCESS_TOKEN` | 变量没加 | 手动加变量 |
| 8 | 重复变量 tab + `${{...}}` 自引用 | Railway 自动生成脏变量 | 点 "Fix" 按钮清除 |
| 9 | apt `libgl1-mesa-glx` 失败 | Debian trixie 包名变了 | 固定 `bookworm` + 改 `libgl1` |
| 10 | 最后 push `2fc62b7` 失败 | 沙箱无凭据无网络 | **待用户本地 `git push origin main`** |

> 当前状态：本地有 `2fc62b7`（修好的 Dockerfile），GitHub 停在 `cbab560`。推上去 Railway 才会用新 Dockerfile 重建。

---

## 7. 关键教训（一句话版）

- **排错第一性原理**：先确认「这一步到底谁在执行、它依赖什么」——是 Git？是 Railway 构建器？是 Docker？还是 apt？定位执行者，问题就解了一半。
- **部署链路每一环都可能卡**：传输、构建器选择、Dockerfile 语法、系统依赖版本、环境变量、凭据，缺一不可。
- **生产镜像要锁版本**：浮动标签（`python:3.11-slim`）会随上游漂移，今天踩的就是这个坑。
- **平台限制要查官方文档**：Railway 的 `RAILWAY_DOCKERFILE_PATH` 是文档里的显式开关，比猜 `railway.json` 字段可靠。
