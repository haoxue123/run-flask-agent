# 知识库授权登记（License Register）

<!--
meta:
  category: 公开指南
  author: 知识库维护者
  year: 2026
  authority_level: 高
  source_type: guideline
  lang: zh
-->

## 用途

本文件记录 `data/knowledge/` 下每份知识文档的**授权状态与合规结论**，满足 §2.1 合规 gate 的"一票否决"审计要求。任何新文档入库前须先在此登记。

## 授权状态图例

| 状态 | 含义 | 是否可直接萃取入库 |
|------|------|-------------------|
| PD | 公共领域 | 是 |
| OPEN | 开放许可（CC 等）/ 官方公开文件 | 是（摘要+改写+署名） |
| OWNED | 自有购买副本，限个人研究 RAG | 是（萃取要点，不整本照搬） |
| COPYRIGHT | 受版权保护、无授权 | 否（仅可引用少量原文+改写概述） |

## 登记清单

| 文档 | 类别 | 授权状态 | 纳入方式 | 备注 |
|------|------|----------|----------|------|
| guide_who_2020.md | I 公开指南 | OPEN（WHO 公开文件） | 摘要+改写+署名 | 非原文照搬 |
| guide_hhs_pag.md | I 公开指南 | OPEN（美国政府公开文件） | 摘要+改写+署名 | 非原文照搬 |
| guide_acsm_fitt.md | I 公开指南 | OPEN（方法学公开）/ 部分 COPYRIGHT | 仅方法学摘要 | 不复制正文 |
| guide_caa_marathon.md | I 公开指南 | OPEN（协会公开标准） | 框架萃取+待官方核实 | 阈值需核对最新版 |
| training_principles.md | 自编 | OWNED（团队/AI 经验） | 直接 | 既有文档 |
| pace_strategy.md | 自编 | OWNED | 直接 | 既有文档 |
| injury_prevention.md | 自编 | OWNED | 直接 | 既有文档 |
| nutrition.md | 自编 | OWNED | 直接 | 既有文档 |
| recovery.md | 自编 | OWNED | 直接 | 既有文档 |
| common_mistakes.md | 自编 | OWNED | 直接 | 既有文档 |
| beginner_guide.md | 自编 | OWNED | 直接 | 既有文档 |
| race_preparation.md | 自编 | OWNED | 直接 | 既有文档 |
| method_japan_china.md | 文献综述+采访 | OPEN（公开报道/官方指南/开放综述/教练公开著作节选） | 二次整理+标注来源+署名 | 2026-07-21 新增；补日本体系+中国精英案例空白，未整本复制书籍 |
| ref_data_augmentation.md | 文献综述(方法索引) | OPEN（公开论文索引/可引用） | 改写概述+文献索引 | 2026-07-21 新增；数据增强与质量过滤理论，附权威文献 |
| book_physiology.md | 生理基础 | OWNED（购买副本，限研究 RAG） | 萃取要点+改写概述+标注出处 | 2026-07-21 新增；Kenney/Wilmore/Costill 等《Physiology of Sport and Exercise》2019；不整本照搬 |
| book_training_theory.md | 训练理论 | OWNED（购买副本，限研究 RAG） | 萃取要点+改写概述+标注出处 | 2026-07-21 新增；Daniels《Daniels' Running Formula》/ Pfitzinger《Advanced Marathoning》2013；不整本照搬 |
| book_running_technique.md | 跑步技术 | OWNED（购买副本，限研究 RAG） | 萃取要点+改写概述+标注出处 | 2026-07-21 新增；Dicharry《Anatomy for Runners》/ Starrett《Becoming a Supple Leopard》2019；不整本照搬 |
| book_nutrition.md | 营养补给 | OWNED（购买副本，限研究 RAG） | 萃取要点+改写概述+标注出处 | 2026-07-21 新增；Jeukendrup 运动营养研究 2020；不整本照搬 |
| book_recovery_injury.md | 恢复伤痛 | OWNED（购买副本，限研究 RAG） | 萃取要点+改写概述+标注出处 | 2026-07-21 新增；Starrett / Magness《The Runner's Guide to Healthy Feet & Ankles》2015；不整本照搬 |
| book_data_power.md | 数据化 | OWNED（购买副本，限研究 RAG） | 萃取要点+改写概述+标注出处 | 2026-07-21 新增；Vance《Run with Power》2016；不整本照搬 |
| book_special_populations.md | 特殊人群 | OWNED（购买副本，限研究 RAG） | 萃取要点+改写概述+标注出处 | 2026-07-21 新增；Sims《ROAR》/ Webb《Older, Faster, Stronger》2016；不整本照搬 |
| ref_numeric_tables.md | 数据化 | OWNED（标准参考萃取） | 数值要点萃取+改写概述+标注出处 | 2026-07-21 新增（Path A / G7 数值查表缺口）；VDOT→配速、心率五区间、ACWR 阈值、功率区间；不整本照搬 |
| book_sport_psychology.md | 运动心理 | OWNED（标准参考萃取） | 萃取要点+改写概述+标注出处 | 2026-07-21 新增（Path A / G1 运动心理缺口）；Weinberg&Gould《运动心理学基础》/ Buman&Marcus 行为改变；不整本照搬 |
| data/relations.json | 数据资产(关系层) | 自编(策展) | Path A+ 靶向关系层 typed triples | 2026-07-22 新增（Path A+ 关系层+意图路由）；词法匹配用，非全库、非图数据库；来源见各 triple 的 source 字段 |

## 合规红线

1. 商用产品**不得整本照搬**受版权保护的畅销书正文。
2. 受版权书籍仅可"萃取要点 + 改写概述 + 标注出处 + 引用少量原文"。
3. 优先依赖 I/J 类开放内容作为权威主干。
4. 每季度复核一次授权状态（见 Phase 4 运营机制）。
