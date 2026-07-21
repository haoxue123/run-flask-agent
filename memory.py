"""
记忆管理模块
============

为教练对话提供短期记忆（Flask session）和长期记忆（data/user_profile.json）的管理。

短期记忆：
    - workflow_state: 当前状态机状态
    - active_plan_id: 当前活跃的训练计划标识
    - last_topic: 上一个话题摘要

长期记忆：
    - goals: 训练目标（主要目标、目标比赛、目标时间）
    - preferences: 用户偏好（可用训练日、偏好的训练时间、备注）
    - history: 用户历史（伤病史、个人最好成绩、训练背景）
    - updated_at: 最后更新时间
"""

import os
import re
import json
import tempfile
import copy
from datetime import datetime


# ============================================================
# 路径常量
# ============================================================

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_MEMORY_PATH = os.path.join(_PROJECT_DIR, "data", "user_profile.json")


# ============================================================
# 默认结构
# ============================================================

_DEFAULT_PROFILE = {
    "goals": {
        "primary": "",
        "target_race": "",
        "target_time": "",
    },
    "preferences": {
        "preferred_days": [],
        "preferred_time": "",
        "notes": "",
    },
    "history": {
        "injuries": [],
        "prs": {},
        "training_background": "",
    },
    "updated_at": "",
}


# ============================================================
# 长期记忆
# ============================================================

def load_long_term_memory(path=None):
    """
    读取长期记忆文件。

    参数：
        path: 文件路径，默认 data/user_profile.json

    返回：
        dict，用户画像字典。文件不存在时返回默认结构。
    """
    if path is None:
        path = _DEFAULT_MEMORY_PATH

    if not os.path.isfile(path):
        return copy.deepcopy(_DEFAULT_PROFILE)  # 深拷贝默认结构

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return copy.deepcopy(_DEFAULT_PROFILE)

    # 合并默认结构，补全缺失的字段
    merged = copy.deepcopy(_DEFAULT_PROFILE)
    for section in ("goals", "preferences", "history"):
        if section in data and isinstance(data[section], dict):
            merged[section].update(data[section])
    if "updated_at" in data:
        merged["updated_at"] = data["updated_at"]

    return merged


def save_long_term_memory(profile, path=None):
    """
    原子写入长期记忆文件。

    参数：
        profile: 用户画像字典
        path:    文件路径，默认 data/user_profile.json
    """
    if path is None:
        path = _DEFAULT_MEMORY_PATH

    data_dir = os.path.dirname(path)
    os.makedirs(data_dir, exist_ok=True)

    profile["updated_at"] = datetime.now().isoformat()

    fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=data_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.isfile(tmp_path):
            os.unlink(tmp_path)
        raise


def format_memory_context(profile):
    """
    将长期记忆格式化为可注入 LLM 的 prompt 上下文。

    参数：
        profile: load_long_term_memory() 的返回值

    返回：
        str，格式化的用户画像文本
    """
    if not profile:
        return ""

    goals = profile.get("goals", {})
    prefs = profile.get("preferences", {})
    history = profile.get("history", {})

    lines = ["【用户画像】"]

    # 目标
    if any(goals.values()):
        lines.append("训练目标：")
        if goals.get("primary"):
            lines.append(f"  - 主要目标：{goals['primary']}")
        if goals.get("target_race"):
            lines.append(f"  - 目标比赛：{goals['target_race']}")
        if goals.get("target_time"):
            lines.append(f"  - 目标时间：{goals['target_time']}")

    # 偏好
    if prefs.get("preferred_days"):
        lines.append(f"可用训练日：{'、'.join(prefs['preferred_days'])}")
    if prefs.get("preferred_time"):
        lines.append(f"偏好的训练时间：{prefs['preferred_time']}")
    if prefs.get("notes"):
        lines.append(f"备注：{prefs['notes']}")

    # 伤病史
    injuries = history.get("injuries", [])
    if injuries:
        lines.append(f"伤病史：{'、'.join(injuries)}")

    # 个人记录
    prs = history.get("prs", {})
    if prs:
        pr_parts = [f"{k}: {v}" for k, v in prs.items()]
        lines.append(f"个人最好成绩：{'、'.join(pr_parts)}")

    # 训练背景
    if history.get("training_background"):
        lines.append(f"训练背景：{history['training_background']}")

    if len(lines) == 1:
        return ""  # 无有效信息

    return "\n".join(lines)


