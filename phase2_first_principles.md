# Phase 2 第一性原理分析：让确定性内核进入"决策"而非仅"审计"

> 本文档是 Phase 2 的开发依据。结论先行：**Phase 1 让数字"算得对"，Phase 2 必须让数字"管得住决策"**——否则科学内核只是个事后审计员，对计划质量毫无约束力。

---

## 0. 当前项目整体状态（Phase 2 的出发点）

| 层 | 状态 |
|----|------|
| 数据层 | `data_tool.build_runner_profile()` 从 GPX/TCX/FIT/CSV 提取跑者画像（近期配速、距离、心率、VO2max、准备度）✅ |
| 生成层 | `llm_agent.generate_training_plan()` 用 deepseek-chat 自由生成计划文本，正则解析为 `structured` ✅ |
| 生理内核 | `physiology.py`（纯标准库）：配速互转、TRIMP、CTL/ATL/TSB、Banister、VO2max、阈值配速+强度分区 ✅（Phase 1） |
| 科学增强 | `plan_science.enrich_plan()` 在 `structured` 上**追加** `corrected_target_pace_sec/trimp/ctl/atl/tsb/pace_warning`，并做周负荷>20% 告警 ✅（Phase 1） |
| 反思层 | `multi_round_reflect()` 用 reasoner 判"通过/不通过"并调整（自由文本，无硬约束）✅ |
| **决策闭环** | ❌ **缺失**：LLM 仍"凭记忆编"配速；`science` 算完即弃，无人消费、无约束、无持久化、无展示** |

**核心矛盾**：Phase 1 的 `science` 字段生成了，但 `routes/plan.py` 只取 `plan_text`/`structured`，`plan.json` 只持久化 `structured`，前端完全不知道 `science` 存在。即——**算得对，但算完就扔**。

---

## 1. 第一性原理拆解：什么叫"推理和决策科学准确"

追问到底，一个训练计划的"科学准确"由 5 个必要条件支撑，缺一不可：

1. **计算正确**（数字来源可信）→ Phase 1 已解决（physiology 确定性内核）。
2. **决策受计算约束**（生成时基于真实能力，而非 LLM 幻觉）→ **Phase 2 核心缺口**。
3. **不违反已知原则/禁忌**（安全护栏）→ Phase 1 仅做了软告警（周增幅>20%），缺硬禁忌。
4. **可解释**（跑者/教练能看懂"为什么这样安排"）→ 当前无。
5. **闭环反馈**（实际执行→修正模型）→ Phase 3。

当前 LLM 生成计划时，2/3/4 全部缺失。LLM 的"科学感"来自模型权重里的泛化知识，**不可验证、不可审计、可能违反基础训练学常识**。这就是"决策不准"的根因。

### 第一性原理结论

> **要让决策科学，确定性内核必须在 LLM "动手之前"就给出约束区间，并在 LLM "动手之后"做硬禁忌校验。** 内核从"审计员"升级为"护栏+参谋"。LLM 退居为"在约束内生成自然语言的执行器"——这正是"AI 辅助决策"的本意，而非"AI 替代决策"。

---

## 2. 前提假设与约束（不可妥协）

| 假设/约束 | 说明 |
|----------|------|
| **整体正确运行不可妥协** | 任何改动不得破坏现有 `/generate`、图表、前端渲染、plan.json 兼容 |
| **纯增量** | 只追加字段（`science`、`corrected_*`、`safety_flags`），绝改 `target_pace_sec` |
| **全程可降级** | 任何 science 计算失败 → 跳过，LLM 行为与原先**完全一致** |
| **无新依赖** | 只用标准库 + 现有 physiology/plan_science，不引入新包 |
| **LLM 仍是生成主体** | 约束以"提示文本"注入，不接管生成；生成失败兜底为无约束 |
| **数据可能缺失** | 跑者可能无心率/无近期数据 → 约束退化但流程不崩 |

---

## 3. Phase 2 目标与实现方案（子模块拆分）

### 3.1 决策约束（事前）：`build_constraints` — 让 LLM 基于真实能力生成
- **做什么**：在 `generate_training_plan` 调 LLM **之前**，用 physiology 算：
  - 跑者阈值配速（如 6'20"）
  - 各类型建议配速区间（轻松跑/间歇/长距离…用 `target_pace_for_type`）
  - 当前 CTL/ATL 负荷基线（状态：偏向疲劳还是已恢复）
  - **安全红线**（硬约束文本）：周负荷增幅 ≤10%、不连续两天高强度、每周≥1 休息日、长距离≤近期最长×1.2（新手更保守）
