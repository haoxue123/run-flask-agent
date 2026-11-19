"""周复盘闭环引擎测试 — 验证确定性规则（不依赖 LLM / 网络）。"""
import json
from datetime import datetime, timedelta

import weekly_review as wr


def _plan_with(volume_per_run=10.0, runs=4):
    return [
        {"day": i + 1, "type": "轻松跑", "distance_km": volume_per_run,
         "is_rest": False, "actual_distance_km": None}
        for i in range(runs)
    ]


def _recent_date(days_ago=1):
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_compliance_full():
    plan = _plan_with(10.0, 4)  # 计划 40km
    actuals = [{"source": "upload", "date": _recent_date(), "distance_km": 40.0,
                "duration_min": 30, "hr": 150}]
    comp = wr.compute_compliance(plan, actuals)
    assert comp["planned_volume_km"] == 40.0
    assert comp["actual_volume_km"] == 40.0
    assert comp["compliance_pct"] == 100


def test_compliance_low_triggers_volume_cut():
    plan = _plan_with(10.0, 4)  # 计划 40km
    actuals = [{"source": "upload", "date": _recent_date(), "distance_km": 10.0,
                "duration_min": 30, "hr": 150}]
    comp = wr.compute_compliance(plan, actuals)
    assert comp["compliance_pct"] == 25
    adj = wr.derive_adjustments(comp, {"acwr": 1.0, "acwr_risk": {}, "has_hr_data": True})
    assert adj["actions"]["volume"] == "减量"


def test_acwr_high_risk_forces_cut():
    load = {"acwr": 1.7, "acwr_risk": {"level": "高风险"}, "has_hr_data": True}
    adj = wr.derive_adjustments({"compliance_ratio": 0.9, "compliance_pct": 90}, load)
    assert adj["risk_level"] == "高风险"
    assert adj["actions"]["volume"] == "减量"
    assert adj["actions"]["intensity"] == "降强度"
    assert adj["actions"]["recovery"] == "增加恢复日"


def test_no_plan_report():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        rep = wr.build_review_report(d)
        assert rep["has_plan"] is False
        assert "还没有训练计划" in rep["report_text"]


def test_report_with_plan_and_actuals(tmp_path):
    plan = _plan_with(10.0, 4)
    (tmp_path / "plan.json").write_text(
        json.dumps({"plan": plan, "meta": {"plan_duration": 7}}), encoding="utf-8"
    )
    manual = {"近期跑步数据": [{
        "日期": _recent_date(), "距离_km": 40.0, "时长_分钟": 30,
        "平均配速_秒每公里": 360, "平均心率": 150,
    }]}
    (tmp_path / "manual_input.json").write_text(json.dumps(manual), encoding="utf-8")

    rep = wr.build_review_report(str(tmp_path))
    assert rep["has_plan"] is True
    assert rep["has_actuals"] is True
    assert rep["compliance"]["compliance_pct"] == 100
    assert "下周调整建议" in rep["report_text"]


def test_anchored_window_excludes_out_of_period():
    """测量窗口必须锚定计划区间：实际数据早于计划开始日，不应计入完成度。"""
    plan = _plan_with(10.0, 4)  # 计划 40km
    actuals = [{"source": "upload", "date": "2026-01-01", "distance_km": 40.0,
                "duration_min": 30, "hr": 150}]
    start = datetime(2026, 7, 31)
    end = datetime(2026, 8, 7)
    comp = wr.compute_compliance(plan, actuals, start_date=start, end_date=end)
    assert comp["executed_in_period"] is False
    assert comp["actual_volume_km"] == 0.0
    assert comp["compliance_pct"] == 0  # 0/40，但因无周期内执行数据，不应触发错误减量


def test_anchored_window_includes_in_period():
    """实际数据落在计划区间内，应按锚定窗口正常计入。"""
    plan = _plan_with(10.0, 4)  # 计划 40km
    actuals = [{"source": "upload", "date": "2026-08-03", "distance_km": 20.0,
                "duration_min": 30, "hr": 150}]
    start = datetime(2026, 7, 31)
    end = datetime(2026, 8, 7)
    comp = wr.compute_compliance(plan, actuals, start_date=start, end_date=end)
    assert comp["executed_in_period"] is True
    assert comp["actual_volume_km"] == 20.0


def test_no_plan_falls_back_to_recent_window():
    """无计划元信息时退回相对今天的窗口，向后兼容老调用。"""
    plan = _plan_with(10.0, 4)
    actuals = [{"source": "upload", "date": _recent_date(), "distance_km": 40.0,
                "duration_min": 30, "hr": 150}]
    comp = wr.compute_compliance(plan, actuals)  # 不传 start/end
    assert comp["executed_in_period"] is True
    assert comp["compliance_pct"] == 100


def test_readiness_explicit_when_no_data():
    """缺静息心率时 readiness 必须显式返回 None，而非冒充中等(70)。"""
    import training_science as ts
    s = ts.calc_training_summary(
        [{"时长_分钟": 30, "平均心率": 150, "日期": "2026-07-18"}], resting_hr=None
    )
    assert s["readiness"] is None
    assert s["readiness_available"] is False
    s2 = ts.calc_training_summary(
        [{"时长_分钟": 30, "平均心率": 150, "日期": "2026-07-18"}],
        resting_hr=55, baseline_resting_hr=55,
    )
    assert s2["readiness"] == 100
    assert s2["readiness_available"] is True


def test_profile_uses_all_runs(tmp_path, monkeypatch):
    """build_runner_profile 应使用全量数据，而非仅最近 5 条。

    自包含：原版本读取本机 data/manual_input.json（被 .gitignore），
    干净 checkout 上没有该文件 -> FileNotFoundError。这里注入临时目录
    与临时数据（7 条），既验证"不截断到 5 条"又不依赖本机私有数据。
    """
    import json as _json
    import data_tool as dt

    runs = [{
        "日期": f"2026-07-{d:02d}", "距离_km": 5.0, "时长_分钟": 30.0,
        "平均配速_秒每公里": 360, "平均心率": 150, "活动名称": "测试跑",
    } for d in range(1, 8)]  # 7 条 > 5，专门验证未截断
    (tmp_path / "manual_input.json").write_text(
        _json.dumps({"近期跑步数据": runs, "目标": {"目标完赛时间": "06:00:00"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(dt, "DEFAULT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(dt, "_profile_cache", None)

    prof = dt.build_runner_profile()
    assert len(prof["近期跑步数据"]) == 7, "应使用全量 7 条，不得截断到 5 条"
    assert prof["基本信息"].get("最近一次跑步日期") is not None