def extract_memory_updates(message, reply):
    """
    从对话中提取可更新的记忆字段。

    使用简单的关键词匹配（非 LLM 调用，避免额外 API 开销）。
    返回需要更新的字段字典，空字典表示无需更新。

    参数：
        message: 用户消息
        reply:   AI 回答

    返回：
        dict，需更新的字段（如 {"goals": {"primary": "全马完赛"}}）
    """
    updates = {}
    combined = message + " " + reply

    # 目标检测
    if "全马" in message or "马拉松" in message:
        updates.setdefault("goals", {})["primary"] = "马拉松完赛"
    if "半马" in message or "半程" in message:
        updates.setdefault("goals", {})["primary"] = "半马完赛"
    if "10公里" in message or "10k" in message.lower() or "10K" in message:
        updates.setdefault("goals", {})["primary"] = "10K 完赛"

    # 目标比赛检测
    race_keywords = ["比赛", "北马", "上马", "广马", "赛事", "目标赛事"]
    if any(kw in message for kw in race_keywords):
        updates.setdefault("goals", {})["target_race"] = "有比赛计划"

    # 可用训练日检测
    day_keywords = {
        "周一": "周一", "周二": "周二", "星期二": "周二",
        "周三": "周三", "周四": "周四", "星期四": "周四",
        "周五": "周五", "周六": "周六", "星期六": "周六",
        "周日": "周日", "星期天": "周日",
    }
    days_found = []
    for kw, day in day_keywords.items():
        if kw in message:
            days_found.append(day)
    if days_found:
        updates.setdefault("preferences", {})["preferred_days"] = list(set(days_found))

    # 伤病史检测（存储清洗后的可读标签，如"膝盖"而非原始子串"膝"）
    injury_keywords = ["受伤", "膝", "膝盖", "踝", "脚踝", "足底", "筋膜炎",
                       "胫骨", "ITBS", "髂胫束", "跟腱", "腰", "髋"]
    injury_labels = {
        "受伤": "有伤病史", "膝": "膝盖", "膝盖": "膝盖", "踝": "脚踝", "脚踝": "脚踝",
        "足底": "足底筋膜炎", "筋膜炎": "足底筋膜炎", "胫骨": "胫骨",
        "ITBS": "髂胫束综合征", "髂胫束": "髂胫束综合征", "跟腱": "跟腱",
        "腰": "腰部", "髋": "髋部",
    }
    for kw in injury_keywords:
        if kw in message or kw in reply:
            label = injury_labels.get(kw, kw)
            updates.setdefault("history", {})["injuries"] = [label]
            break

    return updates if updates else {}


