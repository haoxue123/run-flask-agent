"""命令遵循率评测（command-following eval）

对应 evals-first 纪律：先有"命令不被听从"的真实痛点，再针对它建一个小 eval。
本评测测量：当用户给出带显式约束的命令（如"每周跑3次、周日20km"）时，
generate_training_plan 生成的 structured 计划是否真的落实了这些约束。

测量方式（2x2 选型：客观 + 有 ground truth）：
  直接解析 structured plan 的字段做代码判定，不依赖 LLM 判官。

用法：
  python eval_command_following.py --mode mock       # 纯本地自检（不调 API），验证 harness + 指令透传
  python eval_command_following.py --mode live       # 调 DeepSeek 真实生成，测量真实"命令遵循率"
  python eval_command_following.py --mode auto       # 有 API key 走 live，否则 mock（默认）
  python eval_command_following.py --limit 5         # 只跑前 N 例

输出：
  命令行打印逐例判定 + 总体"命令遵循率"；同时写 data/eval/command_following_results.json
"""

import os
import re
import json
import argparse
from datetime import datetime

# ----------------------------------------------------------------------------
# 样例跑者画像（内联，保证评测可独立运行；真实产品用 data_tool.build_runner_profile）
# ----------------------------------------------------------------------------
SAMPLE_PROFILE = {
    "目标": {"目标比赛": "半程马拉松", "目标距离": "21.1km", "目标完赛时间": "2:00:00"},
    "近期跑步数据": [
        {"日期": "2026-07-20", "距离_km": 10.0, "平均配速_秒每公里": 360, "类型": "轻松跑"},
        {"日期": "2026-07-22", "距离_km": 12.0, "平均配速_秒每公里": 350, "类型": "节奏跑"},
    ],
    "健康指标": {"静息心率": 55, "睡眠时长_小时": 7.5},
}

WD = ["一", "二", "三", "四", "五", "六", "日"]


# ----------------------------------------------------------------------------
# plan 解析辅助
# ----------------------------------------------------------------------------
def _runs(structured):
    out = []
    for d in structured:
        if d.get("is_rest"):
            continue
        t = (d.get("type") or "")
        if t in ("休息日", "休息"):
            continue
        out.append(d)
    return out


def _weekday_runs(structured, wd):
    return [d for d in _runs(structured) if d.get("weekday") == wd]


def _dist(d):
    try:
        return float(d.get("distance_km") or 0)
    except (TypeError, ValueError):
        return 0.0


# ----------------------------------------------------------------------------
# 检查断言（每个返回 (passed: bool, detail: str)）
# ----------------------------------------------------------------------------
def chk_weekly_n(n, duration):
    def f(structured):
        expected = round(n * duration / 7)
        actual = len(_runs(structured))
        ok = abs(actual - expected) <= 1
        return ok, f"跑步天数={actual}, 期望≈{expected}(每周{n}次/{duration}天)"
    return f


def chk_weekday_distance(wd, km, tol=2):
    def f(structured):
        cand = _weekday_runs(structured, wd)
        if not cand:
            return False, f"周{wd} 无跑步日"
        best = max(_dist(d) for d in cand)
        ok = best >= km - tol
        return ok, f"周{wd} 最长距离={best}km, 期望≥{km}"
    return f


def chk_weekday_type(wd, kw):
    def f(structured):
        cand = _weekday_runs(structured, wd)
        hit = [d for d in cand if kw in (d.get("type") or "")]
        ok = bool(hit)
        return ok, f"周{wd} 类型命中'{kw}'={ok} (实际:{[d.get('type') for d in cand]})"
    return f


def chk_has_type(kw):
    def f(structured):
        hit = [d for d in _runs(structured) if kw in (d.get("type") or "")]
        ok = bool(hit)
        return ok, f"计划中含'{kw}'={ok}"
    return f


def chk_no_weekend_run():
    def f(structured):
        wk = _weekday_runs(structured, "六") + _weekday_runs(structured, "日")
        ok = len(wk) == 0
        return ok, f"周末跑步次数={len(wk)} (期望0)"
    return f


def chk_weekday_rest(wd):
    def f(structured):
        day = [d for d in structured if d.get("weekday") == wd]
        if not day:
            return False, f"周{wd} 不存在"
        d = day[0]
        rest = d.get("is_rest") or (d.get("type") in ("休息日", "休息"))
        return bool(rest), f"周{wd} 休息={rest}"
    return f


