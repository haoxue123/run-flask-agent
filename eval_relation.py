# -*- coding: utf-8 -*-
"""
eval_relation.py —— Path A+ 关系层检索小评测（离线、无 LLM / 无 embedding）

验证靶向关系层的词法召回是否合理：因果/解释类问题应召回对应 triples，
事实/生成类问题应返回空串（不注入、行为不变）。

运行：E:/python/python.exe eval_relation.py
"""
import sys
import os

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)
import relation_layer


# 正向样本：(query, 期望在 relation_context 中出现的子串)
POSITIVE = [
    ("为什么跑步会膝盖疼", "跑步膝"),
    ("为什么配速上不去", "配速停滞"),
    ("为什么要循序渐进增加跑量", "跑量周增幅"),
    ("为什么受伤风险会升高", "ACWR"),
    ("为什么要在软路面跑步", "胫骨内侧应力综合征"),
    ("为什么需要加强臀中肌", "髂胫束综合征"),
    ("为什么80/20训练有效", "80/20训练法则"),
    ("为什么睡眠对恢复很重要", "睡眠"),
    ("为什么积极自我对话有用", "自我对话"),
    ("为什么跑步前要补充碳水", "碳水化合物"),
]

# 负向样本：事实/生成类，期望返回空串
NEGATIVE = [
    "我最近一共跑了多少公里",
    "帮我生成一个 16 周马拉松训练计划",
    "今天天气怎么样",
]


def main():
    total = len(POSITIVE) + len(NEGATIVE)
    passed = 0
    print(f"关系层 triple 总数: {relation_layer.get_triple_count()}\n")

    for q, expect in POSITIVE:
        ctx = relation_layer.retrieve_relations(q)
        ok = bool(ctx) and expect in ctx
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] 正例: {q!r} → 期望含 {expect!r} | 命中={'是' if ctx else '否'}")

    for q in NEGATIVE:
        ctx = relation_layer.retrieve_relations(q)
        ok = ctx == ""
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] 负例: {q!r} → 期望空串 | 实际={'空' if ctx == '' else '非空'}")

    print(f"\n=== 关系层评测: PASS {passed} / FAIL {total - passed} / 共 {total} ===")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
