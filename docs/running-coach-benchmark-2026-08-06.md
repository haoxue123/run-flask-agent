# 马拉松训练计划 Agent · GitHub 竞品赛道调研与方向建议

> 调研时间：2026-08-06
> 调研口径：GitHub 上「跑步训练计划生成 / AI 教练 / 训练数据接入」相关开源项目
> 数据来源：GitHub 搜索页 + GitHub API（`stargazers_count`）。star 数为调研当日快照，会变动。

---

## 一、一句话核心结论

**「马拉松训练计划生成 / AI 跑步教练」这个窄赛道，在 GitHub 上没有高 star 标杆。**

- 用 GitHub search API 检索 `running coach training plan`，共匹配 67 个仓库，**排第一的只有 15 star**，绝大多数是 0–3 star。
- 真正几千 star 的跑步项目（如 `running_page` 4494★）做的是**个人跑步数据可视化主页**，不是训练计划生成器。
- 真正有「计划生成 / AI 教练」属性的项目，star 普遍在 **2–59** 之间，且多为 2025–2026 年新建的实验性项目。

**这意味着**：你想「借鉴高 star 成熟方案」这个前提不成立。这个赛道还在极早期，没有社区验证的范本。你的困惑本质是「没有现成答案」，这反而是定义自己方向的机会，而不是落后。

---

## 二、10 个相关项目清单（按 star 降序）

> 说明：下面 10 个聚焦「训练计划 / AI 教练 / 数据接入」方向。star 标注核实方式：API = 用 GitHub API 逐一核对；Search = 来自 GitHub 搜索结果页显示数字。

