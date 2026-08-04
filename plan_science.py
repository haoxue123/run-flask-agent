"""
计划科学增强模块（Phase 1 上层逻辑）
============================================================
消费 llm_agent 解析出的"结构化训练计划"，叠加确定性生理内核(physiology)
计算出的科学字段，并做安全校验。

核心约束：
  - 纯增量：只在每个训练日 dict 上【追加】字段
    （corrected_target_pace_sec / trimp / ctl / atl / tsb / pace_warning），
    【绝不修改】原有的 target_pace_sec / target_pace，因此
    visualize 图表、plan.json、前端渲染行为完全不变。
  - 可降级：任何缺数据/异常都降级为"跳过该项计算"，绝不抛异常中断主流程。
  - 无外部依赖：仅依赖标准库与本项目 physiology。

enrich_plan(structured, profile) 是入口。
"""

from __future__ import annotations

import physiology as phy
from typing import Dict, List, Optional


# ============================================================
# 数据提取辅助
# ============================================================

def _recent_paces_and_distances(profile: dict):
    """从跑者画像提取近期配速(秒)与距离(km)序列。"""
    runs = (profile.get("近期跑步数据") or []) if isinstance(profile, dict) else []
    paces, dists, hrs_avg, hrs_max = [], [], [], []
    for r in runs:
        if not isinstance(r, dict):
            continue
        p = r.get("平均配速_秒每公里")
        if p:
            paces.append(p)
            dists.append(r.get("距离_km"))
        if r.get("平均心率"):
            hrs_avg.append(r["平均心率"])
        if r.get("最大心率"):
            hrs_max.append(r["最大心率"])
    return paces, dists, hrs_avg, hrs_max


def _seed_loads_from_runs(profile: dict):
    """用近期跑步估算当前 CTL/ATL 种子值。无数据返回 (0.0, 0.0)。"""
    runs = (profile.get("近期跑步数据") or []) if isinstance(profile, dict) else []
    health = (profile.get("健康指标") or {}) if isinstance(profile, dict) else {}
    rest_hr = health.get("静息心率")
    max_hr = None
    for r in runs:
        if isinstance(r, dict) and r.get("最大心率"):
            mr = r["最大心率"]
            max_hr = mr if max_hr is None else max(max_hr, mr)

    ctl, atl = 0.0, 0.0
    any_load = False
    for r in runs:
        if not isinstance(r, dict):
            continue
        dur = r.get("时长_分钟") or 0
        dist = r.get("距离_km") or 0
        # 时长缺失时用 距离×7min/km 近似
        dur = dur if dur else (dist * 7.0)
        if not dur:
            continue
        avg_hr = r.get("平均心率")
        trimp = phy.banister_trimp(dur, avg_hr, rest_hr, max_hr)
        if trimp is None:
            trimp = phy.trimp_from_duration(dur, phy.intensity_factor_for_type(r.get("活动名称")))
        ctl = phy.update_load(ctl, trimp, phy.CTL_TIME_CONSTANT)
        atl = phy.update_load(atl, trimp, phy.ATL_TIME_CONSTANT)
        any_load = True
    return (ctl, atl) if any_load else (0.0, 0.0)


# ============================================================
# 负荷与配速核心计算
# ============================================================

def _estimate_duration_min(item: dict, threshold_sec: Optional[int]) -> float:
    """估算单次训练时长(分钟)，用于无心率时的负荷近似。"""
    if item.get("duration_min"):
        return float(item["duration_min"])
    dist = item.get("distance_km") or 0
    if dist:
        base_sec = item.get("target_pace_sec") or threshold_sec or 420
        return dist * (base_sec / 60.0)
    return 0.0


def _validate_pace_consistency(item: dict, recent_paces: List[float]) -> Optional[str]:
    """检查该训练日配速与跑者近期能力是否一致。

    返回警告字符串或 None。只基于相对关系，不做武断覆盖。
    """
    sec = item.get("target_pace_sec")
    if sec is None or not recent_paces:
        return None
    fastest_recent = min(recent_paces)
    t = (item.get("type") or "")
    # 轻松/恢复类不应快于近期最快配速（否则偏难）
    if any(k in t for k in ("轻松", "恢复", "慢跑")) and sec < fastest_recent:
        return (f"{t}配速({phy.sec_to_pace_str(sec)})快于你近期最快配速"
                f"({phy.sec_to_pace_str(fastest_recent)})，可能偏难")
    # 间歇/冲刺类若慢于近期最快，强度可能不足
    if any(k in t for k in ("间歇", "冲刺")) and sec > fastest_recent * 1.15:
        return (f"{t}配速({phy.sec_to_pace_str(sec)})慢于你近期最快配速"
                f"({phy.sec_to_pace_str(fastest_recent)})，强度可能不足")
    return None


