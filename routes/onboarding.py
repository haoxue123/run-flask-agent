"""引导与注册系统路由 — 轻量身份 + 水平评估 + 计划推荐（无密码鉴权）

第一性原理取舍：
- 单用户作品不需要"账号/密码/多租户"；真正的价值是「个性化」与「按水平推荐计划」。
- 因此"注册"被收敛为「写入教练名 + 用户昵称」到既有的单画像记忆层，
  不引入登录墙、不新建用户库。
- 水平/意图/目标被映射为科学内核（generate_training_plan）的入口参数，
  由确定性内核生成计划，而非让 LLM 凭空编造。
"""
import os
import json
import tempfile
import shutil
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app

import data_tool
import llm_agent
import memory

onboarding_bp = Blueprint("onboarding", __name__, url_prefix="/api/onboarding")

# ----------------------------------------------------------------
# 预设映射：水平 / 意图 / 目标距离（科学内核的"入口旋钮"）
# ----------------------------------------------------------------
LEVEL_PRESETS = {
    "beginner": {
        "label": "初跑者（跑龄 < 6 个月 / 能连续跑 5K）",
        "plan_duration": 30,
        "weekly_volume_km": 15,
        "focus": "基础耐力 + 正确跑姿 + 防伤",
        "recommend_goal": "10K 完赛",
    },
    "intermediate": {
        "label": "进阶跑者（规律训练半年以上）",
        "plan_duration": 30,
        "weekly_volume_km": 35,
        "focus": "阈值跑 + 节奏跑 + 里程积累",
        "recommend_goal": "半马完赛",
    },
    "advanced": {
        "label": "成熟跑者（有比赛经验 / 周期化训练）",
        "plan_duration": 90,
        "weekly_volume_km": 55,
        "focus": "专项强度 + 周期化 + 峰值训练",
        "recommend_goal": "全马破纪录",
    },
}

INTENT_PRESETS = {
    "finish": {"label": "安全完赛", "emphasis": "稳步积累里程，优先避免受伤"},
    "race": {"label": "追求成绩", "emphasis": "加入间歇/阈值，周期化强度负荷"},
    "health": {"label": "健康维持", "emphasis": "轻松有氧为主，规律即可"},
    "weight_loss": {"label": "减脂塑形", "emphasis": "中低强度高频次，配合饮食缺口"},
}

GOAL_DISTANCE = {
    "5k": "5公里",
    "10k": "10公里",
    "half": "半程马拉松",
    "full": "全程马拉松",
}


# ----------------------------------------------------------------
# 内核对接
# ----------------------------------------------------------------
def _build_runner_profile(onboarding):
    """用引导输入合成一个喂给科学内核的跑者画像（不依赖 GPS 文件）。"""
    goal = onboarding.get("goal") or "10k"
    dist = GOAL_DISTANCE.get(goal, "10公里")
    # 复用既有画像构建（无运动文件时返回基础结构，内核降级处理）
    profile = data_tool.build_runner_profile()
    target = profile.setdefault("目标", {})
    target["目标距离"] = dist
    if onboarding.get("target_time"):
        target["目标完赛时间"] = onboarding["target_time"]
    profile["水平"] = onboarding.get("level")
    profile["训练意图"] = onboarding.get("intent")
    profile["可用训练日"] = onboarding.get("available_days", [])
    return profile


def _write_plan_json(data_dir, structured, meta, science):
    """原子写入 plan.json（与 routes/plan.py 同策略：临时文件 + 备份）。"""
    plan_path = os.path.join(data_dir, "plan.json")
    payload = {"plan": structured, "meta": meta}
    if science is not None:
        payload["science"] = science
    if os.path.isfile(plan_path):
        try:
            shutil.copy2(plan_path, plan_path + ".bak")
        except OSError:
            pass
    fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=data_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, plan_path)
    except Exception:
        if os.path.isfile(tmp_path):
            os.unlink(tmp_path)
        raise


