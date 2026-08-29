"""数据管理路由 — 上传、刷新、画像、Garmin 同步"""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional
from flask import Blueprint, request, jsonify, current_app

import data_tool
import garmin_sync

data_bp = Blueprint("data", __name__)
logger = logging.getLogger(__name__)

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
            full_path = os.path.join(upload_dir, safe_name)
            f.save(full_path)
            saved.append(full_path)
    return saved


def _write_manual_input(form, data_dir):
    """将表单手动数据写入 data/manual_input.json，保留既有健康/体能指标。"""
    target_time = form.get("target_time", "06:00:00").strip() or "06:00:00"
    target_race = form.get("target_race", "").strip() or None
    target_distance = form.get("target_distance", "").strip() or None

    manual_path = os.path.join(data_dir, "manual_input.json")
    existing_health = {}
    existing_fitness = {}
    existing_recent = []
    existing_race = {}
    if os.path.isfile(manual_path):
        try:
            with open(manual_path, "r", encoding="utf-8") as f:
                existing_cfg = json.load(f)
            existing_health = existing_cfg.get("健康指标", {}) or {}
            existing_fitness = existing_cfg.get("体能指标", {}) or {}
            existing_recent = existing_cfg.get("近期跑步数据", []) or []
            existing_race = existing_cfg.get("近期比赛成绩", {}) or {}
        except (json.JSONDecodeError, OSError):
            pass

    run_date = form.get("run_date", "").strip()
    run_distance = form.get("run_distance", "").strip()
    run_duration = form.get("run_duration", "").strip()
    run_pace = form.get("run_pace", "").strip()
    run_hr = form.get("run_hr", "").strip()
    run_name = form.get("run_name", "").strip()

    # 近期比赛成绩（用于推导 VDOT）：保留既有，仅当本次表单提供时更新
    race_distance = form.get("race_distance", "").strip()
    race_time = form.get("race_time", "").strip()
    race = dict(existing_race) if existing_race else {}
    if race_distance and race_time:
        race_sec = _parse_duration_to_sec(race_time)
        if race_sec:
            try:
                race = {"距离_km": float(race_distance), "时间_秒": race_sec}
            except (ValueError, TypeError):
                pass

    # 追加而非覆盖：保留历史手动跑步，新记录排在末尾
    recent_runs = list(existing_recent)
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
        "近期比赛成绩": race if race else {},
        "近期跑步数据": recent_runs,
        "健康指标": existing_health,
        "体能指标": existing_fitness,
    }

    with open(manual_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    return config


def _parse_duration_to_sec(value: str) -> Optional[float]:
    """把时长字符串转为秒。支持 '40:00' / '40' / '1:20:00'。失败返回 None。"""
    if not value:
        return None
    s = str(value).strip()
    parts = [p for p in s.replace("'", ":").replace('"', ":").split(":") if p != ""]
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 1:
            return float(parts[0])
    except (ValueError, TypeError):
        return None
    return None


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

        saved_paths = []
        if saved_files:
            saved_paths = _save_uploaded_files(saved_files, upload_dir)

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

        # 解析本次上传的文件，给出"已摄入"反馈，让数据接入可见
        ingested_runs = []
        if saved_paths:
            try:
                for r in data_tool.get_recent_runs(files=saved_paths, limit=50):
                    ingested_runs.append({
                        "日期": r.get("日期"),
                        "距离_km": r.get("距离_km"),
                        "平均配速_秒每公里": r.get("平均配速_秒每公里"),
                        "平均心率": r.get("平均心率"),
                    })
            except Exception:
                pass

        data_tool.invalidate_profile_cache()
        return jsonify({
            "success": True,
            "message": "数据已更新",
            "ingested_count": len(ingested_runs),
            "ingested_runs": ingested_runs,
            "manual_added": has_manual,
        })
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

        # VDOT：若用户填了近期比赛成绩，则推导有氧能力与配速表
        import vdot as vdot_mod
        vdot_val = None
        vdot_paces = None
        try:
            race = vdot_mod.read_recent_race_from_manual(data_dir)
            vdot_val = vdot_mod.vdot_from_recent_race(race)
            if vdot_val is not None:
                dist = (race or {}).get("距离_km") or (race or {}).get("distance_km") or 10.0
                vdot_paces = vdot_mod.vdot_training_paces(vdot_val, float(dist))
        except Exception:
            pass

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
            "vdot": vdot_val,
            "vdot_paces": vdot_paces,
        })
    except Exception as e:
        return jsonify({
            "ok": False, "error": str(e),
            "recent_runs": [],
            "stats": {"thirty_day_volume": 0, "avg_pace": 0, "weekly_frequency": 0},
            "target_race": "", "target_time": "",
        })


