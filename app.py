"""
Flask Web 应用入口
==================
将马拉松训练计划 Agent 封装为 Web 应用，支持上传运动文件或填写手动数据，
点击按钮后展示生成的训练计划（含配速折线图）。

启动：python app.py
访问：http://127.0.0.1:5000
"""
import os
from flask import Flask

from routes.pages import pages_bp
from routes.data import data_bp
from routes.plan import plan_bp
from routes.coach import coach_bp
from routes.eval import eval_bp
import rag


def _load_dotenv(path=None):
    """最小 .env 加载（stdlib only）：将 .env 中的 KEY=VALUE 注入 os.environ。

    - 不覆盖已存在的环境变量（环境变量的优先级高于 .env）
    - 跳过空行与 # 注释；支持单/双引号包裹的值
    - 文件缺失或读取失败时静默忽略，不影响后续启动校验
    """
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


# 在启动校验前加载 .env（使「在 .env 中设置 SECRET_KEY」的提示生效）
_load_dotenv()


def _validate_secret_key():
    """SECRET_KEY 启动前置硬校验：缺失或使用弱/默认值一律拒绝启动。

    返回通过校验的密钥；任何不合规情况直接抛出 RuntimeError 并退出，
    绝不以随机值静默兜底（旧逻辑 os.urandom 兜底会导致会话重启即失效、
    多 worker 串号）。密钥本身不会被写入日志 / 错误堆栈 / 序列化输出。
    """
    key = os.getenv("SECRET_KEY")
    if not key:
        raise RuntimeError(
            "启动失败：缺少环境变量 SECRET_KEY。\n"
            "请先生成并配置固定密钥后再启动，例如：\n"
            "  export SECRET_KEY=$(openssl rand -hex 32)\n"
            "或在 .env 中设置 SECRET_KEY=****（务必使用强随机值，勿提交到仓库）。"
        )
    weak = {"", "change-me", "changeme", "default", "secret", "secret-key",
            "123456", "password", "test", "dev", "development", "mysecret"}
    if key in weak or len(key) < 16:
        raise RuntimeError(
            "启动失败：SECRET_KEY 过弱或使用了默认值（长度需 >= 16 且不能是常见占位符）。\n"
            "请更换为强随机密钥，例如：export SECRET_KEY=$(openssl rand -hex 32)"
        )
    return key


def create_app():
    """Flask 应用工厂。"""
    app = Flask(__name__)

    # Session 配置：SECRET_KEY 为启动前置硬约束（缺失/弱值直接拒绝启动，见 _validate_secret_key）
    app.secret_key = _validate_secret_key()

    # 目录配置
    base_dir = os.path.dirname(os.path.abspath(__file__))
    app.config["BASE_DIR"] = base_dir
    app.config["DATA_DIR"] = os.path.join(base_dir, "data")
    app.config["UPLOAD_DIR"] = os.path.join(app.config["DATA_DIR"], "uploads")
    app.config["CHART_DIR"] = os.path.join(app.config["DATA_DIR"], "charts")

    # 注册蓝图
    app.register_blueprint(pages_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(plan_bp)
    app.register_blueprint(coach_bp)
    app.register_blueprint(eval_bp)

    return app


app = create_app()

if __name__ == "__main__":
    print("=" * 50)
    print("马拉松训练计划 Agent Web 应用")
    print("访问：http://127.0.0.1:5000")
    print("=" * 50)

    # 预建 RAG 知识库索引（避免首次对话时等待）
    try:
        rag.prebuild_index()
    except Exception as e:
        print(f"[WARN] RAG 索引预建失败（将在首次对话时自动重试）：{e}")

    # 生产/TRAE 环境切勿开启 debug：Werkzeug 调试器存在 RCE 风险。
    # 通过环境变量 FLASK_DEBUG=true 显式开启（默认 False）。
    app.run(debug=os.getenv("FLASK_DEBUG", "False").lower() == "true", port=5000, use_reloader=False, threaded=True)