| # | 项目 | star | 技术栈 | 定位一句话 | 方向标签 |
|---|------|------|--------|-----------|---------|
| 1 | [Raistlfiren/garmin-csv-plan](https://github.com/Raistlfiren/garmin-csv-plan) | 59 (Search) | PHP | 把训练计划 CSV 自动灌进 Garmin Connect | 计划→手表同步 |
| 2 | [hhopke/intervals-icu-mcp](https://github.com/hhopke/intervals-icu-mcp) | 49 (Search) | Python | Intervals.icu 的 MCP 读写服务器，支持训练数据/计划/结构化 workout 生成 | MCP 生态 |
| 3 | [eddmann/intervals-icu-mcp](https://github.com/eddmann/intervals-icu-mcp) | 34 (Search) | Python | 另一个 Intervals.icu MCP，让 LLM 交互训练数据/分析/计划 | MCP 生态 |
| 4 | [hoovercj/time-to-run](https://github.com/hoovercj/time-to-run) | 21 (Search) | TypeScript | 生成跑步训练计划（受 defy.org calendar hack 启发） | 计划生成器 |
| 5 | [xiaolouJB/ai-running-coach](https://github.com/xiaolouJB/ai-running-coach) | 21 (API) | Python / Claude Skill | COROS 数据 × 丹尼尔斯理论 AI 教练，最完整的开源方法论层（VDOT/准备度/伤病/力量/营养均为确定性脚本） | 方法论天花板 |
| 6 | [ColinEberhardt/claude-running-coach](https://github.com/ColinEberhardt/claude-running-coach) | 15 (API) | Python | AI 跑步教练 Claude skill，个性化计划 + 分析 + 循证指导 | 计划生成器 |
| 7 | [apsureda/garmin-planner](https://github.com/apsureda/garmin-planner) | 11 (Search) | Python | 导入 / 排期训练计划到 Garmin 手表 | 计划→手表同步 |
| 8 | [Jack-Abyss/claude-garmin](https://github.com/Jack-Abyss/claude-garmin) | 11 (Search) | Python | Garmin Connect 数据接入 Claude 的 MCP（计划/恢复/分析） | 数据接入 / MCP |
| 9 | [GGGGGGx08/coros-trainer](https://github.com/GGGGGGx08/coros-trainer) | 2 (API) | Python / Streamlit | COROS 训练助手 Pro：丹尼尔斯配速 + AI 教练 + 可视化看板 | 计划生成器 |
| 10 | [mlamberts78/strava-openai-coach](https://github.com/mlamberts78/strava-openai-coach) | 0 (API) | Python | Strava API + OpenAI 做每日 / 每周训练分析复盘 | 复盘分析 |

**补充参照（人气天花板，但非计划生成）**：
- [yihong0618/running_page](https://github.com/yihong0618/running_page) — **4494★ (API)** — TypeScript — 自动同步 Garmin/Strava/Nike 数据生成个人跑步主页（GitHub Actions + Vercel）。它代表「跑步 + 开源」的最高人气方向是**数据可视化展示**，不是教练。

---

## 三、从这批项目里提炼出的 6 个发展趋势

1. **数据接入从「手动上传」走向「手表 / 平台直连」**
   几乎每个新项目都接了至少一个数据源：Garmin、COROS、Strava。手动上传 GPX/TCX/FIT 是起步，但「直连手表」是普遍演进方向。

2. **「确定性科学内核 + LLM 解释」成为主流架构**
   丹尼尔斯 VDOT、心率区间、80/20 强度分配、10% 递增法则被反复使用。典型如 `xiaolouJB/ai-running-coach` 把 VDOT、身体准备度、营养补水全部做成**纯函数脚本**，LLM 只复述、不心算、不幻觉。这与你已做的「确定性科学内核」方向一致。

3. **从「一次性生成计划」走向「动态调整闭环」**
   `xiaolouJB` 做每周 rescore（上周执行度评分 + 疲劳检查 + 下周调整）；`strava-openai-coach` 做日 / 周复盘；`diegosimao` 的 roadmap 写着 real-time adjustment。**「执行 → 复盘 → 调整」的周循环，是当前 AI 教练差异化的核心，多数小项目还没做到。**

4. **MCP 生态正在崛起**
   `intervals-icu-mcp`（两个）、`claude-garmin` 让 LLM 直接读写训练平台数据。这是 2025–2026 的新方向——让你的 Agent 能「写回」Garmin / Intervals.icu，而不只是读。

5. **本地 / 隐私优先**
   部分项目（如 `janagoudarniroop/genai`）用 Ollama 本地模型离线运行，数据不出本机。对国内用户也契合「数据隐私」诉求。

6. **从「训练」扩展到「伤病 / 力量 / 营养全周期」**
   `xiaolouJB` 已拆出 injury（伤病分级康复）、strength（15 动作力量库）、nutrition（赛事营养）模块。单一计划生成器正在变成「全周期健康教练」。

---

## 四、结合你项目现状的方向建议

### 你的现状（已有资产）
- Flask + DeepSeek 双模型 + ChromaDB RAG + 确定性科学内核（`physiology.py` / `plan_science.py`）
- GPX / TCX / FIT 解析、教练对话、mem0 长期记忆、关系层（Path A+）、离线评测 harness
- 已部署 Railway 供面试演示；chat history 保存 bug 在排查中

### 你的相对优势（对比上面这批项目）
- **工程深度明显领先**：你有「确定性科学内核 + RAG 问答 + 长期记忆」的完整链路，而多数竞品只是装了个 `SKILL.md` 的 demo，没有后端和科学内核。
- **Web App 产品形态**：你已有可演示的网页，产品感比纯 CLI / Claude skill 强，适合做作品集和面试 demo。

### 你的短板（对比趋势）
- **动态调整闭环缺失**：目前是「上传 → 生成一次计划」，没有「执行 → 复盘 → 调整」的周循环。这是 AI 教练差异化的关键。
- **真实数据源接入弱**：Garmin 同步实测 OAuth 失败（之前日志 DI-OAuth2 400），目前靠手动上传。手动上传其实不失为稳妥起步，但可补 Intervals.icu MCP 或 COROS 接入做加分。
- **生态位未定**：是继续做 Web App，还是包成 MCP / Claude skill？趋势显示 MCP 是新热点，但你已有 Web App 优势，不必硬转。

### 具体下一步建议（按价值排序）
1. **先修好 chat history 保存 bug**。这是「长期记忆」和「动态调整闭环」的地基——地基不稳，闭环无从谈起。
2. **把差异化押在「动态调整闭环 + 长期记忆」**。让教练能基于历史计划与实际执行做周复盘、调整下周课表。这是别人普遍没做好的，也是你已有基础能延展的。
3. **维持 Web App 形态**，但加一个轻量「导出到 Garmin / Intervals」出口（借鉴 `garmin-csv-plan` 的思路），提升实用性，不阻塞核心。
4. **方法论对齐丹尼尔斯 VDOT**（若科学内核还没系统化用）。这是开源社区的最大公约数，面试也好讲。
5. **不必焦虑 star**。这个赛道没有高 star 标杆，你和竞品基本在同一起跑线，而你的工程完成度已领先多数 demo。

---

## 五、诚实声明（事实 / 推测区分）

- **事实**：以上 star 数均来自 GitHub 搜索页或 API 在 2026-08-06 的返回；「赛道无高 star 标杆」由 search API 的 `total_count=67` 与最高 15 star 佐证。
- **推测**：第 4–6 条「趋势」是基于这批项目的共性归纳，属于我的判断，不是某篇权威报告的结论。
- **未入选但值得知道**：`janagoudarniroop/genai`（Ollama 本地教练）、`diegosimao/Personalized-Training-Assistant-Agent`（0★，Garmin→Streamlit 半马计划）、`jandroav/vtrain`（5★，Go 写的 VDOT 计划生成器），方向类似但 star 低，未纳入主表。
