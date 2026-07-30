"""
DeepSeek LLM 调用封装模块
============================================================
本模块封装 DeepSeek LLM 调用，承载闭环中的"推理"环节：
根据跑者真实数据生成科学、安全的训练计划。

模型：
    deepseek-chat  → 生成训练计划（推理 + 行动），支持 JSON 结构化输出

核心函数：
    call_deepseek()              底层调用封装
    generate_training_plan()     生成训练计划（含科学增强）
============================================================
"""

import os
import re
import json
import time
import threading

from dotenv import load_dotenv
from openai import OpenAI

# Phase 1：确定性生理内核与科学增强层（纯标准库，无副作用）
import physiology
import plan_science


# ============================================================
# 计划文本解析正则（与 visualize.py 共享逻辑）
# ============================================================

# 天数行：`第 X 天（周X）｜训练类型`
_DAY_RE = re.compile(r"第\s*(\d+)\s*天[（(]\s*周(.)\s*[）)]\s*[|｜]\s*(.+)")
# 配速行：`目标配速：X'XX"/km`
_PACE_RE = re.compile(r'目标配速[：:]\s*(\d+)[\'＇′’]\s*(\d+)["＂″”]')
# 距离行：`距离：X 公里`
_DIST_RE = re.compile(r'距离[：:]\s*([\d.]+)\s*公?里')
# 时长行：`预计时长：XX 分钟`
_DURATION_RE = re.compile(r'预计时长[：:]\s*([\d.]+)\s*分钟')
# 心率区间行：`心率区间：区间描述`
_HR_ZONE_RE = re.compile(r'心率区间[：:]\s*(.+)')
# 说明行：`说明：...`
_NOTE_RE = re.compile(r'说明[：:]\s*(.+)')


# ============================================================
# Prompt 加载与模板渲染
# ============================================================

# prompts 目录路径
_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

# Prompt 内存缓存：{文件名: 内容}
_prompt_cache = {}
# Prompt 缓存读写锁（threaded=True 下多线程并发访问）
_prompt_lock = threading.Lock()


def load_prompt(name, **kwargs):
    """从 prompts/ 目录加载 Markdown 文件，并用 kwargs 替换 {{变量}} 占位符。

    参数：
        name:    prompt 文件名（不含 .md 扩展名），如 "plan_generator_system"
        **kwargs: 模板变量，如 plan_duration=7, goal_desc="为半马训练"

    返回：
        str，渲染后的 prompt 文本

    示例：
        load_prompt("plan_generator_system", plan_duration=7, goal_desc="", extra_requirements="")
    """
    if name not in _prompt_cache:
        with _prompt_lock:
            if name not in _prompt_cache:
                filepath = os.path.join(_PROMPTS_DIR, f"{name}.md")
                if not os.path.isfile(filepath):
                    raise FileNotFoundError(f"Prompt 文件不存在：{filepath}")
                with open(filepath, "r", encoding="utf-8") as f:
                    _prompt_cache[name] = f.read()

    template = _prompt_cache[name]

    # 替换 {{variable}} 占位符
    for key, value in kwargs.items():
        template = template.replace("{{" + key + "}}", str(value) if value is not None else "")

    return template


def classify_intent(message, history_summary="", current_state=None):
    """
    使用 LLM 分类用户意图，决定状态机转换。

    参数：
        message:         用户消息
        history_summary: 对话历史摘要（可选，用于上下文中分类）
        current_state:   当前状态机状态（可选，用于智能降级）

    返回：
        str: "chat" / "assess" / "advise" / "plan" / "track"
        分类失败时，若 current_state 有效则返回 current_state，否则返回 "chat"
    """
    if not message or not message.strip():
        return None

    system_prompt = load_prompt("intent_classifier_system")

    # 构建用户消息
    user_msg = f"用户消息：{message.strip()}"
    if current_state:
        user_msg = f"当前状态：{current_state}\n\n{user_msg}"
    if history_summary:
        user_msg = f"对话历史：{history_summary}\n\n{user_msg}"

    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]
        result = call_deepseek(messages, temperature=0.0)
        reply = result.get("content", "") if isinstance(result, dict) else ""
    except Exception as e:
        print(f"[classify_intent] LLM 调用失败：{e}")
        return None

    if not reply:
        return None

    # 解析结果
    intent = reply.strip().lower()
    valid_intents = {"chat", "assess", "advise", "plan", "track"}

    # 尝试提取第一个有效意图词
    for word in intent.split():
        word = word.strip(".,;:!?\"'")
        if word in valid_intents:
            return word

    # 智能降级：LLM 分类失败时，优先保持当前状态，否则默认进入聊天
    if current_state:
        return current_state
    return "chat"


