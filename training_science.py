"""
训练科学计算模块
================
基于运动科学原理（Banister TRIMP 模型、ACWR 等）提供训练负荷、
恢复状态和训练效果的科学量化计算。

核心函数：
    calc_trimp()              — 计算单次训练的训练冲量（TRIMP）
    calc_weekly_trimp()       — 计算周总 TRIMP
    calc_acwr()               — 计算急性:慢性负荷比（ACWR）
    calc_training_load_pct()  — 将 TRIMP 转为百分比负荷指标
    calc_readiness()          — 基于静息心率偏差计算就绪度
    calc_training_summary()   — 一站式计算所有训练科学指标
    format_acwr_risk()        — 将 ACWR 值转为可读的风险等级

参考：
    - Banister, E. W. (1991). Modeling elite athletic performance.
    - Gabbett, T. J. (2016). The training-injury prevention paradox.
============================================================
"""

import math


def calc_trimp(duration_min, avg_hr, resting_hr=None, max_hr=None, age=None):
    """计算单次训练的训练冲量（TRIMP, Training Impulse）。

    使用 Banister 的指数加权模型，区分不同强度区间的生理负荷。

    参数：
        duration_min: 训练时长（分钟）
        avg_hr:       平均心率（bpm）
        resting_hr:   静息心率（bpm），默认 60
        max_hr:       最大心率（bpm），默认 220 - age
        age:          年龄，默认 30

    返回：
        float，TRIMP 值（0-200+），None 表示数据不足无法计算

    公式：
        ΔHR = (HR_avg - HR_rest) / (HR_max - HR_rest)
        TRIMP = duration_min × ΔHR × 0.64 × e^(1.92 × ΔHR)
    """
    if not duration_min or not avg_hr:
        return None

    # 默认值
    if resting_hr is None or resting_hr <= 0:
        resting_hr = 60
    if max_hr is None or max_hr <= 0:
        if age is None:
            age = 30
        max_hr = 220 - age

    # 边界保护
    if max_hr <= resting_hr:
        return None

    delta_hr = (avg_hr - resting_hr) / (max_hr - resting_hr)

    # 心率低于静息心率视为恢复活动，给予最小负荷
    if delta_hr <= 0:
        return duration_min * 0.5

    # Banister 公式
    trimp = duration_min * delta_hr * 0.64 * math.exp(1.92 * delta_hr)
    return round(trimp, 1)


def calc_weekly_trimp(runs, resting_hr=None, max_hr=None, age=None):
    """计算一周内所有跑步记录的总 TRIMP。

    参数：
        runs:       跑步记录列表，每项含 时长_分钟、平均心率
        resting_hr: 静息心率
        max_hr:     最大心率
        age:        年龄

    返回：
        float，周总 TRIMP
    """
    total = 0.0
    for run in runs:
        duration = run.get("时长_分钟")
        avg_hr = run.get("平均心率")
        if duration and avg_hr:
            trimp = calc_trimp(duration, avg_hr, resting_hr, max_hr, age)
            if trimp:
                total += trimp
    return round(total, 1)


def calc_acwr(acute_load, chronic_load):
    """计算急性:慢性负荷比（ACWR）。

    ACWR 是预测运动损伤风险的核心指标：
    - < 0.8：训练不足
    - 0.8 - 1.3：安全区间（最佳）
    - 1.3 - 1.5：中等风险
    - > 1.5：高风险

    参数：
        acute_load:   急性负荷（近 7 天平均 TRIMP）
        chronic_load: 慢性负荷（近 28 天平均 TRIMP）

    返回：
        float，ACWR 比值，None 表示数据不足
    """
    if not chronic_load or chronic_load == 0:
        return None
    return round(acute_load / chronic_load, 2)


