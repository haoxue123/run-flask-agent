"""
模型评测模块
============================================================
自动评测 LLM 回答质量，支持：
  - 从 CSV 加载评测集
  - LLM 裁判四维度评分（准确性 / 相关性 / 完整性 / 实用性）
  - Bad Case 自动记录
  - 评测结果持久化（data/eval_results.json）
============================================================
"""

import os
import csv
import json
import time
from datetime import datetime

import llm_agent

# 数据目录
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_EVAL_CSV = os.path.join(_DATA_DIR, "eval_questions.csv")
_RESULTS_JSON = os.path.join(_DATA_DIR, "eval_results.json")
_BAD_CASES_DIR = os.path.join(_DATA_DIR, "bad_cases")

# 评分维度
_DIMENSIONS = ["准确性", "相关性", "完整性", "实用性"]


def load_questions(path=None):
    """
    从 CSV 加载评测集。

    返回：
        list[dict]: [{id, category, question, expected_themes}, ...]
    """
    filepath = path or _EVAL_CSV
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"评测集文件不存在：{filepath}")

    questions = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("id") and row.get("question"):
                questions.append({
                    "id": row["id"].strip(),
                    "category": row.get("category", "").strip(),
                    "question": row["question"].strip(),
                    "expected_themes": row.get("expected_themes", "").strip(),
                })
    return questions


def judge_answer(question, answer, expected_themes=""):
    """
    LLM 裁判：从四维度对回答评分。

    参数：
        question:        评测题
        answer:          模型回答
        expected_themes: 预期主题词（分号分隔）

    返回：
        dict: {
            "scores": {"准确性": int, "相关性": int, "完整性": int, "实用性": int},
            "total": int,
            "comment": str
        }
    """
    judge_prompt = f"""你是一个跑步教练领域的专业评测员。请对以下 AI 回答进行评分。

【评分维度】（每个维度 1-5 分，5 分最高）
- 准确性：回答的运动科学知识是否正确，无事实错误
- 相关性：回答是否紧扣问题，不跑题
- 完整性：是否覆盖了关键要点，回答是否充分
- 实用性：建议是否具体可操作，跑者能否直接应用

【预期主题词】{expected_themes if expected_themes else "无"}

【用户问题】
{question}

【AI 回答】
{answer}

请严格按以下 JSON 格式返回（不要包含其他文字）：
{{
    "准确性": <分数>,
    "相关性": <分数>,
    "完整性": <分数>,
    "实用性": <分数>,
    "comment": "<简短评语，说明优缺点>"
}}"""

    try:
        messages = [
            {"role": "system", "content": "你是一个专业的跑步教练评测员，严格按 JSON 格式输出评分。"},
            {"role": "user", "content": judge_prompt},
        ]
        result = llm_agent.call_deepseek(messages, temperature=0.0)
        reply = result.get("content", "") if isinstance(result, dict) else ""

        # 提取 JSON
        json_str = reply.strip()
        # 处理可能的 markdown 代码块包裹
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        scores = json.loads(json_str)

        # 验证并标准化
        validated = {}
        for dim in _DIMENSIONS:
            score = scores.get(dim, 3)
            validated[dim] = max(1, min(5, int(score)))

        return {
            "scores": validated,
            "total": sum(validated.values()),
            "comment": scores.get("comment", ""),
        }
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        # 解析失败时返回默认评分
        return {
            "scores": {dim: 3 for dim in _DIMENSIONS},
            "total": 12,
            "comment": f"评分解析失败：{e}",
        }
    except Exception as e:
        return {
            "scores": {dim: 3 for dim in _DIMENSIONS},
            "total": 12,
            "comment": f"评分异常：{e}",
        }