def chk_all_distance_between(lo, hi):
    def f(structured):
        rs = _runs(structured)
        if not rs:
            return False, "无跑步日"
        ok = all(lo <= _dist(d) <= hi for d in rs)
        dists = [round(_dist(d), 1) for d in rs]
        return ok, f"各次距离={dists} 期望[{lo},{hi}]"
    return f


def chk_weekly_exact_n(n, duration):
    """压测（长周期）：每一个完整周都应恰好 n 次跑步，而非仅平均达标。"""
    def f(structured):
        weeks = {}
        for d in structured:
            w = (int(d.get("day", 1)) - 1) // 7
            if d.get("is_rest") or (d.get("type") in ("休息日", "休息")):
                weeks.setdefault(w, 0)
            else:
                weeks[w] = weeks.get(w, 0) + 1
        total_weeks = duration // 7
        bad = [f"第{w+1}周={weeks.get(w, 0)}" for w in range(total_weeks)
               if weeks.get(w, 0) != n]
        ok = not bad
        return ok, (f"完整周{total_weeks}个，每周均需={n}次："
                    + ("全部符合 ✔" if ok else "不符→ " + "; ".join(bad)))
    return f


def chk_weekday_distance_every(wd, km, tol=2):
    """压测（长周期）：周X 的每一次（每周）距离都应≈km，而非仅最长一周达标。"""
    def f(structured):
        cand = _weekday_runs(structured, wd)
        if not cand:
            return False, f"周{wd} 无跑步日"
        off = [round(_dist(d) - km, 1) for d in cand if abs(_dist(d) - km) > tol]
        ok = not off
        return ok, (f"周{wd} 共{len(cand)}次，偏离>±{tol}km 的有{len(off)}次"
                    + (" ✔" if ok else f"，偏离值:{off}"))
    return f


def chk_weekday_type_every(wd, kw):
    """压测（长周期）：周X 的每一次（每周）类型都应命中 kw，而非仅某周。"""
    def f(structured):
        cand = _weekday_runs(structured, wd)
        miss = [d.get("day") for d in cand if kw not in (d.get("type") or "")]
        ok = not miss
        return ok, (f"周{wd} 共{len(cand)}次，未命中'{kw}'的有{len(miss)}次"
                    + (" ✔" if ok else f"，缺失日:{miss}"))
    return f


