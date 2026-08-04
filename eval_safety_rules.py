# -*- coding: utf-8 -*-
"""
安全护栏评测脚本（试炼循环 · Critic 工具）
============================================================
评测对象：plan_science.check_safety_rules(structured, profile)
评测维度：
  - 召回率 recall   = 所有违规用例中，被抓出的「已标注违规项」占比
  - 误报数 fp        = 所有合规用例中，被误判为违规的项数之和
  - 新增规则覆盖     = 代码中是否出现预定义的新规则名（R6~R9）
  - 回归门           = 由外部 test_smoke.py / plan_science __main__ 保证，本脚本只报护栏指标

数据集：内嵌、带标签的合成计划。
  - 违规集：每个用例 seeds 1~2 条已知违规（rule 名写入 expect）
  - 合规集：expect 为空，任何被抓出的项都算误报

运行：
  python eval_safety_rules.py            # 打印报告 + 写 data/eval/safety_rules_report.md
  python eval_safety_rules.py --json     # 额外写 data/eval/safety_rules_results.json

全程离线，仅需标准库 + 本项目 physiology / plan_science。
"""
import os
import sys
import json
import argparse
from datetime import datetime

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import plan_science as ps  # noqa: E402

# 预定义的新规则名（Builder 实现后应在 check_safety_rules 中出现）
NEW_RULE_NAMES = [
    "单次训练过长",
    "周高强度天数过多",
    "每周多长距离",
    "缺乏减量周",
]

# ============================================================
# 用例构造辅助
# ============================================================
def rest(day):
    return {"day": day, "type": "休息", "is_rest": True, "distance_km": 0,
            "duration_min": 0, "target_pace_sec": None}

def sess(day, t, dist, dur, pace):
    return {"day": day, "type": t, "is_rest": False, "distance_km": dist,
            "duration_min": dur, "target_pace_sec": pace}

def base_week(start, recent_max=15, long_dist=12, long_dur=75, paces=(400, 340, 360)):
    """一个合规的单周：含休息日、≤3 高强度天、单次≤150min、1 次长距离、无连续高强度。"""
    p_easy, p_int, p_tempo = paces
    return [
        rest(start),
        sess(start + 1, "轻松跑", 3, 25, p_easy),
        sess(start + 2, "间歇跑", 4, 30, p_int),
        sess(start + 3, "轻松跑", 3, 25, p_easy),
        sess(start + 4, "长距离", long_dist, long_dur, 410),
        rest(start + 5),
        sess(start + 6, "节奏跑", 5, 30, p_tempo),
    ]

def trained_profile(recent_max=15, runs=None):
    if runs is None:
        runs = [
            {"距离_km": recent_max, "平均配速_秒每公里": 400, "平均心率": 155, "最大心率": 175},
            {"距离_km": recent_max * 0.8, "平均配速_秒每公里": 410, "平均心率": 160, "最大心率": 178},
        ]
    return {
        "近期跑步数据": runs,
        "健康指标": {"静息心率": 60},
        "体能指标": {"VO2max": 55, "训练准备度": 80},
    }

def wk_scaled(start, factor):
    """base_week 的比例缩放版：把该周所有时长/距离乘 factor，用于构造带恢复周的计划。"""
    wk = base_week(start)
    for it in wk:
        if not it.get("is_rest"):
            it["duration_min"] = max(1, round(it["duration_min"] * factor))
            it["distance_km"] = round(it["distance_km"] * factor, 1)
    return wk

def novice_profile():
    # 近期最长跑设为 12km，使标准长距离(12km)落在 1.2x 边界内(=14.4)不被误判
    return {
        "近期跑步数据": [
            {"距离_km": 12, "平均配速_秒每公里": 420, "平均心率": 150, "最大心率": 170},
            {"距离_km": 10, "平均配速_秒每公里": 430, "平均心率": 155, "最大心率": 172},
        ],
        "健康指标": {"静息心率": 62},
        "体能指标": {"VO2max": 38, "训练准备度": 45},
    }


