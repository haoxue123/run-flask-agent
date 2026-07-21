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


def create_app():
    """Flask 应用工厂。"""
    app = Flask(__name__)

    # Session 配置：优先从环境变量读取，缺失时回退到随机密钥（每次启动不同，生产环境务必配置 SECRET_KEY）
    app.secret_key = os.getenv("SECRET_KEY") or os.urandom(24).hex()

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