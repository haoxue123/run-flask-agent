# Skill 注册中心

import json

SKILL_REGISTRY = {}

# render_tool_prompt() 输出缓存：SKILL_REGISTRY 在导入后基本不变（仅 register_skill 可能热插拔），
# 每次教练请求都会调用 render_tool_prompt()，缓存可省去重复的排序+拼接开销（性能优化）。
_TOOL_PROMPT_CACHE = None


def _default_parse_args(raw):
    """默认参数解析：尝试 JSON，失败返回空 dict。"""
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        return {}


def register_skill(name: str, description: str, fn: callable, args: list = None, parse_args=None) -> None:
    """将 skill 注册到 SKILL_REGISTRY

    Args:
        name:        skill 名称（LLM 调用时的 [TOOL:name]）
        description: 一句话能力描述（注入 system prompt 供 LLM 判断何时调用）
        fn:          skill 执行函数
        args:        必填参数名列表（用于参数校验与自动生成工具声明）
        parse_args:  将 LLM 传来的原始字符串解析为参数字典的函数；
                     内聚到各 skill，router 无需感知具体参数长相（消除泄漏抽象）
    """
    global _TOOL_PROMPT_CACHE
    _TOOL_PROMPT_CACHE = None  # 注册变更（含热插拔）使工具声明缓存失效
    SKILL_REGISTRY[name] = {
        "name": name,
        "description": description,
        "fn": fn,
        "args": args or [],
        "parse_args": parse_args or _default_parse_args,
    }


def get_skill(name: str):
    """获取注册项（含 fn / parse_args / args）。"""
    return SKILL_REGISTRY.get(name)


def tool_call(name: str, args: dict) -> dict:
    """通过名称调用已注册的 skill（错误可分型）。

    Returns:
        skill 函数的返回值，或 {"error": ..., "kind": "unknown_skill"|"exec_error"}
    """
    skill = SKILL_REGISTRY.get(name)
    if skill is None:
        return {"error": f"未知 Skill: {name}", "kind": "unknown_skill"}
    try:
        return skill["fn"](**args)
    except Exception as e:
        return {"error": str(e), "kind": "exec_error"}


def parse_and_call(name: str, raw_args_str: str) -> dict:
    """解析参数并执行 skill（router 统一入口，错误可分型）。

    错误类型：
        unknown_skill — 技能不存在
        bad_args      — 参数解析失败或缺少必填参数
        exec_error    — 执行抛出异常
    """
    skill = SKILL_REGISTRY.get(name)
    if skill is None:
        return {"error": f"未知 Skill: {name}", "kind": "unknown_skill"}
    try:
        args = skill["parse_args"](raw_args_str)
    except Exception as e:
        return {"error": f"参数解析失败: {e}", "kind": "bad_args"}
    required = skill.get("args") or []
    missing = [a for a in required if a not in (args or {})]
    if missing:
        return {"error": f"缺少必填参数: {missing}", "kind": "bad_args"}
    try:
        return skill["fn"](**args)
    except Exception as e:
        return {"error": str(e), "kind": "exec_error"}


def render_tool_prompt() -> str:
    """从 SKILL_REGISTRY 单一事实源生成 [TOOL:...] 工具声明块。

    作为「热插拔」落地的关键：新增/删除 skill 只需改注册中心，
    LLM 可见的工具声明会自动同步，无需手工维护 prompts/coach_chat_system.md。
    """
    global _TOOL_PROMPT_CACHE
    if _TOOL_PROMPT_CACHE is not None:
        return _TOOL_PROMPT_CACHE
    lines = [
        "## 可用工具（Skill）",
        "",
        "你可以调用以下工具来获取数据或生成图表。调用格式为：",
        "[TOOL:工具名]{\"参数名\": \"参数值\"}[/TOOL]",
        "",
    ]
    for i, (name, info) in enumerate(sorted(SKILL_REGISTRY.items()), 1):
        lines.append(f"### {i}. {name} — {info['description']}")
        arg_names = info.get("args") or []
        if arg_names:
            args_doc = "{" + ", ".join(f'"{a}"' for a in arg_names) + "}"
            lines.append(f"参数：{args_doc}")
        lines.append("")

    lines += [
        "重要规则：",
        "- 只有在确实需要数据或图表时才调用工具",
        "- 调用工具时，回答中只需包含 TOOL 标记，不要添加其他内容",
        "- 工具执行后你会收到结果，再基于结果生成最终回答",
    ]
    rendered = "\n".join(lines)
    _TOOL_PROMPT_CACHE = rendered
    return rendered


def _parse_data_query(raw):
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"question": raw}


def _parse_chart_generate(raw):
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"data_context": raw}


# 自动注册内置 skills
try:
    from skills.data_query import query_runner_data
    register_skill(
        name="data_query",
        description="查询跑者数据，用自然语言问题获取数据摘要",
        fn=query_runner_data,
        args=["question"],
        parse_args=_parse_data_query,
    )
except ImportError as e:
    print(f"[SKILL] 注册 data_query 失败（已跳过）：{e}")

try:
    from skills.chart_generate import generate_chart
    register_skill(
        name="chart_generate",
        description="生成配速图表 PNG",
        fn=generate_chart,
        args=["data_context"],
        parse_args=_parse_chart_generate,
    )
except ImportError as e:
    print(f"[SKILL] 注册 chart_generate 失败（已跳过）：{e}")