def recommend_and_generate(onboarding, data_dir):
    """按水平/意图合成画像 → 调科学内核生成计划 → 落 plan.json。"""
    profile = _build_runner_profile(onboarding)
    preset = LEVEL_PRESETS.get(onboarding.get("level"), LEVEL_PRESETS["intermediate"])
    plan_duration = int(onboarding.get("plan_duration") or preset["plan_duration"])
    result = llm_agent.generate_training_plan(profile, plan_duration=plan_duration)
    structured = result["structured"]
    science = result.get("science")
    target = profile.get("目标", {})
    meta = {
        "generated_at": datetime.now().isoformat(),
        "plan_duration": plan_duration,
        "target_race": target.get("目标距离", ""),
        "target_time": target.get("目标完赛时间", ""),
        "source": "onboarding",
    }
    _write_plan_json(data_dir, structured, meta, science)
    return {
        "plan": result["plan_text"],
        "structured": structured,
        "science": science,
        "preset": preset,
        "plan_duration": plan_duration,
    }


# ----------------------------------------------------------------
# 接口
# ----------------------------------------------------------------
@onboarding_bp.route("", methods=["GET"])
def get_state():
    """返回当前身份/引导状态 + 可选预设（供前端向导渲染）。"""
    profile = memory.load_long_term_memory()
    return jsonify({
        "identity": profile.get("identity", {}),
        "onboarding": profile.get("onboarding", {}),
        "levels": LEVEL_PRESETS,
        "intents": INTENT_PRESETS,
        "goals": GOAL_DISTANCE,
    })


@onboarding_bp.route("/identity", methods=["POST"])
def save_identity():
    """仅保存教练名 + 用户昵称（轻量"注册"的核心个性化数据）。"""
    data = request.get_json(silent=True) or {}
    profile = memory.load_long_term_memory()
    profile.setdefault("identity", {})
    profile["identity"]["coach_name"] = (data.get("coach_name") or "").strip()
    profile["identity"]["user_nickname"] = (data.get("user_nickname") or "").strip()
    memory.save_long_term_memory(profile)
    return jsonify({"success": True, "identity": profile["identity"]})


@onboarding_bp.route("/complete", methods=["POST"])
def complete():
    """完成引导：保存身份 + 水平/意图/目标/可用日 → 生成推荐计划。

    即使 DeepSeek 计划生成失败（如密钥缺失），引导状态仍落盘成功，
    前端据此提示用户稍后手动生成，不阻断引导流程。
    """
    data = request.get_json(silent=True) or {}
    profile = memory.load_long_term_memory()
    profile.setdefault("identity", {})
    profile["identity"]["coach_name"] = (data.get("coach_name") or "").strip()
    profile["identity"]["user_nickname"] = (data.get("user_nickname") or "").strip()

    level = data.get("level", "")
    onboarding = profile.setdefault("onboarding", {})
    onboarding.update({
        "level": level,
        "intent": data.get("intent", ""),
        "goal": data.get("goal", ""),
        "target_time": (data.get("target_time") or "").strip(),
        "available_days": data.get("available_days", []),
        "plan_duration": int(
            data.get("plan_duration")
            or LEVEL_PRESETS.get(level, {}).get("plan_duration", 30)
        ),
        "completed": True,
    })
    memory.save_long_term_memory(profile)

    try:
        gen = recommend_and_generate(onboarding, current_app.config["DATA_DIR"])
    except Exception as e:  # 计划生成依赖外部 API，失败不应阻断引导
        return jsonify({
            "success": True,
            "identity": profile["identity"],
            "onboarding": onboarding,
            "plan_error": f"{type(e).__name__}: {e}",
        })

    return jsonify({
        "success": True,
        "identity": profile["identity"],
        "onboarding": onboarding,
        "plan": gen["plan"],
        "structured": gen["structured"],
        "science": gen["science"],
        "preset": gen["preset"],
        "plan_duration": gen["plan_duration"],
    })
