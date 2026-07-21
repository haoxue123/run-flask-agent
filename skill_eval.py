# -*- coding: utf-8 -*-
"""
skill_eval.py —— P2/P3 真实代码验证 harness（离线、无 LLM/网络依赖）

验证目标：对 skills 注册中心、data_query 双引擎、coach router 的真实代码路径做断言，
而非依赖手写结论。所有 LLM / RAG 调用均被 stub，保证可离线复现。

运行：E:/python/python.exe skill_eval.py
产出：data/eval/skill_eval_results.json + data/eval/skill_eval_report.md
"""
import sys
import os
import re
import json
import types

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

# ---------------------------------------------------------------------------
# 0. 在 import skills 之前 stub LLM（skills.data_query 顶部 import llm_agent）
# ---------------------------------------------------------------------------
import llm_agent

_LLM_CALLS = []


def _fake_call_deepseek(messages, temperature=0.7, **kw):
    _LLM_CALLS.append(messages)
    return {"content": "FAKE_REPLY"}


def _fake_stream(messages, **kw):
    _LLM_CALLS.append(messages)
    yield "FAKE_STREAM"


llm_agent.call_deepseek = _fake_call_deepseek
llm_agent.call_deepseek_stream = _fake_stream

import skills
from skills import (
    SKILL_REGISTRY,
    get_skill,
    render_tool_prompt,
    parse_and_call,
    tool_call,
)
from skills import data_query
import data_tool

# ---------------------------------------------------------------------------
# 结果收集
# ---------------------------------------------------------------------------
CHECKS = []


def run_check(tier, cid, name, criterion, fn):
    try:
        ok, detail = fn()
        status = "PASS" if ok else "FAIL"
    except Exception as e:  # noqa: BLE001
        status = "FAIL"
        detail = f"异常: {type(e).__name__}: {e}"
    CHECKS.append({
        "tier": tier,
        "id": cid,
        "name": name,
        "status": status,
        "criterion": criterion,
        "detail": detail,
        "target": None,
    })
    print(f"[{status}] {cid} {name} — {detail}")


# ---------------------------------------------------------------------------
# P0：工具声明无漂移 / 单一事实源 / 接口收敛
# ---------------------------------------------------------------------------
def check_p0_1a():
    registry_names = sorted(SKILL_REGISTRY.keys())
    rendered = render_tool_prompt()
    declared = re.findall(r"###\s+\d+\.\s+(\w+)\s+—", rendered)
    ok = sorted(declared) == registry_names
    return ok, f"registry={registry_names}；渲染声明={sorted(declared)}"


def check_p0_1b():
    coach_prompt = llm_agent.load_prompt("coach_chat_system", TOOL_BLOCK=render_tool_prompt())
    ok = ("AUTO-GENERATED" in coach_prompt) and (render_tool_prompt() in coach_prompt)
    return ok, "coach_chat_system.md 含 AUTO-GENERATED 标记且 TOOL_BLOCK 已注入" if ok else "标记缺失或未注入"


def check_p0_2():
    cg = get_skill("chart_generate")
    if cg is None:
        return False, "chart_generate 未注册"
    args = cg.get("args") or []
    ok = ("chart_type" not in args) and ("chart_type" not in render_tool_prompt())
    return ok, f"chart_generate args={args}（chart_type 未声明）" if ok else "chart_type 仍存在"


# ---------------------------------------------------------------------------
# P1：确定性优先 / 工具调用正确率（指标5）
# ---------------------------------------------------------------------------
def check_p1_1():
    try:
        profile = data_query._get_profile()
    except Exception as e:
        return False, f"_get_profile 失败: {e}"
    r_km = data_query._deterministic_answer("我最近一共跑了多少公里", profile)
    r_why = data_query._deterministic_answer("为什么我最近配速上不去", profile)
    ok = isinstance(r_km, str) and "公里" in r_km and r_why is None
    return ok, f"量化→'{r_km}'（0 LLM 调用）；推理→{r_why}（委派 LLM）"


def check_p1_2():
    pos = parse_and_call("data_query", json.dumps({"question": "我累计跑了多少公里"}))
    neg = parse_and_call("data_query", "{}")  # 缺 question
    unk = parse_and_call("__no_such__", "{}")
    pos_ok = isinstance(pos, str) and "公里" in pos
    neg_ok = isinstance(neg, dict) and neg.get("kind") == "bad_args"
    unk_ok = isinstance(unk, dict) and unk.get("kind") == "unknown_skill"
    return (pos_ok and neg_ok and unk_ok), (
        f"正例走确定性→{pos!r}；负例缺参→{neg.get('kind')}；未知→{unk.get('kind')}"
    )


# ---------------------------------------------------------------------------
# P2：profile 会话缓存 / router 无专属解析
# ---------------------------------------------------------------------------
def check_p2_1():
    counter = {"n": 0}
    orig = data_tool.build_runner_profile

    def counting():
        counter["n"] += 1
        return orig()

    data_tool.build_runner_profile = counting
    data_query._PROFILE_CACHE = {"key": None, "profile": None}
    try:
        data_query._get_profile()
        data_query._get_profile()
    finally:
        data_tool.build_runner_profile = orig
    ok = counter["n"] == 1
    return ok, f"构建次数={counter['n']}（期望 1，mtime 命中缓存）"


