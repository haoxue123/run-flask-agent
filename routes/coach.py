"""教练对话路由 — SSE 流式响应 + 对话上下文记忆"""
import os
import json
import re
import threading
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, Response, stream_with_context, current_app, session

import data_tool
import safeio
import visualize
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

# 轻量回复分类：纯问候 / 寒暄 / 致谢，可走模板短路（跳过 RAG + LLM + profile 重算）
_GREET_TOKENS = ["你好", "您好", "hi", "hello", "hey", "在吗", "在么", "在不在",
                "嗨", "哈喽", "早安", "晚安", "早上好", "下午好", "晚上好",
                "好久不见", "好呀", "在的在的", "谢谢", "感谢", "多谢",
                "好的", "好嘞", "收到", "不客气", "辛苦了"]
# 领域 / 问题词：出现即视为真实问题，绝不当作纯问候
_GREET_DOMAIN_KW = ["配速", "心率", "距离", "训练", "计划", "伤", "痛", "比赛",
                   "建议", "分析", "数据", "跑", "公里", "km", "步频", "恢复",
                   "负荷", "目标", "饮食", "睡眠", "体重", "怎么", "为什么",
                   "多少", "如何", "能否", "可以", "帮我", "查询", "查一下"]


def _classify_lightweight(message):
    """判定是否为纯问候 / 寒暄 / 致谢（可走轻量模板）。

    边界规则（避免误判短问题）：
      1) 必须包含问候词；
      2) 不得包含任何领域 / 问题词（如"配速""帮我分析"）——否则视为真实问题；
      3) 长度上限 12 字，超长视为带内容的句子。
    返回模板字符串，或 None（非轻量，走正常链路）。
    """
    s = (message or "").strip()
    if not s:
        return None
    low = s.lower()
    if not any(g.lower() in low for g in _GREET_TOKENS):
        return None
    if any(k in s for k in _GREET_DOMAIN_KW):
        return None
    if len(s) > 12:
        return None
    if any(w in s for w in ["谢谢", "感谢", "多谢", "辛苦", "不客气", "好的", "收到", "好嘞"]):
        pool = [
            "不客气，随时找我聊训练就好 💪",
            "应该的～有任何跑步问题尽管说。",
            "收到！需要分析配速、计划或伤病时，直接把问题发我就行。",
        ]
    else:
        pool = [
            "你好，跑者！我是你的 AI 跑步教练 👟 想看训练计划、分析配速，还是聊聊最近的跑步数据？",
            "嗨，跑者！随时可以问我训练、配速、伤病或计划相关的问题。",
            "在的～今天想先定个小目标，还是回顾下最近的训练？",
        ]
    return pool[abs(hash(s)) % len(pool)]


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


# 生成动词：出现且语义为"要生成计划"时触发直接生成
_GEN_VERBS = ["生成", "制定", "做一", "出一个", "出个", "安排一", "给我", "帮我", "规划", "写一", "输出", "来一", "排一"]
_PLAN_WORDS = ["计划", "训练表", "训练安排", "排个", "课表"]


def _is_plan_generation_command(message):
    """判断用户消息是否为"直接生成计划"命令（而非仅查看/讨论计划）。

    触发条件（满足任一）：
      1) 含计划类词 + 生成动词（如"生成计划""帮我制定训练表"）；
      2) 含显式量化约束（"每周3次""周日20公里"）；
      3) 含具体训练类型要求（"长跑""间歇跑"）。
    否则视为普通计划讨论，走正常对话（STATE_PLAN 会引导去页面）。
    """
    s = message or ""
    if any(w in s for w in _PLAN_WORDS) and any(v in s for v in _GEN_VERBS):
        return True
    if re.search(r"每周\s*\d+\s*次", s):
        return True
    if re.search(r"周[一二三四五六日].*?(\d+)\s*(公里|km)", s):
        return True
    if "长跑" in s or "间歇跑" in s or "lsd" in s.lower():
        return True
    return False


def _infer_plan_duration(message):
    """从用户消息推断计划周期（天）。默认 7。"""
    if "月" in message:
        return 30
    if "季" in message or "90" in message or "三个月" in message:
        return 90
    return 7


