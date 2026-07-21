# 部署方案 · 落地版（第一性原理 + 六维度覆盖）

> 适用对象：马拉松训练计划 Agent（Flask 单应用，国内跑步用户，早期小流量，低运维）
> 配套配置：`deploy/Dockerfile` · `deploy/docker-compose.yml` · `deploy/nginx.conf` · `.github/workflows/deploy.yml` · `deploy/backup.sh`

---

## 0. 当前条件盘点（第一性原理输入）

| 维度 | 我的判断 | 依据 | 置信度 |
|------|----------|------|--------|
| 技术栈 | Flask + DeepSeek(生成+反思) + gpxpy/fitparse + matplotlib + ChromaDB(可选 RAG) | `requirements.txt` / `app.py` / `rag.py` | 高 |
| 项目规模 | 单进程 Web 应用，无微服务拆分，蓝图式路由 | `routes/` 5 个蓝图 | 高 |
| 团队人数 | 1 人（你 solo 构建与维护） | 记忆：独立做 PRD/原型；无协作仓痕迹 | 中（推断） |
| 预算上限 | 月 ≤ 200 元（轻量档） | 低运维偏好 + 早期阶段 | 中（推断） |
| 预期流量 | 日活 ≤ 数十，偶发峰值 | 单进程、文件级持久化、原型转 MVP | 中（推断） |

**翻转条件**（任一成立则结论改为方案 C）：
- 团队 ≥ 3 人且需要独立环境隔离 → 上 K8s / 云托管多实例
- 月预算 ≥ 2000 元且日活 ≥ 千 → 上负载均衡 + 云数据库
- 需要 99.95%+ 可用性 SLA → 多可用区部署

> 下面按"1 人 / ≤200 元 / 日活数十"推进。如某项不符，先告诉我，结论随之调整。

---

## 1. 方案结论

**方案 A 增强版 = 轻量云服务器 + Docker Compose + 轻量 CI/CD（Git 触发）**

- 不选 **方案 B（云函数）**：应用有状态（`data/` 含运动文件 + ChromaDB 向量库），云函数临时磁盘会丢数据，需重构数据层才可用。
- 不选 **方案 C（K8s）**：团队/流量/预算均未达阈值，属过度工程，反而抬高运维负担。
- 选 A 增强：用 Docker 镜像解决环境一致性，用 Git Actions 解决 CI/CD，用单实例 + 纵向扩容覆盖扩缩容，云监控 + uptime-kuma 覆盖可观测，成本最低。

---

## 2. 前置要求逐条核验

| 前置要求 | 状态 | 说明 / 补法 |
|----------|------|-------------|
| 代码已入 Git | ✅ | 仓库存在；需确保推到远程（GitHub/Gitee） |
| 可构建 Docker 镜像 | ✅ | `deploy/Dockerfile` 已就绪 |
| 有常驻服务器预算 | ⚠️ 待你购买 | 腾讯云轻量 2核2G Ubuntu ~60 元/月 |
| 域名 或 接受 IP 访问 | ✅ | 内测可先用公网 IP，无需备案 |
| 密钥不入库 | ✅ | `DEEPSEEK_API_KEY` / `SECRET_KEY` 走 `.env`（已 gitignore） |
| 可达 DeepSeek API | ✅ | 国内网络直连 platform.deepseek.com 正常 |
| **数据备份机制** | ❌→补 | 见 `deploy/backup.sh` + 服务器快照（当前缺失，部署前必做） |
| **生产 SECRET_KEY** | ❌→补 | `app.py` 第 26 行：不配则每次重启会话/教练记忆失效，需在 `.env` 设固定值 |

---

## 3. 六维度落地设计

### 3.1 环境一致性（开发 / 测试 / 生产）
- **统一镜像**：`deploy/Dockerfile` 构建的同一镜像，dev/test/prod 共用，杜绝"我这能跑"。
- **差异仅来自 env**：`DEEPSEEK_API_KEY` / `SECRET_KEY` / `FLASK_ENV` 注入，不写死。
- **本地联调**：`cd deploy && docker compose up -d`，挂载本地 `../data`。
- **测试环境**：同镜像另起容器，挂独立测试数据卷（如 `data_test/`），跑 smoke test 后销毁。
- **版本锁定**：Python 3.11、依赖写入 `requirements.txt` 固定，避免漂移。

### 3.2 CI/CD 自动化
- **机制**：GitHub Actions（见 `.github/workflows/deploy.yml`）。`push` 到 `main` → SSH 进服务器 → `git pull` → `docker compose build` → `docker compose up -d` → 清理悬空镜像。
- **免自建 registry**：直接服务器拉代码构建，省存储与流量成本（适合单人）。
- **回滚**：构建前镜像保留，异常时 `docker compose up -d <旧tag>` 即可回退。
- **质量门**（建议补）：在 `build` 前加 `flake8` / 一个 `test_smoke.py`（访问 `/` 返回 200），失败不部署。当前项目无测试，列为准入项。
- **Gitee 用户**：用 Gitee CI 等价配置，或服务器挂 webhook 触发同一脚本。

