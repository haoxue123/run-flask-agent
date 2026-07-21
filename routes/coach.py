"""教练对话路由 — SSE 流式响应 + 对话上下文记忆"""
import os
import json
import re
import tempfile
import shutil
import threading
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, Response, stream_with_context, current_app, session

import data_tool
import llm_agent
import training_science as ts
import rag
import workflow
import memory
from llm_agent import load_prompt
import skills
from openai import APIConnectionError, AuthenticationError

coach_bp = Blueprint("coach", __name__)

# 对话历史保留最近 N 轮
MAX_HISTORY_ROUNDS = 10
# 拼入 LLM 上下文时使用最近 N 轮
CONTEXT_ROUNDS = 5

# Profile 缓存（避免每次对话都重新解析所有数据文件）
_profile_cache = {"data": None, "timestamp": 0}
_PROFILE_CACHE_TTL = 30  # 缓存有效期（秒）

# 计划缓存（避免每次对话都读磁盘）
_plan_cache = {"data": None, "mtime": 0}

# 缓存读写锁（threaded=True 下多线程并发访问全局缓存需加锁，以 rag.py 为样板）
_cache_lock = threading.Lock()

# 意图关键词快速匹配（避免简单问候也调 LLM）
_QUICK_INTENT_MAP = {
    "你好": "chat", "谢谢": "chat", "再见": "chat", "晚安": "chat",
    "早安": "chat", "早上好": "chat", "晚上好": "chat", "hi": "chat",
    "hello": "chat", "hey": "chat", "在吗": "chat", "好的": "chat",
    # 运动领域关键词
    "分析": "assess", "数据": "assess", "配速": "assess", "评估": "assess",
    "建议": "advise", "提高": "advise", "改善": "advise", "推荐": "advise",
    "计划": "plan", "安排": "plan", "训练表": "plan",
    "进度": "track", "完成": "track", "打卡": "track",
}


def _get_cached_profile():
    """获取缓存的跑者画像，30 秒内复用。"""
    now = datetime.now().timestamp()
    with _cache_lock:
        if _profile_cache["data"] is not None and (now - _profile_cache["timestamp"]) < _PROFILE_CACHE_TTL:
            return _profile_cache["data"]
    try:
        profile = data_tool.build_runner_profile()
    except Exception:
        profile = {}
    with _cache_lock:
        _profile_cache["data"] = profile
        _profile_cache["timestamp"] = now
    return profile


def _get_cached_plan(data_dir):
    """获取缓存的训练计划，按 mtime 判是否需要重新加载。"""
    plan_path = os.path.join(data_dir, "plan.json")
    with _cache_lock:
        if not os.path.isfile(plan_path):
            _plan_cache["data"] = None
            _plan_cache["mtime"] = 0
            return None
        try:
            mtime = os.path.getmtime(plan_path)
        except OSError:
            _plan_cache["data"] = None
            _plan_cache["mtime"] = 0
            return None
        if _plan_cache["data"] is not None and _plan_cache["mtime"] == mtime:
            return _plan_cache["data"]
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                _plan_cache["data"] = json.load(f)
            _plan_cache["mtime"] = mtime
        except (json.JSONDecodeError, OSError):
            _plan_cache["data"] = None
            _plan_cache["mtime"] = 0
        return _plan_cache["data"]


def _quick_intent_check(message):
    """快速关键词意图检查，命中则跳过 LLM classify_intent 调用。
    返回 intent 字符串或 None（未命中，需走 LLM）。"""
    msg_lower = message.strip().lower()
    for keyword, intent in _QUICK_INTENT_MAP.items():
        if keyword in msg_lower:
            return intent
    return None


def _chat_history_path(data_dir):
    return os.path.join(data_dir, "chat_history.json")


