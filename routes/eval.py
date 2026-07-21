"""评测 API 路由 — 执行评测与查看结果"""
from flask import Blueprint, jsonify, request

import eval

eval_bp = Blueprint("eval", __name__, url_prefix="/api")


@eval_bp.route("/eval/run", methods=["POST"])
def run_eval():
    """执行评测流程。通过 ?sample=N 参数限制评测题数，避免超时。"""
    try:
        sample = request.args.get("sample", type=int)
        results = eval.run_evaluation(max_questions=sample)
        return jsonify({
            "success": True,
            "summary": results.get("summary", {}),
            "details": results.get("details", []),
            "bad_case_count": results.get("bad_case_count", 0),
            "total_questions": results.get("total_questions", 0),
            "timestamp": results.get("timestamp", ""),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@eval_bp.route("/eval/results", methods=["GET"])
def get_eval_results():
    """获取最近一次评测结果。"""
    results = eval.load_results()
    if results:
        return jsonify({"results": results})
    return jsonify({"results": None, "last_run": None})