# 部署与上线方案：Netlify / Vercel 第一性原理评估

> 背景：朋友建议使用 Netlify / Vercel 这类"操作简单、适合部署"的线上托管平台。
> 本文从第一性原理重新拆解，给出可行性判定、平台选型、完整流程、关键配置，并确认与项目最终目标的一致性。

---

## 0. 结论速览（TL;DR）

- **核心判定**：Vercel / Netlify 是「静态站 + Serverless」平台，**不适合直接托管本项目的"有状态 Flask 后端"**。它们只适合托管**静态可嵌入 Demo + 文档**。
- **若坚持"朋友说的简单托管"精神、且要真能跑 Flask + 持久化**：真正的等价平台是 **Railway / Render**（对有状态 Python 后端同样"一键 Git 部署 + 持久磁盘"），比 Vercel 更贴合本项目。
- **与最终目标一致性**：**纯 Vercel / Netlify 托管后端 = 不一致**（违背零重构 + 有状态 + 国内低延迟）；**方案 A（轻量云 + Docker Compose，已建）/ Railway / Render** 才一致。

---

## 1. 项目核心目标与需求（第一性原理回溯）

| 维度 | 内容 |
|------|------|
| 产品形态 | Flask 单进程 Web 应用；DeepSeek 双模型（生成 + 反思）；解析 GPX/TCX/FIT；可选 ChromaDB RAG + matplotlib |
| 受众 | 国内跑步人群；阶段 = 早期小流量；运维偏好 = 低 |
| **硬约束（决定部署形态）** | **有状态**：`data/` 含上传运动文件 + ChromaDB 向量库 + `user_profile.json` / `chat_history.json`，必须跨请求持久化 |
| 关键交互 | 教练对话是 **SSE 流式**（长连接），非一次性请求-响应 |
| 最终目标（简历作品） | 让面试官"点开真东西"验证确实做出来了；低运维；**国内访问低延迟**；尽量**零重构** |
| 衍生需求 | DeepSeek API Key 作为秘钥注入；可选自定义域名（**国内需 ICP 备案 2–4 周**） |

> 决定部署形态的只有一个不可妥协项：**`data/` 必须持久化**。这直接排除了所有"临时文件系统"的 Serverless 平台作为后端主机的资格。

---

## 2. Netlify & Vercel 可行性分析

### 2.1 两者共性（优势）
- Git 驱动 CI/CD、预览环境、全球 CDN、免费 TLS、自定义域名、极简 DX。

### 2.2 架构前提（致命点）
- **无状态 Serverless / 静态**：函数实例 ephemeral，文件系统非持久，有超时与冷启动。

| 维度 | Vercel | Netlify |
|------|--------|---------|
| 主战场 | Next.js / Node 前端 | 静态 / JAMstack |
| Python 后端支持 | `@vercel/python`（`api/` 下 WSGI handler） | Netlify Functions（Python） |
| 函数超时 | 10s(hobby) / 60s(pro) | 10s(free) / 26s |
| 持久磁盘 | **无**（仅 `/tmp` 临时，调用后即销毁） | **无**（仅 `/tmp` 临时） |
| 大陆节点 | **无**，国内访问高延迟 | **无**，国内访问高延迟 |
| 免费额度 | 充裕 | 充裕 |

### 2.3 与本项目的冲突点（关键）
1. **有状态冲突**：`data/` 必须在多次请求间持久存在。Serverless 的 `/tmp` 每次调用可能落在不同实例、调用后即销毁 → 上传文件、Chroma 向量库、记忆 JSON 全部丢失。
2. **ChromaDB 向量库**：需持久磁盘路径；Serverless 下要么每次冷启动从对象存储重建（慢、复杂），要么换托管向量库（重构 `rag.py`）。
3. **SSE 流式**：教练对话用 `stream_with_context`，Serverless 对长连接 / 流式支持差、易超时。
4. **国内延迟**：Vercel / Netlify **无大陆节点**，国内面试官访问延迟高，与"国内低延迟"目标直接冲突。
5. **ICP 备案**：用默认域名（`.vercel.app` / `.netlify.app`）无需备案，但绑定国内可访问自定义域名仍受限于其无大陆基础设施；真正国内合规域名仍需大陆服务器。

### 2.4 它们真正擅长的场景
- 静态可嵌入 Demo（`showcase/marathon-coach-embed` 等纯前端）✓
- 文档站（`architecture-doc.html`）✓（现用 GitHub Pages，可平移）
- 纯前端 + 第三方 API 的 JAMstack ✓

---

## 3. 基于需求选平台（判定）

| 候选 | 是否跑得成本项目后端 | 说明 |
|------|----------------------|------|
| **Vercel / Netlify（直接托管 Flask）** | ❌ 不可行 | 需把数据层重构为无状态（R2/S3 + 托管向量库），违背"零重构" |
| **Railway / Render** | ✅ 可行且简单 | 持久磁盘 + Git 部署 + 免费/低价，对**有状态 Python 后端**体验接近 Vercel，是真能跑 Flask 的"简单托管" |
| **方案 A：轻量云 + Docker Compose（已建）** | ✅ 最贴合 | 零重构、data/ 持久化交给服务器磁盘 / Docker volume、国内低延迟、运维低 |
| **混合：Vercel/Netlify 仅静态 + 后端跑方案 A** | ✅ 可行 | 复用已有静态 Demo，后端不动 |

