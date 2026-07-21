# 跑步教练 Web 端 · UI 设计优化分析报告

> 分析对象：`templates/*.html` + `static/*.css|js`（线上实现）
> 对照基准：`design/running-coach-web/*.design` + `orchestration-summary.json`（设计契约）
> 分析维度：可用性 · 视觉一致性 · 交互体验 · 信息架构
> 约束：所有优化为「增量增强」，不改变设计契约的页面结构与 Token 体系

---

## 1) 当前设计问题清单（按优先级排序）

| 优先级 | 编号 | 问题 | 维度 | 证据位置 |
|---|---|---|---|---|
| **P0** | #1 | 开发者内容泄漏到跑者界面：主导航「评测」页 + 仪表盘「模型质量」卡 | 信息架构 / 受众错配 | `base.html:80`、`index.html:424-458` |
| **P0** | #2 | 顶部「搜索 / 通知 / 头像」与侧栏 Orb 卡均为「假按钮」，无任何行为 | 可用性 / 信任 | `base.html:45-67`、`theme.css:232-242` |
| **P0** | #3 | 设计契约与实现「双轨分裂」：契约为 总览/分析/趋势/计划/教练，实现为 总览/洞察/计划/教练/评测；且 `design/pages/*` 与 `templates/*` 为两套独立源码 | 信息架构 / 一致性 | `.design` 画布、`base.html:72-82` |
| **P1** | #4 | 侧边栏宽度契约不一致：契约 240px，实现 220px | 视觉一致性 | `theme.css:587,678` vs `orchestration-summary.json` |
| **P1** | #5 | 徽章 / 强调色两套实现：`.badge-*` 硬编码 rgba，`.insight-card-badge` 用 color-mix；`execution.css` focus 阴影硬编码 | 视觉一致性 | `theme.css:808-821`、`execution.css:146` |
| **P1** | #6 | 仪表盘缺配速 / 心率趋势图：`kpi.js` 已支持但页面无 `#paceChart/#hrChart`，`renderCharts()` 空转 | 交互 / 信息完整 | `index.html:629`、`kpi.js:88-149` |
| **P1** | #7 | 响应式断点跳跃：KPI 2 列直跳 5 列，中屏（800–1100px）拥挤 | 视觉 / 密度 | `index.html:11-19` |
| **P1** | #8 | 强调层级不一致：洞察卡用 4px lime 左边框，其余卡 border-led | 视觉一致性 | `index.html:462` |
| **P2** | #9 | `color-mix` 浏览器兼容风险（Safari <16.2 / Chrome <111 失效） | 健壮性 | `index.html:144` 等多处 |
| **P2** | #10 | 动效未尊重 `prefers-reduced-motion`：Orb / 骨架屏 / typing 持续动画 | 可访问性 | `theme.css`、`coach.html:316` |
| **P2** | #11 | 移动端底部导航缺「评测」且分组丢失，与桌面 IA 不一致 | 信息架构 | `base.html:111-124` |
| **P2** | #12 | 暗/亮主色语义不统一：亮色 `#7AA800` 深绿 vs 暗色 `#C6FF1F` 亮柠檬；图表线直接取 `--rc-primary` 在亮色下对比偏低 | 视觉一致性 | `theme.css:26,160` |

---

## 2) 第一性原理分析 + 优化方案

### #1 开发者内容泄漏到跑者界面　【P0】

**第一性原理**
设计的根本目标是服务「进阶跑者」的核心需求——**一眼看懂状态、知道今天该做什么**。模型评测指标（准确性/相关性/完整性/实用性、Bad Case 数）是**产品内部的模型质量度量**，对跑者零决策价值。把它放进主导航和仪表盘，违背「专业克制可信赖」的品牌基调，且稀释了真正重要的训练信号，是典型的信息架构「受众错配」。

**优化方案（增量、不破坏契约）**
- 将「评测」从 `base.html` 主导航移除，降级到用户菜单 / 设置区的「开发者」入口（或 `/admin` 路由 + 开发态 flag），与设计契约的 5 页 IA 对齐。
- 仪表盘「模型质量」卡替换为设计本就规划的「教练周报 / 今日建议」卡（带依据链），即把 `insight` 卡片上移为仪表盘 hero 区，强化「今天该做什么」。
- 该改动**收敛**而非扩张实现 IA，与设计契约兼容。

```html
<!-- base.html：移除评测导航项 -->
{'group': '规划', 'items': [
  {'href': '/training', 'label': '训练计划', 'icon': 'calendar-days'},
  {'href': '/coach',    'label': 'AI 教练',  'icon': 'message-circle'},
]},
```

---

### #2 非功能性「假按钮」　【P0】

**第一性原理**
任何可见、可点击、带 hover 态的控件都向用户作出「可用」的承诺。一个点了没反应的按钮教会用户「这个界面不可信」——这是可用性的复利负债，比「没有该功能」更糟。

**优化方案**
- **搜索**：短期若无检索后端，直接隐藏 `search-wrapper`；中期实现客户端过滤（对仪表盘跑步列表 / 全局跑步记录做模糊匹配），复用 `profileData`。
- **通知 / 头像**：接真实入口（通知抽屉、用户菜单），或至少加空态提示；暂不接则移除红点 `badge-dot` 以免误导。
- **侧栏 Orb 卡**：`cursor:pointer` 但无 `onclick` → 包裹为 `<a href="/coach">`，让「AI 教练在线」成为进入教练台的入口，物尽其用。

```html
<!-- base.html：让 Orb 卡可点击 -->
<a href="/coach" class="orb-card" style="text-decoration:none;">
  <div class="coach-orb"></div> … 
</a>
```

---

