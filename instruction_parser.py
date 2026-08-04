"""指令→约束解析内核（试炼循环 Round 6 / Builder 产物）

目标：把用户的自然语言训练指令，解析成结构化、可机器校验的约束。
无论最终由哪个 LLM 生成计划，产品都必须先"听懂"用户命令；
本模块即该"听懂"能力，纯标准库、离线可跑，是安全护栏之外的第二道可信度防线。

设计原则（来自 gauntlet-loop skill 的工程契约）：
  - 仅依赖标准库（re / typing），不引入新依赖。
  - 解析与校验分离：parse_constraints 只做"理解"，verify_plan 只做"验证"。
  - 确定性：同一输入永远得到同一输出（无随机）。

约束数据结构（ConstraintSet）：
  {
    "weekly_freq": int | "every_other" | None,   # 每周跑步次数；"隔天"=every_other
    "weekday_rules": [                              # 指定周几的细化要求
        {"weekday": "二", "distance_km": 8.0, "type_kw": "轻松"},  # 字段均可缺省
        ...
    ],
    "weekday_only": bool,   # 只在工作日跑（周一~周五）
    "no_weekend": bool,     # 周末不跑
    "rest_days": ["四", ...],  # 指定休息的周几
  }
"""


import re
from typing import Optional

WD_LIST = ["一", "二", "三", "四", "五", "六", "日"]


def _find_weekdays(text: str):
    """返回指令中出现的所有周几字符（保持出现顺序，去重）。"""
    return [wd for wd in WD_LIST if wd in text]