def _stream_generated_plan(message, data_dir, history, start_epoch=None):
    """当用户给出明确计划指令时，直接调用生成并把结果流式返回。

    关键修复：把用户的原始命令作为 user_instructions 传入 generate_training_plan，
    使 LLM 严格落实用户指令，而非只按画像+科学约束生成（修复"命令不被听从"）。
    """
    full_reply = []

    def gen():
        nonlocal full_reply
        try:
            # mem0 跨会话长期记忆：计划生成命令路径也必须注入，
            # 否则教练出的计划读不到用户的旧伤/周跑量限制等长期记忆。
            # （计划生成只用 profile + instructions，不读聊天历史，故 mem0 是唯一来源）
            try:
                import uuid
                from mem0_store import get_all_memories, format_mem0_context

                _uid = session.get("mem0_uid") or session.setdefault(
                    "mem0_uid", uuid.uuid4().hex
                )
                # 出计划要的是用户『全部』长期记忆（伤病/限制/偏好），
                # 用 get_all 而非 query 检索，避免相关性阈值把关键事实过滤掉。
                _mems = get_all_memories(_uid, limit=20)
                _mem0_ctx = format_mem0_context(_mems)
            except Exception:
                _mem0_ctx = ""

            profile = _get_cached_profile()
            plan_duration = _infer_plan_duration(message)
            effective_instructions = message
            if _mem0_ctx:
                effective_instructions = message + "\n\n" + _mem0_ctx
            result = llm_agent.generate_training_plan(
                profile, plan_duration=plan_duration, user_instructions=effective_instructions
            )
            plan_text = result.get("plan_text", "")
            structured = result.get("structured", [])
            science = result.get("science", {})

            # --- 自动写入训练计划系统（与 /generate 路径对齐）---
            try:
                target = profile.get("目标", {}) or {}
                plan_path = os.path.join(data_dir, "plan.json")
                meta = {
                    "generated_at": datetime.now().isoformat(),
                    "plan_duration": plan_duration,
                    "target_race": target.get("目标比赛", ""),
                    "target_time": target.get("目标完赛时间", ""),
                    "source": "coach_chat",
                }
                plan_payload = {"plan": structured, "meta": meta, "science": science}
                safeio.safe_write_json(plan_path, plan_payload)
                # 生成配速图表
                chart_dir = current_app.config.get("CHART_DIR", os.path.join(data_dir, "charts"))
                chart_filename = f"pace_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                os.makedirs(chart_dir, exist_ok=True)
                chart_path = os.path.join(chart_dir, chart_filename)
                visualize.plot_pace_chart(structured, output_path=chart_path)
                chart_url = f"/chart/{chart_filename}"
            except Exception:
                chart_url = ""

            header = "📋 已根据你的指令生成训练计划：\n\n"
            yield f"data: {json.dumps({'chunk': header})}\n\n"
            full_reply.append(header)
            # 按句切分推送，体验接近打字机
            pieces = re.split(r'(?<=[。！？\n])', plan_text)
            for c in pieces:
                if c:
                    full_reply.append(c)
                    yield f"data: {json.dumps({'chunk': c})}\n\n"
            # 发送结构化计划数据供前端渲染为卡片（替代纯文本的土格式）
            if structured:
                yield f"data: {json.dumps({'plan_data': {'days': structured, 'chart_url': chart_url}})}\n\n"
            note = "\n\n✅ 训练计划已自动保存至「训练计划」页面，可直接前往查看和调整。"
            full_reply.append(note)
            yield f"data: {json.dumps({'chunk': note})}\n\n"
        except Exception as e:
            err = f"\n⚠️ 生成失败：{type(e).__name__}: {e}"
            full_reply.append(err)
            yield f"data: {json.dumps({'chunk': err})}\n\n"
        finally:
            # 与正常对话流程一致：保存历史 + 更新记忆
            now = datetime.now().isoformat()
            reply_text = "".join(full_reply)
            history.append({"role": "user", "content": message, "time": now})
            history.append({"role": "assistant", "content": reply_text, "time": now})
            max_entries = MAX_HISTORY_ROUNDS * 2
            if len(history) > max_entries:
                history[:] = history[-max_entries:]
            # 会话作废检查：点过"新对话"则跳过历史持久化（回复仍返回给用户）
            if start_epoch is None or _load_epoch(data_dir) == start_epoch:
                try:
                    _save_chat_history(data_dir, history)
                except Exception:
                    pass
                try:
                    memory.update_profile_summary_from_history(history)
                except Exception:
                    pass
            try:
                memory.update_long_term_memory(message, reply_text)
                session["memory_profile"] = memory.load_long_term_memory()
            except Exception:
                pass
            yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(gen()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    """稳健写入对话历史（去删除化：备份 + 截断式覆盖写 + 校验）。"""
    path = _chat_history_path(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    safeio.safe_write_json(path, history)


# ---------------------------------------------------------------------------
# 会话 epoch：用于"新对话"语义的原子作废标记
# 单进程单用户场景下，用 data/chat_epoch 文件存一个自增整数。
# "新对话"时递增 epoch 并清空历史；进行中的对话在收尾保存历史前比对 epoch，
# 若已变化则丢弃本次保存，彻底消除「清空 vs 流式回复收尾写回」的竞态复活。
# ---------------------------------------------------------------------------
def _chat_epoch_path(data_dir):
    return os.path.join(data_dir, "chat_epoch")


def _load_epoch(data_dir):
    path = _chat_epoch_path(data_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int((f.read() or "0").strip() or "0")
    except (OSError, ValueError):
        return 0


def _bump_epoch(data_dir):
    """递增会话 epoch（线程安全）。返回新的 epoch 值。"""
    with _cache_lock:
        ep = _load_epoch(data_dir) + 1
        try:
            os.makedirs(data_dir, exist_ok=True)
            with open(_chat_epoch_path(data_dir), "w", encoding="utf-8") as f:
                f.write(str(ep))
        except OSError:
            pass
        return ep


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


# ---------------------------------------------------------------------------
# 参考来源解析：把教练回复末尾的「参考来源」段落解析成结构化卡片数据
# ---------------------------------------------------------------------------
_REF_TAIL_RE = re.compile(r'[（(]([^（）]+?)[,，]\s*(\d{4})\s*[)）]\s*[·•]\s*(.+?\.md)\s*$')
_REF_SIMPLE_RE = re.compile(r'^《?(.+?)》?\s*[·•]\s*(.+?\.md)\s*$')


def _parse_references(text):
    """从教练回复中解析「参考来源」段落，返回结构化列表。

    支持两种格式：
      - 《书名》（作者，年份）· file.md   （书名自身可能含括号，如《运动营养学（Sport Nutrition）》）
      - 《标题》· file.md  （自编文档，无作者/年份）
    返回 [{"title", "author", "year", "file"}, ...]
    """
    if not text:
        return []
    idx = text.find('参考来源')
    if idx == -1:
        return []
    block = text[idx:]
    refs = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line.startswith('-'):
            continue
        item = line[1:].strip()
        m = _REF_TAIL_RE.search(item)
        if m:
            title = item[:m.start()].strip().strip('《》').strip()
            refs.append({
                "title": title,
                "author": m.group(1).strip(),
                "year": m.group(2).strip(),
                "file": m.group(3).strip(),
            })
            continue
        m = _REF_SIMPLE_RE.match(item)
        if m:
            refs.append({
                "title": m.group(1).strip().strip('《》').strip(),
                "author": None,
                "year": None,
                "file": m.group(2).strip(),
            })
    return refs


@coach_bp.route("/api/coach/chat", methods=["POST"])
def api_coach_chat():
    """AI 教练对话端点（SSE 流式响应 + 对话记忆）。"""
    data = request.get_json()
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"reply": "请告诉我你的问题。"})

    data_dir = current_app.config["DATA_DIR"]

    # mem0 跨会话长期记忆：本会话用户标识（首次访问生成，存 session）
    import uuid
    import threading
    from mem0_store import (
        search_memories,
        format_mem0_context,
        add_memories,
        delete_all_memories,
    )
    uid = session.get("mem0_uid") or session.setdefault("mem0_uid", uuid.uuid4().hex)

    # ---- 轻量问候短路：纯问候/寒暄/致谢直接模板回复，跳过 RAG + LLM + profile 重算 ----
    light = _classify_lightweight(message)
    if light is not None:
        def _gen_light():
            yield f"data: {json.dumps({'chunk': light})}\n\n"
            yield "data: [DONE]\n\n"
        return Response(
            stream_with_context(_gen_light()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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

    # ---- 计划生成命令拦截：用户给出明确指令时直接生成，而非仅引导去页面 ----
    # 修复"我让他做命令生成计划，但他不听从"：把用户命令作为一等输入传入生成
    if intent == "plan" and _is_plan_generation_command(message):
        return _stream_generated_plan(message, data_dir, history, _load_epoch(data_dir))

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

    system_prompt = load_prompt("coach_chat_system", TOOL_BLOCK=skills.render_tool_prompt())
    state_prompt = workflow.get_state_prompt(next_state)

    # 动态组装 system prompt
    system_prompt = system_prompt + "\n\n" + state_prompt
    if memory_context:
        system_prompt = system_prompt + "\n\n" + memory_context

    # mem0 跨会话长期记忆（自动抽取的用户事实/偏好），检索后注入 system prompt
    try:
        mem0_mems = search_memories(message, uid, limit=5)
        mem0_ctx = format_mem0_context(mem0_mems)
        if mem0_ctx:
            system_prompt = system_prompt + "\n\n" + mem0_ctx
    except Exception:
        pass

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

    # Path A+：因果/解释类问题额外注入靶向关系层（词法匹配，零 LLM 成本；事实类问题不变）
    try:
        import relation_layer
        if relation_layer.classify_intent_relation(message):
            rel_ctx = relation_layer.retrieve_relations(message)
            if rel_ctx:
                context = context + "\n\n" + rel_ctx
    except Exception:
        pass

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context},
    ]

    # 收集流式响应（用于保存到历史）
    full_reply = []

    def generate():
        nonlocal full_reply
        max_tool_rounds = 2
        # 捕获会话 epoch：本次对话收尾保存前会再次比对，变化则说明期间点过"新对话"
        _chat_start_epoch = _load_epoch(data_dir)
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

                # 解析并执行 Skill（解析逻辑内聚到各 skill，router 不感知具体参数）
                skill_result = skills.parse_and_call(skill_name, skill_args_str)
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
        references = _parse_references(reply_text)

        # 会话作废检查：若点过"新对话"，epoch 已变化，跳过历史持久化，
        # 避免竞态把旧对话写回（否则"新对话"后历史又复活）。
        if _load_epoch(data_dir) == _chat_start_epoch:
            history.append({"role": "user", "content": message, "time": now})
            history.append({
                "role": "assistant",
                "content": reply_text,
                "time": now,
                "references": references,
            })
            # 只保留最近 MAX_HISTORY_ROUNDS 轮（每轮 = user + assistant）
            max_entries = MAX_HISTORY_ROUNDS * 2
            if len(history) > max_entries:
                history[:] = history[-max_entries:]
            try:
                _save_chat_history(data_dir, history)
            except Exception:
                pass  # 保存失败不阻塞响应

            # 增量更新用户画像结构化摘要（随对话演进）
            try:
                memory.update_profile_summary_from_history(history)
            except Exception:
                pass  # 摘要更新失败不阻塞响应

        # 更新长期记忆（跨会话用户事实，即便开了新对话也保留）
        try:
            memory.update_long_term_memory(message, reply_text)
            # 刷新 session 中的记忆缓存，下次请求会重新加载
            session["memory_profile"] = memory.load_long_term_memory()
        except Exception as e:
            print(f"[memory] 更新长期记忆失败：{e}", flush=True)

        # mem0 跨会话长期记忆写入（后台抽取，不阻塞响应；尊重否定/撤销意图）
        try:
            neg = memory.detect_negation_intent(message)
            if neg["type"] == "REVoke_ALL":
                delete_all_memories(uid)
            elif neg["type"] != "REVoke_LAST":
                threading.Thread(
                    target=add_memories,
                    args=([{"role": "user", "content": message}, {"role": "assistant", "content": reply_text}], uid),
                    daemon=True,
                ).start()
        except Exception as e:
            print(f"[mem0] 写入失败：{e}", flush=True)

        # 解析「参考来源」并作为结构化事件下发（前端渲染为可点击卡片）
        try:
            if references:
                yield f"data: {json.dumps({'references': references}, ensure_ascii=False)}\n\n"
        except Exception:
            pass

        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@coach_bp.route("/api/coach/history", methods=["GET"])
def api_coach_history():
    """返回已持久化的对话历史，供前端在加载/刷新/切换标签后重水合。"""
    data_dir = current_app.config["DATA_DIR"]
    history = _load_chat_history(data_dir)
    return jsonify({"ok": True, "history": history})


@coach_bp.route("/api/coach/history/clear", methods=["POST"])
def api_coach_history_clear():
    """清空对话历史（开始新对话）。同时递增 epoch，作废进行中的旧对话保存。"""
    data_dir = current_app.config["DATA_DIR"]
    try:
        _save_chat_history(data_dir, [])
        _bump_epoch(data_dir)
    except Exception:
        return jsonify({"ok": False, "error": "清空失败"}), 500
    return jsonify({"ok": True})


@coach_bp.route("/api/coach/profile-summary", methods=["GET"])
def api_coach_profile_summary():
    """返回结构化用户画像摘要（标签 + 置信度 + 来源片段），供"查看我的记忆"等场景。"""
    data_dir = current_app.config["DATA_DIR"]
    # memory 模块按固定路径读写 data/profile_summary.json
    summary = memory.load_profile_summary()
    return jsonify({"ok": True, "summary": summary})