# ============================================================
# 底层调用封装
# ============================================================

# DeepSeek API 基础地址（兼容 OpenAI SDK 协议）
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 模型名称常量
# 默认升级到 DeepSeek V4 系列；可用环境变量 DEEPSEEK_MODEL_CHAT / DEEPSEEK_MODEL_REASONER 覆盖。
# 2026-07：原 deepseek-chat / deepseek-reasoner 已被平台下线，仅接受 deepseek-v4-flash / deepseek-v4-pro。
MODEL_CHAT = os.getenv("DEEPSEEK_MODEL_CHAT", "deepseek-v4-flash")      # 生成初稿：快、稳、避开偶发超时
MODEL_REASONER = os.getenv("DEEPSEEK_MODEL_REASONER", "deepseek-v4-pro")  # 反思/自检：旗舰，用用户确认的 PRO


_dotenv_loaded = False
_client = None  # OpenAI 客户端单例，避免每次请求重建


def _get_client():
    """返回复用型 DeepSeek API 客户端（绕过系统代理）。首次调用时创建并缓存。"""
    global _dotenv_loaded, _client
    if not _dotenv_loaded:
        load_dotenv()
        _dotenv_loaded = True
    if _client is not None:
        return _client
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError(
            "未找到 DeepSeek API Key。请在 .env 文件中填写 DEEPSEEK_API_KEY。\n"
            "可以在 https://platform.deepseek.com 申请（新用户有免费额度）。"
        )
    # 清除代理环境变量（TRAE IDE 的代理会干扰 DeepSeek API 直连）
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(k, None)
    _client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL,
                     timeout=120.0, max_retries=0)
    return _client


def call_deepseek(messages, model=MODEL_CHAT, temperature=0.7, max_retries=3, response_format=None):
    """
    通用 DeepSeek 调用封装（含指数退避重试）。

    参数：
        messages:    消息列表，格式同 OpenAI Chat Completions
        model:       模型名称（deepseek-chat 或 deepseek-reasoner）
        temperature: 采样温度（注意：reasoner 模型不支持此参数，会自动忽略）
        max_retries: 最大重试次数（默认 3）

    返回：
        dict，包含：
        {
            "content": "模型的最终回答",
            "reasoning": "思维链内容（仅 reasoner 有，chat 模型为 None）",
            "usage": {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
        }
    """
    client = _get_client()

    # 构建调用参数
    kwargs = {
        "model": model,
        "messages": messages,
    }

    if model == MODEL_CHAT and temperature is not None:
        kwargs["temperature"] = temperature
    # Phase 3：结构化输出（JSON Mode）透传；默认 None 不影响现有调用
    if response_format is not None:
        kwargs["response_format"] = response_format

    last_error = None
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(**kwargs)
            message = response.choices[0].message

            reasoning = getattr(message, "reasoning_content", None)
            content = message.content

            # 提取 Token 用量
            usage = {}
            if hasattr(response, "usage") and response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens or 0,
                    "completion_tokens": response.usage.completion_tokens or 0,
                    "total_tokens": response.usage.total_tokens or 0,
                }

            return {
                "content": content,
                "reasoning": reasoning,
                "usage": usage,
            }
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt  # 指数退避：1s, 2s, 4s
                print(f"  [重试] API 调用失败（{e}），{wait}s 后重试（第 {attempt + 1}/{max_retries} 次）...")
                time.sleep(wait)
            else:
                print(f"  [失败] API 调用最终失败（{e}），已重试 {max_retries} 次")

    raise last_error


