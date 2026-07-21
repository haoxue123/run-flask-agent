# -*- coding: utf-8 -*-
"""
评测测试数据生成器
============================================================
为 4 指标离线评测生成可复现的测试用例数据集：

  metric1_profiles.json  —— 60 条多样化跑者画像（用于指标1 JSON成功率 + 指标2 配速偏差）
  metric3_rag.json       —— 20 道跑步科学问答（标注预期知识源文件）
  metric4_intent.json    —— 30 条意图分类样本（覆盖 5 类意图）

画像结构兼容 plan_science.build_constraints / enrich_plan：
  {
    "目标": {"目标比赛", "目标完赛时间", "目标距离"},
    "近期跑步数据": [{"活动名称","距离_km","平均配速_秒每公里","平均心率","最大心率","时长_分钟"}, ...],
    "体能指标": {"VO2max","训练准备度"},
    "健康指标": {"静息心率"}
  }

运行：python gen_eval_data.py
依赖：仅标准库（保证任何环境可复现）
"""

import os
import json
import random

random.seed(20260721)  # 固定种子 → 可复现

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_OUT_DIR = os.path.join(_PROJECT_DIR, "data", "eval")
os.makedirs(_OUT_DIR, exist_ok=True)


# ============================================================
# 指标1 + 指标2：跑者画像（60 条）
# ============================================================

# 场景模板：(场景名, 目标字典, 基础配速秒/km, VO2max, 训练准备度, 静息心率, 跑步条数)
_SCENARIOS = [
    ("减脂慢跑",   {"目标比赛": "", "目标完赛时间": "", "目标距离": ""},
     420, 38, 55, 64, 6),
    ("马拉松备赛", {"目标比赛": "全程马拉松", "目标完赛时间": "3:45:00", "目标距离": "42.195km"},
     370, 52, 72, 52, 7),
    ("恢复跑回归", {"目标比赛": "", "目标完赛时间": "", "目标距离": ""},
     430, 41, 60, 60, 5),
    ("新手入门",   {"目标比赛": "", "目标完赛时间": "", "目标距离": ""},
     455, 34, 42, 70, 5),
    ("进阶提升",   {"目标比赛": "半程马拉松", "目标完赛时间": "1:45:00", "目标距离": "21.0975km"},
     355, 56, 78, 50, 7),
    ("伤后回归",   {"目标比赛": "", "目标完赛时间": "", "目标距离": ""},
     440, 40, 58, 62, 5),
    ("越野训练",   {"目标比赛": "越野跑 30km", "目标完赛时间": "", "目标距离": "30km"},
     400, 48, 70, 56, 6),
    ("5K提速",     {"目标比赛": "5公里赛", "目标完赛时间": "22:00", "目标距离": "5km"},
     345, 54, 80, 54, 7),
    ("半马备赛",   {"目标比赛": "半程马拉松", "目标完赛时间": "1:50:00", "目标距离": "21.0975km"},
     380, 49, 68, 55, 7),
    ("一般健身",   {"目标比赛": "", "目标完赛时间": "", "目标距离": ""},
     410, 44, 63, 66, 6),
]

_ACTIVITY_TYPES = ["轻松跑", "长距离慢跑", "间歇跑", "节奏跑", "恢复跑", "慢跑"]


def _build_runs(base_pace, n, rest_hr, max_hr):
    """生成近期跑步数据，保证至少 3 条距离>=4km 的可持续跑（阈值可算）。"""
    runs = []
    for i in range(n):
        # 配速在基础值附近波动
        pace = int(base_pace + random.randint(-20, 35))
        # 距离：穿插长距离与短距离
        if i % 3 == 2:
            dist = round(random.uniform(12.0, 21.0), 1)      # 长距离
            pace = int(base_pace + random.randint(5, 30))     # 长距离略慢
        elif i % 4 == 3:
            dist = round(random.uniform(4.0, 6.0), 1)         # 间歇/节奏
            pace = int(base_pace - random.randint(10, 35))    # 偏快
        else:
            dist = round(random.uniform(5.0, 9.0), 1)
        act = _ACTIVITY_TYPES[i % len(_ACTIVITY_TYPES)]
        dur = round(dist * (pace / 60.0), 1)
        avg_hr = int(rest_hr + (max_hr - rest_hr) * random.uniform(0.62, 0.85))
        runs.append({
            "活动名称": act,
            "距离_km": dist,
            "平均配速_秒每公里": pace,
            "平均心率": avg_hr,
            "最大心率": max_hr,
            "时长_分钟": dur,
        })
    # 保证前 3 条都是 >=4km 的可持续跑
    for j in range(3):
        if runs[j]["距离_km"] < 4.0:
            runs[j]["距离_km"] = round(random.uniform(5.0, 8.0), 1)
            runs[j]["平均配速_秒每公里"] = int(base_pace + random.randint(-10, 20))
    return runs


def _build_profiles():
    profiles = []
    pid = 0
    # 每个场景生成 6 条（10 场景 × 6 = 60）
    for scn_name, goal, base_pace, vo2, readiness, rest_hr, n in _SCENARIOS:
        for k in range(6):
            max_hr = rest_hr + random.randint(118, 140)
            runs = _build_runs(base_pace, n, rest_hr, max_hr)
            profile = {
                "目标": dict(goal),
                "近期跑步数据": runs,
                "体能指标": {"VO2max": vo2, "训练准备度": readiness},
                "健康指标": {"静息心率": rest_hr},
            }
            pid += 1
            profiles.append({
                "id": f"P{pid:03d}",
                "scenario": scn_name,
                "profile": profile,
            })
    return profiles


