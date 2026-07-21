# 马拉松训练计划 Agent · 部署指南（方案 A：轻量云服务器 + Docker Compose）

适用前提：国内跑步用户、早期小流量、低运维偏好。本方案**零代码重构**，
把有状态的数据（运动文件 + ChromaDB 向量库）交给服务器磁盘持久化。

---

## 一、为什么选方案 A 而不是 CloudBase 云函数（方案 B）

本产品是**有状态**的：
- `data/` 目录存放用户上传的 GPX/TCX/FIT 与 `manual_input.json`
- ChromaDB（`sentence-transformers`）把向量落盘到本地

Serverless 云函数的本地磁盘在两次调用之间会被清空，直接部署会导致用户数据丢失。
CloudBase 自带的对象存储 / MongoDB 能解决，但需要**重构数据层**，不符合"低运维"。
→ 因此先用一台常驻服务器跑 Docker，数据天然持久化。

---

## 二、服务器选型（腾讯云轻量应用服务器）

| 配置 | 适用 | 月费（参考） |
|------|------|------|
| 2核2G / 4M带宽 | 个人 + 小跑团，Flask 单应用够用 | ~60 元 |
| 2核4G / 6M带宽 | 想把 gunicorn workers 开到 3-4 | ~100 元 |

系统选 Ubuntu 22.04，开放 80/443 安全组。

---

## 三、部署步骤

```bash
# 1. 登录服务器，安装 Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # 退出重登生效

# 2. 上传项目（deploy/ 与仓库根目录一起）
#    把整个 agent 项目拷到服务器，例如 /opt/marathon-agent

# 3. 配置密钥（不要提交到仓库）
cd /opt/marathon-agent/deploy
echo "DEEPSEEK_API_KEY=sk-xxxx" > .env

# 4. 启动
docker compose up -d

# 5. 验证
curl http://localhost        # 或浏览器访问服务器公网 IP
```

---

## 四、HTTPS 与备案（国内必看）

- **先用公网 IP 或临时域名验证**：不备案也能用 IP 直接访问，适合内测。
- **自定义域名 + HTTPS**：
  1. 在国内注册域名后需 **ICP 备案（2-4 周）**，备案通过才能用国内服务器解析自定义域名。
  2. 备案期间可并行申请 Let's Encrypt 证书：
     ```bash
     # 在服务器上
     sudo apt install certbot
     sudo certbot certonly --webroot -w /var/www/html -d your-domain.com
     # 证书生成后，把 fullchain.pem / privkey.pem 放到 deploy/certs/，
     # 并取消 nginx.conf 中 HTTPS 段的注释，重启 nginx 容器。
     ```
  3. 证书每 90 天续期：`certbot renew` 配 crontab。

---

## 五、运维增强（保持低运维）

- **自动更新镜像**：加一个 watchtower 服务，push 新镜像后自动拉取重启。
- **自动重启**：`restart: always` 已配置，进程崩了会拉起。
- **监控**：CloudBase / 轻量服务器控制台自带 CPU、流量监控；可加 uptime-kuma 做存活告警。

---

## 六、镜像瘦身（可选）

`requirements.txt` 里的 `sentence-transformers` 会拉 torch（~800MB+）。
若暂不使用 RAG 语义检索，新建 `requirements-lean.txt` 去掉
`chromadb` 与 `sentence-transformers`，并改 Dockerfile 第 18 行为
`RUN pip install --no-cache-dir -r requirements-lean.txt gunicorn`，
镜像可从 ~1.5GB 降到 ~400MB。

---

## 七、何时升级到方案 C（生产级高可用）

出现以下信号再动：
- 日活 > 数百、需要横向扩容 → gunicorn workers 不够时引入负载均衡 + 多实例
- 数据重要性上升 → ChromaDB / 文件迁移到云数据库 + 对象存储，做定时备份
- 可用性要求高 → 前端接 CDN，后端 K8s / 云托管，加日志与监控面板

---

## 八、如果坚持要"零运维"（需重构数据层）

改用 **腾讯云 CloudBase 云托管（容器模式，非云函数）**：
- 容器模式有持久化文件系统，比云函数更适合有状态服务
- GPX 文件改存 CloudBase 对象存储，结构化数据改存 CloudBase MongoDB
- 成本：小流量近乎免费，大流量按量
- 代价：需改 `data_tool.py` 与 `rag.py` 的数据访问层，工作量中等
