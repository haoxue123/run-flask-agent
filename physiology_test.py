"""
生理学核心模块单元测试（标准库，无需 pytest）
============================================================
运行：python physiology_test.py
覆盖：配速互转、TRIMP、EWMA/CTL、Banister 形态、VO2max、阈值与分区。
"""

import math
import physiology as phy


def _ok(name):
    print(f"  ✓ {name}")


def test_pace_roundtrip():
    assert phy.pace_str_to_sec("6:17") == 377
    assert phy.pace_str_to_sec("6'17\"") == 377
    assert phy.pace_str_to_sec("6:17\"/km") == 377
    assert phy.pace_str_to_sec(377) == 377
    assert phy.pace_str_to_sec(None) is None
    assert phy.pace_str_to_sec("垃圾") is None
    assert phy.sec_to_pace_str(377) == "6'17\""
    assert phy.sec_to_pace_str(None) is None
    _ok("配速互转与异常输入")


def test_banister_trimp():
    # HRr=0.8, 60min
    tr = phy.banister_trimp(60, 160, 60, 185)
    assert tr is not None and tr > 0
    # 心率缺失应返回 None
    assert phy.banister_trimp(60, None, 60, 185) is None
    # 静息==最大（分母为0）应返回 None
    assert phy.banister_trimp(60, 100, 100, 100) is None
    # 更短时长应产生更小 TRIMP
    tr_short = phy.banister_trimp(30, 160, 60, 185)
    assert tr_short < tr
    _ok("Banister TRIMP 与边界")


def test_trimp_from_duration():
    assert phy.trimp_from_duration(60, 0.5) == 30.0
    assert phy.trimp_from_duration(0, 0.5) == 0.0
    assert phy.trimp_from_duration(60, 2.0) == 60.0  # 裁剪到 1.0
    _ok("无心率 TRIMP 近似")


def test_intensity_factor():
    assert phy.intensity_factor_for_type("休息") == 0.0
    assert phy.intensity_factor_for_type("力量训练") == 0.0
    assert 0.0 < phy.intensity_factor_for_type("间歇跑") <= 1.0
    assert phy.intensity_factor_for_type("未知类型X") == 0.5
    _ok("训练类型→强度因子")


def test_ewma_ctl_atl():
    # 稳态：长期恒定负荷，CTL→接近负荷，ATL 更快接近
    traj = phy.compute_load_trajectory([100.0] * 100)
    last = traj[-1]
    assert 90 < last["ctl"] < 110
    assert abs(last["atl"] - 100.0) < 1.0
    assert last["ctl"] > 0 and last["atl"] > 0
    # 初期 TSB 应为负（疲劳>体能）
    assert traj[3]["tsb"] < 0
    _ok("EWMA CTL/ATL 收敛与形态")


def test_load_ramp_recovery():
    # 先高负荷后停训：CTL 缓降，ATL 快降，TSB 由负转正（恢复）
    loads = [80.0] * 20 + [0.0] * 14
    traj = phy.compute_load_trajectory(loads)
    assert traj[19]["tsb"] < 0          # 训练中疲劳占优
    assert traj[-1]["tsb"] > traj[19]["tsb"]  # 停训后状态回升
    assert traj[-1]["ctl"] < traj[19]["ctl"]   # 体能缓降
    _ok("负荷爬坡→恢复 的 TSB 轨迹")


def test_banister_supercompensation():
    # 减量(taper)：停训后短期表现高于训练中（疲劳先消退）
    loads = [60.0] * 20
    perf_during = phy.banister_performance(19, loads, day_offsets=list(range(20)))
    perf_taper = phy.banister_performance(26, loads, day_offsets=list(range(20)))
    assert perf_taper > perf_during
    # 单调性：随训练进行表现先升（体能累积）
    perf_5 = phy.banister_performance(5, loads, day_offsets=list(range(20)))
    perf_15 = phy.banister_performance(15, loads, day_offsets=list(range(20)))
    assert perf_15 > perf_5
    _ok("Banister 超量恢复形态")


def test_vo2max_hr():
    vo2 = phy.estimate_vo2max_from_hr(60, 190)
    assert vo2 is not None and 40 < vo2 < 70
    assert phy.estimate_vo2max_from_hr(None, 190) is None
    assert phy.estimate_vo2max_from_hr(60, None) is None
    _ok("VO2max 心率法与边界")


def test_threshold_and_zones():
    thr, method = phy.estimate_threshold_pace([400, 410, 420, 430], [5, 4, 3, 2])
    assert thr is not None and "≥4km" in method
    easy = phy.target_pace_for_type(thr, "轻松跑")
    tempo = phy.target_pace_for_type(thr, "节奏跑")
    interval = phy.target_pace_for_type(thr, "间歇跑")
    rest_run = phy.target_pace_for_type(thr, "恢复跑")
    # 配速顺序：间歇 < 节奏 < 轻松 < 恢复（秒数越小越快）
    assert interval < tempo < easy < rest_run
    # 非跑步类型返回 None
    assert phy.target_pace_for_type(thr, "休息") is None
    assert phy.target_pace_for_type(thr, "力量训练") is None
    # 数据不足
    none_thr, reason = phy.estimate_threshold_pace([], [])
    assert none_thr is None and reason
    _ok("阈值配速估计与 Daniels 分区顺序")


def main():
    print("=" * 50)
    print("生理学核心单元测试")
    print("=" * 50)
    test_pace_roundtrip()
    test_banister_trimp()
    test_trimp_from_duration()
    test_intensity_factor()
    test_ewma_ctl_atl()
    test_load_ramp_recovery()
    test_banister_supercompensation()
    test_vo2max_hr()
    test_threshold_and_zones()
    print("\n✅ 全部测试通过")


if __name__ == "__main__":
    main()
