# 知识库运营手册（Phase 4 · 持续运营机制）

> 本手册将《知识库专业书籍引入规划.md》中的方法论固化为可重复执行的流程，确保知识库在运动科技方向上**持续、系统、合规**地增强。
> 适用范围：`data/knowledge/` 下所有 `.md` 知识文档；检索系统见 `rag.py`。

---

## 1. 入库 Checklist（每新增一份文档必过）

> 加权评分卡（详见规划 §3）。每项 1–5 分，按权重求和；**总分 ≥ 3.5 才纳入**；合规 gate 为**一票否决**。

| 维度 | 权重 | 评分要点 |
|------|------|----------|
| 权威性（authority） | 25% | 来源是否为官方机构/顶级期刊/公认权威著作；`authority_level` 高=5 |
| 证据强度（evidence） | 20% | 是否有实证研究/元分析支撑，而非个人观点 |
| 相关性（relevance） | 15% | 是否直接服务跑者训练/配速/伤痛/营养等核心问题 |
| 实用性（practical） | 15% | 是否可转化为具体可执行的训练建议 |
| 时效性（timeliness） | 10% | 观点是否仍为当前共识；运动科学更新周期约 5–10 年 |
| 受众匹配（audience） | 5% | 是否贴合国内大众跑者/马拉松备赛场景 |
| 本地化（localization） | 5% | 是否有中文/国内适用版本或已做本地化改写 |
| 互补性（complement） | 5% | 是否填补现有知识维度空白（避免重复） |

**合规 gate（任一为否 → 一票否决，禁止入库）：**
- [ ] 是否整本照搬受版权保护的畅销书正文？→ 若是，**禁止**
- [ ] 受版权书籍是否仅做"萃取要点 + 改写概述 + 标注出处 + 引用少量原文"？→ 必须
- [ ] 是否优先依赖 I/J 类开放内容作为权威主干？→ 应优先
- [ ] 是否已在 `_licenses.md` 完成授权登记？→ 必须

---

## 2. 增量入库步骤（标准作业流程）

```
① 合规 gate 判定 ──不通过──▶ 拒绝，说明原因
        │通过
        ▼
② 内容萃取：PDF/EPUB → 提取正文 → 按「## 二级标题」结构化
        ▼
③ 元数据增强：在 H1 后写入 <!-- meta: ... --> 块
   （category / author / year / authority_level / source_type / lang）
        ▼
④ 中文摘要层：为英文来源补一段「概述与中文要点摘要」（提升中文问句召回）
        ▼
⑤ 重建索引 + 回归：rag.build_index() → 跑检索回归（全库 / category 过滤 / 冲突重排）
        ▼
⑥ 授权登记：在 _licenses.md 追加一行（状态 + 纳入方式 + 备注）
        ▼
⑦ 收尾：提交变更，记录到季度评审台账
```

### 元数据块模板（必填 6 字段）
```markdown
# <中文标题>

<!--
meta:
  category: <公开指南|生理基础|训练理论|跑步技术|营养补给|恢复伤痛|数据化|特殊人群|自编>
  author: <作者/机构>
  year: <年份>
  authority_level: <高|中|低>
  source_type: <guideline|book|note|review>
  lang: <zh|en>
-->
```

---

## 3. 季度评审 SOP

**频率**：每季度首月第一周。
**执行人**：知识库维护者（当前为开发侧）。

1. **新版指南复核**：检查 WHO / HHS / ACSM / 中国田协等是否发布新版公开指南，更新 `guide_*.md` 并标注版本年。
2. **新论文/新专著扫描**：对高权重维度（权威25%+证据20%）补入近 2 年高质量综述。
3. **过期内容下架**：标注 `year` 超过 10 年且无新证据支撑、且与当前共识冲突的内容，降级为"历史参考"或直接移除。
4. **冲突仲裁**（见 §4）：复核既有结论是否出现高权威反例。
5. **检索质量抽检**：随机 10 个真实跑者问题，检查 top3 是否命中权威源；若权威源命中率 < 60%，触发中文摘要层/嵌入模型升级。

---

## 4. 冲突仲裁规则

当不同来源对同一问题给出**不同结论**时：

1. **同结论、不同权威** → 高 `authority_level` 优先（检索已自动按融合分 `fused_score` 重排）。
2. **结论冲突** → 在 `format_knowledge_context` 输出中**同时标注双方来源与权威度**，并向用户提示"存在不同观点，建议结合自身情况/咨询医生"。
3. **低权威（低）来源** → 仅作为"补充视角"，不与高/中权威结论并列主张。
4. **自编（中）笔记** 与 **权威指南（高）** 冲突 → 以权威指南为准，自编笔记需复核修正。

> 仲裁依据统一为 `rag.py` 中的 `_AUTH_WEIGHT = {"高":1.0, "中":0.65, "低":0.35}` 与 `rerank_with_authority()`。

---

## 5. 当前知识库结构（截至本手册建立）

- **I 类公开指南（权威主干）**：`guide_who_2020` / `guide_hhs_pag` / `guide_acsm_fitt` / `guide_caa_marathon`
- **专业维度萃取（书籍）**：`book_physiology` / `book_training_theory` / `book_running_technique` / `book_nutrition` / `book_recovery_injury` / `book_data_power` / `book_special_populations`
- **自编/综述**：`training_principles` `pace_strategy` `injury_prevention` `nutrition` `recovery` `common_mistakes` `beginner_guide` `race_preparation` `method_japan_china` `ref_data_augmentation`
- **治理文件**：`_licenses.md`（授权登记）、本手册 `OPERATIONS.md`

**索引**：`data/chroma_db/`（ChromaDB 持久化，含上述 6 字段元数据；当前 191 文本块 / 22 文档）