def update_long_term_memory(message, reply, path=None):
    """
    从对话中提取信息并更新长期记忆。
    合并已有记忆，只更新被提取到的字段。

    第一性原理：记忆写入前先经过「否定意图层」。
      - REVoke_ALL：清空全部记忆。
      - REVoke_LAST：不存储本轮（"别管这个 / 忘掉刚才说的"）。
      - NEGATE_FACT：主动移除被否定的事实字段（"我膝盖不疼"不再记为伤病史）。
      - 否则：走原有正向提取；若消息含否定词则整体抑制，避免矛盾写入。

    参数：
        message: 用户消息
        reply:   AI 回答
        path:    文件路径，默认 data/user_profile.json
    """
    intent = detect_negation_intent(message)
    if intent["type"] == "REVoke_ALL":
        revoke_memory("all", path)
        return
    if intent["type"] == "REVoke_LAST":
        return  # 不存储本轮
    if intent["type"] == "NEGATE_FACT":
        profile = load_long_term_memory(path)
        fld = intent.get("field")
        if fld == "history":
            # 外科手术式：仅移除被否定词修饰的标签，同句断言的其他标签保留
            _negate_field_labels(profile, "history", "injuries", message,
                                 _INJURY_KW, _INJURY_LABELS)
        elif fld == "goals":
            profile["goals"] = dict(_DEFAULT_PROFILE["goals"])
        elif fld == "preferences":
            profile["preferences"] = dict(_DEFAULT_PROFILE["preferences"])
        save_long_term_memory(profile, path)
        return

    # 含否定词时抑制正向提取，避免"我膝盖不疼"被误记为伤病史
    if any(tok in (message + " " + (reply or "")) for tok in _NEG_TOKENS):
        return

    updates = extract_memory_updates(message, reply)
    if not updates:
        return

    profile = load_long_term_memory(path)

    # 深度合并更新
    for section, fields in updates.items():
        if section in profile and isinstance(profile[section], dict):
            for key, value in fields.items():
                existing = profile[section].get(key)
                # 列表字段（如 injuries、preferred_days）追加而非覆盖
                if isinstance(existing, list) and isinstance(value, list):
                    for item in value:
                        if item not in existing:
                            existing.append(item)
                else:
                    profile[section][key] = value

    save_long_term_memory(profile, path)


# ============================================================
# 否定意图识别层（记忆撤销 / 不存储）
# 第一性原理：写入记忆前必须先判断"用户是否在撤回/否定"，而非无条件关键词存储。
# ============================================================

# 显式撤销指令：用户要求"忘掉 / 别记 / 不存"等
_REVOKE_TOKENS = ["忘掉", "忘了", "忘记", "忘", "别记", "别存", "别保", "别管",
                  "不要记", "不要存", "不要保", "不用记", "不用存", "忽略",
                  "删掉", "删除", "清除", "清空", "撤销", "抹掉"]
# 否定词：出现在事实词附近表示"不是 / 没有该事实"
_NEG_TOKENS = ["不", "没", "没有", "不再", "不想", "不是", "未", "勿", "别"]
# 事实关键词族（与 extract_memory_updates 对应）
_GOAL_KW = ["全马", "马拉松", "半马", "半程", "10公里", "10k", "10K"]
_INJURY_KW = ["受伤", "膝", "膝盖", "踝", "脚踝", "足底", "筋膜炎", "胫骨",
             "ITBS", "髂胫束", "跟腱", "腰", "髋"]
_PREF_DAY_KW = ["周一", "周二", "星期二", "周三", "周四", "星期四", "周五",
               "周六", "星期六", "周日", "星期天"]

# 伤病史关键词 → 可读标签（与 extract_memory_updates 内 injury_labels 对齐）
_INJURY_LABELS = {
    "受伤": "有伤病史", "膝": "膝盖", "膝盖": "膝盖", "踝": "脚踝", "脚踝": "脚踝",
    "足底": "足底筋膜炎", "筋膜炎": "足底筋膜炎", "胫骨": "胫骨",
    "ITBS": "髂胫束综合征", "髂胫束": "髂胫束综合征", "跟腱": "跟腱",
    "腰": "腰部", "髋": "髋部",
}


def detect_negation_intent(message):
    """识别否定 / 撤销意图，返回分类结果。

    返回：
        {"type": "REVoke_ALL" | "REVoke_LAST" | "NEGATE_FACT" | "NONE",
         "field": 可选，被否定的事实字段}

    判定层级（先识别"是否要撤回"，再识别"撤回 / 否定哪个事实"）：
        1) 含显式撤销词 -> 撤销。带"全部/所有/一切/记忆"或纯"忘掉"为 REVoke_ALL；
           带"刚才/之前/这个/它"或"别管这个"为 REVoke_LAST（不存储本轮）。
        2) 含事实关键词 且 同句含否定词 -> NEGATE_FACT（撤销该事实，并从记忆移除）。
        3) 否则 NONE。
    """
    msg = (message or "").strip()
    if not msg:
        return {"type": "NONE"}

    if any(tok in msg for tok in _REVOKE_TOKENS):
        if any(w in msg for w in ["全部", "所有", "一切", "记忆", "我的记忆", "记录"]):
            return {"type": "REVoke_ALL"}
        # 默认按"刚才说的 / 这句"处理：不存储本轮（LAST）
        return {"type": "REVoke_LAST"}

    neg = any(tok in msg for tok in _NEG_TOKENS)
    if neg:
        if any(k in msg for k in _GOAL_KW):
            return {"type": "NEGATE_FACT", "field": "goals"}
        if any(k in msg for k in _INJURY_KW):
            return {"type": "NEGATE_FACT", "field": "history"}
        if any(k in msg for k in _PREF_DAY_KW):
            return {"type": "NEGATE_FACT", "field": "preferences"}
    return {"type": "NONE"}