# ----------------------------------------------------------------------------
# 测试用例（用户命令 + 期望约束的 ground truth 检查）
# ----------------------------------------------------------------------------
CASES = [
    {"id": 1, "duration": 7,
     "instruction": "每周跑3次，周日安排一次20公里长距离慢跑",
     "checks": [chk_weekly_n(3, 7), chk_weekday_distance("日", 20), chk_weekday_type("日", "长距离")]},
    {"id": 2, "duration": 7,
     "instruction": "每周跑4次，其中周二做间歇跑",
     "checks": [chk_weekly_n(4, 7), chk_weekday_type("二", "间歇")]},
    {"id": 3, "duration": 7,
     "instruction": "我想隔天跑一次，每次8公里轻松跑",
     "checks": [chk_weekly_n(3, 7), chk_all_distance_between(7, 9)]},
    {"id": 4, "duration": 7,
     "instruction": "只在工作日跑步，周末休息",
     "checks": [chk_no_weekend_run()]},
    {"id": 5, "duration": 7,
     "instruction": "准备半马，周中两次轻松跑，周六长距离15公里",
     "checks": [chk_weekly_n(3, 7), chk_weekday_distance("六", 15), chk_has_type("轻松")]},
    {"id": 6, "duration": 7,
     "instruction": "每周跑5次，周四休息",
     "checks": [chk_weekly_n(5, 7), chk_weekday_rest("四")]},
    {"id": 7, "duration": 7,
     "instruction": "周日来个25公里LSD长距离",
     "checks": [chk_weekday_distance("日", 25), chk_weekday_type("日", "长距离")]},
    {"id": 8, "duration": 7,
     "instruction": "每周3次，两次轻松跑一次节奏跑",
     "checks": [chk_weekly_n(3, 7), chk_has_type("轻松"), chk_has_type("节奏")]},
    {"id": 9, "duration": 30,
     "instruction": "月计划，每周4次，周日15公里长距离",
     "checks": [chk_weekly_n(4, 30), chk_weekday_distance("日", 15), chk_weekday_type("日", "长距离")]},
    {"id": 10, "duration": 7,
     "instruction": "每周跑3次，周五做节奏跑，周日10公里恢复跑",
     "checks": [chk_weekly_n(3, 7), chk_weekday_type("五", "节奏"), chk_weekday_distance("日", 10)]},
    {"id": 11, "duration": 7,
     "instruction": "帮我制定计划：每周跑4次，周二和周四轻松跑，周六间歇跑",
     "checks": [chk_weekly_n(4, 7), chk_weekday_type("二", "轻松"), chk_weekday_type("四", "轻松"), chk_weekday_type("六", "间歇")]},
    {"id": 12, "duration": 7,
     "instruction": "生成训练计划，每周2次，周三8公里、周日16公里长距离",
     "checks": [chk_weekly_n(2, 7), chk_weekday_distance("三", 8), chk_weekday_distance("日", 16)]},

    # ---- 长周期压测：聚焦"每周精确值"场景（21天/90天）----
    # 这类用例专门捕捉"长周期计划仅平均达标、并非每周都精确遵循"的薄弱点。
    {"id": 13, "duration": 21,
     "instruction": "21天计划，每周跑4次，每个周日都安排18公里长距离慢跑",
     "checks": [chk_weekly_exact_n(4, 21), chk_weekday_distance_every("日", 18),
                chk_weekday_type_every("日", "长距离")]},
    {"id": 14, "duration": 21,
     "instruction": "三周训练计划，每周3次，每周六都做10公里节奏跑",
     "checks": [chk_weekly_exact_n(3, 21), chk_weekday_distance_every("六", 10),
                chk_weekday_type_every("六", "节奏")]},
    {"id": 15, "duration": 90,
     "instruction": "90天计划，每周跑5次，每个周日都做20公里长距离慢跑",
     "checks": [chk_weekly_exact_n(5, 90), chk_weekday_distance_every("日", 20),
                chk_weekday_type_every("日", "长距离")]},
    {"id": 16, "duration": 90,
     "instruction": "三个月计划，每周跑4次，每个周二做间歇跑、每个周日做15公里长距离（每周都如此）",
     "checks": [chk_weekly_exact_n(4, 90), chk_weekday_type_every("二", "间歇"),
                chk_weekday_distance_every("日", 15), chk_weekday_type_every("日", "长距离")]},
]


# ----------------------------------------------------------------------------
# 生成器：live（真实 LLM）/ mock（本地自检）
# ----------------------------------------------------------------------------
def _live_generate(instruction, duration):
    import llm_agent  # 延迟导入：mock 模式无需 API/依赖
    # 模型名默认跟随项目代码（已升级 deepseek-v4-pro）；
    # 可用 EVAL_MODEL / EVAL_MODEL_REASONER 环境变量覆盖以便快速对比。
    if os.getenv("EVAL_MODEL"):
        llm_agent.MODEL_CHAT = os.getenv("EVAL_MODEL")
    if os.getenv("EVAL_MODEL_REASONER"):
        llm_agent.MODEL_REASONER = os.getenv("EVAL_MODEL_REASONER")
    result = llm_agent.generate_training_plan(
        SAMPLE_PROFILE, plan_duration=duration, user_instructions=instruction
    )
    return result.get("structured", []), result.get("self_check")


def _mock_generate(instruction, duration):
    """本地自检：尽力按指令解析出一份结构化计划，仅用于验证 harness 与指令透传。"""
    m = re.search(r"每周[^\d]*(\d+)\s*次", instruction)
    weekly = int(m.group(1)) if m else 3
    # 显式点名的周几（"周日安排18公里"/"周六做节奏跑" 等），排除泛指"每周"
    explicit = [wd for wd in WD
                if re.search(rf"周{wd}(?:[^周]*(?:公里|km|做|安排|跑|长距离|间歇|节奏|lsd|恢复))?", instruction)]
    # 跑步日 = 显式点名周几 + 补足默认工作日，凑满 weekly 次
    run_wds = list(explicit)
    for wd in ["二", "三", "四", "五", "一", "六", "日"]:
        if len(run_wds) >= weekly:
            break
        if wd not in run_wds:
            run_wds.append(wd)
    run_wds = run_wds[:weekly] if weekly <= 7 else list(WD)
    days = []
    for i in range(duration):
        wd = WD[i % 7]
        if wd in run_wds:
            t = "轻松跑"
            dist = 8.0
            # 指令里的周X距离覆盖（[^周] 防止跨周几误捕获，如"周二…周日15"）
            mm = re.search(rf"周{wd}[^周]*?(\d+)\s*(公里|km)", instruction)
            if mm:
                dist = float(mm.group(1))
            # 周X附近的训练类型（按该周几所在分句推导）
            seg = re.search(rf"周{wd}[^周]*", instruction)
            segtext = seg.group(0) if seg else instruction
            if "间歇" in segtext:
                t = "间歇跑"
            elif "节奏" in segtext:
                t = "节奏跑"
            elif "长距离" in segtext or "lsd" in segtext.lower():
                t = "长距离慢跑"
            elif "恢复" in segtext:
                t = "恢复跑"
            elif "轻松" in segtext:
                t = "轻松跑"
            days.append({"day": i + 1, "weekday": wd, "type": t,
                         "distance_km": dist, "target_pace": "6'30\"",
                         "is_rest": False})
        else:
            days.append({"day": i + 1, "weekday": wd, "type": "休息日", "is_rest": True})
    return days, None