# ============================================================
# Phase 2：决策约束（事前）与安全护栏（事后）
# ============================================================

def _week_of(item: dict) -> int:
    """从训练日 dict 推算所属周序号（1-based）。"""
    day_no = item.get("day") or 1
    try:
        day_no = int(day_no)
    except (ValueError, TypeError):
        day_no = 1
    return (day_no - 1) // 7 + 1


def _format_constraint_text(threshold_sec, method, suggested_paces, load_baseline,
                            redlines, plan_duration):
    """把约束结构渲染成可直接拼进 user_prompt 的中文文本。"""
    lines = ["【科学约束 · 必须遵循】"]
    if threshold_sec is not None:
        lines.append(f"- 跑者估算阈值配速：{phy.sec_to_pace_str(threshold_sec)}（{method}）")
    if suggested_paces:
        pace_desc = "、".join(f"{p['type']}≈{p['pace_str']}" for p in suggested_paces)
        lines.append(f"- 各类型建议配速（允许±10秒浮动）：{pace_desc}")
    tsb = round(load_baseline["ctl"] - load_baseline["atl"], 1)
    lines.append(f"- 当前负荷基线：CTL={load_baseline['ctl']}, ATL={load_baseline['atl']}"
                 f"（TSB={tsb}，正值代表状态较好/已恢复）")
    if plan_duration >= 30:
        lines.append("- 计划按周分组，每 3-4 周安排 1 个减量恢复周（跑量降 30-40%）")
    lines.append("- 安全红线：")
    for r in redlines:
        lines.append(f"  · {r}")
    lines.append("请严格基于以上阈值配速与安全红线生成计划，不要给出明显偏离的配速或违反红线的安排。")
    return "\n".join(lines)


def build_constraints(profile: dict, plan_duration: int = 7) -> dict:
    """在 LLM 生成计划【之前】用确定性内核算"科学约束"，用于注入 prompt 指导生成。

    这是 Phase 2 的核心：把 LLM 从"凭记忆编配速"约束到"基于真实阈值能力生成"。

    返回 dict：
        {
          "status": "ok" | "partial" | "skipped",
          "threshold_pace_sec": int|None,
          "threshold_method": str,
          "suggested_paces": [{"type": str, "pace_sec": int, "pace_str": str}, ...],
          "load_baseline": {"ctl": float, "atl": float},
          "safety_redlines": [str, ...],
          "constraint_text": str,   # 可直接拼进 user_prompt 的中文文本
        }
    任何异常都降级为 status="skipped" 且 constraint_text=""，调用方可安全忽略。
    """
    out = {
        "status": "ok",
        "threshold_pace_sec": None,
        "threshold_method": "",
        "suggested_paces": [],
        "load_baseline": {"ctl": 0.0, "atl": 0.0},
        "safety_redlines": [],
        "constraint_text": "",
    }
    try:
        if not isinstance(profile, dict):
            out["status"] = "skipped"
            return out

        paces, dists, hrs_avg, hrs_max = _recent_paces_and_distances(profile)
        seed_ctl, seed_atl = _seed_loads_from_runs(profile)
        out["load_baseline"] = {"ctl": round(seed_ctl, 1), "atl": round(seed_atl, 1)}

        threshold_sec, method = phy.estimate_threshold_pace(paces, dists)
        out["threshold_pace_sec"] = threshold_sec
        out["threshold_method"] = method

        # 各类型建议配速（速度百分比法）
        if threshold_sec is not None:
            for t in ("恢复跑", "轻松跑", "长距离", "马拉松配速", "节奏跑", "间歇跑", "冲刺跑"):
                sec = phy.target_pace_for_type(threshold_sec, t)
                if sec is not None:
                    out["suggested_paces"].append({
                        "type": t, "pace_sec": sec, "pace_str": phy.sec_to_pace_str(sec),
                    })

        # 安全红线（硬约束）
        redlines = [
            "每周总训练负荷(TRIMP)增幅不超过 10%，避免伤病风险",
            "不安排连续两天高强度训练（间歇/冲刺/节奏/比赛配速之间需插入轻松或休息）",
            "每周至少安排 1 个完全休息日",
            "单次长距离距离不超过跑者近期最长距离的 1.2 倍",
        ]
        fitness = profile.get("体能指标") or {}
        vo2 = fitness.get("VO2max")
        readiness = fitness.get("训练准备度")
        is_novice = (vo2 is not None and vo2 < 40) or (readiness is not None and readiness < 50)
        if is_novice:
            redlines.append("该跑者为新手/状态偏低，高强度训练天数占比不超过 20%")
        out["safety_redlines"] = redlines

        out["constraint_text"] = _format_constraint_text(
            threshold_sec, method, out["suggested_paces"], out["load_baseline"], redlines, plan_duration
        )
        out["status"] = "ok" if threshold_sec is not None else "partial"
        return out
    except Exception:
        out["status"] = "skipped"
        out["constraint_text"] = ""
        return out