def revoke_memory(scope="all", path=None):
    """撤销记忆：scope='all' 清空为默认结构；'last' 由调用方决定（此处等同不存储）。"""
    if path is None:
        path = _DEFAULT_MEMORY_PATH
    if scope == "all":
        save_long_term_memory(dict(_DEFAULT_PROFILE), path)
    return True


def _negate_field_labels(profile, section, key, message, keywords, label_map):
    """在被否定事实的字段中做外科手术式更新：
    仅移除"被否定词修饰"的关键词标签；同句里未被否定的关键词仍视为有效
    断言并保留/补入。避免 '我膝盖不疼了，但我脚踝受伤了' 把合法断言一起抹掉。
    """
    cur = (profile.setdefault(section, {}).get(key) or [])
    clauses = re.split(r"[，。、；！？,.;!?]", message)
    negated, asserted = set(), set()
    for kw in keywords:
        if kw not in message:
            continue
        label = label_map.get(kw, kw)
        in_neg_clause = any(
            kw in c and any(t in c for t in _NEG_TOKENS)
            for c in clauses
        )
        (negated if in_neg_clause else asserted).add(label)
    new = [x for x in cur if x not in negated]
    for a in asserted:
        if a not in new:
            new.append(a)
    profile[section][key] = new


# ============================================================
# 用户画像结构化摘要（标签 + 置信度 + 来源片段，支持增量更新）
# ============================================================
_DEFAULT_SUMMARY_PATH = os.path.join(_PROJECT_DIR, "data", "profile_summary.json")

# 摘要候选提取：目标 / 伤病史 / 偏好 / 立场
_SUM_GOAL = [("全马", "目标·全马完赛"), ("马拉松", "目标·全马完赛"),
             ("半马", "目标·半马完赛"), ("半程", "目标·半马完赛"),
             ("10公里", "目标·10K完赛"), ("10k", "目标·10K完赛"), ("10K", "目标·10K完赛")]
_SUM_INJURY = {"膝": "伤病史·膝盖", "膝盖": "伤病史·膝盖", "踝": "伤病史·脚踝",
               "脚踝": "伤病史·脚踝", "足底": "伤病史·足底筋膜炎", "筋膜炎": "伤病史·足底筋膜炎",
               "胫骨": "伤病史·胫骨", "跟腱": "伤病史·跟腱", "腰": "伤病史·腰部", "髋": "伤病史·髋部"}
_SUM_STANCE_RE = re.compile(r"(我(觉得|认为|更喜欢|倾向于|想|希望|打算|决定)|我个人|我的看法)")


def _extract_summary_candidates(messages):
    """从消息列表提取摘要候选：(标签, 来源片段)。"""
    cands = []
    for m in messages:
        text = (m.get("content") or "") if isinstance(m, dict) else str(m)
        if not text:
            continue
        low = text.lower()
        for kw, tag in _SUM_GOAL:
            if kw.lower() in low:
                cands.append((tag, text[:40]))
                break
        for kw, tag in _SUM_INJURY.items():
            if kw in text:
                cands.append((tag, text[:40]))
                break
        for day in ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]:
            if day in text:
                cands.append(("偏好·训练日·" + day, text[:40]))
                break
        if _SUM_STANCE_RE.search(text):
            cands.append(("立场·明确表达", text[:40]))
    return cands


