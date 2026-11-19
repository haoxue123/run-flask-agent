# -*- coding: utf-8 -*-
"""
最小 pytest smoke 测试（失败即阻断）
====================================
与现有「评测 harness」（offline_eval / skill_eval / eval_relation，产出报告）
不同，本文件是失败即阻断的回归测试，用于 CI / 本地快速验证：

  - 核心模块导入不报错（任何 import 崩溃都会让测试失败）
  - 确定性科学内核（physiology）配速互转 / 强度分区 / 减量超量恢复
  - 计划科学增强（plan_science）增量字段、不破坏原字段、安全护栏
  - 关系层意图路由（relation_layer）因果判定与结构化检索
  - LLM 计划文本 → 结构化 JSON 解析（llm_agent）
  - RAG 检索管线离线可用（默认嵌入，无需 torch）

运行：
    pytest tests/test_smoke.py -q
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import physiology as phy
import plan_science as ps
import relation_layer as rl
import llm_agent as la
import rag


# ============================================================
# 1) 核心模块导入体检（失败即阻断）
# ============================================================

def test_core_module_imports():
    mods = [
        "workflow", "memory", "skills", "relation_layer", "garmin_sync",
        "scheduler", "mem0_store", "data_tool", "training_science",
        "plan_science", "physiology", "rag", "llm_agent", "visualize",
        "routes.pages", "routes.data", "routes.plan", "routes.coach",
        "routes.eval", "routes.tokens",
    ]
    failed = []
    for m in mods:
        try:
            __import__(m)
        except Exception as e:  # noqa: BLE001 - 收集所有导入失败
            failed.append(f"{m}: {type(e).__name__}: {e}")
    assert not failed, "以下模块导入失败：\n" + "\n".join(failed)


# ============================================================
# 2) 确定性科学内核（physiology）
# ============================================================

def test_pace_roundtrip():
    assert phy.pace_str_to_sec("6:17") == 377
    assert phy.pace_str_to_sec("6'17\"") == 377
    assert phy.sec_to_pace_str(377) == "6'17\""


def test_target_pace_ordering():
    thr = 360  # 阈值配速 360 秒/km
    easy = phy.target_pace_for_type(thr, "轻松跑")
    interval = phy.target_pace_for_type(thr, "间歇跑")
    assert easy is not None and interval is not None
    assert interval < easy          # 间歇比轻松快 → 秒数更小
    assert phy.target_pace_for_type(thr, "休息") is None  # 非跑步类型返回 None


def test_banister_taper_recovery():
    loads = [50.0] * 20
    during = phy.banister_performance(19, loads, day_offsets=list(range(20)))
    taper = phy.banister_performance(25, loads, day_offsets=list(range(20)))
    assert taper > during           # 减量后疲劳先消退 → 表现回升


# ============================================================
# 3) 计划科学增强（plan_science）
# ============================================================

_MOCK_PLAN = [
    {"day": 1, "weekday": "一", "type": "轻松跑", "target_pace_sec": 390,
     "distance_km": 3, "duration_min": 20, "is_rest": False},
    {"day": 2, "weekday": "二", "type": "休息", "target_pace_sec": None,
     "distance_km": 0, "is_rest": True},
    {"day": 3, "weekday": "三", "type": "间歇跑", "target_pace_sec": 340,
     "distance_km": 4, "duration_min": 30, "is_rest": False},
]
_MOCK_PROFILE = {
    "近期跑步数据": [
        {"距离_km": 5, "平均配速_秒每公里": 400, "平均心率": 155, "最大心率": 175},
        {"距离_km": 4, "平均配速_秒每公里": 410, "平均心率": 160, "最大心率": 178},
    ],
    "健康指标": {"静息心率": 60},
    "体能指标": {"VO2max": None, "训练准备度": 70},
}


def test_enrich_plan_preserves_original_pace():
    plan = [dict(d) for d in _MOCK_PLAN]
    orig = {d["day"]: d["target_pace_sec"] for d in plan}
    summary = ps.enrich_plan(plan, _MOCK_PROFILE)
    for d in plan:
        assert d["target_pace_sec"] == orig[d["day"]], "原配速被意外修改"
    assert summary["threshold_pace_sec"] is not None
    for d in plan:
        if not d["is_rest"]:
            assert "corrected_target_pace_sec" in d


def test_build_constraints_nonempty():
    cons = ps.build_constraints(_MOCK_PROFILE, plan_duration=7)
    assert cons["constraint_text"]
    assert cons["suggested_paces"]
    assert cons["safety_redlines"]


def test_safety_rules_detects_violations():
    viol = [
        {"day": 1, "type": "间歇跑", "target_pace_sec": 340, "distance_km": 4,
         "duration_min": 30, "is_rest": False},
        {"day": 2, "type": "冲刺跑", "target_pace_sec": 330, "distance_km": 3,
         "duration_min": 20, "is_rest": False},
        {"day": 3, "type": "节奏跑", "target_pace_sec": 350, "distance_km": 5,
         "duration_min": 30, "is_rest": False},
        {"day": 4, "type": "轻松跑", "target_pace_sec": 400, "distance_km": 4,
         "duration_min": 30, "is_rest": False},
    ]
    flags = ps.check_safety_rules(viol, _MOCK_PROFILE)
    rules = [f["rule"] for f in flags]
    assert "连续高强度" in rules
    assert "无休息日" in rules


# ============================================================
# 4) 关系层意图路由（relation_layer）
# ============================================================

def test_classify_intent_relation():
    assert rl.classify_intent_relation("为什么跑步会膝盖疼？") is True
    # 注意：关键词「怎么」较宽，含它的句子也会判为因果（词法局限）；
    # 这里用完全不含因果词的句子验证负面分支。
    assert rl.classify_intent_relation("推荐一双缓震跑鞋") is False
    assert rl.classify_intent_relation("") is False


def test_retrieve_relations_causal_hit(tmp_path, monkeypatch):
    """关系检索：自带三元组夹具，不依赖 data/relations.json（非版本化数据）。"""
    import json as _json
    relations = {"triples": [{
        "subject": "跑步膝",
        "aliases": ["膝盖疼", "膝痛"],
        "predicate": "成因",
        "object": "髌股关节压力过大",
        "note": "跑量增长过快或股四头肌薄弱会增加髌股关节负荷",
    }]}
    rp = tmp_path / "relations.json"
    rp.write_text(_json.dumps(relations, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(rl, "RELATIONS_PATH", str(rp))
    monkeypatch.setattr(rl, "_triples", None)
    monkeypatch.setattr(rl, "_index", None)

    assert rl.get_triple_count() > 0
    # 因果且含实体别名「膝盖疼」→ 应返回非空结构化推理辅助文本
    ctx = rl.retrieve_relations("为什么跑步会膝盖疼？")
    assert ctx
    # 非因果 → 必定返回空串（不注入，行为不变）
    assert rl.retrieve_relations("推荐一双跑鞋") == ""


# ============================================================
# 5) LLM 计划文本 → 结构化 JSON 解析（llm_agent）
# ============================================================

_SAMPLE_PLAN = """第1天（周一）| 轻松跑
目标配速：6'17"
距离：5公里
预计时长：30分钟
心率区间：Zone2
说明：轻松慢跑
第2天（周二）| 休息
"""


def test_parse_plan_to_structured():
    res = la._parse_plan_to_structured(_SAMPLE_PLAN)
    assert len(res) == 2

    d1 = res[0]
    assert d1["day"] == 1
    assert d1["type"] == "轻松跑"
    assert d1["target_pace_sec"] == 377
    assert d1["distance_km"] == 5
    assert d1["duration_min"] == 30
    assert d1["heart_rate_zone"] == "Zone2"
    assert d1["is_rest"] is False

    d2 = res[1]
    assert d2["is_rest"] is True
    assert d2["target_pace_sec"] is None


# ============================================================
# 6) RAG 检索管线离线可用（默认嵌入，无需 torch）
# ============================================================

def test_rag_retrieve_returns_list():
    rag.reset_index()
    out = rag.retrieve_knowledge("间歇跑配速如何确定", top_k=3)
    assert isinstance(out, list)