def check_safety_rules(structured: List[dict], profile: dict) -> List[dict]:
    """计划生成【之后】的硬禁忌校验，输出 safety_flags 列表。

    每条：{"level": "warning"|"caution", "rule": str, "detail": str, "day": int|None}
    纯函数；任何异常返回空列表（绝不中断主流程）。

    覆盖的训练学硬规则（共 9 条，rule 字段即下列名称）：
      1) 连续两天高强度（无休息间隔）→ warning        【公认：连续硬课缺乏恢复】
      2) 单周无完全休息日 → warning                    【公认：每周需至少 1 休息日】
      3) 周负荷增幅 >10% → warning                     【公认：10% 周增量安全线；
         但豁免「恢复周(≤峰值70%)及其后重建斜坡(≤峰值95%)」，避免合法周期化被误报】
      4) 长距离距离超过近期最长 ×1.2 → caution         【公认：递进原则】
      5) 新手高强度占比 >20% → warning                 【公认：新手耐受低】
      6) 单次训练时长 >150 分钟 → warning              【公认：单次≤~150min 上限】
      7) 单周高强度训练日 >3 天 → warning              【主流惯例：硬课每周≤3天】
      8) 单周长距离次数 >1 → caution                   【经典原则：每周一次长距离】
      9) 中长计划(≥12周)缺乏减量/恢复周 → warning
         【阈值按运动科学文献校准：恢复周跑量降 30–50%(文献 ctyeh/rundida/pneux)，
           即周负荷降至峰值 50–70%；此处取 70%(=降30%，区间下界)为判定线，
           对时长取整+强度权重导致的偏差鲁棒。触发周数取 ≥12 周为保守取舍：
           文献建议每 3–4 周一次，但短计划强判会因「周负荷增幅≤10%」规则与恢复周
           后重建冲突而误报，故仅长计划强制。】
    新增规则(6~9)在试炼循环首轮加入；规则 9 阈值在第二轮按文献(ctyeh/rundida/pneux,
    Mujika & Padilla 2003 减量研究)校准为 70%/≥12周（曾试 65% 因取整偏差漏判恢复周，
    回退至 70%）。
    """
    flags: List[dict] = []
    try:
        if not isinstance(structured, list) or not structured:
            return flags

        paces, dists, hrs_avg, hrs_max = _recent_paces_and_distances(profile)
        recent_max_dist = max([d for d in dists if d] or [0])
        threshold_sec, _ = phy.estimate_threshold_pace(paces, dists)

        high_intensity_types = ("间歇", "冲刺", "节奏", "比赛配速", "比赛")
        is_high = lambda t: any(k in (t or "") for k in high_intensity_types)

        items = [it for it in structured if isinstance(it, dict)]

        # 规则1：连续两天高强度
        for i in range(len(items) - 1):
            t1 = items[i].get("type") or ""
            t2 = items[i + 1].get("type") or ""
            if (is_high(t1) and is_high(t2)
                    and not items[i].get("is_rest") and not items[i + 1].get("is_rest")):
                flags.append({
                    "level": "warning", "rule": "连续高强度",
                    "detail": f"第{items[i].get('day')}天({t1})与第{items[i+1].get('day')}天({t2})"
                              f"连续高强度，建议插入轻松跑或休息",
                    "day": items[i + 1].get("day"),
                })

        # 规则2+3：周负荷增幅 & 单周无休息日
        weekly: Dict[int, float] = {}
        weekly_has_rest: Dict[int, bool] = {}
        for it in items:
            w = _week_of(it)
            if it.get("is_rest"):
                weekly_has_rest[w] = True
                continue
            dur = _estimate_duration_min(it, threshold_sec)
            intensity = phy.intensity_factor_for_type(it.get("type"))
            trimp = float(phy.trimp_from_duration(dur, intensity))
            weekly[w] = weekly.get(w, 0.0) + trimp
            weekly_has_rest.setdefault(w, False)

        for w in sorted(weekly.keys()):
            if not weekly_has_rest.get(w, False):
                flags.append({
                    "level": "warning", "rule": "无休息日",
                    "detail": f"第{w}周没有任何完全休息日，建议插入至少 1 天休息",
                    "day": None,
                })
        # 规则3：周负荷增幅（容忍「恢复周后的渐进重建斜坡」）
        #   依据：周期化训练中，恢复周(deload)后负荷应渐进回升；此类单步约 10%
        #   的回升是健康重建而非无脑加量，不应报警。
        #   判定：用状态机跟踪「重建窗口」——上周是恢复周(≤峰值70%)且本周回升时
        #   打开窗口，窗口内单调回升的各步豁免周增幅规则；直到本周下降(不再单调
        #   回升)才关闭。这样整段「恢复周→回到峰值」斜坡都被豁免，且不会把
        #   「两周直接猛增到超过基线」(如 +25%/+40%) 误豁免(round3 实测修正)。
        peak_trimp = max(weekly.values()) if weekly else 0.0
        prev_week_trimp = None
        prev_was_recovery = False
        rebuild_open = False
        for w in sorted(weekly.keys()):
            if prev_week_trimp:
                ratio = weekly[w] / prev_week_trimp
                # 打开窗口：上周是恢复周且本周回升
                if prev_was_recovery and weekly[w] >= prev_week_trimp:
                    rebuild_open = True
                # 关闭窗口：本周下降（不再单调回升）
                if rebuild_open and weekly[w] < prev_week_trimp:
                    rebuild_open = False
                exempt = prev_was_recovery or rebuild_open
                if ratio > 1.10 and not exempt:
                    flags.append({
                        "level": "warning", "rule": "周负荷增幅超10%",
                        "detail": f"第{w}周负荷较上周增长 {int((ratio-1)*100)}%，"
                                  f"超过10%安全线，伤病风险升高",
                        "day": None,
                    })
            prev_week_trimp = weekly[w]
            prev_was_recovery = weekly[w] <= 0.70 * peak_trimp

        # 规则4：长距离超常
        for it in items:
            if "长距离" in (it.get("type") or "") and it.get("distance_km"):
                if recent_max_dist and it["distance_km"] > recent_max_dist * 1.2:
                    flags.append({
                        "level": "caution", "rule": "长距离超常",
                        "detail": f"第{it.get('day')}天长距离 {it['distance_km']}km 超过近期最长 "
                                  f"{recent_max_dist}km 的1.2倍，注意循序渐进",
                        "day": it.get("day"),
                    })

        # 规则5：新手高强度占比
        fitness = profile.get("体能指标") or {}
        vo2 = fitness.get("VO2max")
        readiness = fitness.get("训练准备度")
        is_novice = (vo2 is not None and vo2 < 40) or (readiness is not None and readiness < 50)
        if is_novice:
            running_days = [it for it in items if not it.get("is_rest")]
            if running_days:
                high_days = [it for it in running_days if is_high(it.get("type"))]
                ratio = len(high_days) / len(running_days)
                if ratio > 0.20:
                    flags.append({
                        "level": "warning", "rule": "新手高强度占比过高",
                        "detail": f"高强度训练占比 {int(ratio*100)}% 超过新手20%上限，建议降低强度频率",
                        "day": None,
                    })

        # 规则6：单次训练时长过长（训练学依据：单次跑步普遍建议 ≤ 约 150 分钟，
        #        过长会显著抬高肌肉损伤与免疫抑制风险；属公认原则）
        for it in items:
            if it.get("is_rest"):
                continue
            dur = _estimate_duration_min(it, threshold_sec)
            if dur > 150:
                flags.append({
                    "level": "warning", "rule": "单次训练过长",
                    "detail": f"第{it.get('day')}天 {it.get('type')} 时长约 {int(dur)} 分钟，"
                              f"超过单次 150 分钟建议上限，注意控制时长避免过度疲劳",
                    "day": it.get("day"),
                })

        # 规则7：单周高强度训练天数过多（依据：硬强度课（间歇/冲刺/节奏/比赛配速）
        #        每周宜 ≤3 天，过多会挤压恢复、抬高伤病率；属主流训练安排惯例）
        weekly_high: Dict[int, int] = {}
        for it in items:
            if is_high(it.get("type")) and not it.get("is_rest"):
                w = _week_of(it)
                weekly_high[w] = weekly_high.get(w, 0) + 1
        for w, cnt in sorted(weekly_high.items()):
            if cnt > 3:
                flags.append({
                    "level": "warning", "rule": "周高强度天数过多",
                    "detail": f"第{w}周有 {cnt} 个高强度训练日，超过每周 ≤3 天的建议上限，"
                              f"伤病与恢复不足风险升高",
                    "day": None,
                })

        # 规则8：单周多次长距离（依据：经典原则「每周一次长距离」，
        #        单周多次长距会累积疲劳且收益递减；属公认安排）
        weekly_long: Dict[int, int] = {}
        for it in items:
            if "长距离" in (it.get("type") or "") and not it.get("is_rest"):
                w = _week_of(it)
                weekly_long[w] = weekly_long.get(w, 0) + 1
        for w, cnt in sorted(weekly_long.items()):
            if cnt > 1:
                flags.append({
                    "level": "caution", "rule": "每周多长距离",
                    "detail": f"第{w}周安排了 {cnt} 次长距离，建议每周至多 1 次长距离，"
                              f"避免累积疲劳",
                    "day": None,
                })

        # 规则9：缺乏减量/恢复周（依据：周期化训练中每 3-4 周应插入 1 个减量周
        #        使负荷回落约 30-40%；此处判定为「周负荷从未降至峰值 70% 以下」即视为缺减量周。
        #        注：该阈值(70%/12周)为工程约定，非绝对金标准，属推测性偏保守设定）
        if weekly:
            max_week = max(weekly.keys())
            if max_week >= 12:
                peak = max(weekly.values())
                # 恢复周判定线：周负荷 ≤ 峰值 70%（=降 30%，文献区间 30–50% 降幅的下界）。
                # 注：曾试 0.65(=降35%)，但名义降35%的恢复周因时长取整+强度权重实际算得
                # ~0.655 峰值，卡在 0.65 之上导致漏判；故取 0.70 以鲁棒接住恢复周。
                has_recovery = any(trimp <= 0.70 * peak for trimp in weekly.values())
                if not has_recovery:
                    flags.append({
                        "level": "warning", "rule": "缺乏减量周",
                        "detail": f"计划共 {max_week} 周但没有任何减量/恢复周"
                                  f"（周负荷未出现 ≤峰值70% 的周），建议每 3-4 周插入 1 个减量周",
                        "day": None,
                    })
        return flags
    except Exception:
        return flags