def _load_chat_history(data_dir):
    """加载对话历史，返回列表 [{role, content, time}, ...]"""
    path = _chat_history_path(data_dir)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_chat_history(data_dir, history):
    """原子写入对话历史。"""
    path = _chat_history_path(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=data_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.isfile(tmp_path):
            os.unlink(tmp_path)
        raise


def _build_history_summary(history, rounds=CONTEXT_ROUNDS):
    """将最近 N 轮对话历史压缩为摘要文本，节省 Token。

    格式：
    【对话历史】
    Q1: 用户问题
    A1: 教练回答（截断至 30 字）
    Q2: ...
    """
    recent = history[-rounds:] if len(history) > rounds else history
    if not recent:
        return ""

    lines = ["【对话历史】"]
    q_count = 0
    for entry in recent:
        role = entry.get("role")
        content = entry.get("content", "") or ""
        if role == "user":
            q_count += 1
            lines.append(f"Q{q_count}: {content[:60]}")
        elif role == "assistant":
            lines.append(f"A{q_count}: {content[:30]}")
    return "\n".join(lines)


@coach_bp.route("/api/coach/chat", methods=["POST"])
def api_coach_chat():
    """AI 教练对话端点（SSE 流式响应 + 对话记忆）。"""
    data = request.get_json()
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"reply": "请告诉我你的问题。"})

    data_dir = current_app.config["DATA_DIR"]

    # 加载对话历史
    history = _load_chat_history(data_dir)
    history_summary = _build_history_summary(history)

    # ---- 工作流状态机 ----
    current_state = session.get("workflow_state", workflow.get_default_state())
    # 快速关键词匹配优先，仅未命中时调 LLM（节省 ~1s 延迟）
    intent = _quick_intent_check(message)
    if intent is None:
        intent = llm_agent.classify_intent(message, history_summary, current_state=current_state)
    next_state = workflow.get_next_state(current_state, intent) if intent else current_state
    session["workflow_state"] = next_state

    # ---- 长期记忆（session 缓存，避免每次对话读磁盘） ----
    user_profile = session.get("memory_profile")
    if user_profile is None:
        user_profile = memory.load_long_term_memory()
        session["memory_profile"] = user_profile
    memory_context = memory.format_memory_context(user_profile)

    # 构建跑者画像（使用缓存）
    profile = _get_cached_profile()

    # 加载当前计划（使用 mtime 缓存，避免每次对话读磁盘）
    plan_data = _get_cached_plan(data_dir)
    plan_text = json.dumps(plan_data.get("plan", []), ensure_ascii=False, indent=2) if plan_data and plan_data.get("plan") else ""

    # 计算训练科学指标
    recent_runs = profile.get("近期跑步数据", []) if profile else []
    resting_hr = (profile.get("健康指标") or {}).get("静息心率") if profile else None
    sleep_hours = (profile.get("健康指标") or {}).get("睡眠时长_小时") if profile else None
    max_hr = None
    for run in recent_runs:
        mhr = run.get("最大心率")
        if mhr:
            max_hr = int(mhr)
            break

    ts_summary = ts.calc_training_summary(
        recent_runs,
        resting_hr=int(resting_hr) if resting_hr else None,
        max_hr=max_hr,
        sleep_hours=float(sleep_hours) if sleep_hours else None,
    ) if recent_runs else {}

    system_prompt = load_prompt("coach_chat_system")
    state_prompt = workflow.get_state_prompt(next_state)

    # 动态组装 system prompt
    system_prompt = system_prompt + "\n\n" + state_prompt
    if memory_context:
        system_prompt = system_prompt + "\n\n" + memory_context

    ts_context = ""
    if ts_summary:
        ts_context = f"""
【训练科学指标】
- 训练负荷：{ts_summary.get('training_load', '-')}%（基于 TRIMP 模型）
- ACWR（负荷比）：{ts_summary.get('acwr', '-')}（{ts_summary.get('acwr_risk', {}).get('level', '-')}）
- 就绪度：{ts_summary.get('readiness', '-')}
- 周 TRIMP：{ts_summary.get('weekly_trimp', '-')}
"""

    # 计划执行情况
    plan_exec_summary = ""
    if plan_data and plan_data.get("plan"):
        try:
            plan_list = plan_data.get("plan", [])
            non_rest = [d for d in plan_list if d.get("type") != "休息"]
            completed = [d for d in non_rest if d.get("completed")]
            total = len(non_rest)
            done = len(completed)

            saved_at = plan_data.get("meta", {}).get("saved_at") or plan_data.get("meta", {}).get("generated_at", "")
            deviations = []
            recent_details = []
            if saved_at and recent_runs:
                plan_start = datetime.fromisoformat(saved_at).date()
                # 构建日期索引：{date: run_dict}，O(n) 替代原来的 O(n*m) 嵌套循环
                run_by_date = {}
                for run in recent_runs:
                    run_date_str = run.get("日期")
                    if run_date_str:
                        try:
                            run_date = datetime.strptime(run_date_str, "%Y-%m-%d").date()
                            run_by_date[run_date] = run
                        except (ValueError, TypeError):
                            pass

                for d in completed:
                    day_num = d.get("day")
                    target_date = plan_start + timedelta(days=day_num - 1)
                    run = run_by_date.get(target_date)
                    if run is None:
                        continue
                    actual_pace = run.get("平均配速_秒每公里")
                    target_pace_str = d.get("pace", "")
                    target_pace = None
                    if target_pace_str:
                        m = re.match(r"(\d+)[:'：](\d+)", target_pace_str.strip())
                        if m:
                            target_pace = int(m.group(1)) * 60 + int(m.group(2))
                        else:
                            try:
                                target_pace = int(target_pace_str) * 60
                            except ValueError:
                                pass
                    if actual_pace is not None and target_pace is not None:
                        deviations.append(actual_pace - target_pace)
                    recent_details.append({
                        "day": day_num, "type": d.get("type"),
                        "date": target_date.isoformat(),
                        "actual_pace": actual_pace,
                        "distance": run.get("距离_km"),
                    })

            avg_dev = round(sum(deviations) / len(deviations)) if deviations else None

            plan_exec_summary = "\n【计划执行情况】\n"
            plan_exec_summary += f"- 已完成 {done}/{total} 天训练日\n"
            if avg_dev is not None:
                sign = "+" if avg_dev > 0 else ""
                plan_exec_summary += f"- 平均配速偏差：{sign}{avg_dev}s/km\n"
            else:
                plan_exec_summary += "- 平均配速偏差：无数据\n"
            if recent_details:
                plan_exec_summary += "- 最近完成的训练日：\n"
                for rd in recent_details[-3:]:
                    pace_str = ""
                    if rd["actual_pace"] is not None:
                        s = int(rd["actual_pace"])
                        pace_str = f"{s//60}'{s%60:02d}\""
                    else:
                        pace_str = "-"
                    plan_exec_summary += f"  Day{rd['day']} {rd['type']} ({rd['date']}): 配速 {pace_str}\n"
        except Exception:
            pass

    context = f"""【跑者数据】
{json.dumps(profile, ensure_ascii=False, indent=2)}

【当前训练计划】
{plan_text if plan_text else "暂无训练计划"}
{ts_context}
{plan_exec_summary}
{history_summary}

【跑者问题】
{message}"""

    # RAG 检索：从知识库获取相关运动科学知识
    try:
        rag_results = rag.retrieve_knowledge(message, top_k=3)
        if rag_results:
            rag_context = rag.format_knowledge_context(rag_results)
            if rag_context:
                context = context + "\n\n" + rag_context
    except Exception as e:
        print(f"[RAG] 检索失败，跳过知识增强：{e}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context},
    ]

    # 收集流式响应（用于保存到历史）
    full_reply = []

    def generate():
        nonlocal full_reply
        max_tool_rounds = 2
        current_messages = messages  # 使用外部构建的 messages

        for tool_round in range(max_tool_rounds):
            full_reply = []
            try:
                # 流式调用 LLM（仅第一轮流式输出，后续轮非流式）
                if tool_round == 0:
                    for chunk in llm_agent.call_deepseek_stream(current_messages):
                        full_reply.append(chunk)
                        yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                else:
                    # 后续轮：非流式，但也要以 chunk 形式输出
                    result = llm_agent.call_deepseek(current_messages, temperature=0.7)
                    reply_text = result.get("content", "") if isinstance(result, dict) else ""
                    full_reply.append(reply_text)
                    yield f"data: {json.dumps({'chunk': reply_text})}\n\n"

                reply_text = "".join(full_reply)

                # 检查 TOOL 标记
                tool_pattern = re.compile(r'\[TOOL:(\w+)\](.*?)\[/TOOL\]', re.DOTALL)
                tool_match = tool_pattern.search(reply_text)

                if not tool_match:
                    # 没有 TOOL 标记，正常结束
                    break

                # 有 TOOL 标记，执行 Skill
                skill_name = tool_match.group(1)
                skill_args_str = tool_match.group(2).strip()

                # 解析 args（JSON 格式）
                try:
                    skill_args = json.loads(skill_args_str)
                except json.JSONDecodeError:
                    skill_args = {"question": skill_args_str} if skill_name == "data_query" else {"chart_type": "pace", "data_context": skill_args_str}

                # 执行 Skill
                skill_result = skills.tool_call(skill_name, skill_args)
                skill_result_str = json.dumps(skill_result, ensure_ascii=False)

                # 将 Skill 结果追加到对话上下文
                current_messages = list(current_messages)  # 复制
                current_messages.append({"role": "assistant", "content": reply_text})
                current_messages.append({"role": "user", "content": f"[Skill 执行结果]\n{skill_result_str}\n\n请基于以上结果重新生成回答。"})

                # 继续下一轮循环

            except APIConnectionError:
                yield f"data: {json.dumps({'error': '无法连接到 DeepSeek API。'})}\n\n"
                return
            except AuthenticationError:
                yield f"data: {json.dumps({'error': 'DeepSeek API 密钥无效或已过期。'})}\n\n"
                return
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                return

        # 保存本轮对话到历史
        now = datetime.now().isoformat()
        reply_text = "".join(full_reply)
        history.append({"role": "user", "content": message, "time": now})
        history.append({"role": "assistant", "content": reply_text, "time": now})
        # 只保留最近 MAX_HISTORY_ROUNDS 轮（每轮 = user + assistant）
        max_entries = MAX_HISTORY_ROUNDS * 2
        if len(history) > max_entries:
            history[:] = history[-max_entries:]
        try:
            _save_chat_history(data_dir, history)
        except Exception:
            pass  # 保存失败不阻塞响应

        # 更新长期记忆
        try:
            memory.update_long_term_memory(message, reply_text)
            # 刷新 session 中的记忆缓存，下次请求会重新加载
            session["memory_profile"] = memory.load_long_term_memory()
        except Exception as e:
            print(f"[memory] 更新长期记忆失败：{e}", flush=True)

        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )