"""Validation and persistence for activity uploads and manual activity input."""
import json
import os
from datetime import datetime
from werkzeug.utils import secure_filename

ALLOWED_EXTS = {".gpx", ".tcx", ".fit", ".csv"}
MAX_UPLOAD_FILES = 10
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class InputError(ValueError):
    pass


def allowed_file(filename):
    return os.path.splitext(filename or "")[1].lower() in ALLOWED_EXTS


def validate_files(files):
    selected = [item for item in files if item and item.filename]
    invalid = [item.filename for item in selected if not allowed_file(item.filename)]
    if invalid:
        raise InputError("仅支持 GPX/TCX/FIT/CSV 文件，不支持的文件：" + ", ".join(invalid))
    if len(selected) > MAX_UPLOAD_FILES:
        raise InputError(f"一次最多上传 {MAX_UPLOAD_FILES} 个文件")
    for item in selected:
        length = item.content_length
        if length is not None and length > MAX_UPLOAD_BYTES:
            raise InputError(f"文件过大：{item.filename}")
    return selected


def save_uploaded_files(files, upload_dir, timestamp=None):
    os.makedirs(upload_dir, exist_ok=True)
    timestamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    saved = []
    for item in validate_files(files):
        name = secure_filename(item.filename)
        if not name:
            raise InputError("文件名无效")
        path = os.path.join(upload_dir, f"{timestamp}_{name}")
        item.save(path)
        if os.path.getsize(path) > MAX_UPLOAD_BYTES:
            os.unlink(path)
            raise InputError(f"文件过大：{item.filename}")
        saved.append(os.path.basename(path))
    return saved


def has_manual_input(form):
    return bool((form.get("run_date") or "").strip() and (form.get("run_distance") or "").strip())


def write_manual_input(form, data_dir):
    """Persist user supplied input while retaining health and fitness settings."""
    manual_path = os.path.join(data_dir, "manual_input.json")
    existing = {}
    try:
        with open(manual_path, "r", encoding="utf-8") as handle:
            existing = json.load(handle)
    except (OSError, json.JSONDecodeError):
        pass
    try:
        distance = float((form.get("run_distance") or "").strip())
        duration = float((form.get("run_duration") or "").strip() or 0) or None
        heart_rate = int((form.get("run_hr") or "").strip() or 0) or None
    except ValueError as exc:
        raise InputError("手动跑步数据格式无效") from exc
    config = {
        "目标": {"目标完赛时间": (form.get("target_time") or "06:00:00").strip() or "06:00:00",
                 "目标比赛": (form.get("target_race") or "").strip() or None,
                 "目标距离": (form.get("target_distance") or "").strip() or None},
        "近期跑步数据": [{"日期": (form.get("run_date") or "").strip(), "距离_km": distance,
                       "时长_分钟": duration, "平均配速_秒每公里": (form.get("run_pace") or "").strip() or None,
                       "平均心率": heart_rate, "活动名称": (form.get("run_name") or "").strip() or "手动记录"}],
        "健康指标": existing.get("健康指标", {}) or {},
        "体能指标": existing.get("体能指标", {}) or {},
    }
    os.makedirs(data_dir, exist_ok=True)
    with open(manual_path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
    return config
