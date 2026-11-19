import io
import json

import pytest
from werkzeug.datastructures import FileStorage, MultiDict

from services.ingestion import InputError, save_uploaded_files, write_manual_input
from services.plans import PlanError, load_plan, save_plan, update_day
from services.storage import atomic_write_json


def test_atomic_write_retains_backup(tmp_path):
    path = tmp_path / "plan.json"
    atomic_write_json(str(path), {"version": 1})
    atomic_write_json(str(path), {"version": 2}, backup=True)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2
    assert json.loads((tmp_path / "plan.json.bak").read_text(encoding="utf-8"))["version"] == 1


def test_uploads_are_sanitized_and_invalid_types_rejected(tmp_path):
    good = FileStorage(io.BytesIO(b"data"), filename="../../run.tcx")
    assert save_uploaded_files([good], str(tmp_path), timestamp="now") == ["now_run.tcx"]
    bad = FileStorage(io.BytesIO(b"data"), filename="run.exe")
    with pytest.raises(InputError):
        save_uploaded_files([bad], str(tmp_path))


def test_manual_input_validates_numbers_and_preserves_sections(tmp_path):
    (tmp_path / "manual_input.json").write_text(json.dumps({"健康指标": {"静息心率": 60}}), encoding="utf-8")
    form = MultiDict({"run_date": "2026-01-01", "run_distance": "5", "run_hr": "150"})
    result = write_manual_input(form, str(tmp_path))
    assert result["近期跑步数据"][0]["距离_km"] == 5.0
    assert result["健康指标"]["静息心率"] == 60
    with pytest.raises(InputError):
        write_manual_input(MultiDict({"run_date": "x", "run_distance": "nope"}), str(tmp_path))


def test_plan_service_preserves_science_and_updates_day(tmp_path):
    save_plan(str(tmp_path), {"plan": [{"day": 1, "type": "轻松跑"}], "science": {"flags": []}})
    updated = update_day(str(tmp_path), {"day": "1", "completed": True, "feeling": "good"})
    assert updated["plan"][0]["completed"] is True
    assert load_plan(str(tmp_path), str(tmp_path))["science"] == {"flags": []}
    with pytest.raises(PlanError):
        update_day(str(tmp_path), {"day": "2"})
