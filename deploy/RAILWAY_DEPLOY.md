# Railway 部署手册（跑步教练 Agent / 马拉松训练计划 Agent）

> 目标：把有状态的 Flask 应用部署到 **Railway**（支持持久磁盘的 Python 平台），
> 拿到一个 `*.railway.app` 公网链接，给面试官点开验证。
> 本手册假设你已选 **Railway** 路径，且代码已在 GitHub 仓库 `haoxue123/run-flask-agent`。

---

## 0. 为什么是 Railway（而不是 Vercel / Netlify）

本项目是**有状态**的：`data/` 目录存上传的运动文件、ChromaDB 向量库、用户记忆 JSON，
必须跨请求持久化。Vercel / Netlify 的 Serverless 文件系统是临时的，会丢数据。
Railway 提供**持久磁盘（Volume）**，且是 Git 一键部署，是本项目后端最合适的「简单托管」。

> 注意：Railway **没有中国大陆节点**，国内访问走境外，延迟比大陆服务器高一些。
> 面试展示够用；若你对延迟敏感，可改用「方案 A：国内轻量云 + Docker Compose」（见 `DEPLOY_CHECKLIST.md`）。

---

## 1. 本项目已做好的兼容改动（助手已完成）

| 改动 | 文件 | 作用 |
|------|------|------|
| 端口读 `$PORT` | `deploy/Dockerfile` | gunicorn 绑定 `${PORT:-5000}`，Railway 注入的 `PORT` 自动生效；本地不注入时回落 5000 |
| 访问口令中间件 | `app.py`（`create_app` 内） | 设 `COACH_ACCESS_TOKEN` 后启用：首屏 `?token=XXX` 验证种 cookie，后续请求自动放行 |
| 401 友好页 | `app.py` | 未带 token 访问时提示「请用带 token 的链接」 |

---

## 2. 在 Railway 控制台操作（约 5 分钟）

### Step 1 — 新建项目并连 GitHub
1. 登录 [railway.app](https://railway.app)（建议用 GitHub 账号授权登录）。
2. `New Project` → **Deploy from GitHub repo** → 选 `haoxue123/run-flask-agent`。
3. Railway 会自动检测根目录 `deploy/Dockerfile` 并构建（无需 `railway.toml`）。

### Step 2 — 设置环境变量（Variables）
进入项目 → 该服务 → **Variables**，添加以下 4 项：

| Key | Value | 说明 |
|-----|-------|------|
| `SECRET_KEY` | 强随机串，如 `python3 -c "import secrets;print(secrets.token_hex(32))"` 的输出 | **必填**，否则应用拒绝启动 |
| `DEEPSEEK_API_KEY` | 你的真实 DeepSeek key | **必填**，调用 LLM 用；绝不外泄 |
| `FLASK_DEBUG` | `false` | 生产关闭 debug（防 RCE） |
| `COACH_ACCESS_TOKEN` | 你自己设的一个随机口令，如 `letmein-xxxx` | **必填**：启用访问口令，防 key 被刷 |

> 复制命令生成随机值（本地终端跑）：
> ```bash
> python3 -c "import secrets;print('SECRET_KEY=',secrets.token_hex(32));print('COACH_ACCESS_TOKEN=',secrets.token_hex(16))"
> ```

### Step 3 — 挂载持久磁盘（Volume）
1. 项目 → 该服务 → **Volumes** → **Add Volume**。
2. 挂载路径填 **`/app/data`**（与 Dockerfile 的 `VOLUME ["/app/data"]` 一致）。
3. 容量默认 1 GB 起步即可（运动文件 + Chroma 不大）。
   > 这一步**关键**：不挂 Volume，`data/` 会随容器重建丢失（上传文件、向量库、记忆全没）。

### Step 4 — 生成域名
1. 项目 → 该服务 → **Settings** → **Networking** → 点 **Generate Domain**。
2. 得到形如 `https://run-flask-agent-production.up.railway.app` 的公网链接。
3. 等待部署状态变 **Active**（首次构建约 2–5 分钟，取决于镜像体积）。

---

## 3. 验证部署成功

```bash
# 1) 不带 token：应返回 401（说明口令保护生效）
curl -sS -o /dev/null -w "%{http_code}\n" https://<你的>.railway.app/

# 2) 带 token：应返回 200 且能看到页面 HTML
curl -sS "https://<你的>.railway.app/?token=<你的COACH_ACCESS_TOKEN>" | head -n 20

# 3) 看 Railway 日志：确认无 import / API 报错
#    Railway 控制台 → 该服务 → Logs
```

给面试官的访问链接：
```
https://<你的>.railway.app/?token=<你的COACH_ACCESS_TOKEN>
```

---

## 4. （可选）自定义域名 + HTTPS

1. Railway 控制台 → 该服务 → **Settings → Networking → Custom Domain**，填你的域名（如 `coach.example.com`）。
2. 按提示在 DNS 加 CNAME 指向 Railway 提供的地址；Railway 自动签发 TLS。
3. **注意**：Railway 无大陆节点，自定义域名也走境外；若要**国内合规 + 低延迟**，仍需大陆服务器 + ICP 备案（见 `DEPLOY_CHECKLIST.md` 方案 A）。

---

## 5. 安全与成本提醒

- **安全**：应用本身无登录系统，靠 `COACH_ACCESS_TOKEN` 做展示期访问限制。
  面试结束后建议：改 token、或暂停该 Railway 服务、或直接删项目，避免 DeepSeek key 被长期暴露。
- **成本**：Railway 有免费额度（每月 $5 用量）；超额后约 **$5/月**。面试展示用完可删项目，零残留。
- **数据**：`data/` 在 Volume 里，删项目前若想保留，请先下载 Volume 或导出 `data/knowledge/`。

---

## 6. 后续更新代码（改完 push 即自动重部署）

Railway 与 GitHub 联动：**每次 `git push` 到 `main` 会自动触发重新部署**。
```bash
git add -A
git commit -m "update"
git push origin main
# 回 Railway 看该服务自动重建
```
