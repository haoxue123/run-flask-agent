"""页面渲染路由"""
import os
import json
import re
from datetime import datetime, timedelta
from flask import Blueprint, render_template, current_app

import data_tool
import training_science as ts
import physiology

pages_bp = Blueprint("pages", __name__)


def _load_plan():
    """加载 plan.json 结构化数据。"""
    plan_path = os.path.join(data_tool.DEFAULT_DATA_DIR, "plan.json")
    if not os.path.isfile(plan_path):
        return None
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _parse_pace_to_seconds(pace_str):
    """将配速字符串（如 '5'30\"' 或 '5:30'）转换为秒数。"""
    if not pace_str or not isinstance(pace_str, str):
        return None
    pace_str = pace_str.strip()
    m = re.match(r"(\d+)[:'：](\d+)", pace_str)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    try:
        return int(pace_str) * 60
    except ValueError:
        return None


@pages_bp.route("/")
def index():
    """首页：数据总览。"""
    try:
        profile = data_tool.build_runner_profile()
        recent_runs = profile.get("近期跑步数据", []) or []

        today = datetime.now().date()

        weekly_distance = 0.0
        for run in recent_runs:
            run_date_str = run.get("日期")
            if run_date_str:
                try:
                    run_date = datetime.strptime(run_date_str, "%Y-%m-%d").date()
                    if (today - run_date).days <= 7:
                        dist = run.get("距离_km")
                        if dist is not None:
                            weekly_distance += float(dist)
                except (ValueError, TypeError):
                    pass
        weekly_distance = round(weekly_distance, 1)

        pace_values = []
        for run in recent_runs:
            p = run.get("平均配速_秒每公里")
            if p is not None:
                pace_values.append(int(p))
        if pace_values:
            avg_pace_seconds = sum(pace_values) // len(pace_values)
            avg_pace = physiology.sec_to_pace_str(avg_pace_seconds)
        else:
            avg_pace = "-"

        if weekly_distance > 0:
            # 使用训练科学模型计算 TRIMP 和 ACWR
            resting_hr = (profile.get("健康指标") or {}).get("静息心率")
            sleep_hours = (profile.get("健康指标") or {}).get("睡眠时长_小时")
            baseline_resting_hr = None
            max_hr = None
            for run in recent_runs:
                mhr = run.get("最大心率")
                if mhr:
                    max_hr = int(mhr)
                    break

            ts_summary = ts.calc_training_summary(
                recent_runs,
                resting_hr=int(resting_hr) if resting_hr else None,
                max_hr=max_hr,
                baseline_resting_hr=baseline_resting_hr,
                sleep_hours=float(sleep_hours) if sleep_hours else None,
            )
            training_load = ts_summary["training_load"]
            readiness = ts_summary["readiness"]
            acwr = ts_summary["acwr"]
            acwr_risk = ts_summary["acwr_risk"]
        else:
            resting_hr = None
            training_load = 0
            readiness = 70
            acwr = None
            acwr_risk = {"level": "未知", "color": "#9E9E9E", "advice": "数据不足"}

        # 计划完成率 & 配速偏差
        plan_data = _load_plan()
        plan_completion = None
        pace_deviation = None

        if plan_data and plan_data.get("plan"):
            plan = plan_data["plan"]
            non_rest_days = [d for d in plan if d.get("type") != "休息"]
            completed_days = [d for d in non_rest_days if d.get("completed")]

            total_non_rest = len(non_rest_days)
            total_completed = len(completed_days)

            if total_non_rest > 0:
                plan_completion = round(total_completed / total_non_rest * 100)

            # 配速偏差：匹配计划日与实际跑步记录
            saved_at_str = plan_data.get("meta", {}).get("generated_at", "")
            if saved_at_str:
                try:
                    plan_start = datetime.fromisoformat(saved_at_str).date()
                    deviations = []
                    for d in completed_days:
                        day_num = d.get("day")
                        target_date = plan_start + timedelta(days=day_num - 1)
                        target_pace = _parse_pace_to_seconds(d.get("pace", ""))
                        for run in recent_runs:
                            run_date_str = run.get("日期")
                            if run_date_str:
                                try:
                                    run_date = datetime.strptime(run_date_str, "%Y-%m-%d").date()
                                    if run_date == target_date:
                                        actual_pace = run.get("平均配速_秒每公里")
                                        if actual_pace is not None and target_pace is not None:
                                            deviations.append(actual_pace - target_pace)
                                        break
                                except (ValueError, TypeError):
                                    pass
                    if deviations:
                        pace_deviation = round(sum(deviations) / len(deviations))
                except (ValueError, TypeError):
                    pass

        kpi = {
            "weekly_distance": weekly_distance,
            "avg_pace": avg_pace,
            "training_load": training_load,
            "readiness": readiness,
            "acwr": acwr,
            "acwr_risk": acwr_risk,
            "plan_completion": plan_completion,
            "pace_deviation": pace_deviation,
        }

        insight = {
            "title": "教练洞察",
            "text": "",
            "items": [],
            "badge": "",
        }

        if weekly_distance >= 20:
            insight["text"] = f"本周跑量充足，累计 {weekly_distance}km。注意恢复，避免过度训练。"
        elif resting_hr is not None and resting_hr > 65:
            insight["text"] = f"静息心率偏高（{resting_hr}bpm），建议降低训练强度，增加恢复日。"
        elif acwr is not None and acwr > 1.3:
            insight["text"] = f"训练负荷比偏高（ACWR={acwr}），{acwr_risk['advice']}"
        elif weekly_distance > 0 and training_load >= 80:
            insight["text"] = f"训练负荷较高（{training_load}%），建议本周安排 1-2 天恢复跑或休息。"
        else:
            insight["text"] = "数据正常，保持当前训练节奏。逐步增加跑量可提升耐力水平。"

        if weekly_distance > 0:
            insight["items"].append({"icon": "activity", "text": f"依据：周跑量 {weekly_distance}km"})
        if resting_hr is not None:
            insight["items"].append({"icon": "heart-pulse", "text": f"依据：静息心率 {resting_hr}bpm"})
        if acwr is not None:
            insight["items"].append({"icon": "activity", "text": f"依据：ACWR {acwr} ({acwr_risk['level']})"})
        if readiness < 70:
            insight["items"].append({"icon": "zap", "text": f"依据：就绪度偏低 {readiness}"})

        if training_load >= 80:
            insight["badge"] = "建议调整"
        elif readiness < 70:
            insight["badge"] = "需关注"
        else:
            insight["badge"] = "正常"

        # --- weekly_series: 最近 7 天每日跑量聚合 ---
        weekly_series = []
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            day_str = day.strftime("%m-%d")
            day_dist = 0.0
            for run in recent_runs:
                run_date_str = run.get("日期")
                if run_date_str:
                    try:
                        run_date = datetime.strptime(run_date_str, "%Y-%m-%d").date()
                        if run_date == day:
                            dist = run.get("距离_km")
                            if dist is not None:
                                day_dist += float(dist)
                    except (ValueError, TypeError):
                        pass
            weekly_series.append({"date": day_str, "distance": round(day_dist, 1)})

        # --- weekly_trend: 本周 vs 上周跑量变化百分比 ---
        this_week_dist = sum(d["distance"] for d in weekly_series)
        last_week_dist = 0.0
        for i in range(7, 14):
            day = today - timedelta(days=i)
            for run in recent_runs:
                run_date_str = run.get("日期")
                if run_date_str:
                    try:
                        run_date = datetime.strptime(run_date_str, "%Y-%m-%d").date()
                        if run_date == day:
                            dist = run.get("距离_km")
                            if dist is not None:
                                last_week_dist += float(dist)
                    except (ValueError, TypeError):
                        pass

        if last_week_dist > 0:
            weekly_trend = round((this_week_dist - last_week_dist) / last_week_dist * 100)
        elif this_week_dist > 0:
            weekly_trend = 100
        else:
            weekly_trend = 0

        # --- coach_weekly: 教练周报卡 ---
        coach_weekly = {
            "summary": f"本周跑量{'充足' if weekly_distance >= 15 else '偏低'}，累计 {weekly_distance}km",
            "suggestion": "建议下周保持配速，增加一次长距离跑" if weekly_distance > 0 else "建议从短距离慢跑开始，逐步建立训练习惯",
            "risk": "注意：训练负荷偏高，建议安排恢复日" if training_load >= 80 else "当前训练负荷在安全范围内",
            "basis": "基于最近 7 天数据分析和训练科学模型",
        }

    except (NameError, FileNotFoundError, KeyError, IndexError, TypeError, ValueError):
        kpi = {"weekly_distance": "-", "avg_pace": "-", "training_load": "-", "readiness": "-", "acwr": None, "acwr_risk": {"level": "未知", "color": "#9E9E9E", "advice": "数据不足"}, "plan_completion": None, "pace_deviation": None}
        insight = {"title": "教练洞察", "text": "暂无数据，请上传运动文件或填写手动数据以获取教练洞察。", "items": [], "badge": "无数据"}
        profile = {}
        weekly_series = []
        weekly_trend = None
        coach_weekly = {
            "summary": "暂无跑量数据",
            "suggestion": "上传运动文件或填写手动数据，开始你的训练之旅",
            "risk": "数据不足，无法评估训练风险",
            "basis": "需要至少 7 天数据以生成教练周报",
        }

    return render_template(
        "index.html",
        profile=profile,
        kpi=kpi,
        insight=insight,
        weekly_series=weekly_series,
        weekly_trend=weekly_trend,
        coach_weekly=coach_weekly,
    )


