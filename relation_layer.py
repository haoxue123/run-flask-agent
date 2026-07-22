# -*- coding: utf-8 -*-
"""
relation_layer.py —— Path A+ 靶向关系层 + 意图路由（确定性、无 embedding、离线）

设计要点（见计划 blazing-beacon-babbage.md）：
- 仅对高价值因果/生理内容做 typed triples 策展（data/relations.json），非全库、非图数据库。
- 词法倒排索引做命中，≤2 跳实体链接做推理链拼接；无任何 embedding / sentence-transformers 依赖。
- classify_intent_relation 用关键词判定"因果/解释" vs "事实/生成"，零 LLM 调用，
  与既有 LLM 工作流意图分类器（chat/assess/advise/plan/track）正交。
- 本模块是「检索辅助」，不注册为 skills 工具，也不 import rag（避免触发 ChromaDB）。

对外接口：
- classify_intent_relation(query) -> bool
- retrieve_relations(query) -> str   # 命中返回 relation_context 文本块，否则 ""
"""
import os
import json
import threading

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
RELATIONS_PATH = os.path.join(REPO_ROOT, "data", "relations.json")

# 因果/解释类意图关键词（词法，零 LLM 成本）
_CAUSAL_KEYWORDS = (
    "为什么", "为何", "怎么", "如何", "机制", "成因", "原理", "原因",
    "关系", "导致", "引发", "差别", "区别", "机理",
)

# 2 跳链接上限，避免噪声
_MAX_LINK_HOPS = 2
_MAX_LINK_TRIPLES = 6

_triples = None
_index = None
_lock = threading.Lock()


def _load():
    """懒加载 + 双重检查锁：读取 JSON 并建立 倒排索引（subject + aliases -> triple 下标）。"""
    global _triples, _index
    if _triples is not None:
        return
    with _lock:
        if _triples is not None:
            return
        try:
            with open(RELATIONS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            triples = data.get("triples", []) or []
        except Exception:
            triples = []
        index = {}
        for i, t in enumerate(triples):
            keys = set()
            s = (t.get("subject") or "").strip()
            if s:
                keys.add(s)
            for a in (t.get("aliases") or []):
                a = (a or "").strip()
                if a:
                    keys.add(a)
            for k in keys:
                index.setdefault(k, []).append(i)
        _triples = triples
        _index = index


def classify_intent_relation(query):
    """判定 query 是否为因果/解释类（需要结构化推理增强）。非此类一律返回 False（走既有 RAG）。"""
    if not query:
        return False
    return any(kw in query for kw in _CAUSAL_KEYWORDS)


def _match_triples(query):
    """返回命中的 triple 下标列表：primary 命中（按命中词数降序）+ ≤2 跳实体链接。"""
    _load()
    if not _index:
        return []
    hit_count = {}
    for kw, idxs in _index.items():
        if kw and kw in query:
            for i in idxs:
                hit_count[i] = hit_count.get(i, 0) + 1
    if not hit_count:
        return []

    primary = [i for i, _ in sorted(hit_count.items(), key=lambda x: (-x[1], x[0]))]
    primary_set = set(primary)

    # 收集 primary 的实体（subject + object）用于链接
    entities = set()
    for i in primary:
        t = _triples[i]
        s = (t.get("subject") or "").strip()
        o = (t.get("object") or "").strip()
        if s:
            entities.add(s)
        if o:
            entities.add(o)

    linked = []
    for i, t in enumerate(_triples):
        if i in primary_set:
            continue
        s = (t.get("subject") or "").strip()
        o = (t.get("object") or "").strip()
        if (s and s in entities) or (o and o in entities):
            linked.append(i)
            if len(linked) >= _MAX_LINK_TRIPLES:
                break

    return primary + linked


def _format_triple(t):
    lines = ["- {s} → {r} → {o}".format(
        s=t.get("subject", ""), r=t.get("relation", ""), o=t.get("object", ""))]
    if t.get("prevention"):
        lines.append("  预防: {}".format(t["prevention"]))
    if t.get("source"):
        lines.append("  来源: {}".format(t["source"]))
    return "\n".join(lines)


def retrieve_relations(query):
    """对因果/解释类 query 返回结构化推理辅助文本块；否则返回空串（不注入、行为不变）。"""
    if not classify_intent_relation(query):
        return ""
    idxs = _match_triples(query)
    if not idxs:
        return ""
    seen = set()
    blocks = ["【相关知识关系（结构化推理辅助）】"]
    for i in idxs:
        if i in seen:
            continue
        seen.add(i)
        blocks.append(_format_triple(_triples[i]))
    return "\n".join(blocks)


def get_triple_count():
    """调试/评测用：返回已加载的 triple 数量。"""
    _load()
    return len(_triples) if _triples else 0