def parse_constraints(text: str) -> dict:
    """把自然语言指令解析成 ConstraintSet。

    返回结构见模块 docstring。所有字段缺省为 None / [] / False。
    """
    constraints: dict = {
        "weekly_freq": None,
        "weekday_rules": [],
        "weekday_only": False,
        "no_weekend": False,
        "rest_days": [],
    }
    if not text:
        return constraints

    # 1) 每周次数："每周跑3次" / "每周3次" / "每周跑 4 次"
    m = re.search(r"每周[^\d]*?(\d+)\s*次", text)
    if m:
        constraints["weekly_freq"] = int(m.group(1))

    # 2) 隔天跑："隔天跑一次" / "隔天跑" / "每隔一天"
    if re.search(r"隔[一天天]", text) or "隔天" in text:
        constraints["weekly_freq"] = "every_other"

    # 3) 只在工作日："只在工作日" / "工作日跑步" / "工作日跑"
    if "工作日" in text:
        constraints["weekday_only"] = True

    # 4) 周末不跑："周末休息" / "不要周末" / "不周末" / "周末不跑"
    if re.search(r"周末休息|不要周末|不周末|周末不跑|周末不?安排", text):
        constraints["no_weekend"] = True

    # 5) 指定休息日："周X休息" / "X休息"（X 为周几）
    for wd in _find_weekdays(text):
        if re.search(rf"周{wd}\s*休息", text):
            if wd not in constraints["rest_days"]:
                constraints["rest_days"].append(wd)

    # 6) 周几细化规则："周X ... 公里" / "周X做/安排 ... 跑" / "周X和周Y都/各<属性>"
    #    关键：支持「并列共享属性」——"周二和周四轻松跑"中"轻松"只写在末尾，
    #    但应赋给周二与周四两个周几。做法：先找出所有"周X"提及并按"和/、"分组，
    #    整组共享同一属性子句。
    mentions = [(mo.start(), mo.group(1)) for mo in re.finditer(r"周([一二三四五六日])", text)]
    groups = []
    for pos, wd in mentions:
        if groups and _conjoined(text, groups[-1]["end"], pos):
            groups[-1]["wds"].append(wd)
            groups[-1]["end"] = pos + 1
        else:
            groups.append({"wds": [wd], "start": pos, "end": pos + 1})
    for g in groups:
        nxt = next((gg["start"] for gg in groups if gg["start"] > g["start"]), len(text))
        clause = text[g["start"]:nxt]
        dist = None
        dm = re.search(r"(\d+(?:\.\d+)?)\s*(公里|km|KM)", clause)
        if dm:
            dist = float(dm.group(1))
        type_kw = None
        for kw in ("长距离", "lsd", "LSD", "间歇", "节奏", "恢复", "轻松", "慢跑"):
            if kw.lower() in clause.lower():
                type_kw = "长距离" if kw in ("长距离", "lsd", "LSD") else kw
                break
        if dist is None and type_kw is None:
            continue  # 该组无属性（如"每周3次"里的周X提及），跳过
        for wd in g["wds"]:
            existing = next((r for r in constraints["weekday_rules"] if r["weekday"] == wd), None)
            rule = {"weekday": wd}
            if dist is not None:
                rule["distance_km"] = dist
            if type_kw is not None:
                rule["type_kw"] = type_kw
            if existing:
                existing.update(rule)
            else:
                constraints["weekday_rules"].append(rule)

    # 7) 周中次数："周中两次轻松跑" → 周中2次 + 显式周几规则数 = 每周总次数
    #    （周中与周末长跑不重叠，故直接相加；仅当未显式给"每周X次"时启用）
    #    支持阿拉伯数字与中文数字（两/二/三/四/五/六）。
    cn_num = {"两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
    m_mid = re.search(r"周中([\d两二三四五六])\s*次", text)
    if m_mid and constraints["weekly_freq"] is None:
        raw = m_mid.group(1)
        n_mid = int(raw) if raw.isdigit() else cn_num.get(raw, 0)
        if n_mid:
            constraints["weekly_freq"] = n_mid + len(constraints["weekday_rules"])

    return constraints


def _conjoined(text: str, a: int, b: int) -> bool:
    """判断两个"周X"提及之间是否由'和'连接（即并列共享属性）。

    注意：'、'是逐项列举分隔符（如"周三8公里、周日16公里"各项距离独立），
    不应视为共享；只有'和'（如"周二和周四轻松跑"）才表示共享同一属性。
    """
    return "和" in text[a:b]


def verify_plan(structured, constraints: dict, duration: int = 7) -> list:
    """校验结构化计划是否满足约束。

    structured: list of {"day","weekday","type","distance_km","is_rest",...}
    返回 violations: list of {"level","rule","detail"}（为空=全部满足）。
    """
    violations: list = []

    def _runs(items):
        out = []
        for d in items:
            if d.get("is_rest") or (d.get("type") in ("休息日", "休息")):
                continue
            out.append(d)
        return out

    def _wd_runs(items, wd):
        return [d for d in _runs(items) if d.get("weekday") == wd]

    def _dist(d):
        try:
            return float(d.get("distance_km") or 0)
        except (TypeError, ValueError):
            return 0.0

    runs = _runs(structured)

    # 每周次数
    wf = constraints.get("weekly_freq")
    if isinstance(wf, int):
        expected = round(wf * duration / 7)
        actual = len(runs)
        if abs(actual - expected) > 1:
            violations.append({
                "level": "warning", "rule": "每周次数不符",
                "detail": f"实际跑步{actual}次，期望≈{expected}（每周{wf}次/{duration}天）",
            })
    elif wf == "every_other":
        expected = duration // 2
        actual = len(runs)
        if abs(actual - expected) > 1:
            violations.append({
                "level": "warning", "rule": "隔天频率不符",
                "detail": f"实际跑步{actual}次，隔天期望≈{expected}次",
            })

    # 只在工作日
    if constraints.get("weekday_only"):
        wk = _wd_runs(structured, "六") + _wd_runs(structured, "日")
        if wk:
            violations.append({
                "level": "warning", "rule": "周末不应跑步",
                "detail": f"工作日模式下周末仍有{len(wk)}次跑步",
            })

    # 周末不跑
    if constraints.get("no_weekend"):
        wk = _wd_runs(structured, "六") + _wd_runs(structured, "日")
        if wk:
            violations.append({
                "level": "warning", "rule": "周末不应跑步",
                "detail": f"要求周末不跑，但周末有{len(wk)}次跑步",
            })

    # 指定休息日
    for wd in constraints.get("rest_days", []):
        day = [d for d in structured if d.get("weekday") == wd]
        if not day:
            violations.append({"level": "warning", "rule": "休息日缺失",
                               "detail": f"周{wd} 在计划中不存在"})
            continue
        d = day[0]
        rest = d.get("is_rest") or (d.get("type") in ("休息日", "休息"))
        if not rest:
            violations.append({"level": "warning", "rule": "休息日未落实",
                               "detail": f"周{wd} 应为休息，但实际安排了跑步"})

    # 周几细化规则
    for rule in constraints.get("weekday_rules", []):
        wd = rule["weekday"]
        cand = _wd_runs(structured, wd)
        if not cand:
            violations.append({"level": "warning", "rule": "周几要求缺失",
                               "detail": f"周{wd} 无跑步日（指令要求该日训练）"})
            continue
        if "distance_km" in rule:
            best = max(_dist(d) for d in cand)
            if best < rule["distance_km"] - 2:
                violations.append({"level": "warning", "rule": "周几距离不足",
                                   "detail": f"周{wd} 最长{best}km < 期望{rule['distance_km']}km"})
        if "type_kw" in rule:
            hit = [d for d in cand if rule["type_kw"] in (d.get("type") or "")]
            if not hit:
                violations.append({"level": "warning", "rule": "周几类型不符",
                                   "detail": f"周{wd} 类型未命中'{rule['type_kw']}'"
                                             f"（实际:{[d.get('type') for d in cand]}）"})

    return violations


if __name__ == "__main__":
    # 自测：覆盖典型与对抗句式
    tests = [
        "每周跑3次，周日安排一次20公里长距离慢跑",
        "我想隔天跑一次，每次8公里轻松跑",
        "只在工作日跑步，周末休息",
        "每周跑4次，周二和周四轻松跑，周六间歇跑",
        "不要周末跑，每周3次",
    ]
    for t in tests:
        print(f"\n指令：{t}")
        c = parse_constraints(t)
        print("  →", c)
        # 用一个满足约束的假计划校验 verify 不误报
        plan = [{"day": i + 1, "weekday": WD_LIST[i % 7], "type": "轻松跑",
                 "distance_km": 8, "is_rest": False} for i in range(7)]
        # 应用约束粗略生成（仅用于自测 verify 不崩）
        v = verify_plan(plan, c, 7)
        print("  verify(朴素计划) violations:", len(v))