def build_cases():
    cases = []

    # ---------- 违规集（每个用例 seeds 已知违规） ----------
    # V1 连续高强度（old）
    p = trained_profile()
    plan = base_week(1)
    plan[2] = sess(3, "冲刺跑", 3, 20, 330)   # day3 间歇 -> 冲刺
    plan[3] = sess(4, "节奏跑", 5, 30, 350)   # day4 紧接 day3 高强度
    cases.append({"id": "V1", "profile": p, "plan": plan,
                  "expect": ["连续高强度"], "note": "day3+day4 连续高强度"})

    # V2 无休息日（old）
    p = trained_profile()
    plan = base_week(1)
    plan[0] = sess(1, "轻松跑", 3, 25, 400)   # 去掉 day1 休息
    plan[5] = sess(6, "轻松跑", 3, 25, 400)   # 去掉 day6 休息
    cases.append({"id": "V2", "profile": p, "plan": plan,
                  "expect": ["无休息日"], "note": "整周无休息日"})

    # V3 周负荷增幅超10%（old）
    p = trained_profile()
    w1 = base_week(1)
    w2 = base_week(8)
    for it in w2:
        if not it.get("is_rest"):
            it["duration_min"] = int(it["duration_min"] * 1.25)
            it["distance_km"] = round(it["distance_km"] * 1.25, 1)
    cases.append({"id": "V3", "profile": p, "plan": w1 + w2,
                  "expect": ["周负荷增幅超10%"], "note": "第2周负荷 +25%"})

    # V4 长距离超常（old）
    p = trained_profile(recent_max=10)
    plan = base_week(1, recent_max=10, long_dist=13, long_dur=80)  # 13 > 10*1.2=12
    cases.append({"id": "V4", "profile": p, "plan": plan,
                  "expect": ["长距离超常"], "note": "长距离 13km > 近期最长10km*1.2"})

    # V5 新手高强度占比过高（old）
    p = novice_profile()
    plan = base_week(1)
    plan[1] = sess(2, "间歇跑", 4, 30, 340)   # day2 高强度 (non-consecutive)
    plan[2] = sess(3, "轻松跑", 3, 25, 400)   # day3 改回轻松，避免与 day2 连续高强度
    plan[3] = sess(4, "冲刺跑", 3, 20, 330)   # day4 高强度 (non-consecutive)
    # 跑步日 = day2,3,4,5,7 =5 天；高强度=2 天 =40% > 20%
    cases.append({"id": "V5", "profile": p, "plan": plan,
                  "expect": ["新手高强度占比过高"], "note": "新手高强度占比 40%"})

    # V6 单次训练过长（R6）
    p = trained_profile()
    plan = base_week(1)
    plan[4] = sess(5, "长距离", 15, 160, 410)  # 160min>150；距离15<=近期18 不触发长距离超常
    cases.append({"id": "V6", "profile": p, "plan": plan,
                  "expect": ["单次训练过长"], "note": "单次 160min"})

    # V7 周高强度天数过多（R7，同时触发连续高强度）
    p = trained_profile()
    plan = base_week(1)
    plan[1] = sess(2, "间歇跑", 4, 30, 340)
    plan[2] = sess(3, "冲刺跑", 3, 20, 330)
    plan[3] = sess(4, "节奏跑", 5, 30, 350)
    plan[6] = sess(7, "比赛配速", 5, 30, 345)
    cases.append({"id": "V7", "profile": p, "plan": plan,
                  "expect": ["周高强度天数过多", "连续高强度"], "note": "单周4个高强度日"})

    # V8 每周多长距离（R8）
    p = trained_profile()
    plan = base_week(1)
    plan[6] = sess(7, "长距离", 10, 65, 410)  # 再加一个长距离
    cases.append({"id": "V8", "profile": p, "plan": plan,
                  "expect": ["每周多长距离"], "note": "单周2次长距离"})

    # V9 缺乏减量周（R9，16周无减量）
    p = trained_profile()
    plan = []
    for s in range(1, 16 * 7 + 1, 7):
        plan += base_week(s)
    cases.append({"id": "V9", "profile": p, "plan": plan,
                  "expect": ["缺乏减量周"], "note": "16周无减量/恢复周"})

    # V10 连续高强度（变体：day5+day6）
    p = trained_profile()
    plan = base_week(1)
    plan[4] = sess(5, "比赛配速", 6, 35, 345)
    plan[5] = sess(6, "间歇跑", 4, 30, 340)
    plan[6] = rest(7)  # 把 day7 改回休息，避免额外噪声
    cases.append({"id": "V10", "profile": p, "plan": plan,
                  "expect": ["连续高强度"], "note": "day5+day6 连续高强度"})

    # V11 周负荷增幅（变体：+40%）
    p = trained_profile()
    w1 = base_week(1)
    w2 = base_week(8)
    for it in w2:
        if not it.get("is_rest"):
            it["duration_min"] = int(it["duration_min"] * 1.4)
            it["distance_km"] = round(it["distance_km"] * 1.4, 1)
    cases.append({"id": "V11", "profile": p, "plan": w1 + w2,
                  "expect": ["周负荷增幅超10%"], "note": "第2周 +40%"})

    # V12 单次训练过长（变体：轻松跑 155min）
    p = trained_profile()
    plan = base_week(1)
    plan[1] = sess(2, "轻松跑", 18, 155, 400)
    cases.append({"id": "V12", "profile": p, "plan": plan,
                  "expect": ["单次训练过长"], "note": "轻松跑 155min"})

    # V13 每周多长距离（变体：3次长距离）
    p = trained_profile()
    plan = base_week(1)
    plan[1] = sess(2, "长距离", 8, 50, 410)
    plan[6] = sess(7, "长距离", 10, 65, 410)
    cases.append({"id": "V13", "profile": p, "plan": plan,
                  "expect": ["每周多长距离"], "note": "单周3次长距离"})

    # ---------- 合规集（expect 为空） ----------
    # C1 标准合规单周
    cases.append({"id": "C1", "profile": trained_profile(), "plan": base_week(1),
                  "expect": [], "note": "标准合规单周"})
    # C2 不同配速的合规单周
    cases.append({"id": "C2", "profile": trained_profile(15),
                  "plan": base_week(1, paces=(390, 330, 350)),
                  "expect": [], "note": "不同配速合规单周"})
    # C3 8周温和（无减量，weeks<12 不触发 R9）
    p = trained_profile()
    plan = []
    for s in range(1, 8 * 7 + 1, 7):
        plan += base_week(s)
    cases.append({"id": "C3", "profile": p, "plan": plan,
                  "expect": [], "note": "8周温和无减量（<12周）"})
    # C4 新手低强度合规周（1高强度日=20%）
    p = novice_profile()
    plan = base_week(1)
    plan[1] = sess(2, "间歇跑", 4, 30, 340)  # 仅1个高强度日，2/5=20% 不超
    plan[2] = sess(3, "轻松跑", 3, 25, 400)  # day3 改轻松，避免与 day2 连续高强度
    plan[3] = sess(4, "轻松跑", 3, 25, 400)
    plan[6] = sess(7, "轻松跑", 3, 25, 400)  # day7 节奏改轻松，避免额外高强度
    cases.append({"id": "C4", "profile": p, "plan": plan,
                  "expect": [], "note": "新手1高强度日(20%)"})
    # C5 边界：长距离=近期最长*1.2（严格 > 才触发，故不误报）
    p = trained_profile(recent_max=15)
    plan = base_week(1, recent_max=15, long_dist=18, long_dur=110)  # 18==15*1.2
    cases.append({"id": "C5", "profile": p, "plan": plan,
                  "expect": [], "note": "长距离=近期最长*1.2 边界"})
    # C6 新手全轻松周
    p = novice_profile()
    plan = [
        rest(1), sess(2, "轻松跑", 3, 25, 420), sess(3, "轻松跑", 3, 25, 420),
        sess(4, "轻松跑", 3, 25, 420), sess(5, "轻松跑", 3, 25, 420),
        rest(6), sess(7, "轻松跑", 3, 25, 420),
    ]
    cases.append({"id": "C6", "profile": p, "plan": plan,
                  "expect": [], "note": "新手全轻松周"})

    # C7 16周周期化（含恢复周 + 恢复周后渐进重建斜坡）
    # 目的：验证「合法周期化计划」对 rule3(周增幅) 与 rule9(缺乏减量周) 均保持沉默
    #       （零误报）。这是 round3 修复 rule3/rule9 耦合后的特异性用例：
    #       恢复周及其后重建斜坡不应触发周增幅警告，有恢复周则不应触发缺减量周。
    p = trained_profile()
    factors = [1.0, 1.0, 1.0, 0.65, 0.715, 0.786, 0.865, 0.951,
               1.0, 1.0, 1.0, 0.65, 0.715, 0.786, 0.865, 0.951]
    plan = []
    for i, f in enumerate(factors, start=1):
        plan += wk_scaled((i - 1) * 7 + 1, f)
    cases.append({"id": "C7", "profile": p, "plan": plan,
                  "expect": [], "note": "16周含恢复周+重建：rule3/rule9 均应沉默(零误报)"})

    return cases


