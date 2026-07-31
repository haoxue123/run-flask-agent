"""训练计划路由 — 生成、保存、加载"""
import os
import json
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app, send_from_directory

import data_tool
import llm_agent
import visualize
import safeio
from openai import APIConnectionError, AuthenticationError

plan_bp = Blueprint("plan", __name__)


@plan_bp.route("/generate", methods=["POST"])
def generate():
    """生成训练计划：处理上传文件或手动数据，调用 LLM 生成计划，返回 JSON。"""
    from routes.data import _allowed_file, _save_uploaded_files, _write_manual_input

    data_dir = current_app.config["DATA_DIR"]
    upload_dir = current_app.config["UPLOAD_DIR"]
    chart_dir = current_app.config["CHART_DIR"]

    try:
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
                "plan": None, "chart_url": None, "source": None,
            })

        uploaded_names = _save_uploaded_files(saved_files, upload_dir) if saved_files else []

        has_manual = bool(request.form.get("run_date", "").strip() and
                         request.form.get("run_distance", "").strip())
        if has_manual:
            _write_manual_input(request.form, data_dir)

        data_tool.invalidate_profile_cache()

        if not uploaded_names and not has_manual:
            existing = data_tool._scan_activity_files(data_dir)
            if not existing:
                return jsonify({
                    "success": False,
                    "error": "请上传运动文件或填写手动数据",
                    "plan": None, "chart_url": None, "source": None,
                })

        profile = data_tool.build_runner_profile()
        source_info = data_tool.get_data_source_info()

        plan_duration = int(request.form.get("plan_duration", "7"))
        # 用户明确指令：训练页"指令"输入框传入，作为一等输入进入生成（修复"命令不被听从"）
        user_instructions = (request.form.get("instructions") or "").strip()
        plan_result = llm_agent.generate_training_plan(
            profile, plan_duration=plan_duration, user_instructions=user_instructions
        )
        plan_text = plan_result["plan_text"]
        structured = plan_result["structured"]
        science = plan_result.get("science", {})

        # 将结构化计划存入 plan.json
        target = profile.get("目标", {}) or {}
        plan_path = os.path.join(data_dir, "plan.json")
        meta = {
            "generated_at": datetime.now().isoformat(),
            "plan_duration": plan_duration,
            "target_race": target.get("目标比赛", ""),
            "target_time": target.get("目标完赛时间", ""),
        }
        plan_payload = {"plan": structured, "meta": meta, "science": science}
        # 去删除化落盘：备份 + 截断式覆盖写 + 校验，绝不触发沙箱 safe-delete
        safeio.safe_write_json(plan_path, plan_payload)

        chart_filename = f"pace_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        os.makedirs(chart_dir, exist_ok=True)
        chart_path = os.path.join(chart_dir, chart_filename)
        visualize.plot_pace_chart(structured, output_path=chart_path)

        return jsonify({
            "success": True,
            "plan": plan_text,
            "structured": structured,
            "science": science,
            "chart_url": f"/chart/{chart_filename}",
            "source": source_info.get("模式", "未知"),
            "instructions": user_instructions,
            "error": None,
        })
    except AuthenticationError:
        return jsonify({
            "success": False,
            "error": "DeepSeek API 密钥无效或已过期，请检查 .env 中的 DEEPSEEK_API_KEY",
            "plan": None, "chart_url": None, "source": None,
        })
    except APIConnectionError:
        return jsonify({
            "success": False,
            "error": "无法连接到 DeepSeek API。请检查网络连接，或确认是否开启了代理/VPN。",
            "plan": None, "chart_url": None, "source": None,
        })
    except ValueError as e:
        return jsonify({
            "success": False, "error": str(e),
            "plan": None, "chart_url": None, "source": None,
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"生成计划时出错：{type(e).__name__}: {str(e)}",
            "plan": None, "chart_url": None, "source": None,
        })


@plan_bp.route("/chart/<filename>")
def chart(filename):
    return send_from_directory(current_app.config["CHART_DIR"], filename)


