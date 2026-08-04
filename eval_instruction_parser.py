"""指令解析内核评测（试炼循环 Round 6 / Critic 工具）

测量 parse_constraints 的「召回率」（听懂了多少用户指令）
与 verify_plan 的「精确率」（合规计划零误报、违规计划必抓）。

纯标准库，离线可跑。用法：
  python eval_instruction_parser.py
输出：逐用例判定 + 总体召回率/精确率 + 对抗句式覆盖 + 是否过杠。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
import instruction_parser as ip  # Builder 产物
from instruction_parser import WD_LIST

# ----------------------------------------------------------------------------
# 带标签评测集：instruction -> 期望约束(ground truth)
# 覆盖原 16 个用例的可解析部分 + 对抗句式（隔天/工作日/周X和周Y/不要周末）
# ----------------------------------------------------------------------------
CASES = [
    # ---- 原 16 用例的可解析部分 ----
    {"id": 1, "instruction": "每周跑3次，周日安排一次20公里长距离慢跑",
     "expect": {"weekly_freq": 3, "weekday_rules": [{"weekday": "日", "distance_km": 20, "type_kw": "长距离"}]}},
    {"id": 2, "instruction": "每周跑4次，其中周二做间歇跑",
     "expect": {"weekly_freq": 4, "weekday_rules": [{"weekday": "二", "type_kw": "间歇"}]}},
    {"id": 3, "instruction": "我想隔天跑一次，每次8公里轻松跑",
     "expect": {"weekly_freq": "every_other"}},
    {"id": 4, "instruction": "只在工作日跑步，周末休息",
     "expect": {"weekday_only": True, "no_weekend": True}},
    {"id": 5, "instruction": "准备半马，周中两次轻松跑，周六长距离15公里",
     "expect": {"weekly_freq": 3, "weekday_rules": [{"weekday": "六", "distance_km": 15, "type_kw": "长距离"}]}},
    {"id": 6, "instruction": "每周跑5次，周四休息",
     "expect": {"weekly_freq": 5, "rest_days": ["四"]}},
    {"id": 7, "instruction": "周日来个25公里LSD长距离",
     "expect": {"weekday_rules": [{"weekday": "日", "distance_km": 25, "type_kw": "长距离"}]}},
    {"id": 8, "instruction": "每周3次，两次轻松跑一次节奏跑",
     "expect": {"weekly_freq": 3}},
    {"id": 9, "instruction": "月计划，每周4次，周日15公里长距离",
     "expect": {"weekly_freq": 4, "weekday_rules": [{"weekday": "日", "distance_km": 15, "type_kw": "长距离"}]}},
    {"id": 10, "instruction": "每周跑3次，周五做节奏跑，周日10公里恢复跑",
     "expect": {"weekly_freq": 3, "weekday_rules": [{"weekday": "五", "type_kw": "节奏"},
                                                     {"weekday": "日", "distance_km": 10, "type_kw": "恢复"}]}},
    {"id": 11, "instruction": "帮我制定计划：每周跑4次，周二和周四轻松跑，周六间歇跑",
     "expect": {"weekly_freq": 4, "weekday_rules": [{"weekday": "二", "type_kw": "轻松"},
                                                     {"weekday": "四", "type_kw": "轻松"},
                                                     {"weekday": "六", "type_kw": "间歇"}]}},
    {"id": 12, "instruction": "生成训练计划，每周2次，周三8公里、周日16公里长距离",
     "expect": {"weekly_freq": 2, "weekday_rules": [{"weekday": "三", "distance_km": 8},
                                                     {"weekday": "日", "distance_km": 16, "type_kw": "长距离"}]}},
    {"id": 13, "instruction": "21天计划，每周跑4次，每个周日都安排18公里长距离慢跑",
     "expect": {"weekly_freq": 4, "weekday_rules": [{"weekday": "日", "distance_km": 18, "type_kw": "长距离"}]}},
    {"id": 14, "instruction": "三周训练计划，每周3次，每周六都做10公里节奏跑",
     "expect": {"weekly_freq": 3, "weekday_rules": [{"weekday": "六", "distance_km": 10, "type_kw": "节奏"}]}},
    {"id": 15, "instruction": "90天计划，每周跑5次，每个周日都做20公里长距离慢跑",
     "expect": {"weekly_freq": 5, "weekday_rules": [{"weekday": "日", "distance_km": 20, "type_kw": "长距离"}]}},
    {"id": 16, "instruction": "三个月计划，每周跑4次，每个周二做间歇跑、每个周日做15公里长距离",
     "expect": {"weekly_freq": 4, "weekday_rules": [{"weekday": "二", "type_kw": "间歇"},
                                                     {"weekday": "日", "distance_km": 15, "type_kw": "长距离"}]}},

    # ---- 对抗句式（Round 6 重点）----
    {"id": 101, "instruction": "我想隔天跑一次",
     "expect": {"weekly_freq": "every_other"}, "adversarial": True},
    {"id": 102, "instruction": "只在工作日跑步，不要周末",
     "expect": {"weekday_only": True, "no_weekend": True}, "adversarial": True},
    {"id": 103, "instruction": "不要周末跑，每周3次",
     "expect": {"weekly_freq": 3, "no_weekend": True}, "adversarial": True},
    {"id": 104, "instruction": "周二和周四都轻松跑，各10公里",
     "expect": {"weekday_rules": [{"weekday": "二", "distance_km": 10, "type_kw": "轻松"},
                                  {"weekday": "四", "distance_km": 10, "type_kw": "轻松"}]}, "adversarial": True},
    {"id": 105, "instruction": "每周跑5次，周三和周日各12公里长距离",
     "expect": {"weekly_freq": 5, "weekday_rules": [{"weekday": "三", "distance_km": 12, "type_kw": "长距离"},
                                                     {"weekday": "日", "distance_km": 12, "type_kw": "长距离"}]}, "adversarial": True},
]


# ----------------------------------------------------------------------------
# 由解析结果反推生成「合规计划」（闭环诚实：生成跟随理解）
# ----------------------------------------------------------------------------
def build_compliant_plan(c, duration):
    freq = c.get("weekly_freq")
    if c.get("weekday_only") or c.get("no_weekend"):
        candidates = ["一", "二", "三", "四", "五"]
    else:
        candidates = list(WD_LIST)
    rest_days = set(c.get("rest_days", []))
    rules = {r["weekday"]: r for r in c.get("weekday_rules", [])}

    # 每周跑步周几 = 显式周几规则(必含) ∪ 补足到 freq
    mandatory = list(rules.keys())
    if isinstance(freq, int):
        n = freq
    elif freq == "every_other":
        n = max(1, len(candidates) // 2)
    else:
        n = max(len(mandatory), 3)
    per_week = list(mandatory)
    for wd in candidates:
        if len(per_week) >= n:
            break
        if wd not in per_week and wd not in rest_days:
            per_week.append(wd)
    if isinstance(freq, int) or freq == "every_other":
        per_week = per_week[:n]

    plan = []
    for i in range(duration):
        wd = WD_LIST[i % 7]
        if wd in rest_days:
            plan.append({"day": i + 1, "weekday": wd, "type": "休息日", "distance_km": 0, "is_rest": True})
        elif wd in per_week or wd in rules:
            rule = rules.get(wd, {})
            if rule.get("type_kw"):
                typ = rule["type_kw"] + ("跑" if not rule["type_kw"].endswith("跑") else "")
            else:
                typ = "轻松跑"
            dist = rule.get("distance_km", 8.0)
            plan.append({"day": i + 1, "weekday": wd, "type": typ, "distance_km": dist, "is_rest": False})
        else:
            plan.append({"day": i + 1, "weekday": wd, "type": "休息日", "distance_km": 0, "is_rest": True})
    return plan


def build_violating_plan(c, duration):
    """真正制造违规：覆盖所有约束类型，确保 verify_plan 必能抓出。

    - weekday_rules：把该周几【所有周次】的距离砍半或类型改错（长周期需全改，
      否则 verify 取 max 会漏判）。
    - rest_days：把该休息日改成跑步。
    - no_weekend / weekday_only：在周末加一次跑步。
    - weekly_freq：额外加 2 次跑步，使周次数超容差。
    """
    plan = build_compliant_plan(c, duration)
    rules = c.get("weekday_rules", [])
    if rules:
        r = rules[0]
        wd = r["weekday"]
        for d in plan:
            if d.get("weekday") == wd and not d.get("is_rest"):
                if "distance_km" in r:
                    d["distance_km"] = max(1.0, d["distance_km"] / 2)
                else:
                    d["type"] = "间歇跑" if r.get("type_kw") != "间歇" else "轻松跑"
        return plan
    if c.get("rest_days"):
        wd = c["rest_days"][0]
        for d in plan:
            if d.get("weekday") == wd:
                d["is_rest"] = False
                d["type"] = "轻松跑"
                d["distance_km"] = 8.0
                break
        return plan
    if c.get("no_weekend") or c.get("weekday_only"):
        for d in plan:
            if d.get("weekday") in ("六", "日") and d.get("is_rest"):
                d["is_rest"] = False
                d["type"] = "轻松跑"
                d["distance_km"] = 8.0
                break
        return plan
    freq = c.get("weekly_freq")
    if isinstance(freq, int) or freq == "every_other":
        added = 0
        for d in plan:
            if d.get("is_rest") and added < 2:
                d["is_rest"] = False
                d["type"] = "轻松跑"
                d["distance_km"] = 8.0
                added += 1
    return plan


# ----------------------------------------------------------------------------
# 评分：召回率（解析器听懂了多少）+ 精确率（校验器误报/漏报）
# ----------------------------------------------------------------------------
def _item_matches(got, exp):
    """单项 ground-truth 是否被解析结果覆盖。"""
    if "weekly_freq" in exp and got.get("weekly_freq") != exp["weekly_freq"]:
        return False
    if "weekday_only" in exp and bool(got.get("weekday_only")) != exp["weekday_only"]:
        return False
    if "no_weekend" in exp and bool(got.get("no_weekend")) != exp["no_weekend"]:
        return False
    if "rest_days" in exp:
        if set(got.get("rest_days", [])) != set(exp["rest_days"]):
            return False
    if "weekday_rules" in exp:
        for er in exp["weekday_rules"]:
            gr = next((r for r in got.get("weekday_rules", []) if r["weekday"] == er["weekday"]), None)
            if not gr:
                return False
            if "distance_km" in er and abs(gr.get("distance_km", 0) - er["distance_km"]) > 2:
                return False
            if "type_kw" in er and gr.get("type_kw") != er["type_kw"]:
                return False
    return True


def main():
    total_items = 0
    matched_items = 0
    adv_cases = [c for c in CASES if c.get("adversarial")]
    adv_pass = 0
    precision_fp = 0   # 合规计划被误报的次数
    precision_fn = 0   # 违规计划未被抓出的次数
    fails = []

    print("=== 指令解析内核评测 (Critic) ===\n")
    for case in CASES:
        got = ip.parse_constraints(case["instruction"])
        exp = case["expect"]
        ok = _item_matches(got, exp)
        # 计项数
        exp_items = 0
        if "weekly_freq" in exp: exp_items += 1
        if "weekday_only" in exp: exp_items += 1
        if "no_weekend" in exp: exp_items += 1
        if "rest_days" in exp: exp_items += 1
        exp_items += len(exp.get("weekday_rules", []))
        # got 是否覆盖 exp（用 _item_matches 已含）
        total_items += exp_items
        if ok:
            matched_items += exp_items
        else:
            fails.append((case["id"], case["instruction"], got))
        if case.get("adversarial") and ok:
            adv_pass += 1

        # 精确率：合规计划应零误报；违规计划应被抓
        c = got
        duration = 7
        # 长周期用例用更大 duration 以容纳每周规则
        if any(k in case["instruction"] for k in ("21天", "三周", "90天", "三个月", "月计划")):
            duration = 21 if "21天" in case["instruction"] or "三周" in case["instruction"] else 90
        compliant = build_compliant_plan(c, duration)
        viol = build_violating_plan(c, duration)
        fp = len(ip.verify_plan(compliant, c, duration))
        fn = len(ip.verify_plan(viol, c, duration))
        precision_fp += fp
        if fn == 0 and (c.get("weekday_rules") or c.get("no_weekend") or c.get("weekday_only")):
            precision_fn += 1  # 本应抓出却没抓

        tag = "对抗" if case.get("adversarial") else "原"
        print(f"[{tag}] #{case['id']:>3} {'✔' if ok else '�’✗'} | {case['instruction'][:30]}")
        if not ok:
            print(f"       期望={exp}")
            print(f"       实得={got}")

    recall = matched_items / total_items if total_items else 0
    adv_rate = adv_pass / len(adv_cases) if adv_cases else 0
    # 硬标准
    recall_ok = recall >= 0.90
    adv_ok = adv_rate >= 1.0
    fp_ok = precision_fp == 0
    fn_ok = precision_fn == 0

    print("\n" + "=" * 50)
    print(f"召回率(解析听懂): {recall*100:.1f}%  (≥90%: {'✔' if recall_ok else '✗'})")
    print(f"对抗句式覆盖:     {adv_pass}/{len(adv_cases)} = {adv_rate*100:.0f}%  (=100%: {'✔' if adv_ok else '✗'})")
    print(f"合规计划误报数:   {precision_fp}  (=0: {'✔' if fp_ok else '✗'})")
    print(f"违规计划漏抓数:   {precision_fn}  (=0: {'✔' if fn_ok else '✗'})")
    print("=" * 50)
    passed = recall_ok and adv_ok and fp_ok and fn_ok
    print(f"CRITIC 过杠: {'✔ PASS' if passed else '✗ FAIL — 打回 Builder'}")
    print(f"失败用例: {[f[0] for f in fails] or '无'}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