def run_eval():
    cases = build_cases()
    rows = []
    tp_total = 0
    expect_total = 0
    fp_total = 0
    detect_total = 0
    for c in cases:
        flags = ps.check_safety_rules(c["plan"], c["profile"])
        detected = {f["rule"] for f in flags}
        expect = set(c["expect"])
        tp = detected & expect
        fp = detected - expect
        fn = expect - detected
        tp_total += len(tp)
        expect_total += len(expect)
        fp_total += len(fp)
        detect_total += len(detected)
        is_viol = bool(expect)
        rows.append({
            "id": c["id"], "kind": "违规" if is_viol else "合规",
            "expect": sorted(expect), "detected": sorted(detected),
            "tp": sorted(tp), "fp": sorted(fp), "fn": sorted(fn),
            "note": c.get("note", ""),
        })
    recall = (tp_total / expect_total) if expect_total else 1.0
    precision = (tp_total / detect_total) if detect_total else 1.0
    # 新规则是否已在代码中实现
    src = open(os.path.join(_PROJECT_DIR, "plan_science.py"), encoding="utf-8").read()
    new_present = {r: (r in src) for r in NEW_RULE_NAMES}
    return {
        "cases": rows, "recall": recall, "precision": precision,
        "tp_total": tp_total, "expect_total": expect_total,
        "fp_total": fp_total, "detect_total": detect_total,
        "new_present": new_present,
    }


