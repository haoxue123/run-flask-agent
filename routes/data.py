"""数据管理路由 — 上传、刷新、画像"""
import os
import json
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, current_app

import data_tool

data_bp = Blueprint("data", __name__)

ALLOWED_EXTS = {".gpx", ".tcx", ".fit", ".csv"}


def _allowed_file(filename):
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTS


def _save_uploaded_files(files, upload_dir, timestamp=None):
    saved = []
    os.makedirs(upload_dir, exist_ok=True)
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for f in files:
        if f and f.filename and _allowed_file(f.filename):
            safe_name = f"{timestamp}_{f.filename}"
            f.save(os.path.join(upload_dir, safe_name))
            saved.append(safe_name)
    return saved


def _write_manual_input(form, data_dir):
    """将表单手动数据写入 data/manual_input.json，保留既有健康/体能指标。"""
    target_time = form.get("target_time", "06:00:00").strip() or "06:00:00"
    target_race = form.get("target_race", "").strip() or None
    target_distance = form.get("target_distance", "").strip() or None

    manual_path = os.path.join(data_dir, "manual_input.json")
    existing_health = {}
    existing_fitness = {}
    if os.path.isfile(manual_path):
        try:
            with open(manual_path, "r", encoding="utf-8") as f:
                existing_cfg = json.load(f)
            existing_health = existing_cfg.get("健康指标", {}) or {}
            existing_fitness = existing_cfg.get("体能指标", {}) or {}
        except (json.JSONDecodeError, OSError):
            pass

    run_date = form.get("run_date", "").strip()
    run_distance = form.get("run_distance", "").strip()
    run_duration = form.get("run_duration", "").strip()
    run_pace = form.get("run_pace", "").strip()
    run_hr = form.get("run_hr", "").strip()
    run_name = form.get("run_name", "").strip()

    recent_runs = []
    if run_date and run_distance:
        run_entry = {
            "日期": run_date,
            "距离_km": float(run_distance) if run_distance else None,
            "时长_分钟": float(run_duration) if run_duration else None,
            "平均配速_秒每公里": run_pace if run_pace else None,
            "平均心率": int(run_hr) if run_hr else None,
            "活动名称": run_name or "手动记录",
        }
        recent_runs.append(run_entry)

    config = {
        "目标": {"目标完赛时间": target_time, "目标比赛": target_race, "目标距离": target_distance},
        "近期跑步数据": recent_runs,
        "健康指标": existing_health,
        "体能指标": existing_fitness,
    }

    with open(manual_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    return config


@data_bp.route("/api/data/refresh", methods=["POST"])
def api_data_refresh():
    """仅保存数据并刷新跑者画像，不生成训练计划。"""
    try:
        data_dir = current_app.config["DATA_DIR"]
        upload_dir = current_app.config["UPLOAD_DIR"]

        files = request.files.getlist("files")
        saved_files = []
        invalid_files = []
        if files:
            for f in files:
                if f and f.filename:
                    if _allowed_file(f.filename):
                        saved_files.append(f)
                    else:
                        invalid_files.append(f.filename)

        if invalid_files:
            return jsonify({
                "success": False,
                "error": f"仅支持 GPX/TCX/FIT/CSV 文件，不支持的文件：{', '.join(invalid_files)}",
            })

        if saved_files:
            _save_uploaded_files(saved_files, upload_dir)

        has_manual = bool(request.form.get("run_date", "").strip() and
                         request.form.get("run_distance", "").strip())
        if has_manual:
            _write_manual_input(request.form, data_dir)

        if not saved_files and not has_manual:
            existing = data_tool._scan_activity_files(data_dir)
            if not existing:
                return jsonify({
                    "success": False,
                    "error": "请上传运动文件或填写手动数据",
                })

        data_tool.invalidate_profile_cache()
        return jsonify({"success": True, "message": "数据已更新，请查看数据总览"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


def _safe_float(val, default=0.0):
    """安全转 float，兼容字符串/None。"""
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


@data_bp.route("/api/data/profile", methods=["GET"])
def api_data_profile():
    """返回跑者画像摘要，供教练右侧上下文面板使用。"""
    try:
        profile = data_tool.build_runner_profile()
        if not profile:
            return jsonify({
                "ok": False, "error": "无法构建跑者画像",
                "recent_runs": [],
                "stats": {"thirty_day_volume": 0, "avg_pace": 0, "weekly_frequency": 0},
                "target_race": "", "target_time": "",
            })

        runs = profile.get("近期跑步数据", []) or []
        goal = profile.get("目标", {}) or {}

        # 按日期倒序，取最近 3 条
        sorted_runs = sorted(runs, key=lambda r: r.get("日期", ""), reverse=True)
        recent_runs = sorted_runs[:3]

        # 近 30 天统计
        now = datetime.now()
        cutoff = now - timedelta(days=30)
        recent_30 = []
        for r in runs:
            try:
                run_date = datetime.strptime(str(r.get("日期", "")), "%Y-%m-%d")
                if run_date >= cutoff:
                    recent_30.append(r)
            except (ValueError, TypeError):
                pass  # 日期无法解析，不计入近 30 天统计

        thirty_day_volume = sum(_safe_float(r.get("距离_km")) for r in recent_30)

        # 按距离加权平均配速
        total_pace_weighted = 0.0
        total_dist = 0.0
        for r in recent_30:
            dist = _safe_float(r.get("距离_km"))
            pace = _safe_float(r.get("平均配速_秒每公里"))
            if dist > 0 and pace > 0:
                total_pace_weighted += pace * dist
                total_dist += dist
        avg_pace = int(round(total_pace_weighted / total_dist)) if total_dist > 0 else 0

        # 周训练频率
        run_count = len(recent_30)
        weekly_frequency = round(run_count / 30 * 7, 1) if run_count > 0 else 0

        return jsonify({
            "ok": True,
            "recent_runs": recent_runs,
            "stats": {
                "thirty_day_volume": round(thirty_day_volume, 1),
                "avg_pace": avg_pace,
                "weekly_frequency": weekly_frequency,
            },
            "target_race": goal.get("目标比赛", "") or "",
            "target_time": goal.get("目标完赛时间", "") or "",
        })
    except Exception as e:
        return jsonify({
            "ok": False, "error": str(e),
            "recent_runs": [],
            "stats": {"thirty_day_volume": 0, "avg_pace": 0, "weekly_frequency": 0},
            "target_race": "", "target_time": "",
        })