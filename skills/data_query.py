import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data_tool
import llm_agent


# 会话级 profile 缓存：同一数据目录下只重建一次（P2-1）
_PROFILE_CACHE = {"key": None, "profile": None}


def _profile_cache_key():
    try:
        return os.path.getmtime(data_tool.DEFAULT_DATA_DIR)
    except OSError:
        return None


def _get_profile():
    key = _profile_cache_key()
    if _PROFILE_CACHE["key"] == key and _PROFILE_CACHE["profile"] is not None:
        return _PROFILE_CACHE["profile"]
    prof = data_tool.build_runner_profile()
    _PROFILE_CACHE["key"] = key
    _PROFILE_CACHE["profile"] = prof
    return prof


def _deterministic_answer(question: str, profile: dict):
    """确定性优先：可精确计算的量化问题直接作答，不调用 LLM。

    对应第一性原理 G5（事实/安全不靠 LLM 自由发挥）与 G6（可降级）。
    命中量化意图返回字符串；推理/建议类或无法确定的量化意图返回 None（委派 LLM）。
    """
    q = question or ""
    runs = profile.get("近期跑步数据")
    if not isinstance(runs, list):
        runs = []

    # 推理/建议类 → 交给 LLM（需要自然语言推理）
    if any(k in q for k in ("为什么", "怎么", "建议", "计划", "如何", "应该", "差别", "原因")):
        return None

    total_km = sum(float(r.get("距离_km") or 0) for r in runs)
    if any(k in q for k in ("公里", "距离", "多少", "跑量")):
        return f"近期累计跑步 {total_km:.1f} 公里（共 {len(runs)} 次记录）。"
    if "配速" in q:
        paces = [float(r["平均配速_秒每公里"]) for r in runs if r.get("平均配速_秒每公里")]
        if paces:
            avg = sum(paces) / len(paces)
            return f"近期平均配速约 {data_tool.format_pace(avg)}（基于 {len(paces)} 次记录）。"
        return "暂无足够配速数据来计算平均配速。"
    if any(k in q for k in ("次数", "几回", "几跑", "多少跑")):
        return f"近期共有 {len(runs)} 次跑步记录。"

    # 其他未识别意图，保守返回 None 交 LLM
    return None


def _rag_fallback(question: str, profile: dict):
    """P3-2：跑者无真实数据（无法靠 data_query 回答）时，回退 RAG 检索知识库。

    双引擎协同：有数据优先用数据，无数据才借知识库，二者不孤立。
    无 chromadb 时优雅跳过（rag 导入失败即返回 None）。
    """
    runs = profile.get("近期跑步数据") or []
    if runs:
        return None  # 有真实数据，不需 RAG
    try:
        import rag
    except ImportError:
        return None
    try:
        results = rag.retrieve_knowledge(question, top_k=3)
        if not results:
            return None
        return rag.format_knowledge_context(results)
    except Exception:
        return None


def query_runner_data(question: str) -> str:
    try:
        profile = _get_profile()
    except Exception as e:
        return f"获取跑者数据失败：{e}"

    # 确定性优先：可精确计算的量化问题不调用 LLM（G5 确定性兜底 / G6 可降级）
    det = _deterministic_answer(question, profile)
    if det is not None:
        return det

    # 精简画像：只保留关键字段，避免上下文过大
    slim = {}
    slim["目标"] = profile.get("目标", {})
    slim["基本信息"] = profile.get("基本信息", {})
    recent_runs = profile.get("近期跑步数据", [])
    if isinstance(recent_runs, list):
        slim["近期跑步数据"] = [
            {k: r.get(k) for k in ("日期", "距离_km", "平均配速_秒每公里", "平均心率", "活动名称") if k in r}
            for r in recent_runs[:10]
        ]
    health = profile.get("健康指标", {})
    if health:
        slim["健康指标"] = {k: v for k, v in health.items() if v is not None}
    fitness = profile.get("体能指标", {})
    if fitness:
        slim["体能指标"] = {k: v for k, v in fitness.items() if v is not None}

    profile_json = json.dumps(slim, ensure_ascii=False, indent=2)

    # P3-2：无数据时回退 RAG，把知识片段注入上下文
    rag_ctx = _rag_fallback(question, profile)
    user_content = f"跑者数据：\n{profile_json}\n\n用户问题：{question}"
    if rag_ctx:
        user_content += f"\n\n【相关知识库补充】\n{rag_ctx}"

    messages = [
        {
            "role": "system",
            "content": "你是跑步数据分析师，根据跑者数据回答用户问题，用中文回答，简洁实用，200字以内",
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]

    try:
        result = llm_agent.call_deepseek(messages, temperature=0.3)
        return result.get("content", "") if isinstance(result, dict) else str(result)
    except Exception as e:
        # 可降级：LLM 失败时用确定性摘要兜底，而非直接报错
        fallback = _deterministic_answer(question, profile)
        if fallback:
            return fallback
        return f"数据分析失败：{e}"
