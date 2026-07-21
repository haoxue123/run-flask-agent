# 上线部署清单（马拉松训练计划 Agent）

> 本文件是「助手已完成的本地准备」+「你需要亲自操作的账号相关步骤」的完整指南。
> 原则：**代码与密钥分离，有状态数据不入库，部署尽量零成本**。

---

## 一、助手已为你准备好的内容（本地，已提交到 git）

| 项目 | 文件 | 说明 |
|------|------|------|
| 架构文档 | `ARCHITECTURE.md` | 第一性原理架构说明 |
| 简历用自包含 HTML | `showcase/architecture-doc.html` | 0 外链、可离线打开、内嵌 SVG |
| 简历用矢量 PDF | `showcase/architecture-doc.pdf` | Chrome 无头导出，可选中文字 |
| GitHub Pages 源 | `docs/index.html` | 上述架构文档副本，Pages 直接托管 |
| 部署配置 | `deploy/Dockerfile`、`deploy/docker-compose.yml`、`deploy/nginx.conf` | 已修复构建上下文 bug |
| 部署说明 | `deploy/DEPLOYMENT_PLAN.md` | 6 维部署方案 + 成本 <¥100/月 |
| CI 自动部署 | `.github/workflows/deploy.yml` | push main 后 SSH 拉取+重建 |
| 忽略规则 | `.gitignore` + `.dockerignore` | 已防止 `.env` 与私人运动数据入库/进镜像 |

### 已修复的关键 Bug
- `docker-compose.yml` 原来 `build: .` 指向 `deploy/` 目录，导致镜像里**没有 `app.py`/`routes/`**，服务必崩。现已改为 `build: { context: .., dockerfile: deploy/Dockerfile }`，构建上下文回到项目根目录。
- 构建上下文改为项目根后，新增项目根 `.dockerignore`，内容与 `.gitignore` 对齐，确保 `.env`、私人 `data/` 不会被打进镜像。

---

## 二、你需要亲自做的（涉及你的账号/付费，助手无法代劳）

### Step 1 — GitHub 仓库（免费）
1. 登录你的 GitHub，新建一个 **Public** 仓库（如 `marathon-coach-agent`）。
2. 本地已 `git init` 并提交，执行：
   ```bash
   git remote add origin https://github.com/<你的用户名>/<仓库名>.git
   git branch -M main
   git push -u origin main
   ```
3. 验证：仓库里**不应**出现 `.env`、`data/uploads/`、`data/chroma_db/`。

### Step 2 — GitHub Pages（免费，给面试官看静态成果）
1. 仓库 → Settings → Pages → Source 选 **Deploy from a branch** → Branch 选 **main** → Folder 选 **/docs**。
2. 保存后约 1 分钟，访问 `https://<用户名>.github.io/<仓库名>/` 即可看到架构文档。
   （这是「作品集证明」，证明你真的写清楚了架构与方案。）

### Step 3 — 租一台轻量云服务器（约 ¥60–100/年）
- 任意厂商（阿里云/腾讯云/华为云/UCloud）选「轻量应用服务器」：1 核 2G，系统选 **Ubuntu 22.04**，地域选国内（面试官在国内）。
- 国内自定义域名需 ICP 备案（2–4 周）；内测先用公网 IP 即可。
- 装好 Docker + Docker Compose（厂商文档一步到位）。

### Step 4 — 服务器上部署（一次命令）
```bash
# 1) 拉代码
git clone https://github.com/<你的用户名>/<仓库名>.git
cd <仓库名>

# 2) 在 deploy/ 下放一份「部署用」.env（注意：是 deploy/.env，不是根目录 .env）
cat > deploy/.env <<'EOF'
DEEPSEEK_API_KEY=你的真实key
SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))")
EOF

# 3) 启动
cd deploy
docker compose up -d --build
```
> ⚠️ `deploy/.env` 仅存在于服务器，不会被 git 提交。它给 docker-compose 提供 `${DEEPSEEK_API_KEY}` 与 `${SECRET_KEY}`。
> 验证：`curl http://localhost:5000` 能返回页面即成功；再把服务器公网 IP:80 发给面试官即可点开。

### Step 5 —（可选）绑定域名 + HTTPS
- 在 `deploy/` 放证书到 `certs/`，取消 `nginx.conf` 中 HTTPS 段的注释，重新 `docker compose up -d nginx`。
- 或用厂商免费证书 + 自动续期（见 `deploy/DEPLOYMENT_PLAN.md`）。

---

## 三、安全提醒（务必看）
- 应用当前**没有登录鉴权层**（任何人拿到 URL 都能用）。内测阶段请：
  - 用**防火墙/安全组只放通 80/443**，或修改 `nginx.conf` 加 `allow <你的IP>` 限制访问；
  - 不要让公网裸奔太久；正式对外前补一个简单口令或 IP 白名单。
- `.env`（含 DeepSeek key）已确认**不入库、不进镜像**；服务器上的 `deploy/.env` 请妥善保管。

## 四、一键验证脚本（服务器上跑）
```bash
docker compose -f deploy/docker-compose.yml ps      # 看 web/nginx 是否 healthy/up
docker compose -f deploy/docker-compose.yml logs web # 看是否有 import / API 报错
curl -sS http://localhost:5000 | head -n 20          # 应看到 HTML
```