### 3.3 扩缩容策略
- **当前**：单实例 gunicorn `workers=2`（2核2G）。
- **纵向（首选）**：升 4G 内存 → `workers=3~4`；改 `docker-compose.yml` 环境变量即可，零架构改动。
- **横向触发阈值**：CPU 持续 > 70% 或并发 > 50 → 加实例 + 负载均衡（nginx upstream 或云负载均衡）。
- **存储减压**：用户 GPX 大文件后续可转存对象存储（COS），本地只留索引。
- **降级保护**：DeepSeek 超时由 `proxy_read_timeout 120s` 兜底，避免请求堆积拖垮进程。

### 3.4 安全与权限管理
- **密钥**：`DEEPSEEK_API_KEY` / `SECRET_KEY` 仅存服务器 `.env`，绝不进 Git；定期轮换。
- **服务器**：SSH 密钥登录、改默认端口、关密码登录；安全组仅开 80/443 + SSH 端口。
- **传输**：certbot 签发 Let's Encrypt，HTTP 301 跳 HTTPS（见 `nginx.conf` 预留段）。
- **上传**：nginx `client_max_body_size 20m` 已限；应用层需校验文件类型（GPX/TCX/FIT）。
- **⚠️ 应用层无鉴权（当前最大安全缺口）**：任何人可访问全部页面并上传。
  - 内测期：用防火墙/安全组限制来源 IP，或在 nginx 加 `basic auth`。
  - 正式公开前：必须加登录（Flask-Login / 反向代理统一鉴权），否则存在被滥用与脏数据风险。

### 3.5 监控与日志
- **日志**：`docker compose logs -f`；nginx 访问日志落 `./logs`（已挂载）。建议应用日志改 JSON 行式便于检索。
- **存活监控**：`uptime-kuma` 容器探 `/` 与 `/coach`，异常发邮件/飞书。
- **指标**：轻量服务器控制台看 CPU/内存/带宽；应用层统计 DeepSeek 调用失败次数。
- **告警**：uptime-kuma 连续 2 次失败即告警，避免静默宕机。

### 3.6 成本预估

| 项 | 费用 | 备注 |
|----|------|------|
| 轻量 2核2G / 4M | ~60 元/月 | 主力 |
| 域名 | ~50 元/年 | 可选，内测可免 |
| ICP 备案 | 0 元（耗时 2-4 周） | 用自定义域名才需 |
| 对象存储（可选） | ~几元/月 | 大文件转存时 |
| uptime-kuma | 0（同机容器） | — |
| **合计** | **< 100 元/月** | 不含流量超额 |

**零成本替代**：腾讯云 CloudBase 云托管（容器模式）小流量近乎免费，但需把 `data/` 数据层重构到对象存储 + MongoDB（中等工作量）。

---

## 4. 风险点与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 单点故障（服务器宕机） | 全站不可用 | `restart: always` + 云厂商定时快照 + 数据备份 |
| 数据丢失（磁盘损坏） | 运动文件/向量库尽失 | `backup.sh` 每日备份 `data/` + 云快照 |
| 无应用鉴权 | 被滥用、脏数据、Key 被刷 | 内测限 IP / basic auth；公开前加登录 |
| 备案未完成用自定义域名 | 被墙/关停 | 先用公网 IP；备案与开发并行 |
| 镜像含 torch 过大（~1.5GB） | 构建/拉取慢 | 用 `requirements-lean.txt` 砍掉 RAG 依赖 → ~400MB |
| 密钥泄露 | DeepSeek 资损 | env 隔离 + 不入库 + 定期轮换 |
| 流量突增打爆单实例 | 响应慢/超时 | 监控告警 + 纵向扩容路径（3.3） |
| 每次重启会话失效 | 教练记忆丢 | 服务器 `.env` 固定 `SECRET_KEY` |

---

## 5. 落地 Checklist

1. ☐ 购轻量 2核2G Ubuntu，安全组开 80/443 + SSH
2. ☐ 服务器装 Docker，`ssh-key` 登录，关密码
3. ☐ `git clone` 仓库到 `/opt/marathon-agent`
4. ☐ 建 `deploy/.env`：`DEEPSEEK_API_KEY=...` `SECRET_KEY=<随机固定串>`
5. ☐ 首次 `cd deploy && docker compose up -d`，访问 IP 验证
6. ☐ 部署 `backup.sh` 并加 crontab（每日 04:00）
7. ☐ 加 uptime-kuma 容器并配告警
8. ☐ 补一个 smoke test；配 GitHub Actions Secrets（SSH_HOST/USER/KEY）
9. ☐ 内测期限 IP 或 basic auth
10. ☐ （可选）并行启动备案 + 域名 + certbot 启用 HTTPS

---

## 6. 交付物清单

| 文件 | 作用 |
|------|------|
| `deploy/Dockerfile` | 构建一致镜像（已修正为单一 data 卷） |
| `deploy/docker-compose.yml` | 编排 web + nginx，单数据卷持久化（已修正） |
| `deploy/nginx.conf` | 反代 + 上传限制 + 预留 HTTPS |
| `deploy/backup.sh` | 每日备份 `data/`（含 chroma_db） |
| `.github/workflows/deploy.yml` | push 即部署的 CI/CD |
| `deploy/README.md` | 操作手册与备案/HTTPS 流程 |
| 本文件 | 方案论证与六维度设计 |