def call_deepseek_stream(messages, model=MODEL_CHAT, temperature=0.7):
    """
    DeepSeek 流式调用封装（用于 SSE 推送）。

    参数：
        messages:    消息列表
        model:       模型名称
        temperature: 采样温度

    返回：
        generator，逐块 yield 文本内容字符串
    """
    client = _get_client()

    kwargs = {
        "model": model,
        "messages": messages,
        "stream": True,
    }

    if model == MODEL_CHAT and temperature is not None:
        kwargs["temperature"] = temperature

    stream = client.chat.completions.create(**kwargs)
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content


# ============================================================
# 生成训练计划（推理 + 行动）
# ============================================================

def _parse_plan_to_structured(plan_text):
    """将 LLM 生成的训练计划文本解析为结构化 JSON 数组。

    这是唯一的计划文本→结构化数据解析入口，供后端和前端统一使用。

    参数：
        plan_text: LLM 输出的训练计划文本

    返回：
        list[dict]，每项包含：
            day, weekday, type, distance_km, target_pace, target_pace_sec,
            duration_min, heart_rate_zone, note, is_rest,
            completed, actual_distance_km, actual_pace_sec, actual_duration_min
    """
    if not plan_text:
        return []

    results = []
    day_matches = list(_DAY_RE.finditer(plan_text))

    for i, m in enumerate(day_matches):
        day_num = int(m.group(1))
        weekday = m.group(2).strip()
        train_type = m.group(3).strip()
        is_rest = "休息" in train_type

        # 切分当前天对应的文本块
        block_start = m.end()
        block_end = day_matches[i + 1].start() if i + 1 < len(day_matches) else len(plan_text)
        block = plan_text[block_start:block_end]

        pace_sec = None
        pace_str = None
        dist_km = None
        duration_min = None
        hr_zone = None
        note = None

        if not is_rest:
            pace_m = _PACE_RE.search(block)
            if pace_m:
                minutes = int(pace_m.group(1))
                seconds = int(pace_m.group(2))
                pace_sec = minutes * 60 + seconds
                pace_str = f"{minutes}'{seconds:02d}\""

            dist_m = _DIST_RE.search(block)
            if dist_m:
                dist_km = float(dist_m.group(1))

            dur_m = _DURATION_RE.search(block)
            if dur_m:
                duration_min = float(dur_m.group(1))

            hr_m = _HR_ZONE_RE.search(block)
            if hr_m:
                hr_zone = hr_m.group(1).strip()

            note_m = _NOTE_RE.search(block)
            if note_m:
                note = note_m.group(1).strip()

        results.append({
            "day": day_num,
            "weekday": weekday,
            "type": train_type,
            "distance_km": dist_km,
            "target_pace": pace_str,
            "target_pace_sec": pace_sec,
            "duration_min": duration_min,
            "heart_rate_zone": hr_zone,
            "note": note,
            "is_rest": is_rest,
            "completed": False,
            "actual_distance_km": None,
            "actual_pace_sec": None,
            "actual_duration_min": None,
        })

    return results


# ============================================================
# 解析增强（Phase 1）：JSON 优先 + 正则兜底
# ============================================================

