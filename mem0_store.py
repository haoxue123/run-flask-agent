"""
mem0 长期记忆层封装（自托管 OSS）
================================

为 AI 教练对话提供跨会话长期记忆：自动从对话抽取事实、存进向量库、
下次对话再检索出来，注入 prompt。

组件（复用项目已有能力 + 国内直连零成本）：
    - LLM     : DeepSeek（抽取事实，复用 DEEPSEEK_API_KEY）
    - Embedder: 首选硅基流动 BAAI/bge-m3（免费）；回退智谱 embedding-3
    - Vector  : Chroma（复用项目已有的向量库能力，独立 collection）

降级策略：
    - 未配置 DEEPSEEK_API_KEY / ZHIPU_API_KEY，或 mem0ai 未安装，或初始化异常
      → ENABLED=False，所有方法安全返回空，不抛异常、不阻塞 coach。
    - 这与 Garmin 同步的降级思路一致：缺外部依赖时优雅跳过，本机/部署服务器填好
      key 即自动生效。

与现有 memory.py 的关系：并存。memory.py 继续管结构化画像（user_profile.json，
含否定意图层）；mem0 管对话级抽取记忆。两者都注入 coach prompt。
"""

import os
import logging
import threading

logger = logging.getLogger("mem0_store")

# 禁用 mem0 内置遥测（PostHog），避免沙箱/离线环境打连接错误日志
os.environ.setdefault("MEM0_TELEMETRY", "False")

_ENABLED = False
_MEMORY = None
_LOCK = threading.Lock()

_COLLECTION = "running_coach_memories"


def _build_config():
    """构造 mem0 配置；缺 DeepSeek key 或 embedder key 时返回 None（触发降级）。

    LLM 用 DeepSeek 做事实抽取（复用 DEEPSEEK_API_KEY）；
    Embedder 首选硅基流动 BAAI/bge-m3（免费模型，OpenAI 兼容），
    未配置时回退智谱 embedding-3。
    已实测：DeepSeek 官方无可用 embedding 端点（404），故向量化需独立服务商。
    """
    ds_key = os.getenv("DEEPSEEK_API_KEY")
    sf_key = os.getenv("SILICONFLOW_API_KEY")
    zp_key = os.getenv("ZHIPU_API_KEY")
    if not ds_key or not (sf_key or zp_key):
        return None

    if sf_key:
        embedder_cfg = {
            "model": os.getenv("MEM0_EMBED_MODEL", "BAAI/bge-m3"),
            "api_key": sf_key,
            "openai_base_url": "https://api.siliconflow.cn/v1",
        }
    else:
        embedder_cfg = {
            "model": os.getenv("MEM0_EMBED_MODEL", "embedding-3"),
            "api_key": zp_key,
            "openai_base_url": "https://open.bigmodel.cn/api/paas/v4",
        }

    project_dir = os.path.dirname(os.path.abspath(__file__))
    chroma_path = os.path.join(project_dir, "data", "mem0_chroma")

    return {
        "version": "v1.1",
        "llm": {
            "provider": "deepseek",
            "config": {
                "model": os.getenv("MEM0_LLM_MODEL", "deepseek-chat"),
                "temperature": 0.1,
                "max_tokens": 2000,
                "api_key": ds_key,
                "deepseek_base_url": "https://api.deepseek.com",
            },
        },
        "embedder": {
            "provider": "openai",
            "config": embedder_cfg,
        },
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": _COLLECTION,
                "path": chroma_path,
            },
        },
    }


def init_mem0():
    """初始化 mem0 长期记忆层。幂等，异常安全。返回是否启用。"""
    global _ENABLED, _MEMORY
    with _LOCK:
        if _MEMORY is not None:
            return _ENABLED
        try:
            from mem0 import Memory  # 延迟导入，未安装时降级

            cfg = _build_config()
            if cfg is None:
                logger.warning(
                    "[mem0] 缺 DEEPSEEK_API_KEY 或 embedder key"
                    "（SILICONFLOW_API_KEY / ZHIPU_API_KEY 二选一），长期记忆降级关闭"
                )
                _ENABLED = False
                return False
            _MEMORY = Memory.from_config(cfg)
            _ENABLED = True
            logger.info(
                "[mem0] 长期记忆层已初始化（DeepSeek + 硅基流动BAAI/bge-m3 + Chroma）"
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[mem0] 初始化失败，降级关闭：{e}")
            _ENABLED = False
            return False


def is_enabled():
    return _ENABLED


def search_memories(query, user_id, limit=5):
    """检索与 query 相关的长期记忆，返回记忆文本列表。"""
    if not _ENABLED or _MEMORY is None:
        return []
    try:
        # mem0 2.0+：search() 不再接受顶层 user_id，需用 filters
        res = _MEMORY.search(query=query, filters={"user_id": user_id}, limit=limit)
        items = res.get("results", []) if isinstance(res, dict) else res
        out = []
        for r in items:
            text = r.get("memory") or r.get("text") or ""
            if text:
                out.append(text)
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[mem0] search 失败：{e}")
        return []


def get_all_memories(user_id, limit=20):
    """取某用户的全部长期记忆（不依赖 query 相关性）。

    用于训练计划生成等需要『完整长期上下文』的场景——此时要的是用户全部
    已知事实（伤病/偏好/限制），而非按当前 query 相关性筛出来的子集。

    注意 mem0 2.0 的 get_all 签名：get_all(*, filters={"user_id": ...}, top_k=N)
    （不是 user_id= / limit=），否则会静默抛 TypeError。
    """
    if not _ENABLED or _MEMORY is None:
        return []
    try:
        res = _MEMORY.get_all(filters={"user_id": user_id}, top_k=limit)
        items = res.get("results", []) if isinstance(res, dict) else res
        out = []
        for r in items:
            text = r.get("memory") or r.get("text") or ""
            if text:
                out.append(text)
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[mem0] get_all 失败：{e}")
        return []


def add_memories(messages, user_id):
    """把一轮对话（[{role,content}, ...]）加入长期记忆（后台抽取事实）。"""
    if not _ENABLED or _MEMORY is None:
        return
    try:
        _MEMORY.add(messages=messages, user_id=user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[mem0] add 失败：{e}")


def delete_all_memories(user_id):
    """清空某用户的全部长期记忆（用于「忘掉一切」类指令）。"""
    if not _ENABLED or _MEMORY is None:
        return
    try:
        _MEMORY.delete_all(user_id=user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[mem0] delete_all 失败：{e}")


def format_mem0_context(memories):
    """把记忆列表格式化为可注入 LLM 的 prompt 段落。空时返回空串。"""
    if not memories:
        return ""
    lines = ["【用户长期记忆（跨会话，来自 mem0）】"]
    for m in memories:
        lines.append(f"- {m}")
    return "\n".join(lines)