**推荐**：
- 若需求 = "零重构 + 有状态 + 国内低延迟 + 低运维" → **方案 A（已落地 `deploy/`）** 或 **Railway**（想要 Vercel 式一键体验时）。
- 朋友建议的"简单托管"精神，对**有状态 Python 后端**的真正等价平台是 **Railway / Render**，而非 Vercel / Netlify。

---

## 4. 本地 → 线上 完整流程

### 主线 A：方案 A（轻量云 + Docker Compose，已建，最贴合）
1. 本地：`git commit` 当前代码（含 `deploy/`）。
2. 租轻量云（国内，~¥60–100/年），开放 80/443，防火墙限 IP（内测）。
3. 服务器：`git clone` → `deploy/.env` 填 `SECRET_KEY` + `DEEPSEEK_API_KEY` → `docker compose up -d --build`。
4. 验证：`curl http://<公网IP>/` → 200。
5. （可选）自定义域名 + ICP 备案 + HTTPS。
6. 配置 `.github/workflows/deploy.yml`（需带 `workflow` scope 的 token）实现 push 自动部署。

### 主线 B：Railway（若选"简单托管"一键体验）
1. `pip` 依赖已固化在 `requirements.txt`，无需改代码（持久磁盘天然支持 `data/`）。
2. 装 `railway` CLI 或网页授权 GitHub。
3. `railway login` → `railway init` → `railway up`（自动检测 Python / Dockerfile）。
4. 在 Railway 控制台设环境变量：`SECRET_KEY`、`DEEPSEEK_API_KEY`、`FLASK_DEBUG=false`。
5. 挂载 Volume 到 `/app/data`（Railway 提供持久磁盘）。
6. 自动分配 `*.railway.app` 域名；可绑定自定义域名。
7. 每次 `git push` 触发重新部署（原生 Git 集成，无需 workflow 文件）。

### 支线：Vercel / Netlify 仅托管静态 Demo（后端仍在方案 A / Railway）
1. 进入 `showcase/marathon-coach-embed/`（纯前端、零依赖）。
2. `vercel deploy --prod`（或 Netlify 拖拽 / `netlify deploy --prod`）。
3. 获 `*.vercel.app` / `*.netlify.app` 链接，可iframe 嵌入腾讯文档。
4. 后端 API 仍由方案 A / Railway 提供，前端通过公网地址调用。

---

## 5. 关键配置项

| 类别 | 配置项 | 说明 |
|------|--------|------|
| 环境变量 | `SECRET_KEY` | **强制**（app.py 启动硬校验，缺失/弱值拒绝启动） |
| 环境变量 | `DEEPSEEK_API_KEY` | LLM 调用秘钥，绝不入库、不写日志 |
| 环境变量 | `FLASK_DEBUG=false` | 生产关闭 debug（防 RCE） |
| 持久化 | `data/` 卷 | 方案 A = Docker volume；Railway = Volume；Vercel/Netlify = **必须改对象存储**（重构） |
| 域名 | 默认 | `*.railway.app` / `*.vercel.app` / `*.netlify.app` |
| 域名 | 自定义 | DNS CNAME；**国内合规需大陆服务器 + ICP 备案（2–4 周）** |
| CI/CD | 方案 A | `.github/workflows/deploy.yml`（待补 workflow scope token） |
| CI/CD | Railway | 原生 Git 集成，push 即部署 |
| 安全 | 内测 | 限 IP / basic auth；公开前加登录，防 DeepSeek key 被刷 |
| 成本 | 方案 A / Railway | 方案 A ~¥5–8/月；Railway 免费额度用尽后 ~$5/月 |

---

## 6. 可行性 / 简洁 / 可执行 + 一致性确认

- **可行性**：方案 A 与 Railway 均**可行且已验证路径**；纯 Vercel / Netlify 后端**不可行**（需重构数据层）。
- **简洁 / 可执行**：方案 A 与 Railway 均"数步上线"；Railway 步骤最少、最贴近朋友说的"简单托管"。
- **与项目最终目标一致？**
  - ✅ **方案 A / Railway + 国内低延迟 + 零重构** → 一致。
  - ❌ **纯 Vercel / Netlify 托管后端** → **不一致**（违背零重构 + 有状态 + 国内低延迟）。
  - ⚠️ **Vercel / Netlify 仅托管静态 Demo** → 一致（只是把展示层换个平台，后端不动）。

**最终建议**：保留已建的「方案 A」作为默认上线路径；若你更想要 Vercel 式一键体验，把后端迁到 **Railway**（几乎零改动）；Vercel / Netlify 只用来托管纯前端 Demo 与文档，不要用来跑 Flask 后端。
