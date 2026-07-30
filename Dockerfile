FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 系统依赖：matplotlib 需要无头渲染，中文显示需要字体，chromadb 可能需要编译
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libgl1-mesa-glx fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# ⚠️ 体积提示：sentence-transformers 会拉取 torch（约 800MB+）。
# 若暂时不用 RAG 语义检索，可改用 requirements-lean.txt（去掉 chromadb / sentence-transformers），
# 镜像体积能从 ~1.5GB 降到 ~400MB。
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# 持久化挂载点：全部有状态数据（运动文件/uploads/charts/chroma_db）均落在 data/ 卷内
# 注意：Railway 不支持 Dockerfile VOLUME 指令，需在 Railway 控制台手动挂载 /app/data

EXPOSE 5000

# 生产用 gunicorn；2核2G 内存建议 workers=2，调大需同步加内存
# 若 app.py 中 Flask 实例变量名不是 app，请改为 "<module>:<var>"
# 监听 Railway 注入的 $PORT（带 5000 兜底：本地 docker-compose 不注入 PORT 时仍可用 5000）
CMD ["sh", "-c", "gunicorn -w 2 -b 0.0.0.0:${PORT:-5000} --timeout 120 app:app"]