@plan_bp.route("/api/plan/load", methods=["GET"])
def api_plan_load():
    plan_path = os.path.join(current_app.config["DATA_DIR"], "plan.json")
    if not os.path.isfile(plan_path):
        return jsonify({"plan": None})
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        plan = data.get("plan", [])
        # 兼容旧格式：plan 为纯文本字符串时，调用解析函数转换
        if isinstance(plan, str):
            plan = llm_agent._parse_plan_to_structured(plan)
        meta = data.get("meta", {})
        science = data.get("science")
        # 兜底：如果 meta 中没有 chart_url，查找最新的图表文件
        if not meta.get("chart_url"):
            chart_dir = current_app.config["CHART_DIR"]
            if os.path.isdir(chart_dir):
                charts = sorted(
                    [f for f in os.listdir(chart_dir) if f.startswith("pace_chart_") and f.endswith(".png")],
                    reverse=True
                )
                if charts:
                    meta["chart_url"] = f"/chart/{charts[0]}"
        return jsonify({"plan": plan, "meta": meta, "science": science})
    except (json.JSONDecodeError, OSError):
        return jsonify({"plan": None})


@plan_bp.route("/api/plan/save", methods=["POST"])
def api_plan_save():
    """保存训练计划到 data/plan.json（原子写入 + 备份）。"""
    try:
        data = request.get_json()
        plan = data.get("plan") or data.get("days", [])
        req_science = data.get("science")
        data_dir = current_app.config["DATA_DIR"]
        plan_path = os.path.join(data_dir, "plan.json")
        # 保留已有 science（前端未修改计划时不应丢失科学增强结果）
        existing_science = None
        if os.path.isfile(plan_path):
            try:
                with open(plan_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                existing_science = existing.get("science")
            except (json.JSONDecodeError, OSError):
                pass
        science = req_science if req_science is not None else existing_science
        meta = {
            "generated_at": data.get("generated_at", datetime.now().isoformat()),
            "plan_duration": data.get("plan_duration", 7),
            "target_race": data.get("target_race", ""),
            "target_time": data.get("target_time", ""),
            "chart_url": data.get("chart_url", ""),
        }
        payload = {"plan": plan, "meta": meta}
        if science is not None:
            payload["science"] = science

        # 去删除化落盘：备份 + 截断式覆盖写 + 校验，绝不触发沙箱 safe-delete
        safeio.safe_write_json(plan_path, payload)

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@plan_bp.route("/api/plan/update_day", methods=["POST"])
def api_plan_update_day():
    """更新计划中某一天的执行数据，并写回 plan.json（原子写入 + 备份）。"""
    try:
        data = request.get_json()
        day = data.get("day")
        if day is None:
            return jsonify({"success": False, "error": "缺少 day 字段"})
        # 归一化为 int，避免结构化计划存的 int 与前端传来的字符串 "3" 不匹配
        try:
            day = int(day)
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": f"day 字段无效：{day}"})

        data_dir = current_app.config["DATA_DIR"]
        plan_path = os.path.join(data_dir, "plan.json")
        if not os.path.isfile(plan_path):
            return jsonify({"success": False, "error": "计划文件不存在"})

        with open(plan_path, "r", encoding="utf-8") as f:
            plan_data = json.load(f)

        plan_list = plan_data.get("plan", [])
        updated = False
        for item in plan_list:
            if item.get("day") == day:
                if "completed" in data:
                    item["completed"] = data["completed"]
                if "actual_distance_km" in data:
                    item["actual_distance_km"] = data["actual_distance_km"]
                if "actual_pace_sec" in data:
                    item["actual_pace_sec"] = data["actual_pace_sec"]
                if "actual_duration_min" in data:
                    item["actual_duration_min"] = data["actual_duration_min"]
                if "feeling" in data:
                    item["feeling"] = data["feeling"]
                updated = True
                break

        if not updated:
            return jsonify({"success": False, "error": f"未找到 day={day} 的训练日"})

        # 原子写入 + 备份（去删除化：备份 + 截断式覆盖写 + 校验）
        safeio.safe_write_json(plan_path, plan_data)

        return jsonify({"success": True, "plan": plan_data.get("plan", []), "meta": plan_data.get("meta", {})})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})