def calc_training_load_pct(weekly_trimp, max_trimp=800):
    """将周 TRIMP 转为 0-100 的百分比负荷指标。

    基于经验值：新手周 TRIMP 约 200-400，中级约 400-600，高级约 600-1000+。

    参数：
        weekly_trimp: 周总 TRIMP
        max_trimp:    参考上限（默认 800，对应中高级跑者）

    返回：
        int，0-100 的负荷百分比
    """
    if weekly_trimp is None or weekly_trimp <= 0:
        return 0
    return min(round(weekly_trimp / max_trimp * 100), 100)


def calc_readiness(resting_hr, baseline_resting_hr=None, sleep_hours=None):
    """基于静息心率偏差和睡眠计算就绪度（0-100）。

    原理：静息心率升高是身体疲劳/恢复不足的早期信号。

    参数：
        resting_hr:          当前静息心率（bpm）
        baseline_resting_hr: 基础静息心率（bpm），默认 60
        sleep_hours:         睡眠时长（小时），默认 7

    返回：
        int，0-100 的就绪度分数
    """
    if not resting_hr:
        return None  # 无静息心率数据，无法评估 → 显式返回 None，不再冒充中等就绪度

    if baseline_resting_hr is None:
        baseline_resting_hr = 60

    # 心率偏差：每升高 1bpm 扣 5 分
    hr_deviation = resting_hr - baseline_resting_hr
    hr_score = max(0, 85 - hr_deviation * 5)

    # 睡眠修正：理想 7-8h，每差 1h 扣 8 分
    sleep_score = 15
    if sleep_hours is not None:
        sleep_deviation = abs(sleep_hours - 7.5)
        sleep_score = max(0, 15 - sleep_deviation * 8)

    readiness = min(round(hr_score + sleep_score), 100)
    return readiness


def format_acwr_risk(acwr):
    """将 ACWR 值转为风险等级描述。

    返回：
        dict: {"level": "安全"|"中等"|"高风险"|"不足", "color": "#...", "advice": "..."}
    """
    if acwr is None:
        return {"level": "未知", "color": "#9E9E9E", "advice": "数据不足，无法评估"}

    if acwr < 0.8:
        return {
            "level": "训练不足",
            "color": "#4DB8FF",
            "advice": "当前训练负荷偏低，可适当增加跑量或强度"
        }
    elif acwr <= 1.3:
        return {
            "level": "安全",
            "color": "#4CAF50",
            "advice": "训练负荷在安全区间，继续保持"
        }
    elif acwr <= 1.5:
        return {
            "level": "中等风险",
            "color": "#FF9800",
            "advice": "训练负荷偏高，建议增加恢复日或降低强度"
        }
    else:
        return {
            "level": "高风险",
            "color": "#FF3B30",
            "advice": "训练负荷过高，受伤风险显著增加，建议立即减量"
        }