def save_bad_case(question, answer, scores, comment=""):
    """
    保存低分回答到 data/bad_cases/。

    参数：
        question: 评测题
        answer:   模型回答
        scores:   评分字典
        comment:  裁判评语
    """
    os.makedirs(_BAD_CASES_DIR, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    # 找当天已有 case 编号
    existing = [f for f in os.listdir(_BAD_CASES_DIR) if f.startswith(today)]
    case_num = len(existing) + 1

    filename = f"{today}_case_{case_num:02d}.json"
    filepath = os.path.join(_BAD_CASES_DIR, filename)

    case = {
        "date": datetime.now().isoformat(),
        "question": question,
        "answer": answer,
        "scores": scores,
        "total": sum(scores.values()) if isinstance(scores, dict) else 0,
        "comment": comment,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False, indent=2)

    return filepath


def run_evaluation(questions=None, progress_callback=None, max_questions=None, delay=0.5):
    """
    执行完整评测流程。

    参数：
        questions:         评测题列表，默认从 CSV 加载
        progress_callback: 进度回调函数 fn(current, total)
        max_questions:     最多评测题数，None 表示全部（用于快速采样）
        delay:             每题间延迟秒数，避免 API 限流

    返回：
        dict: {
            "summary": {"准确性": avg, "相关性": avg, "完整性": avg, "实用性": avg, "total": avg},
            "details": [{id, category, question, answer, scores, total, comment}, ...],
            "bad_case_count": int,
            "timestamp": str
        }
    """
    if questions is None:
        questions = load_questions()

    if max_questions and max_questions > 0:
        questions = questions[:max_questions]

    total = len(questions)
    details = []
    dim_sums = {dim: 0 for dim in _DIMENSIONS}
    total_sum = 0
    bad_case_count = 0

    for i, q in enumerate(questions):
        if progress_callback:
            progress_callback(i + 1, total)

        # 生成回答
        try:
            messages = [
                {"role": "system", "content": "你是一个专业的跑步教练，请用中文回答跑者的问题。回答要准确、实用、具体。"},
                {"role": "user", "content": q["question"]},
            ]
            result = llm_agent.call_deepseek(messages, temperature=0.3)
            answer = result.get("content", "") if isinstance(result, dict) else ""
        except Exception as e:
            answer = f"[生成失败：{e}]"

        # 裁判评分
        expected = q.get("expected_themes", "")
        judgment = judge_answer(q["question"], answer, expected)

        scores = judgment["scores"]
        total_score = judgment["total"]
        comment = judgment["comment"]

        for dim in _DIMENSIONS:
            dim_sums[dim] += scores[dim]
        total_sum += total_score

        detail = {
            "id": q["id"],
            "category": q["category"],
            "question": q["question"],
            "answer": answer[:500],
            "scores": scores,
            "total": total_score,
            "comment": comment,
        }
        details.append(detail)

        # Bad Case 记录
        if total_score < 12:
            save_bad_case(q["question"], answer, scores, comment)
            bad_case_count += 1

        # 避免 API 限流
        time.sleep(delay)

    # 汇总
    summary = {
        dim: round(dim_sums[dim] / total, 2) if total > 0 else 0
        for dim in _DIMENSIONS
    }
    summary["total"] = round(total_sum / total, 2) if total > 0 else 0

    results = {
        "summary": summary,
        "details": details,
        "bad_case_count": bad_case_count,
        "total_questions": total,
        "timestamp": datetime.now().isoformat(),
    }

    save_results(results)
    return results


def save_results(results, path=None):
    """保存评测结果到 JSON 文件。"""
    filepath = path or _RESULTS_JSON
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def load_results(path=None):
    """
    加载最近评测结果。

    返回：
        dict 或 None
    """
    filepath = path or _RESULTS_JSON
    if not os.path.isfile(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ============================================================
# 独立运行入口
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("跑步教练模型评测")
    print("=" * 60)

    questions = load_questions()
    print(f"\n加载评测集：{len(questions)} 题")

    categories = {}
    for q in questions:
        cat = q["category"]
        categories[cat] = categories.get(cat, 0) + 1
    for cat, count in categories.items():
        print(f"  {cat}: {count} 题")

    print("\n开始评测...")
    results = run_evaluation(questions)

    print(f"\n评测完成！")
    print(f"  总分: {results['summary']['total']:.2f}/20")
    print(f"  准确性: {results['summary']['准确性']:.2f}")
    print(f"  相关性: {results['summary']['相关性']:.2f}")
    print(f"  完整性: {results['summary']['完整性']:.2f}")
    print(f"  实用性: {results['summary']['实用性']:.2f}")
    print(f"  Bad Case: {results['bad_case_count']} 个")
    print(f"  结果已保存到: {_RESULTS_JSON}")