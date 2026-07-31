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
from routes.tokens import tokens_bp
import rag
from scheduler import init_scheduler, shutdown_scheduler


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
    app.register_blueprint(tokens_bp)

    # ---- 轻量访问口令（面试展示防 DeepSeek key 被刷）----
    # 仅当环境变量 COACH_ACCESS_TOKEN 非空时启用。
    # 用法：打开带 ?token=XXX 的链接，首屏验证后种会话 cookie，
    # 后续所有请求（含 POST 发消息/生成计划）自动放行，前端 JS 无需改动。
    import base64
    from flask import request, abort, after_this_request

    @app.before_request
    def _require_access_token():
        token = os.getenv("COACH_ACCESS_TOKEN")
        if not token:
            return  # 未启用口令保护，放行（本地/内测宽松）
        path = request.path
        if path.startswith("/static") or path in ("/healthz", "/health"):
            return  # 放行静态资源与健康检查，避免页面样式/健康检测失败
        if request.cookies.get("coach_token") == token:
            return  # 已通过验证的会话
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:].strip() == token:
            return
        if auth.startswith("Basic "):
            try:
                _u, _p = base64.b64decode(auth[6:]).decode("utf-8", "ignore").split(":", 1)
                if _p == token:
                    return
            except Exception:
                pass
        if request.args.get("token") == token:  # 首屏带 token 链接：种 cookie 后继续原请求
            @after_this_request
            def _set_cookie(resp):
                resp.set_cookie("coach_token", token, httponly=True,
                                samesite="Lax", max_age=60 * 60 * 24 * 7)
            return
        abort(401, "Access token required")

    @app.errorhandler(401)
    def _unauthorized(_e):
        return ("<h2>需要访问口令</h2>"
                "<p>这是面试展示专用部署，请使用主办方提供的带 <code>?token=</code> 的链接访问。</p>"), 401

    return app


app = create_app()

# 启动清理：尽力回收历史 mkstemp 残留的临时 JSON 文件
# （沙箱护栏下可能无法真正删除，purge_stray_temp_files 已静默忽略，故此处不再包裹 try）
try:
    import safeio
    safeio.purge_stray_temp_files(app.config["DATA_DIR"])
except Exception:
    pass

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

    # 启动 Garmin 自动同步调度：立即拉一次最新数据 + 定时刷新
    # （同步失败仅告警，不阻断应用；沙箱/无网络环境会优雅跳过）
    try:
        init_scheduler()
    except Exception as e:
        print(f"[WARN] Garmin 自动同步调度启动失败（不影响应用）：{e}")

    # 初始化 mem0 跨会话长期记忆层（自托管 OSS：DeepSeek + 智谱embedding + Chroma）
    # 缺 key / 未安装时优雅降级，不阻断应用
    try:
        import mem0_store
        mem0_store.init_mem0()
    except Exception as e:
        print(f"[WARN] mem0 初始化失败（不影响应用）：{e}")

    # 生产/TRAE 环境切勿开启 debug：Werkzeug 调试器存在 RCE 风险。
    # 通过环境变量 FLASK_DEBUG=true 显式开启（默认 False）。
    # host 默认 127.0.0.1；设为 0.0.0.0 可让容器/预览代理从外部网卡访问
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5000"))
    app.run(host=host, debug=os.getenv("FLASK_DEBUG", "False").lower() == "true", port=port, use_reloader=False, threaded=True)