def generate_profile_summary(history):
    """基于完整对话历史生成结构化摘要（标签 + 置信度 + 计数 + 来源片段）。"""
    cands = _extract_summary_candidates(history)
    agg = {}
    for tag, src in cands:
        a = agg.setdefault(tag, {"count": 0, "sources": []})
        a["count"] += 1
        if len(a["sources"]) < 3 and src not in a["sources"]:
            a["sources"].append(src)
    tags = []
    for tag, info in agg.items():
        conf = round(min(1.0, 0.5 + 0.1 * info["count"]), 2)
        tags.append({"tag": tag, "confidence": conf, "count": info["count"], "sources": info["sources"]})
    tags.sort(key=lambda x: (-x["count"], x["tag"]))
    return {"updated_at": datetime.now().isoformat(), "tags": tags}


def load_profile_summary(path=None):
    if path is None:
        path = _DEFAULT_SUMMARY_PATH
    if not os.path.isfile(path):
        return {"updated_at": "", "tags": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"updated_at": "", "tags": []}


def save_profile_summary(summary, path=None):
    if path is None:
        path = _DEFAULT_SUMMARY_PATH
    data_dir = os.path.dirname(path)
    os.makedirs(data_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=data_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.isfile(tmp):
            os.unlink(tmp)
        raise


def update_profile_summary_from_history(history, path=None):
    """随新对话增量更新：基于（已截断的）完整历史重新生成并落盘。

    说明：历史本身随对话增长（coach.py 截断为最近 N 轮），摘要随之演进，
    即实现"随新对话增量更新"。如需纯 delta 合并可调用 merge_profile_summary。
    """
    summary = generate_profile_summary(history)
    save_profile_summary(summary, path)
    return summary


def merge_profile_summary(prev, new):
    """纯增量合并：将 new 的标签并入 prev（按 tag 累加计数、合并来源）。"""
    merged = {t["tag"]: dict(t) for t in prev.get("tags", [])}
    for t in new.get("tags", []):
        if t["tag"] in merged:
            m = merged[t["tag"]]
            m["count"] = m.get("count", 0) + t.get("count", 0)
            src = set(m.get("sources", []))
            for s in t.get("sources", []):
                if len(src) < 3:
                    src.add(s)
            m["sources"] = list(src)
            m["confidence"] = round(min(1.0, 0.5 + 0.1 * m["count"]), 2)
        else:
            merged[t["tag"]] = dict(t)
    tags = sorted(merged.values(), key=lambda x: (-x["count"], x["tag"]))
    return {"updated_at": new.get("updated_at", ""), "tags": tags}


# ============================================================
# 模块自测
# ============================================================

if __name__ == "__main__":
    print("=" * 50)
    print("记忆管理模块自测")
    print("=" * 50)

    # 测试 1：加载默认
    print("\n--- 默认结构 ---")
    mem = load_long_term_memory()
    print(json.dumps(mem, ensure_ascii=False, indent=2))

    # 测试 2：格式化上下文
    print("\n--- 格式化上下文 ---")
    ctx = format_memory_context(mem)
    print(ctx if ctx else "(无有效信息)")

    # 测试 3：提取记忆更新
    print("\n--- 提取记忆更新 ---")
    test_msgs = [
        ("我想跑全马", "好的，马拉松训练需要..."),
        ("我膝盖有点疼", "建议你先休息，冰敷膝盖..."),
        ("我周一周三周五可以跑", "好的，我会安排周一三五训练..."),
    ]
    for msg, reply in test_msgs:
        updates = extract_memory_updates(msg, reply)
        print(f"  消息: '{msg}' → 更新: {updates}")

    # 测试 4：保存和加载
    print("\n--- 保存和加载 ---")
    test_path = os.path.join(_PROJECT_DIR, "data", "user_profile_test.json")
    test_profile = dict(_DEFAULT_PROFILE)
    test_profile["goals"]["primary"] = "测试目标"
    save_long_term_memory(test_profile, test_path)
    loaded = load_long_term_memory(test_path)
    print(f"  保存后加载: goals.primary = {loaded['goals']['primary']}")
    os.remove(test_path)  # 清理