### #3 设计契约与实现「双轨分裂」　【P0】

**第一性原理**
设计系统存在的第一性理由是**消除歧义、保证一致性**。当「设计稿」（`design/pages/*`）和「代码」（`templates/*`）成为两套独立真相源，二者必然随时间漂移，最终没人知道哪个为准。

**优化方案**
- 确立**唯一真相源**：以 `orchestration-summary.json` 的 5 页契约为准，在 `templates/base.html` 顶部注释声明导航契约，禁止随意增删顶层页。
- 「洞察」合并「分析+趋势」是合理的密度优化（已用 tab 实现），予以保留；唯一需纠正的是契约之外的「评测」页（见 #1）。
- 建议：设计稿更新后，用一次 diff 校验 `nav_data` 与契约一致，作为发布卡点。

---

### #4 侧边栏宽度契约不一致　【P1】

**第一性原理**
布局栅格（sidebar 240px + 主区 max-w-7xl）是设计契约的硬约束，任何偏离都会在跨页时产生 20px 的「对不齐」视差，损害专业感。

**优化方案**（一行级修改，零风险）
```css
/* theme.css */
.sidebar { width: 240px; }            /* 原 220px */
.main-content { margin-left: 240px; }  /* 原 220px */
```

---

### #5 徽章 / 强调色两套实现　【P1】

**第一性原理**
同一语义（成功/警告/危险）必须有唯一视觉表达式，否则用户无法建立稳定的颜色-含义联想。

**优化方案**
- 统一徽章底色为 `color-mix`，与 `.insight-card-badge` 一致，并加 rgba 兜底：
```css
.badge-success { background: rgba(47,169,104,0.12); background: color-mix(in srgb, var(--state-success) 14%, transparent); color: var(--state-success); }
/* warning / danger 同理 */
```
- `execution.css:146` 的 `box-shadow: 0 0 0 2px rgba(122,168,0,0.15)` 改为 `var(--rc-primary-bg)`，与 `theme.css` focus 态统一。

---

### #6 仪表盘缺配速/心率趋势图　【P1】

**第一性原理**
仪表盘的核心价值是「趋势感知」。KPI 给的是静态数字，而 `kpi.js` 已写好配速/心率趋势渲染逻辑——页面却没挂 `#paceChart/#hrChart` 容器，导致 `renderCharts(profileData)` 静默空转。这是「已建能力未落地」的体验缺口。

**优化方案**
在仪表盘「中部双列」右侧「就绪度」下方，新增两张 sparkline 卡（复用 `kpi.js` 既有逻辑，无需改 JS）：
```html
<div class="card"><h2>配速趋势</h2><div style="height:160px"><canvas id="paceChart"></canvas></div></div>
<div class="card"><h2>心率趋势</h2><div style="height:160px"><canvas id="hrChart"></canvas></div></div>
```

---

### #7 响应式断点跳跃　【P1】

**第一性原理**
5 张 KPI 卡在 ≥769px 即铺满，但 800–1000px 的笔记本屏下每张仅 ~150px，64px 英雄数字 + 单位会挤压换行。密度应随视口**渐进**而非突变。

**优化方案**（增中间档，不改结构）
```css
.kpi-grid { grid-template-columns: repeat(2,1fr); }            /* 移动 */
@media (min-width:560px){ .kpi-grid { grid-template-columns: repeat(3,1fr); } }
@media (min-width:1024px){ .kpi-grid { grid-template-columns: repeat(5,1fr); } } /* 仅宽屏铺满 */
```

---

### #8 强调层级不一致　【P1】

**第一性原理**
视觉强调是稀缺资源。设计基调为「border-led 克制」，洞察卡却用 4px lime 实色左边框，等于把最高强调级给了「一条提示」，削弱了真正 CTA 的权重。

**优化方案**
将左边框降为 2px 或改为卡内 lime 小标签，保留区隔但不抢戏；与「border-led」原则对齐。

---

### #9–#12 健壮性 / 可访问性（P2，简要）

- **#9 color-mix 兜底**：所有 `color-mix` 声明前补一行 rgba 兜底背景。
- **#10 动效降级**：在 `theme.css` 追加 `@media (prefers-reduced-motion: reduce){ .coach-orb,.ds-skeleton,.typing-dot,.skeleton-line{animation:none!important} }`。
- **#11 移动 IA 对齐**：底部导航与桌面导航项保持一致（若 #1 移除评测，则两端同步移除）。
- **#12 亮色图表对比**：亮色主题下图表线改用 `--rc-primary-strong`（深绿 `#4E7000`）以保证细线可读性，暗色保持 `--rc-primary` 柠檬。

---

## 3) 优化后的预期效果说明

1. **信任回归（P0）**：清除「假按钮」与开发者内容后，界面「言出必行」，跑者对产品专业度的感知显著上升；仪表盘首屏聚焦「状态 + 今日建议」，决策路径缩短。
2. **信息聚焦（P0/#6）**：模型评测回归开发者域，跑者视角更纯；仪表盘补齐配速/心率趋势，从「看数字」升级为「看走向」。
3. **一致性与可维护性（P1）**：侧栏宽度、徽章色、强调层级、响应式档位统一后，跨页视觉对齐，后续新增页面可直接套用契约，维护成本下降。
4. **包容性与鲁棒性（P2）**：动效尊重系统偏好、色板兼容旧浏览器、亮色对比达标，覆盖更广用户与设备，降低线上「看不清/动不停」的反馈工单。

> 总体：本次优化全部为**增量补强**——改动集中在 CSS 变量、导航项、2 个空容器与若干兜底声明，**未触碰设计契约的页面结构、Token 命名与组件契约**，可安全合并且易于回滚。
