import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data_tool
import llm_agent


def query_runner_data(question: str) -> str:
    try:
        profile = data_tool.build_runner_profile()
    except Exception as e:
        return f"获取跑者数据失败：{e}"

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

    messages = [
        {
            "role": "system",
            "content": "你是跑步数据分析师，根据跑者数据回答用户问题，用中文回答，简洁实用，200字以内",
        },
        {
            "role": "user",
            "content": f"跑者数据：\n{profile_json}\n\n用户问题：{question}",
        },
    ]

    try:
        result = llm_agent.call_deepseek(messages, temperature=0.3)
        return result.get("content", "") if isinstance(result, dict) else str(result)
    except Exception as e:
        return f"数据分析失败：{e}"