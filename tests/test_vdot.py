"""P3 测试：Daniels VDOT 计算内核
覆盖：比赛→VDOT、VDOT→配速表、反解时间、画像注入、训练类型映射。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import vdot
import physiology


def test_vdot_from_race_known_value():
    """10km/40min → VDOT 应在 50~54（与 Daniels 表一致）。"""
    v = vdot.vdot_from_race(10.0, 40 * 60)
    assert v is not None
    assert 50 < v < 54, f"VDOT 偏离预期: {v}"


def test_vdot_invalid_inputs():
    assert vdot.vdot_from_race(0.5, 100) is None        # 距离<1km
    assert vdot.vdot_from_race(10.0, 0) is None          # 时间无效
    assert vdot.vdot_from_race(None, 100) is None


def test_training_paces_ordering():
    """分区顺序：I 快于 T 快于 M 快于 E 慢端。"""
    paces = vdot.vdot_training_paces(52.0, 10.0)
    assert paces["I"] < paces["T"] < paces["M"] < paces["E"][0]
    # E 为 [慢, 快] 区间
    assert paces["E"][0] > paces["E"][1]


def test_training_paces_match_daniels_table():
    """VDOT→配速应与项目知识库 Daniels 查表一致（消除"测试绿但错"）。"""
    # 表节点处应精确命中
    for v, (e, m, t, i) in vdot._VDOT_PACE_TABLE_SEC_PER_KM.items():
        paces = vdot.vdot_training_paces(float(v), 10.0)
        assert abs(paces["M"] - m) < 2, f"VDOT={vdot} M 偏离: {paces['M']} vs {m}"
        assert abs(paces["T"] - t) < 2, f"VDOT={vdot} T 偏离: {paces['T']} vs {t}"
        assert abs(paces["I"] - i) < 2, f"VDOT={vdot} I 偏离: {paces['I']} vs {i}"
        # E 区间应包裹表中单车 E 配速
        assert paces["E"][0] >= e >= paces["E"][1], f"VDOT={vdot} E 区间未包裹 {e}"


def test_vdot_50_paces():
    """VDOT=50 直接对照 Daniels 简化表（5:05 / 4:20 / 3:50 / 3:25）。"""
    paces = vdot.vdot_training_paces(50.0, 10.0)
    assert abs(paces["M"] - 260) < 1
    assert abs(paces["T"] - 230) < 1
    assert abs(paces["I"] - 205) < 1
    assert paces["E"][1] <= 305 <= paces["E"][0]  # 包裹 5:05



def test_race_time_inverse():
    """VDOT→时间 反解应回到原比赛时间附近。"""
    v = vdot.vdot_from_race(10.0, 40 * 60)
    t = vdot.race_time_sec_from_vdot(v, 10.0)
    assert abs(t - 2400) < 30, f"反解偏差过大: {t}"


def test_vdot_from_recent_race_dict():
    race = {"距离_km": 5.0, "时间_秒": 20 * 60}
    v = vdot.vdot_from_recent_race(race)
    assert v is not None and 45 < v < 55
    # 字段兼容英文键
    race_en = {"distance_km": 5.0, "time_sec": 20 * 60}
    assert vdot.vdot_from_recent_race(race_en) == v


def test_zone_and_target_pace_for_type():
    """训练类型→分区→配速映射正确。"""
    assert physiology.zone_for_workout_type("轻松跑") == "E"
    assert physiology.zone_for_workout_type("间歇跑") == "I"
    assert physiology.zone_for_workout_type("马拉松配速") == "M"
    assert physiology.zone_for_workout_type("节奏跑") == "T"
    assert physiology.zone_for_workout_type("冲刺跑") == "R"
    assert physiology.zone_for_workout_type("瑜伽") is None

    pace = physiology.target_pace_for_type_vdot(52.0, "间歇跑", 10.0)
    assert pace is not None and 180 < pace < 210  # ~3:17/km (VDOT52 I 配速)
    # 无 VDOT 应安全返回 None
    assert physiology.target_pace_for_type_vdot(None, "间歇跑") is None


def test_vdot_block_from_profile():
    profile = {"近期比赛成绩": {"距离_km": 10.0, "时间_秒": 40 * 60}}
    block = physiology.vdot_block_from_profile(profile)
    assert block and "VDOT" in block
    # 无比赛成绩返回 None
    assert physiology.vdot_block_from_profile({"近期比赛成绩": {}}) is None
