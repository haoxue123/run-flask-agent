"""P2 测试：流式 FIT 摄入管线（瘦 JSON / 内存恒定 / 错误隔离）
覆盖：extract_record_fields 抽取、_summarize 汇总、ingest_fit_stream 错误隔离。
真实 FIT 解析依赖 fitparse（未装则返回 error，不崩）。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import parsers
from parsers.fit import extract_record_fields
from parsers.ingest import _summarize, ingest_fit_stream, ingest_directory


def test_extract_record_fields():
    raw = {
        "timestamp": "2026-01-01 08:00:00",
        "distance": 1234.5,
        "heart_rate": 150,
        "speed": 3.2,
        "altitude": 12.5,
        "cadence": 170,
    }
    out = extract_record_fields(raw)
    assert out["distance_m"] == 1234.5
    assert out["heart_rate"] == 150
    assert out["speed_mps"] == 3.2
    assert out["cadence"] == 170
    # 缺失字段应为 None
    assert extract_record_fields({})["heart_rate"] is None


def test_summarize_basic():
    from datetime import datetime
    recs = [
        {"timestamp": datetime(2026, 1, 1, 8, 0, 0), "distance_m": 0, "heart_rate": 140,
         "speed_mps": 3.0, "altitude_m": 10, "cadence": 160},
        {"timestamp": datetime(2026, 1, 1, 8, 9, 59), "distance_m": 2000, "heart_rate": 160,
         "speed_mps": 3.3, "altitude_m": 12, "cadence": 170},
    ]
    s = _summarize(recs)
    assert s["distance_km"] == 2.0
    assert s["duration_s"] == 599.0
    assert s["avg_heart_rate"] == 150.0
    assert s["max_heart_rate"] == 160
    assert s["avg_cadence"] == 165.0
    assert s["point_count"] == 2


def test_ingest_fit_stream_no_fitparse_or_missing():
    # 文件不存在 / fitparse 未装：必须优雅返回 error，不抛异常
    res = ingest_fit_stream("no_such_file.fit", os.path.join(ROOT, "data", "uploads"))
    assert "error" in res
    assert res["path"] == "no_such_file.fit"


def test_ingest_directory_empty():
    # 不存在的目录应返回空列表，不报错
    assert ingest_directory(os.path.join(ROOT, "data", "nonexistent_dir"), "x") == []