def calc_training_summary(runs, resting_hr=None, max_hr=None, age=None,
                          baseline_resting_hr=None, sleep_hours=None,
                          max_trimp=800):
    """一站式计算所有训练科学指标。

    参数：
        runs:                 跑步记录列表（至少含 时长_分钟、平均心率、日期）
        resting_hr:           当前静息心率
        max_hr:               最大心率
        age:                  年龄
        baseline_resting_hr:  基础静息心率
        sleep_hours:          睡眠时长
        max_trimp:            TRIMP 参考上限

    返回：
        dict: {
            "weekly_trimp": float,
            "training_load": int,       # 0-100
            "acwr": float or None,
            "acwr_risk": dict,
            "readiness": int or None,           # 0-100；无静息心率时为 None（数据不足）
            "readiness_available": bool,        # 是否有足够数据计算就绪度
            "avg_trimp_per_run": float,
            "run_count": int,
        }
    """
    from datetime import datetime, timedelta

    if not runs:
        return {
            "weekly_trimp": 0,
            "training_load": 0,
            "acwr": None,
            "acwr_risk": format_acwr_risk(None),
            "readiness": calc_readiness(resting_hr, baseline_resting_hr, sleep_hours),
            "readiness_available": bool(resting_hr),
            "avg_trimp_per_run": 0,
            "run_count": 0,
        }

    today = datetime.now().date()

    # 计算每条记录的 TRIMP 并分类为急性（7天）和慢性（28天）
    acute_trimps = []
    chronic_trimps = []
    run_count = 0

    for run in runs:
        duration = run.get("时长_分钟")
        avg_hr = run.get("平均心率")
        if not duration or not avg_hr:
            continue

        trimp = calc_trimp(duration, avg_hr, resting_hr, max_hr, age)
        if trimp is None:
            continue

        run_count += 1

        # 判断日期范围
        date_str = run.get("日期")
        if date_str:
            try:
                run_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                days_ago = (today - run_date).days
                if days_ago <= 7:
                    acute_trimps.append(trimp)
                if days_ago <= 28:
                    chronic_trimps.append(trimp)
            except (ValueError, TypeError):
                # 无法解析日期，归入慢性负荷
                chronic_trimps.append(trimp)
                acute_trimps.append(trimp)
        else:
            chronic_trimps.append(trimp)
            acute_trimps.append(trimp)

    # 周总 TRIMP
    weekly_trimp = round(sum(acute_trimps), 1)

    # 训练负荷百分比
    training_load = calc_training_load_pct(weekly_trimp, max_trimp)

    # ACWR：急性（7天平均）vs 慢性（28天平均）
    acute_avg = sum(acute_trimps) / 7 if acute_trimps else 0
    chronic_avg = sum(chronic_trimps) / 28 if chronic_trimps else 0
    acwr = calc_acwr(acute_avg, chronic_avg) if chronic_avg > 0 else None

    # 就绪度
    readiness = calc_readiness(resting_hr, baseline_resting_hr, sleep_hours)
    readiness_available = bool(resting_hr)

    # 平均每次 TRIMP
    avg_trimp = round(sum(acute_trimps) / len(acute_trimps), 1) if acute_trimps else 0

    return {
        "weekly_trimp": weekly_trimp,
        "training_load": training_load,
        "acwr": acwr,
        "acwr_risk": format_acwr_risk(acwr),
        "readiness": readiness,
        "readiness_available": readiness_available,
        "avg_trimp_per_run": avg_trimp,
        "run_count": run_count,
    }


# ============================================================
# 模块自测
# ============================================================

if __name__ == "__main__":
    print("=" * 50)
    print("训练科学模块自测")
    print("=" * 50)

    # 测试 1：单次 TRIMP 计算
    print("\n--- TRIMP 计算 ---")
    # 30 分钟轻松跑，心率 140，静息 60，最大 190
    trimp_easy = calc_trimp(30, 140, 60, 190)
    print(f"轻松跑 30min@140bpm: TRIMP = {trimp_easy}")

    # 30 分钟高强度跑，心率 170
    trimp_hard = calc_trimp(30, 170, 60, 190)
    print(f"高强度 30min@170bpm: TRIMP = {trimp_hard}")

    print(f"强度差异倍数: {trimp_hard / trimp_easy:.1f}x")

    # 测试 2：ACWR
    print("\n--- ACWR 计算 ---")
    for acute, chronic in [(300, 300), (400, 300), (500, 300), (200, 300)]:
        acwr = calc_acwr(acute, chronic)
        risk = format_acwr_risk(acwr)
        print(f"  急性={acute} 慢性={chronic} → ACWR={acwr} → {risk['level']} ({risk['advice']})")

    # 测试 3：综合指标
    print("\n--- 综合指标 ---")
    mock_runs = [
        {"日期": "2026-07-19", "时长_分钟": 30, "平均心率": 145},
        {"日期": "2026-07-17", "时长_分钟": 45, "平均心率": 150},
        {"日期": "2026-07-15", "时长_分钟": 25, "平均心率": 140},
    ]
    summary = calc_training_summary(mock_runs, resting_hr=58, max_hr=190, baseline_resting_hr=55)
    for k, v in summary.items():
        print(f"  {k}: {v}")