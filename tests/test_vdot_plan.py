"""VDOT 配速锚点接入计划生成的单元测试。

验证点：
  1. 画像含近期比赛成绩时，build_vdot_anchor 返回含 VDOT 与分区映射的锚点文本；
  2. 无比赛成绩时返回空串（不阻断计划生成）；
  3. 异常输入（格式错/缺字段）安全返回空串；
  4. 异常安全：即使 vdot 模块导入失败也不抛错。
"""
import sys
import types

import pytest

import llm_agent


def _profile_with_race(distance_km=10.0, time_sec=2400):
    return {
        "近期比赛成绩": {"距离_km": distance_km, "时间_秒": time_sec},
        "目标": {},
        "近期跑步数据": [],
        "健康指标": {},
        "体能指标": {},
    }


def test_anchor_with_race_contains_vdot_and_zones():
    anchor = llm_agent.build_vdot_anchor(_profile_with_race())
    assert "VDOT" in anchor
    # 五个强度区映射都应出现
    for zone in ("I 区", "T 区", "M 区", "E 区"):
        assert zone in anchor, f"锚点缺少分区映射：{zone}"


def test_anchor_without_race_is_empty():
    anchor = llm_agent.build_vdot_anchor({"近期比赛成绩": None})
    assert anchor == ""
    # 完全没有该字段也应返回空串
    assert llm_agent.build_vdot_anchor({}) == ""


def test_anchor_malformed_race_is_safe():
    # 缺时间字段
    assert llm_agent.build_vdot_anchor({"近期比赛成绩": {"距离_km": 10.0}}) == ""
    # 字段类型错误
    assert llm_agent.build_vdot_anchor({"近期比赛成绩": {"距离_km": "x", "时间_秒": "y"}}) == ""


def test_anchor_import_failure_is_safe(monkeypatch):
    # 模拟 vdot 模块导入失败，确认不抛错且返回空串
    real_import = __builtins__.__import__ if isinstance(__builtins__, types.ModuleType) else __builtins__["__import__"]

    def fake_import(name, *a, **k):
        if name == "vdot" or name.startswith("vdot."):
            raise ImportError("boom")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", fake_import)
    try:
        assert llm_agent.build_vdot_anchor(_profile_with_race()) == ""
    finally:
        monkeypatch.undo()


def test_vdot_value_matches_daniels():
    # 集成校验：10km/40min 应推导 VDOT≈52（与 Daniels 表吻合，事实非推测）
    from vdot import vdot_from_profile
    v = vdot_from_profile(_profile_with_race())
    assert v is not None and 50 < v < 54