def _extract_json_block(text):
    """从 LLM 输出中抽取 JSON（支持 ```json 围栏 或裸 JSON 数组）。失败返回 None。"""
    if not text:
        return None
    t = text.strip()
    # 去掉 ```json ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    # 直接解析
    try:
        return json.loads(t)
    except (json.JSONDecodeError, ValueError):
        pass
    # 退一步：截取首个 [ 到最后的 ]（数组）
    start, end = t.find("["), t.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _map_json_item(raw, idx):
    """将单个 JSON 计划项映射为标准结构化 schema（兼容中英文键名）。"""
    if not isinstance(raw, dict):
        return None

    def get_any(*keys):
        low = {k.lower(): k for k in raw.keys()}
        for k in keys:
            if k.lower() in low:
                return raw[low[k.lower()]]
        return None

    day = get_any("day", "天", "天数")
    weekday = get_any("weekday", "周", "星期")
    wtype = get_any("type", "训练类型", "类型") or "休息"
    dist = get_any("distance", "distance_km", "距离", "距离_km")
    pace_raw = get_any("target_pace", "pace", "目标配速", "配速")
    pace_sec = get_any("target_pace_sec", "目标配速秒")
    duration = get_any("duration", "duration_min", "预计时长", "时长")
    hr_zone = get_any("heart_rate_zone", "心率区间", "心率")
    note = get_any("note", "说明")

    if pace_sec is None and pace_raw is not None:
        pace_sec = physiology.pace_str_to_sec(pace_raw)
    try:
        pace_sec = int(pace_sec) if pace_sec is not None else None
    except (ValueError, TypeError):
        pace_sec = None
    try:
        dist = float(dist) if dist is not None else None
    except (ValueError, TypeError):
        dist = None
    try:
        duration = float(duration) if duration is not None else None
    except (ValueError, TypeError):
        duration = None

    is_rest = ("休息" in str(wtype)) or bool(get_any("is_rest"))

    return {
        "day": int(day) if day is not None else idx + 1,
        "weekday": weekday or "",
        "type": wtype,
        "distance_km": dist,
        "target_pace": physiology.sec_to_pace_str(pace_sec) if pace_sec else None,
        "target_pace_sec": pace_sec,
        "duration_min": duration,
        "heart_rate_zone": hr_zone,
        "note": note,
        "is_rest": is_rest,
        "completed": False,
        "actual_distance_km": None,
        "actual_pace_sec": None,
        "actual_duration_min": None,
    }


def _days_to_text(days):
    """将结构化 days 渲染为人类可读的训练计划文本。

    用于 LLM 返回 JSON 但缺少 summary 字段时的安全兜底，
    确保前端拿到的 plan_text 永远不是裸 JSON。
    """
    if not days:
        return ""
    lines = []
    for d in days:
        if d.get("is_rest"):
            lines.append(f"第{d.get('day')}天（周{d.get('weekday','')}）｜休息日")
            continue
        t = d.get("type") or ""
        line = f"第{d.get('day')}天（周{d.get('weekday','')}）｜{t}"
        if d.get("distance_km"):
            line += f"\n距离：{d.get('distance_km')} 公里"
        if d.get("target_pace"):
            line += f"\n目标配速：{d.get('target_pace')}/km"
        if d.get("duration_min"):
            line += f"\n预计时长：{d.get('duration_min')} 分钟"
        if d.get("heart_rate_zone"):
            line += f"\n心率区间：{d.get('heart_rate_zone')}"
        if d.get("note"):
            line += f"\n说明：{d.get('note')}"
        lines.append(line)
    return "\n\n".join(lines)


def _parse_plan_full(plan_text):
    """计划文本 → {'days': [...], 'summary': str|None}。

    Phase 1/3 结构化输出策略：
      1. 优先尝试 JSON（LLM 用 response_format 返回结构化对象）：
         - days/计划/items/日程 数组 → 结构化列表
         - summary/plan_text/叙述 字段 → 人类可读叙述（供前端展示）
      2. 失败则回退到原有正则解析 `_parse_plan_to_structured`（兼容纯文本）。

    全程异常安全：任何解析异常都回退到正则，绝不抛出。
    """
    # 1) JSON 优先
    try:
        data = _extract_json_block(plan_text)
        if isinstance(data, dict):
            days_src = (data.get("days") or data.get("计划")
                        or data.get("items") or data.get("日程"))
            summary = (data.get("summary") or data.get("plan_text")
                       or data.get("叙述") or data.get("计划正文"))
            if isinstance(days_src, list) and days_src:
                items = [m for m in (_map_json_item(r, i)
                          for i, r in enumerate(days_src)) if m]
                if items:
                    return {
                        "days": items,
                        "summary": summary if isinstance(summary, str) and summary.strip() else None,
                    }
        elif isinstance(data, list):
            items = [m for m in (_map_json_item(r, i)
                      for i, r in enumerate(data)) if m]
            if items:
                return {"days": items, "summary": None}
    except Exception:
        pass
    # 2) 正则兜底
    return {"days": _parse_plan_to_structured(plan_text), "summary": None}