@pages_bp.route("/analysis")
def analysis():
    from flask import redirect
    return redirect("/insights", code=301)


@pages_bp.route("/training")
def training():
    try:
        profile = data_tool.build_runner_profile()
    except (FileNotFoundError, KeyError, IndexError, TypeError, ValueError):
        profile = {}
    return render_template("training.html", profile=profile)


@pages_bp.route("/trends")
def trends():
    from flask import redirect
    return redirect("/insights", code=301)


@pages_bp.route("/insights")
def insights():
    try:
        profile = data_tool.build_runner_profile()
        recent_runs = profile.get("近期跑步数据", []) or []
    except (FileNotFoundError, KeyError, IndexError, TypeError, ValueError):
        profile = {}
        recent_runs = []

    # 构建训练进展图表数据（匹配计划日与实际跑步记录）
    plan_data = _load_plan()
    plan_chart_data = []
    if plan_data and plan_data.get("plan") and recent_runs:
        saved_at_str = plan_data.get("meta", {}).get("generated_at", "")
        if saved_at_str:
            try:
                plan_start = datetime.fromisoformat(saved_at_str).date()
                for d in plan_data["plan"]:
                    if d.get("type") == "休息":
                        continue
                    day_num = d.get("day")
                    target_date = plan_start + timedelta(days=day_num - 1)
                    completed = d.get("completed", False)
                    actual_pace = None
                    for run in recent_runs:
                        run_date_str = run.get("日期")
                        if run_date_str:
                            try:
                                run_date = datetime.strptime(run_date_str, "%Y-%m-%d").date()
                                if run_date == target_date:
                                    actual_pace = run.get("平均配速_秒每公里")
                                    break
                            except (ValueError, TypeError):
                                pass
                    plan_chart_data.append({
                        "date": target_date.isoformat(),
                        "type": d.get("type"),
                        "completed": completed,
                        "actual_pace": actual_pace,
                    })
            except (ValueError, TypeError):
                pass

    return render_template("insights.html", profile=profile, recent_runs=recent_runs, plan_chart_data=plan_chart_data)


@pages_bp.route("/coach")
def coach():
    try:
        profile = data_tool.build_runner_profile()
        has_profile = bool(profile and profile.get("近期跑步数据"))
    except Exception:
        has_profile = False
    return render_template("coach.html", has_profile=has_profile)