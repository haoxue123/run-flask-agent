# Skill 注册中心

SKILL_REGISTRY = {}


def register_skill(name: str, description: str, fn: callable) -> None:
    """将 skill 注册到 SKILL_REGISTRY"""
    SKILL_REGISTRY[name] = {
        "name": name,
        "description": description,
        "fn": fn,
    }


def tool_call(name: str, args: dict) -> dict:
    """通过名称调用已注册的 skill

    Args:
        name: skill 名称
        args: 传递给 skill 函数的参数

    Returns:
        skill 函数的返回值，或包含 error 的 dict
    """
    skill = SKILL_REGISTRY.get(name)
    if skill is None:
        return {"error": f"未知 Skill: {name}"}

    try:
        return skill["fn"](**args)
    except Exception as e:
        return {"error": str(e)}


# 自动注册内置 skills
try:
    from skills.data_query import query_runner_data
    register_skill(
        name="data_query",
        description="查询跑者数据，用自然语言问题获取数据摘要",
        fn=query_runner_data,
    )
except ImportError as e:
    print(f"[SKILL] 注册 data_query 失败（已跳过）：{e}")

try:
    from skills.chart_generate import generate_chart
    register_skill(
        name="chart_generate",
        description="生成配速图表 PNG",
        fn=generate_chart,
    )
except ImportError as e:
    print(f"[SKILL] 注册 chart_generate 失败（已跳过）：{e}")