def parse_plan_to_structured(plan_text):
    """计划文本 → 结构化列表（向后兼容，仅供需要纯列表的调用方）。

    若要同时拿到人类可读叙述，请使用 `_parse_plan_full`。
    """
    return _parse_plan_full(plan_text)["days"]


def _postprocess_plan(plan_text):
    """解析 LLM 输出：JSON 优先 + 正则兜底，并合成人类可读 plan_text。"""
    parsed = _parse_plan_full(plan_text)
    structured = parsed["days"]
    if parsed.get("summary"):
        pt = parsed["summary"]
    elif structured:
        pt = _days_to_text(structured)
    else:
        pt = plan_text
    return pt, structured


def _format_feedback(missing):
    """把结构化自检缺失项转换为精确中文反馈，供重试回灌到 prompt。"""
    parts = []
    for m in missing:
        if not isinstance(m, dict):
            parts.append(str(m))
            continue
        aspect = m.get("aspect")
        if aspect == "weekday_distance":
            wd = m.get("weekday", "")
            exp = m.get("expected_km")
            act = m.get("actual_km")
            wk = m.get("week")
            if wk:
                parts.append(f"第{wk}周周{wd}应为{exp}km，实际{act}km")
            else:
                parts.append(f"周{wd}应为{exp}km，实际{act}km")
        elif aspect == "weekly_count":
            parts.append(f"每周应跑{m.get('expected')}次，实际生成了{m.get('actual')}次跑步日")
        elif aspect == "weekday_type":
            parts.append(f"周{m.get('weekday')}应为{m.get('expected')}类型，实际{m.get('actual')}")
        else:
            parts.append(m.get("detail") or str(m))
    return parts