@data_bp.route("/api/data/actuals", methods=["GET"])
def api_data_actuals():
    """返回汇聚后的实际执行数据（周复盘闭环的输入），让数据接入可视化、可验证。

    数据来自 weekly_review.load_actuals：计划按天记录 + 上传解析 + 手动录入三源合并。
    """
    try:
        import weekly_review
        actuals = weekly_review.load_actuals(current_app.config["DATA_DIR"])
        total_distance = round(sum((a.get("distance_km") or 0) for a in actuals), 1)
        by_source = {}
        for a in actuals:
            src = a.get("source", "unknown")
            by_source[src] = by_source.get(src, 0) + 1
        return jsonify({
            "ok": True,
            "count": len(actuals),
            "total_distance_km": total_distance,
            "by_source": by_source,
            "items": actuals,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@data_bp.route("/api/data/vdot", methods=["GET"])
def api_data_vdot():
    """由近期比赛成绩推导 VDOT 与全强度区配速表。无比赛成绩返回 ok=False。"""
    try:
        import vdot as vdot_mod
        data_dir = current_app.config["DATA_DIR"]
        race = vdot_mod.read_recent_race_from_manual(data_dir)
        vdot_val = vdot_mod.vdot_from_recent_race(race)
        if vdot_val is None:
            return jsonify({
                "ok": False,
                "error": "未配置近期比赛成绩。请在数据页填写「近期比赛：距离 + 时间」后重试。",
                "vdot": None,
                "paces": None,
            })
        dist = (race or {}).get("距离_km") or (race or {}).get("distance_km") or 10.0
        paces = vdot_mod.vdot_training_paces(vdot_val, float(dist))
        block = vdot_mod.format_vdot_block(vdot_val, float(dist))
        return jsonify({
            "ok": True,
            "vdot": vdot_val,
            "race": race,
            "paces_sec": paces,
            "block": block,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@data_bp.route("/api/data/sync_garmin", methods=["POST"])
def api_sync_garmin():
    """从 Garmin Connect 同步最近跑步活动到 data/ 目录，并刷新画像缓存。"""
    try:
        data_dir = current_app.config.get("DATA_DIR", data_tool.DEFAULT_DATA_DIR)
        count = int(request.args.get("count", 5))
        fmt = request.args.get("fmt", "tcx").lower()
        if fmt not in {"tcx", "gpx", "fit"}:
            return jsonify({
                "success": False,
                "error": "fmt 仅支持 tcx、gpx、fit 三种格式",
            }), 400

        downloaded = garmin_sync.pull_recent_runs(
            count=count,
            output_dir=data_dir,
            fmt=fmt,
        )
        garmin_sync.record_sync_success(downloaded, source="manual")
        data_tool.invalidate_profile_cache()

        return jsonify({
            "success": True,
            "count": len(downloaded),
            "files": [os.path.basename(p) for p in downloaded],
        })
    except RuntimeError as e:
        garmin_sync.record_sync_failure(str(e), source="manual")
        logger.warning("Garmin sync runtime error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        garmin_sync.record_sync_failure(str(e), source="manual")
        logger.exception("Garmin sync failed")
        return jsonify({"success": False, "error": str(e)}), 500


@data_bp.route("/api/data/garmin_login", methods=["POST"])
def api_garmin_login():
    """启动可见浏览器做 Garmin 交互登录（MFA 在浏览器窗口输入），成功写 Cookie。"""
    try:
        garmin_sync.garmin_login(headless=False)
        return jsonify({"success": True, "message": "浏览器登录成功，已保存会话"})
    except RuntimeError as e:
        logger.warning("Garmin 浏览器登录失败：%s", e)
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.exception("Garmin 浏览器登录异常")
        return jsonify({"success": False, "error": str(e)}), 500


@data_bp.route("/api/data/sync_status", methods=["GET"])
def api_sync_status():
    """返回 Garmin 同步状态 + 当前画像最新跑步日期，供前端判断数据是否新鲜。"""
    try:
        state = garmin_sync.read_sync_state()
        latest_run = None
        try:
            profile = data_tool.build_runner_profile()
            latest_run = (profile.get("基本信息") or {}).get("最近一次跑步日期")
        except Exception:
            pass

        # 判断数据是否“新鲜”：最近一次成功同步在 30 分钟内
        fresh = False
        last_success = state.get("updated_at") if state.get("result") == "success" else None
        if last_success:
            try:
                from datetime import datetime as _dt
                last_dt = _dt.fromisoformat(last_success)
                fresh = (_dt.now() - last_dt).total_seconds() < 30 * 60
            except (ValueError, TypeError):
                fresh = False

        return jsonify({
            "ok": True,
            "state": state,
            "latest_run_date": latest_run,
            "fresh": fresh,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "state": {}, "fresh": False})