# ============================================================
# 指标3：RAG 检索（20 题，标注预期知识源文件）
# ============================================================

# 8 个知识源文件
_KNOWLEDGE_FILES = [
    "beginner_guide.md", "common_mistakes.md", "injury_prevention.md",
    "nutrition.md", "pace_strategy.md", "race_preparation.md",
    "recovery.md", "training_principles.md",
]

_RAG_QUERIES = [
    ("新手应该如何开始跑步？需要什么装备？", "beginner_guide.md"),
    ("第一次跑步前要不要做热身？怎么热身？", "beginner_guide.md"),
    ("跑步时常见的错误有哪些？", "common_mistakes.md"),
    ("为什么不能每次都跑太快？", "common_mistakes.md"),
    ("跑步膝盖疼是怎么回事？怎么预防跑步膝？", "injury_prevention.md"),
    ("髂胫束综合征有什么症状和应对方法？", "injury_prevention.md"),
    ("跑步前和跑步后应该吃什么？", "nutrition.md"),
    ("长距离跑步途中如何补充电解质和能量？", "nutrition.md"),
    ("什么是负分割配速策略？比赛怎么分配配速？", "pace_strategy.md"),
    ("日常训练应该用什么样的配速？", "pace_strategy.md"),
    ("比赛前一周应该怎么减量？", "race_preparation.md"),
    ("马拉松比赛当天有什么注意事项？", "race_preparation.md"),
    ("跑完之后如何更快恢复？需要拉伸吗？", "recovery.md"),
    ("睡觉对跑步恢复有多重要？", "recovery.md"),
    ("什么是 80/20 训练法则？", "training_principles.md"),
    ("训练为什么要循序渐进、逐步增量？", "training_principles.md"),
    ("跑步姿势不正确会有什么问题？", "common_mistakes.md"),
    ("如何判断自己训练过度了？", "recovery.md"),
    ("半程马拉松备赛的周期化训练怎么安排？", "training_principles.md"),
    ("海拔和高温环境下比赛配速要不要调整？", "pace_strategy.md"),
]


def _build_rag():
    out = []
    for i, (q, src) in enumerate(_RAG_QUERIES, 1):
        out.append({
            "id": f"R{i:02d}",
            "query": q,
            "expected_source": src,
        })
    return out


# ============================================================
# 指标4：意图分类（30 条，覆盖 5 类）
# ============================================================

_INTENT_SAMPLES = {
    "chat": [
        "你好", "谢谢你的建议", "今天天气不错啊", "你是真人教练吗",
        "周末愉快", "哈哈这个建议有用",
    ],
    "assess": [
        "我最近练得怎么样", "我的体能有进步吗", "帮我看看我现在的训练状态",
        "我这周跑量多少", "我的心率数据正常吗", "我是不是练过度了",
    ],
    "advise": [
        "怎么提高配速", "膝盖疼怎么办", "跑前应该吃什么",
        "如何避免小腿抽筋", "下雨天怎么训练比较好", "我的跑步姿势要怎么改",
    ],
    "plan": [
        "帮我生成一个训练计划", "我想练半程马拉松", "给我制定一个减脂计划",
        "下个月有全马帮我排期", "给我安排一周的训练", "帮我调整一下当前计划",
    ],
    "track": [
        "今天的训练完成了吗", "这周我完成了几个训练", "我的计划完成率是多少",
        "昨天实际跑的和计划比怎么样", "我还差几次训练没做", "我的进度是不是落后了",
    ],
}


def _build_intent():
    out = []
    i = 0
    for intent, texts in _INTENT_SAMPLES.items():
        for t in texts:
            i += 1
            out.append({
                "id": f"I{i:02d}",
                "text": t,
                "expected_intent": intent,
            })
    return out


# ============================================================
# 写出
# ============================================================

def main():
    profiles = _build_profiles()
    rag = _build_rag()
    intent = _build_intent()

    p_path = os.path.join(_OUT_DIR, "metric1_profiles.json")
    r_path = os.path.join(_OUT_DIR, "metric3_rag.json")
    i_path = os.path.join(_OUT_DIR, "metric4_intent.json")

    with open(p_path, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)
    with open(r_path, "w", encoding="utf-8") as f:
        json.dump(rag, f, ensure_ascii=False, indent=2)
    with open(i_path, "w", encoding="utf-8") as f:
        json.dump(intent, f, ensure_ascii=False, indent=2)

    # 分布统计
    scn_count = {}
    for p in profiles:
        scn_count[p["scenario"]] = scn_count.get(p["scenario"], 0) + 1

    print(f"✓ 指标1/2 画像: {len(profiles)} 条 → {p_path}")
    print(f"    场景分布: {scn_count}")
    print(f"✓ 指标3 RAG:  {len(rag)} 题 → {r_path}")
    print(f"✓ 指标4 意图: {len(intent)} 条 → {i_path}")
    int_count = {}
    for it in intent:
        int_count[it["expected_intent"]] = int_count.get(it["expected_intent"], 0) + 1
    print(f"    意图分布: {int_count}")


if __name__ == "__main__":
    main()