def self_check_instructions(user_instructions, structured):
    """用 reasoner 核对生成计划是否覆盖用户每条指令（reflection-pattern 自检）。

    异常安全：任何失败都返回 covered=True（不阻断主流程），仅标记 self_check_error。
    """
    if not user_instructions or not structured:
        return {"covered": True, "missing": [], "analysis": "无指令或计划为空，跳过自检",
                "self_check_error": None}
    lines = []
    for d in structured:
        wd = d.get("weekday", "")
        t = d.get("type", "")
        dist = d.get("distance_km")
        if d.get("is_rest"):
            lines.append(f"周{wd}：休息")
        else:
            lines.append(f"周{wd}：{t} {dist}km" if dist else f"周{wd}：{t}")
    plan_summary = "\n".join(lines)
    try:
        system = load_prompt("plan_selfcheck_system")
    except Exception:
        system = '你是训练计划指令遵循审查员。核对用户指令是否被计划落实，输出 JSON：{"covered":bool,"missing":[str],"analysis":str}'
    user_msg = f"""【用户的明确指令】
{user_instructions}

【训练计划结构化摘要】
{plan_summary}

请严格按 JSON 格式输出核对结论。"""
    try:
        resp = call_deepseek(
            [{"role": "system", "content": system},
             {"role": "user", "content": user_msg}],
            model=MODEL_REASONER,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        data = _extract_json_block(resp["content"])
        if isinstance(data, dict):
            covered = bool(data.get("covered", True))
            missing_raw = data.get("missing", []) or []
            if not isinstance(missing_raw, list):
                missing_raw = [missing_raw]
            missing = []
            for m in missing_raw:
                if isinstance(m, dict):
                    missing.append(m)
                elif isinstance(m, str):
                    missing.append({"aspect": "other", "detail": m})
                else:
                    missing.append({"aspect": "other", "detail": str(m)})
            return {"covered": covered, "missing": missing,
                    "analysis": str(data.get("analysis", "")),
                    "self_check_error": None}
    except Exception as e:
        return {"covered": True, "missing": [], "analysis": f"自检调用失败，跳过:{e}",
                "self_check_error": str(e)}
    return {"covered": True, "missing": [], "analysis": "自检未返回有效 JSON，跳过",
            "self_check_error": "bad_json"}


def generate_training_plan(runner_profile, plan_duration=7, user_instructions="", self_check=True):
    """
    根据跑者画像生成训练计划。

    这是闭环中的"推理→行动"环节：
    AI 拿到工具使用模式取回的真实数据，思考后输出一份计划。

    参数：
        runner_profile:    跑者画像字典（由 data_tool.build_runner_profile 生成）
        plan_duration:     计划天数（7/30/90），默认 7
        user_instructions: 用户的明确指令/命令（如"每周跑3次、周日长跑20km"）。
                            作为一等输入注入 prompt，LLM 必须严格落实；
                            科学约束仅作安全上限，不得以此覆盖用户指令。

    返回：
        dict: {"plan_text": str, "structured": list[dict], "science": dict}
    """
    # 目标比赛信息
    target = runner_profile.get("目标", {}) or {}
    target_race = target.get("目标比赛", "")
    target_time = target.get("目标完赛时间", "")
    target_dist = target.get("目标距离", "")

    # 构建目标描述
    goal_desc = ""
    if target_race:
        goal_desc = f"跑者正在为 {target_race} 训练"
        if target_dist:
            goal_desc += f"（{target_dist}）"
        if target_time:
            goal_desc += f"，目标完赛时间 {target_time}"
        goal_desc += "。计划需适配该比赛距离。"
    elif target_time:
        goal_desc = f"跑者的目标完赛时间为 {target_time}。"

    # 30 天/90 天计划额外要求
    extra_requirements = ""
    if plan_duration >= 30:
        weeks = plan_duration // 7
        extra_requirements = f"""6. 计划按周分组，共 {weeks} 周
7. 每周至少包含 1 次长距离慢跑（LSD），逐步增加距离
8. 每 3-4 周安排 1 个减量恢复周（跑量降低 30-40%）
9. 第 1 周先建立基础，最后 1-2 周为比赛做准备（减量调整）"""

    # 从 prompts/ 加载系统提示
    system_prompt = load_prompt(
        "plan_generator_system",
        plan_duration=str(plan_duration),
        goal_desc=goal_desc,
        extra_requirements=extra_requirements,
    )

    # Phase 2：在生成前用确定性内核算"科学约束"，注入 prompt 指导 LLM（可降级）
    constraints = {}
    try:
        constraints = plan_science.build_constraints(runner_profile, plan_duration=plan_duration)
    except Exception:
        constraints = {}

    # 用户消息：把跑者画像 + 科学约束 + 用户明确指令作为上下文喂给 AI
    user_prompt = (
        f"请根据以下跑者的真实手表数据，生成未来 {plan_duration} 天的训练计划。\n\n"
        f"【跑者画像】\n{json.dumps(runner_profile, ensure_ascii=False, indent=2)}\n"
    )
    if user_instructions:
        user_prompt += (
            f"\n【用户明确指令 · 必须遵循】\n{user_instructions}\n"
            f"（以上为用户的个性化要求，必须在计划中严格落实；科学约束仅作为安全上限，"
            f"不得以此忽略或覆盖用户的明确指令。）\n"
        )
    if constraints.get("constraint_text"):
        user_prompt += f"\n{constraints['constraint_text']}\n"
    # Phase 3：要求 LLM 以结构化 JSON 对象返回（与 _parse_plan_full 的 JSON 分支对齐）
    json_format_instruction = (
        "\n\n【输出格式要求】请严格以单个 JSON 对象返回，不要包含任何额外说明文字。结构如下：\n"
        "{\n"
        '  "summary": "人类可读的训练计划叙述，例如：第1天（周一）轻松跑 5km，配速 6\'30\\"；第2天（周二）休息日。",\n'
        '  "days": [\n'
        "    {\n"
        '      "day": 1,\n'
        '      "weekday": "一",\n'
        '      "type": "轻松跑",\n'
        '      "distance_km": 5.0,\n'
        '      "target_pace": "6\'30\\"",\n'
        '      "duration_min": 33,\n'
        '      "heart_rate_zone": "Zone 2（有氧基础）",\n'
        '      "note": "热身 1km 后进入目标配速",\n'
        '      "is_rest": false\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "字段说明：\n"
        "- type 使用中文（轻松跑 / 间歇跑 / 节奏跑 / 长距离慢跑 / 休息日 等）；\n"
        '- target_pace 用 分\'秒" 格式（如 6\'30"）；\n'
        "- 休息日用 is_rest: true，distance_km / target_pace 可省略；\n"
        "- summary 必填，是一段连贯的人类可读叙述，前端会直接展示它。"
    )
    user_prompt += json_format_instruction
    user_prompt += "\n\n请基于以上真实数据、科学约束与【用户明确指令】，为这位跑者制定科学、安全、循序渐进且严格遵循用户指令的训练计划，并严格按上述 JSON 格式输出。"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    print(f"正在生成训练计划（{MODEL_CHAT}，结构化 JSON 输出）...")
    result = call_deepseek(
        messages,
        model=MODEL_CHAT,
        temperature=0.7,
        response_format={"type": "json_object"},
    )

    plan_text = result["content"]
    # Phase 1/3：结构化输出优先 + 正则兜底（全程异常安全）
    parsed = _parse_plan_full(plan_text)
    structured = parsed["days"]
    # plan_text 优先用 LLM 给出的人类可读叙述（summary），
    # 避免把裸 JSON 暴露给前端；summary 缺失时由 days 合成可读文本。
    if parsed.get("summary"):
        plan_text = parsed["summary"]
    elif structured:
        plan_text = _days_to_text(structured)

    # ---- 自检 + 一次重试（reflection-pattern）----
    sc = None
    if user_instructions and self_check:
        sc = self_check_instructions(user_instructions, structured)
        if sc and not sc.get("covered") and sc.get("missing"):
            feedback = "；".join(_format_feedback(sc["missing"]))
            print(f"  [自检未通过] 缺失：{feedback} —— 回灌重试一次")
            user_prompt2 = (user_prompt
                            + "【自检反馈 · 必须修正】上一次生成未落实："
                            + feedback
                            + "。请严格修正后重新输出。")
            messages2 = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt2},
            ]
            try:
                result2 = call_deepseek(
                    messages2, model=MODEL_CHAT, temperature=0.7,
                    response_format={"type": "json_object"})
                plan_text, structured = _postprocess_plan(result2["content"])
                # 重试后重新自检，用修正后的计划更新 sc（避免 sc 仍反映旧计划的未通过状态）
                sc = self_check_instructions(user_instructions, structured)
                sc["retried"] = True
            except Exception as e:
                sc["retried"] = False
                sc["retry_error"] = str(e)

    # Phase 1：确定性生理内核增量增强（可降级：任何异常都不影响主流程）
    science = {}
    try:
        science = plan_science.enrich_plan(structured, runner_profile)
    except Exception as e:
        science = {
            "status": "skipped",
            "warnings": [f"科学增强跳过：{type(e).__name__}: {e}"],
        }

    # Phase 2：把事前约束并入 science（纯增量，供前端展示与持久化；可降级）
    try:
        if constraints:
            science["constraints"] = constraints
    except Exception:
        pass

    return {"plan_text": plan_text, "structured": structured, "science": science, "self_check": sc}