# ----------------------------------------------------------------------------
# 执行
# ----------------------------------------------------------------------------
def run_case(case, mode):
    duration = case["duration"]
    instruction = case["instruction"]
    if mode == "live":
        structured, sc = _live_generate(instruction, duration)
    else:
        structured, sc = _mock_generate(instruction, duration)

    check_results = []
    for chk in case["checks"]:
        try:
            ok, detail = chk(structured)
        except Exception as e:
            ok, detail = False, f"检查异常:{e}"
        check_results.append({"ok": ok, "detail": detail})
    passed = all(r["ok"] for r in check_results)
    return {"structured_len": len(structured), "passed": passed,
            "checks": check_results, "self_check": sc}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["mock", "live", "auto"], default="auto")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", type=str, default="",
                    help="只跑指定 id（逗号分隔，如 13,14,15,16），用于单独压测新增样例")
    args = ap.parse_args()

    mode = args.mode
    if mode == "auto":
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass
        key = os.getenv("DEEPSEEK_API_KEY")
        mode = "live" if key else "mock"

    cases = CASES[:args.limit] if args.limit else CASES
    if args.ids:
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
        cases = [c for c in cases if c["id"] in ids]
    print(f"=== 命令遵循率评测 (mode={mode}) ===\n")

    results = []
    per_check_pass = 0
    per_check_total = 0
    case_pass = 0
    sc_pass = 0
    sc_total = 0
    for case in cases:
        r = run_case(case, mode)
        results.append({"id": case["id"], "instruction": case["instruction"],
                        "passed": r["passed"], "checks": r["checks"],
                        "self_check": r.get("self_check")})
        if r["passed"]:
            case_pass += 1
        for c in r["checks"]:
            per_check_total += 1
            if c["ok"]:
                per_check_pass += 1
        sc = r.get("self_check")
        if mode == "live" and sc is not None:
            sc_total += 1
            if sc.get("covered"):
                sc_pass += 1
        status = "✅" if r["passed"] else "❌"
        print(f"[{status}] #{case['id']} {case['instruction']}")
        for c in r["checks"]:
            print(f"      {'✅' if c['ok'] else '❌'} {c['detail']}")
        if mode == "live" and sc is not None:
            cov = "通过" if sc.get("covered") else "未通过"
            retry = "（已重试修正）" if sc.get("retried") else ""
            print(f"      自检:{cov}{retry} {sc.get('analysis','')}")

    case_rate = case_pass / len(cases) if cases else 0
    check_rate = per_check_pass / per_check_total if per_check_total else 0
    print("\n=== 汇总 ===")
    print(f"模式: {mode}")
    print(f"案例遵循率(全部约束通过): {case_pass}/{len(cases)} = {case_rate:.0%}")
    print(f"单条约束遵循率: {per_check_pass}/{per_check_total} = {check_rate:.0%}")
    if mode == "live" and sc_total:
        print(f"指令遵循自检通过率(reasoner): {sc_pass}/{sc_total} = {sc_pass/sc_total:.0%}")

    # 持久化
    out = {
        "mode": mode,
        "generated_at": datetime.now().isoformat(),
        "case_follow_rate": case_rate,
        "check_follow_rate": check_rate,
        "self_check_pass_rate": (sc_pass / sc_total) if sc_total else None,
        "cases": results,
    }
    try:
        os.makedirs("data/eval", exist_ok=True)
        with open("data/eval/command_following_results.json", "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("结果已写 data/eval/command_following_results.json")
    except Exception as e:
        print(f"写结果失败: {e}")


if __name__ == "__main__":
    main()