def enrich_plan(structured: List[dict], profile: dict) -> dict:
    """对结构化训练计划做科学增量增强。

    参数：
        structured: llm_agent._parse_plan_to_structured 的输出（会被原地追加字段）
        profile:    data_tool.build_runner_profile 返回的跑者画像

    返回：summary dict
        {
          "status": "ok" | "partial" | "skipped",
          "threshold_pace_sec": int|None,
          "threshold_method": str,
          "vo2max": float|None,
          "warnings": [str, ...],
          "weekly_load": [{"week": int, "trimp": float}, ...]
        }
    """
    summary: Dict = {
        "status": "ok",
        "threshold_pace_sec": None,
        "threshold_method": "",
        "vo2max": None,
        "warnings": [],
        "weekly_load": [],
        "safety_flags": [],
    }
    try:
        if not isinstance(structured, list) or not structured:
            summary["status"] = "skipped"
            summary["warnings"].append("结构化计划为空，跳过科学增强")
            return summary

        paces, dists, hrs_avg, hrs_max = _recent_paces_and_distances(profile)
        rest_hr = None
        max_hr = None
        if isinstance(profile, dict):
            health = profile.get("健康指标") or {}
            fitness = profile.get("体能指标") or {}
            rest_hr = health.get("静息心率")
            max_hr = max(hrs_max) if hrs_max else None
            # VO2max：优先用画像中的体能指标，否则用心率法估算
            vo2 = fitness.get("VO2max")
            if vo2 is None and rest_hr and max_hr:
                vo2 = phy.estimate_vo2max_from_hr(rest_hr, max_hr)
            summary["vo2max"] = vo2

        # 阈值配速（用于科学建议配速）
        threshold_sec, method = phy.estimate_threshold_pace(paces, dists)
        summary["threshold_pace_sec"] = threshold_sec
        summary["threshold_method"] = method

        # 种子负荷
        seed_ctl, seed_atl = _seed_loads_from_runs(profile)
        ctl, atl = seed_ctl, seed_atl

        weekly: Dict[int, float] = {}
        prev_week_trimp: Optional[float] = None

        for item in structured:
            if not isinstance(item, dict):
                continue
            is_rest = item.get("is_rest", False)
            t = item.get("type") or "休息"
            intensity = phy.intensity_factor_for_type(t)

            # 1) 建议配速（仅追加 corrected_*，不覆盖 target_pace_sec）
            if not is_rest and threshold_sec is not None:
                item["corrected_target_pace_sec"] = phy.target_pace_for_type(threshold_sec, t)

            # 2) 逐日负荷（休息日负荷=0）
            #    注：LLM 生成的 heart_rate_zone 是文本描述，无具体 bpm，
            #    故统一用"时长×强度因子"近似 TRIMP（与 physiology 保持一致）。
            if is_rest or intensity <= 0:
                day_trimp = 0.0
            else:
                dur = _estimate_duration_min(item, threshold_sec)
                day_trimp = float(phy.trimp_from_duration(dur, intensity))

            # 更新 CTL/ATL/TSB
            ctl = phy.update_load(ctl, day_trimp, phy.CTL_TIME_CONSTANT)
            atl = phy.update_load(atl, day_trimp, phy.ATL_TIME_CONSTANT)
            item["trimp"] = round(day_trimp, 1)
            item["ctl"] = round(ctl, 1)
            item["atl"] = round(atl, 1)
            item["tsb"] = round(ctl - atl, 1)

            # 3) 周负荷累计
            day_no = item.get("day") or (len(weekly) + 1)
            week_no = (int(day_no) - 1) // 7 + 1
            weekly[week_no] = weekly.get(week_no, 0.0) + day_trimp

            # 4) 配速一致性校验
            warn = _validate_pace_consistency(item, paces)
            if warn:
                item["pace_warning"] = warn
                summary["warnings"].append(f"第{day_no}天：{warn}")

        # 5) 周负荷进展检查（增幅 >20% 且非最后一周 → 伤病风险）
        week_list = sorted(weekly.keys())
        for i, w in enumerate(week_list):
            trimp = weekly[w]
            summary["weekly_load"].append({"week": w, "trimp": round(trimp, 1)})
            if i > 0 and prev_week_trimp:
                ratio = trimp / prev_week_trimp if prev_week_trimp else 0
                is_last = (i == len(week_list) - 1)
                if ratio > 1.20 and not is_last:
                    summary["warnings"].append(
                        f"第{w}周负荷较上周增长 {int((ratio-1)*100)}%，"
                        f"超过20%安全线，伤病风险升高，建议减量或拆分"
                    )
            prev_week_trimp = trimp

        # 6) 体能指标预警
        if isinstance(profile, dict):
            fitness = profile.get("体能指标") or {}
            readiness = fitness.get("训练准备度")
            if readiness is not None and readiness < 50:
                summary["warnings"].append(
                    f"训练准备度偏低（{readiness}），建议本周降低强度并关注恢复"
                )

        if not summary["warnings"]:
            summary["status"] = "ok"
        else:
            summary["status"] = "partial"

        # Phase 2：硬禁忌校验（独立 try/except，绝不中断主流程）
        try:
            summary["safety_flags"] = check_safety_rules(structured, profile)
        except Exception:
            summary["safety_flags"] = []
        return summary

    except Exception as e:  # 任何意外都不应中断主流程
        summary["status"] = "skipped"
        summary["warnings"].append(f"科学增强异常已跳过：{type(e).__name__}: {e}")
        return summary