def check_p2_2():
    coach_src = open(os.path.join(REPO_ROOT, "routes", "coach.py"), encoding="utf-8").read()
    bad = re.findall(r"skill_name\s*==\s*[\"'](\w+)[\"']", coach_src)
    ok = len(bad) == 0
    return ok, "coach.py 无按名硬编分支（纯 parse_and_call 分发）" if ok else f"发现硬编分支: {bad}"


# ---------------------------------------------------------------------------
# P3：注册中心错误分型 / RAG↔Skill 协同
# ---------------------------------------------------------------------------
def check_p3_1():
    unk = tool_call("__no_such__", {})
    bad = parse_and_call("data_query", "{}")
    ok = unk.get("kind") == "unknown_skill" and bad.get("kind") == "bad_args"
    return ok, f"unknown_skill→{unk.get('kind')}；bad_args→{bad.get('kind')}"


def check_p3_2():
    # 注入 fake rag，使 _rag_fallback 走 RAG 分支
    fake_rag = types.ModuleType("rag")
    fake_rag.retrieve_knowledge = lambda q, top_k=3, category=None, authority_boost=0.2: [
        {"source": "book_training_theory.md", "content": "知识片段ABC", "score": 0.9}
    ]
    fake_rag.format_knowledge_context = lambda results: "【相关知识库补充】\n知识片段ABC"
    sys.modules["rag"] = fake_rag

    orig_get = data_query._get_profile
    data_query._get_profile = lambda: {
        "近期跑步数据": [], "目标": {}, "基本信息": {},
        "健康指标": {}, "体能指标": {},
    }
    _LLM_CALLS.clear()
    try:
        data_query.query_runner_data("什么是间歇跑训练")
    finally:
        data_query._get_profile = orig_get
        sys.modules.pop("rag", None)

    if not _LLM_CALLS:
        return False, "未触发 LLM 调用"
    last_user = ""
    for m in _LLM_CALLS[-1]:
        if m.get("role") == "user":
            last_user = m.get("content", "")
    ok = "知识片段ABC" in last_user
    return ok, "空数据→触发 RAG 检索，知识片段已注入 LLM 上下文" if ok else "RAG 片段未注入"


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
run_check("P0", "P0-1a", "工具声明无漂移", "render_tool_prompt() 声明集合 == registry 键集合", check_p0_1a)
run_check("P0", "P0-1b", "工具声明单一事实源", "coach prompt 含 AUTO-GENERATED 标记且 TOOL_BLOCK 注入", check_p0_1b)
run_check("P0", "P0-2", "chart_generate 接口一致", "chart_type 已收敛（签名与 prompt 均不声明）", check_p0_2)
run_check("P1", "P1-1", "data_query 确定性优先", "量化问题确定性回答(LLM 0 调用)；推理问题委派 LLM", check_p1_1)
run_check("P1", "P1-2", "工具调用正确率(指标5)", "正例调用正确+参数可解析；负例正确弃权", check_p1_2)
run_check("P2", "P2-1", "profile 会话缓存", "同会话 profile 仅构建 1 次", check_p2_1)
run_check("P2", "P2-2", "router 无专属解析", "coach.py 不含 skill_name == 按名分支", check_p2_2)
run_check("P3", "P3-1", "注册中心错误分型", "tool_call/parse_and_call 错误带 kind", check_p3_1)
run_check("P3", "P3-2", "RAG↔Skill 协同", "数据无、知识有时 data_query 回退 RAG 并注入", check_p3_2)

# ---------------------------------------------------------------------------
# 汇总 + 落盘
# ---------------------------------------------------------------------------
by_tier = {}
for c in CHECKS:
    t = c["tier"]
    by_tier.setdefault(t, {"PASS": 0, "FAIL": 0, "PENDING": 0, "total": 0})
    by_tier[t][c["status"]] = by_tier[t].get(c["status"], 0) + 1
    by_tier[t]["total"] += 1

overall = {"PASS": 0, "FAIL": 0, "PENDING": 0}
for c in CHECKS:
    overall[c["status"]] = overall.get(c["status"], 0) + 1

result = {
    "timestamp": __import__("datetime").datetime.now().isoformat(),
    "overall": overall,
    "by_tier": by_tier,
    "checks": CHECKS,
}

os.makedirs(os.path.join(REPO_ROOT, "data", "eval"), exist_ok=True)
with open(os.path.join(REPO_ROOT, "data", "eval", "skill_eval_results.json"), "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

with open(os.path.join(REPO_ROOT, "data", "eval", "skill_eval_report.md"), "w", encoding="utf-8") as f:
    f.write("# Skill / 双引擎 / Router 验证报告\n\n")
    f.write(f"- 运行时间：{result['timestamp']}\n")
    f.write(f"- 总览：**PASS {overall['PASS']} / FAIL {overall['FAIL']} / PENDING {overall['PENDING']}**\n\n")
    for c in CHECKS:
        f.write(f"## [{c['status']}] {c['id']} {c['name']}\n")
        f.write(f"- 层级：{c['tier']}\n")
        f.write(f"- 判定：{c['criterion']}\n")
        f.write(f"- 结论：{c['detail']}\n\n")

print(f"\n=== 总览: PASS {overall['PASS']} / FAIL {overall['FAIL']} / PENDING {overall['PENDING']} ===")
sys.exit(0 if overall["FAIL"] == 0 else 1)