- **怎么注入**：约束文本作为一段结构化"科学约束"追加进 user_prompt（可降级）。
- **决策依据**：LLM 无配速生理学先验可验证性；给它"阈值配速 6'20"，它生成的配速就不会是 5'00" 这种幻觉值。**把不可验证的生成，约束在可验证的区间内。**

### 3.2 安全护栏（事后）：`check_safety_rules` — 硬禁忌校验
- **做什么**：计划生成后，逐条检查训练学硬规则，输出 `safety_flags: [{level, rule, detail, day}]`：
  - 连续两天高强度（间歇/冲刺/节奏）→ warning
  - 周负荷增幅 >10%（比 Phase 1 的 20% 更严，取 TrainingPeaks 经典 10% 线）→ warning
  - 单周无完全休息日 → warning
  - 长距离距离 > 近期最长 ×1.2 → caution
  - 新手（VO2max/准备度低）高强度比例过高 → warning
- **决策依据**：Phase 1 只查"周增幅>20%"这一条且是软告警；真实训练学有多条不可违反的底线。护栏让"通过反思"有客观标准，而非 reasoner 自由心证。

### 3.3 数据流动：透传 + 持久化 — 让 science 不再被丢弃
- **做什么**：
  - `/generate` 返回 JSON 增加 `"science"` 字段
  - `plan.json` 持久化 `"science"`
  - `/api/plan/save` 保留已有 science（合并，避免保存时丢失）
  - `/api/plan/load` 读回 science
- **决策依据**：science 不流动就永远无法被前端展示（可解释）或被反馈闭环消费（Phase 3）。这是"让算完的数据活起来"的必需管道。**纯加字段，前端不消费也不破坏。**

### 3.4 前端可视化（Phase 2 子阶段 D，下一轮）
- 在训练页/教练页用 Chart.js 画 CTL/ATL/TSB 负荷曲线，渲染 `safety_flags` 预警卡片、逐日"建议配速 vs 计划配速"对比。
- **本轮不做**：涉及改 JS/模板，风险高于后端；但 3.3 已把数据推到前端门口，下一轮可零摩擦接入。

---

## 4. Phase 2 具体任务清单

| # | 模块 | 交付 | 风险 |
|---|------|------|------|
| A | `plan_science.build_constraints` | 约束文本+结构化约束字典 | 低（纯函数） |
| B | `plan_science.check_safety_rules` | safety_flags 列表 | 低（纯函数） |
| C | `enrich_plan` 接入 B | summary 增加 `safety_flags` | 低（增量） |
| D | `generate_training_plan` 注入 A | user_prompt 加约束段（可降级） | 低 |
| E | `routes/plan.py` 透传+持久化 | /generate 返回、plan.json、save/load | 低（加字段） |
| F | 离线集成测试 | 断言零回归 + science 全链路流动 | — |

---

## 5. 为什么这些决策"有依据而非惯例"

- **为什么用"提示注入"而非"LLM 微调"**：微调需训练数据/算力/标注，且不可解释；提示注入零成本、可审计、今天就能上。第一性原理：用最低成本消除"不可验证生成"这一最大风险。
- **为什么周增幅红线取 10% 而非 20%**：10% 是 TrainingPeaks/多本训练学教材的通用安全增量线；Phase 1 的 20% 偏宽松。取更严值提前预警。
- **为什么护栏在"生成后"而非"生成中拦截"**：生成中拦截需结构化输出+重试，复杂且可能卡死；生成后校验+告警，跑者/教练可见，符合"可解释"且实现简单。
- **为什么持久化 science 而非每次重算**：重算需保留 profile 上下文；持久化让 plan.json 自包含，前端/反馈闭环直接读，且跨会话稳定。

---

## 6. 不在 Phase 2 范围（避免范围蔓延）

- RAG/知识图谱：Phase 2 用"硬编码训练学规则"已足够覆盖硬禁忌；KG 是 Phase 4 的可解释增强，非必需。
- RL/个性化 Bandit：需反馈数据（执行结果），属 Phase 3 闭环。
- 前端重写：3.3 铺路即可，可视化下一轮。

**Phase 2 验收标准**：原 `target_pace_sec` 不变；`/generate` 返回的 science 含 `constraints` 与 `safety_flags`；plan.json 含 science；routes 导入正常；集成测试零回归。
