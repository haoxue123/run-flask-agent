"""周复盘闭环引擎
================

第一性原理：一个教练型训练 Agent 的真正价值，不在于"出一张计划表"，
而在于"现实偏离计划时，能动态适配"。本模块把**已存的训练计划**与
**实际执行数据**做比对，用确定性科学内核（TRIMP / ACWR / 完成度）量化偏差，
推导出下周的调整方案（跑量 / 强度 / 恢复）。

设计约束（与 physiology.py 一致）：
  1. 确定性部分不依赖 LLM —— 可离线运行、可单测、可审计。
  2. 纯标准库 + 既有科学模块，不引入新依赖。
  3. 所有偏差判断基于可解释的规则，而非黑盒。

数据来源：
  - 计划：data/plan.json（structured 计划，含每天 distance_km / actual_*）
  - 实际执行：
      a) 计划里已记录的 actual_distance_km（最可靠，绑定具体训练日）
      b) 上传文件解析的近期跑步（data_tool.build_runner_profile）
      c) 手动录入（data/manual_input.json）
"""

from __future__ import annotations

import os
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import data_tool
import training_science
import physiology
import memory
import safeio

DEFAULT_DATA_DIR = getattr(data_tool, "DEFAULT_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))


# ============================================================
# 加载层
# ============================================================
def load_plan(data_dir: str = DEFAULT_DATA_DIR):
    """加载 data/plan.json，返回 (plan_list, meta)。无计划返回 (None, None)。"""
    path = os.path.join(data_dir, "plan.json")
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None, None
    plan = data.get("plan", [])
    if isinstance(plan, str):  # 兼容极旧格式（纯文本），本引擎只处理结构化
        plan = []
    return plan, data.get("meta", {})


def _norm_distance(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def load_actuals(data_dir: str = DEFAULT_DATA_DIR) -> List[Dict[str, Any]]:
    """汇聚实际执行数据：计划按天记录 + 上传解析 + 手动录入。"""
    actuals: List[Dict[str, Any]] = []

    # 1) 计划里已记录的 actual_*（最可靠，绑定到具体训练日）
    plan, _ = load_plan(data_dir)
    if plan:
        for d in plan:
            if d.get("actual_distance_km") is not None:
                actuals.append({
                    "source": "plan_day",
                    "date": None,
                    "distance_km": _norm_distance(d.get("actual_distance_km")),
                    "pace_sec": _norm_distance(d.get("actual_pace_sec")),
                    "duration_min": _norm_distance(d.get("actual_duration_min")),
                    "type": d.get("type"),
                })

    # 2) 上传文件解析的近期跑步（用 get_all_runs：全量、不依赖缓存，避免漏掉窗口内数据）
    try:
        for r in data_tool.get_all_runs(data_dir=data_dir):
            actuals.append({
                "source": "upload",
                "date": r.get("日期"),
                "distance_km": _norm_distance(r.get("距离_km")),
                "pace_sec": _norm_distance(r.get("平均配速_秒每公里")),
                "duration_min": _norm_distance(r.get("时长_分钟")),
                "hr": _norm_distance(r.get("平均心率")),
            })
    except Exception:
        pass

    # 3) 手动录入
    manual_path = os.path.join(data_dir, "manual_input.json")
    if os.path.isfile(manual_path):
        try:
            with open(manual_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for r in (cfg.get("近期跑步数据") or []):
                actuals.append({
                    "source": "manual",
                    "date": r.get("日期"),
                    "distance_km": _norm_distance(r.get("距离_km")),
                    "pace_sec": _norm_distance(r.get("平均配速_秒每公里")),
                    "duration_min": _norm_distance(r.get("时长_分钟")),
                    "hr": _norm_distance(r.get("平均心率")),
                })
        except (json.JSONDecodeError, OSError):
            pass

    return actuals


# ============================================================
# 测量层：计划 vs 实际
# ============================================================
def compute_compliance(plan, actuals, window_days: int = 7,
                      start_date=None, end_date=None) -> Dict[str, Any]:
    """比对计划与实际，输出完成度指标。

    采用"跑量 + 频次"双指标，避免脆弱的日期精确匹配：
      - planned_volume_km：计划里所有非休息且有距离的训练日距离之和
      - actual_volume_km：实际执行（落在窗口内，或无日期的 plan_day actual）的距离之和

    测量窗口优先锚定到计划自身的覆盖区间 [start_date, end_date]；
    若未传入 start_date/end_date，则退回"相对于今天 window_days 天"的兼容行为。
    """
    planned_runs = [
        d for d in plan
        if not d.get("is_rest") and _norm_distance(d.get("distance_km"))
    ]
    planned_volume = sum(_norm_distance(d.get("distance_km")) or 0 for d in planned_runs)
    planned_run_count = len(planned_runs)

    def in_window(a):
        # 计划内 actual 无日期（绑定到具体训练日），一律计入
        if a.get("date") is None:
            return True
        try:
            d = datetime.strptime(str(a["date"]), "%Y-%m-%d")
        except (ValueError, TypeError):
            return True
        if start_date is not None and end_date is not None:
            return start_date <= d <= end_date
        cutoff = datetime.now() - timedelta(days=window_days)
        return d >= cutoff

    recent = [a for a in actuals if in_window(a)]
    actual_volume = sum(a.get("distance_km") or 0 for a in recent)
    actual_run_count = len([a for a in recent if (a.get("distance_km") or 0) > 0])

    compliance = round(actual_volume / planned_volume, 2) if planned_volume > 0 else None
    missed = max(0, planned_run_count - actual_run_count)

    return {
        "planned_volume_km": round(planned_volume, 1),
        "actual_volume_km": round(actual_volume, 1),
        "planned_run_count": planned_run_count,
        "actual_run_count": actual_run_count,
        "executed_in_period": actual_run_count > 0,
        "compliance_ratio": compliance,
        "compliance_pct": round(compliance * 100) if compliance is not None else None,
        "missed_runs": missed,
        "window_days": window_days,
    }


def compute_load(actuals, ref_date=None, acute_days: int = 7,
                 chronic_days: int = 28) -> Dict[str, Any]:
    """用有心率的跑步计算急性/慢性负荷与 ACWR。

    acute = 近 acute_days 天 TRIMP；chronic = 近 chronic_days 天 TRIMP（无日期的记录保守计入 chronic）。
    负荷窗口锚定到 ref_date（默认今天）；传入计划结束日即可评估"计划期末"的身体负荷。
    """
    if ref_date is None:
        ref_date = datetime.now()
    cutoff7 = ref_date - timedelta(days=acute_days)
    cutoff28 = ref_date - timedelta(days=chronic_days)

    def parse_date(a):
        if not a.get("date"):
            return None
        try:
            return datetime.strptime(str(a["date"]), "%Y-%m-%d")
        except (ValueError, TypeError):
            return None

    acute, chronic = [], []
    for a in actuals:
        if not (a.get("duration_min") and a.get("hr")):
            continue
        run = {"时长_分钟": a["duration_min"], "平均心率": a["hr"]}
        d = parse_date(a)
        if d is None:
            chronic.append(run)
            continue
        if d >= cutoff7:
            acute.append(run)
        if d >= cutoff28:
            chronic.append(run)

    acute_trimp = training_science.calc_weekly_trimp(acute)
    chronic_trimp = training_science.calc_weekly_trimp(chronic)
    acwr = training_science.calc_acwr(acute_trimp, chronic_trimp)
    risk = training_science.format_acwr_risk(acwr)

    return {
        "acute_trimp": acute_trimp,
        "chronic_trimp": chronic_trimp,
        "acwr": acwr,
        "acwr_risk": risk,
        "has_hr_data": bool(acute or chronic),
    }


# ============================================================
# 决策层：推导下周调整方案（确定性规则）
# ============================================================
def derive_adjustments(compliance, load, profile: Optional[dict] = None) -> Dict[str, Any]:
    """基于完成度与负荷，推导下周调整动作。所有规则可解释。"""
    reasons: List[str] = []
    actions = {
        "volume": "维持",       # 跑量：维持 / 增量 / 减量
        "intensity": "维持",    # 强度：维持 / 降强度
        "recovery": "维持",     # 恢复：维持 / 增加恢复日
    }
    risk = "安全"

    # 1) 负荷风险（ACWR）
    acwr = load.get("acwr")
    if acwr is not None:
        if acwr > 1.5:
            risk = "高风险"
            actions["volume"] = "减量"
            actions["intensity"] = "降强度"
            actions["recovery"] = "增加恢复日"
            reasons.append(
                f"负荷风险高（ACWR={acwr}）：近一周负荷相对长期负荷骤增，受伤风险显著，"
                f"必须减量并增加恢复日。"
            )
        elif acwr >= 1.3:
            risk = "中等风险"
            actions["recovery"] = "增加恢复日"
            reasons.append(
                f"负荷风险中等（ACWR={acwr}）：负荷偏高，增加恢复日、强度不再上调。"
            )
        elif acwr < 0.8:
            reasons.append(
                f"负荷偏低（ACWR={acwr}）：训练不足，身体适应前提下可小幅增量。"
            )
            if actions["volume"] == "维持":
                actions["volume"] = "增量"

    # 2) 计划完成度
    comp = compliance.get("compliance_ratio") if compliance else None
    executed_in_period = bool(compliance.get("executed_in_period")) if compliance else False
    if comp is not None and executed_in_period:
        if comp < 0.6:
            if actions["volume"] != "减量":
                actions["volume"] = "减量"
            reasons.append(
                f"计划完成度仅 {compliance['compliance_pct']}%：实际跑量明显低于计划，"
                f"下周适当减量，避免欠账堆积造成挫败与受伤。"
            )
        elif comp >= 0.9 and risk in ("安全", "训练不足"):
            if actions["volume"] == "维持":
                actions["volume"] = "增量"
            reasons.append(
                f"计划完成度 {compliance['compliance_pct']}%：执行良好且负荷安全，"
                f"可小幅增量（5–10%）继续进步。"
            )

    # 3) 伤病史（保守优先）
    if profile:
        injuries = (profile.get("history", {}) or {}).get("injuries") or []
        if injuries:
            reasons.append(f"伤病史：{', '.join(injuries)}。保守处理：强度不增、恢复日充足。")
            if actions["intensity"] == "维持":
                actions["intensity"] = "降强度"
            if actions["recovery"] == "维持":
                actions["recovery"] = "增加恢复日"

    if not reasons:
        reasons.append("计划执行与负荷均处于健康区间，维持当前节奏即可。")

    return {"risk_level": risk, "actions": actions, "reasons": reasons}


# ============================================================
# 编排层：生成完整复盘报告
# ============================================================
def _plan_window(meta) -> Tuple[Optional[datetime], Optional[datetime]]:
    """从计划 meta 推导计划覆盖区间 [start, end]；缺失则返回 (None, None)。"""
    if not meta:
        return None, None
    gen = meta.get("generated_at")
    duration = meta.get("plan_duration")
    if not gen or not duration:
        return None, None
    try:
        start = datetime.fromisoformat(gen)
        end = start + timedelta(days=int(duration))
        return start, end
    except (ValueError, TypeError):
        return None, None


def build_review_report(data_dir: str = DEFAULT_DATA_DIR, profile: Optional[dict] = None) -> Dict[str, Any]:
    """一站式生成周复盘报告（确定性部分，不调用 LLM）。"""
    plan, meta = load_plan(data_dir)
    actuals = load_actuals(data_dir)
    if profile is None:
        try:
            profile = memory.load_long_term_memory()
        except Exception:
            profile = {}

    has_plan = bool(plan)
    has_actuals = bool(actuals)

    # 测量窗口锚定到计划自身的覆盖区间；无计划或元信息缺失时退回"相对今天"的兼容行为
    start, end = _plan_window(meta)
    if has_plan:
        if start and end:
            compliance = compute_compliance(plan, actuals, start_date=start, end_date=end)
        else:
            compliance = compute_compliance(plan, actuals)
    else:
        compliance = None
    if has_actuals:
        load = compute_load(actuals, ref_date=end) if end else compute_load(actuals)
    else:
        load = {
            "acute_trimp": None, "chronic_trimp": None, "acwr": None,
            "acwr_risk": training_science.format_acwr_risk(None), "has_hr_data": False,
        }

    adjustments = derive_adjustments(compliance, load, profile)
    report_text = _format_report_text(has_plan, has_actuals, compliance, load, adjustments, meta)

    return {
        "generated_at": datetime.now().isoformat(),
        "has_plan": has_plan,
        "has_actuals": has_actuals,
        "compliance": compliance,
        "load": load,
        "adjustments": adjustments,
        "report_text": report_text,
    }


def _format_report_text(has_plan, has_actuals, compliance, load, adjustments, meta) -> str:
    """把结构化复盘渲染为自然语言（中文）。"""
    lines = ["📊 **本周训练复盘**", ""]
    if not has_plan:
        lines.append("⚠️ 还没有训练计划。请先生成一份计划，复盘才有对照基准。")
        return "\n".join(lines)
    if not has_actuals:
        lines.append("⚠️ 还没有实际执行数据。请上传运动文件（GPX/TCX/FIT）或手动记录本周跑步，"
                     "复盘才能量化偏差。")
        # 仍给出负荷与计划的静态信息
        comp = compliance
        adj = adjustments
        lines.append("")
        lines.append(f"📅 当前计划周期：{meta.get('plan_duration', '?')} 天")
        if comp:
            lines.append(f"📏 计划周跑量：{comp['planned_volume_km']} km（{comp['planned_run_count']} 个跑步日）")
        lines.append(f"🧭 调整建议（基于静态信息）：跑量{adj['actions']['volume']}、"
                     f"强度{adj['actions']['intensity']}、{adj['actions']['recovery']}。")
        return "\n".join(lines)

    comp = compliance
    load_info = load
    adj = adjustments

    # 完成度
    lines.append(f"📅 计划周期：{meta.get('plan_duration', '?')} 天")
    if comp:
        if comp.get("executed_in_period"):
            lines.append(
                f"📏 计划跑量 {comp['planned_volume_km']} km / 实际 {comp['actual_volume_km']} km"
                f"（完成度 {comp['compliance_pct'] if comp['compliance_pct'] is not None else '—'}%）"
            )
            lines.append(f"🏃 计划跑步日 {comp['planned_run_count']} 个 / 实际完成 {comp['actual_run_count']} 个"
                         f"（缺 {comp['missed_runs']} 个）")
        else:
            lines.append(
                f"📏 计划周期跑量目标 {comp['planned_volume_km']} km，"
                f"但周期内未匹配到执行数据（实际数据可能早于计划开始日，或尚未上传）。"
            )

    # 负荷
    if load_info.get("has_hr_data"):
        acwr = load_info.get("acwr")
        risk = load_info.get("acwr_risk", {})
        lines.append(f"⚡ 急性负荷(近7天) TRIMP={load_info.get('acute_trimp')}；"
                     f"慢性负荷(近28天) TRIMP={load_info.get('chronic_trimp')}")
        lines.append(f"📈 ACWR={acwr}（{risk.get('level', '未知')}）— {risk.get('advice', '')}")
    else:
        lines.append("⚡ 暂无带心率的执行数据，无法计算负荷/ACWR（建议手表同步或记录心率）。")

    # 调整建议
    lines.append("")
    lines.append("🔧 **下周调整建议**")
    a = adj["actions"]
    lines.append(f"  - 跑量：{a['volume']}")
    lines.append(f"  - 强度：{a['intensity']}")
    lines.append(f"  - 恢复：{a['recovery']}")
    lines.append("")
    lines.append("💡 **依据**")
    for r in adj["reasons"]:
        lines.append(f"  - {r}")

    return "\n".join(lines)


# ============================================================
# 可选的 LLM 增强：生成下周计划（依赖 API，失败安全降级）
# ============================================================
def suggest_next_week_plan(data_dir: str = DEFAULT_DATA_DIR) -> Dict[str, Any]:
    """基于复盘结果，调用 LLM 生成并保存下周计划（覆盖 plan.json）。"""
    report = build_review_report(data_dir)
    adj = report.get("adjustments", {})
    a = adj.get("actions", {})
    comp_pct = report.get("compliance", {})
    comp_pct = comp_pct.get("compliance_pct") if comp_pct else None
    instruction = (
        f"基于本周复盘：计划完成度{comp_pct if comp_pct is not None else '未知'}%，"
        f"负荷风险等级「{adj.get('risk_level')}」。"
        f"下周请按以下方向调整：跑量{a.get('volume', '维持')}、"
        f"强度{a.get('intensity', '维持')}、{a.get('recovery', '维持')}。"
        f"保持训练日总数与既有节奏大致一致，只调整距离/强度分布。"
    )
    try:
        import llm_agent
        profile = memory.load_long_term_memory()
        result = llm_agent.generate_training_plan(
            profile, plan_duration=7, user_instructions=instruction
        )
        plan_path = os.path.join(data_dir, "plan.json")
        payload = {
            "plan": result.get("structured", []),
            "meta": {
                "generated_at": datetime.now().isoformat(),
                "plan_duration": 7,
                "source": "weekly_review",
                "review_instruction": instruction,
            },
            "science": result.get("science", {}),
        }
        safeio.safe_write_json(plan_path, payload)
        return {"ok": True, "plan_text": result.get("plan_text", ""), "instruction": instruction}
    except Exception as e:
        return {"ok": False, "error": str(e), "instruction": instruction}


if __name__ == "__main__":
    rep = build_review_report()
    print(json.dumps(rep, ensure_ascii=False, indent=2))
