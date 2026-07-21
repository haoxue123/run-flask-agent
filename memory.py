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
import json
import tempfile
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
        return dict(_DEFAULT_PROFILE)  # 深拷贝默认结构

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULT_PROFILE)

    # 合并默认结构，补全缺失的字段
    merged = dict(_DEFAULT_PROFILE)
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

    参数：
        message: 用户消息
        reply:   AI 回答
        path:    文件路径，默认 data/user_profile.json
    """
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