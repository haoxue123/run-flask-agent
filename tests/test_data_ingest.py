"""P2 测试：执行数据接入一等公民
覆盖：手动录入追加（修复覆盖 bug）、load_actuals 多源合并、get_all_runs 解析 CSV。
"""
import os
import json
import sys
import importlib

import pytest

# 让测试能在项目根目录直接 import 各模块
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import data_tool
import weekly_review


def _write_manual(data_dir, date, distance, pace="6:17", hr=150):
    from routes import data as data_routes
    form = {
        "run_date": date,
        "run_distance": str(distance),
        "run_duration": "25",
        "run_pace": pace,
        "run_hr": str(hr),
    }
    data_routes._write_manual_input(form, data_dir)


def test_manual_input_appends_not_overwrites(tmp_path):
    """关键回归：多次手动录入应追加，而不是覆盖历史。"""
    d = str(tmp_path)
    _write_manual(d, "2026-08-01", 5.0)
    _write_manual(d, "2026-08-03", 8.0)

    path = os.path.join(d, "manual_input.json")
    assert os.path.isfile(path)
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    runs = cfg.get("近期跑步数据", [])
    assert len(runs) == 2, f"手动录入被覆盖，实际条数={len(runs)}"
    dates = {r["日期"] for r in runs}
    assert dates == {"2026-08-01", "2026-08-03"}


def test_load_actuals_merges_plan_and_manual(tmp_path):
    """load_actuals 应合并：计划按天记录(plan_day) + 手动录入(manual)。"""
    d = str(tmp_path)
    # 计划含一个已记录实际执行的天
    plan = {
        "plan": [
            {"day": 1, "type": "轻松跑", "distance_km": 5, "actual_distance_km": 5.0,
             "actual_pace_sec": 370, "actual_duration_min": 28.0},
            {"day": 2, "type": "休息", "is_rest": True},
        ],
        "meta": {"plan_duration": 7},
    }
    with open(os.path.join(d, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False)
    # 手动录入一条
    _write_manual(d, "2026-08-02", 6.0)

    actuals = weekly_review.load_actuals(d)
    sources = [a["source"] for a in actuals]
    assert "plan_day" in sources, "计划按天实际数据未进入 actuals"
    assert "manual" in sources, "手动录入未进入 actuals"
    # 手动 1 条 + 计划 1 条（休息日无 actual）
    assert len(actuals) == 2


def test_get_all_runs_parses_csv(tmp_path):
    """get_all_runs 应解析 data 目录下的 CSV，不依赖 limit=5。"""
    d = str(tmp_path)
    csv_path = os.path.join(d, "Activities.csv")
    content = (
        "Date,Activity Type,Distance,Time,Avg HR\n"
        "2026-07-20,Running,6.0,30:00,150\n"
        "2026-07-22,Running,10.0,55:00,152\n"
        "2026-07-25,Running,12.0,68:00,154\n"
        "2026-07-28,Running,8.0,44:00,151\n"
        "2026-07-30,Running,5.0,27:00,149\n"
        "2026-08-01,Running,7.0,38:00,150\n"
    )
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write(content)

    runs = data_tool.get_all_runs(data_dir=d)
    assert len(runs) == 6, f"get_all_runs 应返回全部 6 条，实际={len(runs)}"
    # 日期倒序
    dates = [r["日期"] for r in runs]
    assert dates == sorted(dates, reverse=True)
    # 关键字段解析正确
    first = runs[0]
    assert first["距离_km"] == 7.0
    assert first["平均心率"] == 150