def render_report(r):
    lines = ["# 安全护栏评测报告", "",
             f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
             f"- 召回率 recall：{r['recall']*100:.1f}%  （{r['tp_total']}/{r['expect_total']} 已标注违规被抓出）",
             f"- 精确率 precision：{r['precision']*100:.1f}%",
             f"- 误报数 fp：{r['fp_total']}（合规用例中被误判的项）",
             f"- 检出项总数：{r['detect_total']}",
             "", "## 新规则实现情况", ""]
    for name, ok in r["new_present"].items():
        lines.append(f"- {'✅' if ok else '⬜'} {name}")
    lines += ["", "## 逐用例明细", "",
              "| 用例 | 类型 | 期望违规 | 实际检出 | 漏检 | 误报 |",
              "|------|------|----------|----------|------|------|"]
    for row in r["cases"]:
        lines.append(
            f"| {row['id']} | {row['kind']} | {','.join(row['expect']) or '-'} "
            f"| {','.join(row['detected']) or '-'} | {','.join(row['fn']) or '-'} "
            f"| {','.join(row['fp']) or '-'} |"
        )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="额外写 JSON 结果")
    args = ap.parse_args()
    r = run_eval()
    report = render_report(r)
    print(report)
    out_dir = os.path.join(_PROJECT_DIR, "data", "eval")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "safety_rules_report.md"), "w", encoding="utf-8") as f:
        f.write(report)
    if args.json:
        with open(os.path.join(out_dir, "safety_rules_results.json"), "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
    # 给 CI/循环一个退出码：召回<90% 或 误报>1 视为未达标
    passed = (r["recall"] >= 0.90) and (r["fp_total"] <= 1)
    print("\n=== CRITIC GATE ===")
    print(f"recall>={0.90:.2f}: {'PASS' if r['recall']>=0.90 else 'FAIL'} ({r['recall']*100:.1f}%)")
    print(f"fp<={1}: {'PASS' if r['fp_total']<=1 else 'FAIL'} ({r['fp_total']})")
    print("OVERALL:", "PASS" if passed else "FAIL")


if __name__ == "__main__":
    main()
