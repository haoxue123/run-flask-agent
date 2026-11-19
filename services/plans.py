"""Plan domain service: generation, compatibility reads and atomic updates."""
import os
from datetime import datetime
import data_tool
import llm_agent
import visualize
from services.storage import atomic_write_json, load_json


class PlanError(ValueError):
    pass


def plan_path(data_dir):
    return os.path.join(data_dir, "plan.json")


def find_latest_chart(chart_dir):
    if not os.path.isdir(chart_dir):
        return None
    charts = sorted((name for name in os.listdir(chart_dir)
                     if name.startswith("pace_chart_") and name.endswith(".png")), reverse=True)
    return f"/chart/{charts[0]}" if charts else None


def load_plan(data_dir, chart_dir):
    payload = load_json(plan_path(data_dir))
    if not isinstance(payload, dict):
        return None
    plan = payload.get("plan", [])
    if isinstance(plan, str):
        plan = llm_agent._parse_plan_to_structured(plan)
    meta = payload.get("meta", {}) or {}
    if not meta.get("chart_url"):
        meta = dict(meta)
        meta["chart_url"] = find_latest_chart(chart_dir) or ""
    return {"plan": plan, "meta": meta, "science": payload.get("science")}


def save_plan(data_dir, data):
    if not isinstance(data, dict):
        raise PlanError("请求体必须是 JSON 对象")
    existing = load_json(plan_path(data_dir), {}) or {}
    plan = data.get("plan") or data.get("days", [])
    if not isinstance(plan, list):
        raise PlanError("plan 必须为数组")
    science = data["science"] if "science" in data else existing.get("science")
    meta = {"generated_at": data.get("generated_at", datetime.now().isoformat()),
            "plan_duration": data.get("plan_duration", 7), "target_race": data.get("target_race", ""),
            "target_time": data.get("target_time", ""), "chart_url": data.get("chart_url", "")}
    payload = {"plan": plan, "meta": meta}
    if science is not None:
        payload["science"] = science
    atomic_write_json(plan_path(data_dir), payload, backup=True)
    return payload


def update_day(data_dir, request_data):
    if not isinstance(request_data, dict) or request_data.get("day") is None:
        raise PlanError("缺少 day 字段")
    try:
        day = int(request_data["day"])
    except (ValueError, TypeError) as exc:
        raise PlanError(f"day 字段无效：{request_data.get('day')}") from exc
    path = plan_path(data_dir)
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise FileNotFoundError("计划文件不存在")
    for item in payload.get("plan", []):
        if item.get("day") == day:
            for key in ("completed", "actual_distance_km", "actual_pace_sec", "actual_duration_min", "feeling"):
                if key in request_data:
                    item[key] = request_data[key]
            atomic_write_json(path, payload, backup=True)
            return payload
    raise PlanError(f"未找到 day={day} 的训练日")


def generate_plan(profile, duration, instructions, data_dir, chart_dir):
    result = llm_agent.generate_training_plan(profile, plan_duration=duration, user_instructions=instructions)
    target = profile.get("目标", {}) or {}
    filename = f"pace_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    os.makedirs(chart_dir, exist_ok=True)
    visualize.plot_pace_chart(result["structured"], output_path=os.path.join(chart_dir, filename))
    payload = {"plan": result["structured"], "meta": {"generated_at": datetime.now().isoformat(),
               "plan_duration": duration, "target_race": target.get("目标比赛", ""),
               "target_time": target.get("目标完赛时间", ""), "chart_url": f"/chart/{filename}"},
               "science": result.get("science", {})}
    atomic_write_json(plan_path(data_dir), payload, backup=True)
    return result, payload