# ============================================================
# 模块自测
# ============================================================

if __name__ == "__main__":
    print("=" * 50)
    print("计划科学增强模块自测（离线，无需 API）")
    print("=" * 50)

    mock_plan = [
        {"day": 1, "weekday": "一", "type": "轻松跑", "target_pace_sec": 390,
         "distance_km": 3, "duration_min": 20, "is_rest": False},
        {"day": 2, "weekday": "二", "type": "休息", "target_pace_sec": None,
         "distance_km": 0, "is_rest": True},
        {"day": 3, "weekday": "三", "type": "间歇跑", "target_pace_sec": 340,
         "distance_km": 4, "duration_min": 30, "is_rest": False},
        {"day": 4, "weekday": "四", "type": "长距离", "target_pace_sec": 410,
         "distance_km": 5, "duration_min": 40, "is_rest": False},
    ]
    mock_profile = {
        "近期跑步数据": [
            {"日期": "2026-07-15", "距离_km": 5, "平均配速_秒每公里": 400,
             "平均心率": 155, "最大心率": 175},
            {"日期": "2026-07-12", "距离_km": 4, "平均配速_秒每公里": 410,
             "平均心率": 160, "最大心率": 178},
        ],
        "健康指标": {"静息心率": 60},
        "体能指标": {"VO2max": None, "训练准备度": 70},
    }

    # 关键断言：原 target_pace_sec 不被修改
    orig_paces = {d["day"]: d["target_pace_sec"] for d in mock_plan}
    summary = enrich_plan(mock_plan, mock_profile)

    print(f"状态: {summary['status']}")
    print(f"阈值配速: {phy.sec_to_pace_str(summary['threshold_pace_sec'])} ({summary['threshold_method']})")
    print(f"VO2max: {summary['vo2max']}")
    print(f"周负荷: {summary['weekly_load']}")
    print(f"警告: {summary['warnings']}")
    for d in mock_plan:
        print(f"  第{d['day']}天 {d['type']}: "
              f"建议配速={phy.sec_to_pace_str(d.get('corrected_target_pace_sec'))}, "
              f"TRIMP={d.get('trimp')}, CTL={d.get('ctl')}, ATL={d.get('atl')}, TSB={d.get('tsb')}")

    # 断言：原配速未被改动
    for d in mock_plan:
        assert d["target_pace_sec"] == orig_paces[d["day"]], "原配速被意外修改！"
    assert summary["threshold_pace_sec"] is not None
    assert all("corrected_target_pace_sec" in d for d in mock_plan if not d["is_rest"])
    print("\n✅ 自测通过：增量增强且未破坏原字段")

    # ---- 新增：build_constraints 自测 ----
    print("\n--- build_constraints 自测 ---")
    cons = build_constraints(mock_profile, plan_duration=7)
    print(f"状态: {cons['status']}")
    print(f"约束文本前2行:\n" + "\n".join(cons["constraint_text"].split("\n")[:3]))
    assert cons["constraint_text"], "约束文本不应为空"
    assert cons["suggested_paces"], "建议配速不应为空"
    assert cons["safety_redlines"], "安全红线不应为空"

    # ---- 新增：check_safety_rules 自测（构造违规计划）----
    print("\n--- check_safety_rules 自测 ---")
    violation_plan = [
        {"day": 1, "weekday": "一", "type": "间歇跑", "target_pace_sec": 340,
         "distance_km": 4, "duration_min": 30, "is_rest": False},
        {"day": 2, "weekday": "二", "type": "冲刺跑", "target_pace_sec": 330,
         "distance_km": 3, "duration_min": 20, "is_rest": False},
        {"day": 3, "weekday": "三", "type": "节奏跑", "target_pace_sec": 350,
         "distance_km": 5, "duration_min": 30, "is_rest": False},
        {"day": 4, "weekday": "四", "type": "轻松跑", "target_pace_sec": 400,
         "distance_km": 4, "duration_min": 30, "is_rest": False},
    ]
    flags = check_safety_rules(violation_plan, mock_profile)
    rules = [f["rule"] for f in flags]
    print(f"触发规则: {rules}")
    assert "连续高强度" in rules, "应检测到连续两天高强度"
    assert "无休息日" in rules, "应检测到单周无休息日"
    print("\n✅ Phase 2 安全护栏自测通过")
