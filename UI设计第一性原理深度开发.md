# 跑步教练 Web · UI 第一性原理深度开发规范

> 输入依据：《原型外观设计第一性原理分析.md》（I1–I8，公理 A1–A4，缺陷 D1–D7）
> 落地状态：本规范全部条目已在当前代码库实现并验证（5 路由 200，服务运行中）
> 文档定位：设计 ↔ 实现的单一对应说明。每个组件给出 **结构说明 / 交互逻辑流程 / 视觉层级**，并回归其本质目的。

---

## 1. 设计约束条件（先定边界，再谈设计）

### 1.1 目标平台
- **形态**：Web 应用，Flask + Jinja2 服务端渲染 + 原生 JS 模块（`window.COACH.*`）+ Chart.js 4.4.1（`<canvas>`）。
- **响应式**：桌面（侧栏 240px + 顶栏 56px）优先；移动端降级为底部 dock + 汉堡抽屉。视口断点 **560px / 769px**。
- **浏览器**：现代引擎（CSS 自定义属性、Grid、`conic-gradient`、`color-mix` 兜底）；尊重 `prefers-reduced-motion: reduce`。

### 1.2 用户群体
- **进阶跑者**（self-coached，借助 AI 教练做训练决策）；中文界面；偏好低密度、专业、克制的视觉。

### 1.3 性能要求
- 暗色优先渲染；图表**真实渲染**（非空占位容器）；
- 氛围层静态化（去除常驻 `fixed` 重绘，I7），感知成本≈0；
- 主题切换时 `destroyAllCharts()` 防 Chart.js 实例泄漏；图表颜色**从 token 实时重算**（非硬编码）；
- 首屏同步构建跑者画像（本地 9 个运动文件），无外部阻塞。

### 1.4 品牌 / 技术约束
- **唯一令牌源** = `static/theme.css`（`--rc-*` 语义变量），由 `design/running-coach-web/theme-tokens.css` 推导；
- 禁止裸色值、禁止 LIME/AMBER 残留（I1 / I8）；任何新组件必须引用 `--rc-*`。

---

## 2. 用户核心需求 → 第一性原理公理

**核心需求**：让跑者「一眼看懂状态 → 知道今天该做什么 → 能问 AI 拿到个性化建议」。

由此反推四条不可绕开的公理（源自分析文档）：

| 公理 | 含义 | 对本 UI 的约束 |
|---|---|---|
| **A1 身份一致性** | 产品须有唯一视觉身份 | 全站只用 GREEN/TEAL/ORANGE 三角色 |
| **A2 视觉层级 = 信息层级** | 强调是稀缺资源，须精确映射"此刻最该看/做" | 英雄数字 > 数据 > AI 引导 > Chrome |
| **A3 信号可信度** | 像按钮的控件必须真有行为 | 无假按钮、无常亮红点、无空 `--` 占位 |
| **A4 感知 ROI** | 每个视觉成本须换回可感知价值 | 氛围层静态化；图表真实；动画尊重降级 |

**组件优先级推导**：状态可见性（A2）＞ 数据可追溯（A4）＞ AI 引导（A3 可信）＞ 装饰（A4 删冗余）。

---

## 3. 视觉身份地基（I1）：GREEN / TEAL / ORANGE 三角色

这是所有组件配色的**唯一真相源**，地基不稳则后续皆漂移。

| 角色 | 变量 | 浅色 | 暗色 | 语义 |
|---|---|---|---|---|
| 主色 Run Green | `--rc-brand` / `--rc-primary` | `#16C784` | `#2BD493` | 健康 / 出发 / 正向 / 主按钮 |
| 辅助色 Data Teal | `--rc-data` | `#0EA5E9` | `#38BDF8` | 数据 / 科学 / 恢复 / 次图表 / AI |
| 强调色 Energy Orange | `--rc-energy` | `#FF6A1A` | `#FF8A47` | CTA / 进行中 |
| 状态轴（唯一映射） | `--state-*` | success/warning/error | | 绿 → 琥珀 → 红 |

**强调色纪律**：橙 **唯一**用于 CTA；青用于数据与 AI 视角；状态轴绿→琥珀→红唯一映射。屏占比 ≤ 8–10%，把稀缺视觉权重留给真正重要的 CTA 与数据。

---

## 4. 关键组件拆解

### 4.1 应用外壳 App Shell
**结构说明**：`top-nav`(56px) + `sidebar`(240px) + `main-content` + `mobile-bottom-nav`(4 tab) + `mobile-overlay`。
- 顶栏：brand（`activity` 图标 + "跑步教练" + `Web` 徽章）+ `nav-spacer` + `nav-actions`（搜索 / 主题切换 / 通知 / 头像）。
- 侧栏：两组导航——`数据`（总览 / 洞察）、`规划`（训练计划 / AI 教练 / 评测）+ 底部 `orb-card`。

**交互逻辑流程**：
1. 点击 `nav-item` → 服务端路由重渲染，`active` 类按 `request.path` 高亮；
2. 移动端汉堡 → `toggleMobileSidebar()` 切换侧栏 + overlay；
3. 底部 dock（总览 / 洞察 / 计划 / 教练）拇指可达，与侧栏 IA 对齐。

**视觉层级**：L4 背景层。中性灰底，品牌绿仅出现在 active/hover——外壳刻意低强调，让主内容区独占注意力（A2）。

---

### 4.2 Coach Orb（签名资产 · I2 状态环）
**结构说明**：`.coach-orb` 圆形，背景
`conic-gradient(var(--rc-brand) calc(var(--orb-readiness,70) * 1%), var(--rc-border-strong) 0)`
形成就绪度弧；中心 `.orb-value` 显示数值；外层 `.orb-card`（`∈ <a href="/coach">`）含 `orb-label` / `orb-status`。

**交互逻辑流程**：
1. `initCoachOrb()` 读 `window.COACH_ORB.readiness`；
2. 有值 → 设 `--orb-readiness` 变量 + 填中心数值；缺失 → 回落中性呼吸态；
3. 点击卡片 → 跳转 `/coach`（**真实入口**，修复原"假按钮"，I4）。

**视觉层级**：L1 品牌层，但权重克制。靠"弧 + 数字"直接回答"今天恢复如何"，对标 Oura/WHOOP——**签名即数据**（A2 + A4：最大视觉资产承载最大信息价值）。

---

### 4.3 KPI 卡片（英雄指标 · A2 顶层）
**结构说明**：`.kpi-grid` 网格（移动 2 列 / ≥769px 4 列；注：I5 的 560/1024 三档密度仅部分落地，当前为 2→4）。每卡 =
`header`(label + icon) + `value`(数字 `4rem` / Inter Tight / `--rc-primary-strong` + unit) + `footer`(trend / badge)。
四卡：周跑量 / 平均配速 / 训练负荷（含 `loadSparkline` + 偏高·适中 badge）/ 就绪度（良好·需关注 badge）。空值显示 `--`（I4：不制造虚假占位，而是引导上传）。

**交互逻辑流程**：数值由服务端 `kpi` dict 注入 → 趋势箭头按 `weekly_trend` 正负着色（success / warning）→ 负荷 ≥80 显"偏高"。

**视觉层级**：L1 最高优先级。英雄数字是"状态一眼懂"的承载（A2），仅用主色 / 状态色，无任何装饰。

---

### 4.4 图表系统（数据可追溯 · A4）
**结构说明**：统一 Chart.js，四图——
- `readinessTrendChart`：7 天跑量柱状（右列主趋势）；
- `loadSparkline`：KPI 卡内极小折线（负荷）；
- `evalRadarChart`：准确性 / 相关性 / 完整性 / 实用性 雷达（**开发者评测页用，见 §8 待收口**）；
- `paceChart` / `hrChart`：配速 / 心率趋势（I6，≥2 次跑步才绘，否则空态）。
颜色取 `--rc-chart-1..5`（绿 / 青 / 橙 / 琥珀 / 红）。

**交互逻辑流程**：
1. `DOMContentLoaded` → 首渲各图；
2. `registerChart()` 登记实例；
3. `rc-theme-change` 事件 → `destroyAllCharts()` → 100ms 后重渲（用新 token 色）。

**视觉层级**：L2 内容层。承接英雄数字后的"为什么 / 趋势"，是数据可追溯的主体。

---

### 4.5 跑步列表（可追溯 · A2）
**结构说明**：`.runlist-table`（日期 / 活动 / 距离 / 配速 / 心率 / chevron）；行 hover 高亮 + chevron 浮现。

**交互逻辑流程**：行点击 → `location.href='/insights'`（携带上下文）；无数据 → 空态给上传 CTA（I4）。

**视觉层级**：L2。等宽数字对齐，`run-pace` 用主色强调，其余中性——把强调精确投在"最该比较的数字"上（A2）。

---

### 4.6 教练周报卡（AI 引导 · A3 可信）
**结构说明**：`.coach-weekly-card` 左边框 `2px solid --rc-data`（teal，I3 收敛层级）；`AI 分析` badge（info 色）；四段（总结 / 建议 / 风险 / 依据），风险段用 `--state-warning`。

**交互逻辑流程**：服务端注入 `coach_weekly` 字典 → 纯展示，无客户端状态。

**视觉层级**：L3 引导层。teal 左边框 = "这是 AI 的数据视角"，与 CTA 橙明确区分（A2：border-led 克制，不抢戏）。

---

### 4.7 顶栏控制件（Chrome · A3）
**结构说明**：搜索（`toggleSearch` 展开 input）/ 主题切换（`sun`+`moon` + localStorage）/ 通知（`bell` + `#notification-dot` **默认 `hidden`**）/ 头像。

**交互逻辑流程**：
1. 搜索 toggle → input 聚焦；`onblur` 空值则收起；
2. 主题 `toggleTheme` → `applyTheme` → 设 `data-theme` + localStorage + 派发 `rc-theme-change`；
3. 通知点**默认隐藏**，仅真实通知时显（I4：不制造假信号）。

**视觉层级**：L4 低强调。通知点"默认隐"是 A3 信号可信的关键修复——用户不再学会"这个界面不可信"。

---

### 4.8 氛围层（A4 成本收益 · I7 降级）
**结构说明**：`.ambient-layer` 全屏 `fixed`、极低 alpha 渐变，按时段切 `is-night / is-dawn / is-noon / is-dusk`；**已静态化**（去除 `will-change` 与 `ambient-drift` 动画，删两个 `@keyframes`）。

**交互逻辑流程**：启动 + 每分钟 `updateAmbient()` 切类。

**视觉层级**：L0 最底层背景，提供"在场感"但不抢戏。静态化后成本≈0、感知仍保留（A4：每个常驻成本必须换回价值，否则噪声）。

---

## 5. 视觉层级关系总图（A2）

```
L0  氛围层            静态渐变背景（在场感，零成本）
 │
L1  英雄 KPI           4rem 主色数字 —— 最高优先级，"状态一眼懂"
 │
L2  数据内容           图表(趋势/雷达) + 跑步列表 —— 主体，可追溯
 │
L3  AI 引导            教练周报（teal 左边框）—— 个性化建议视角
 │
L4  外壳 Chrome        导航 / 搜索 / 主题 / 通知 —— 低强调背景层
```

**强调色纪律**：橙 = 唯一 CTA（上传数据）；青 = 数据 / AI 视角；状态轴 绿→琥珀→红。强调色屏占比 ≤ 8–10%。

---

## 6. 交互逻辑总流程

```
首屏加载
  └─ 服务端构建跑者画像(本地 9 文件) → 注入 kpi / profile / weekly_series
       └─ 渲染外壳 + KPI + 图表 + 列表 + 周报

导航分支
  ├─ nav-item / dock / Orb 卡  → 路由 → 服务端重渲染(active 高亮)
  ├─ 主题 toggle              → data-theme 切换 → rc-theme-change → 图表销毁重渲
  └─ 数据动作(上传/生成/咨询)  → /training 或 /coach 对应路由

关键状态
  ├─ window.COACH_ORB.readiness  → Orb 状态环驱动
  ├─ localStorage['coach-theme'] → 主题持久化
  └─ window._chartInstances     → 主题切换时统一销毁防泄漏
```

---

## 7. 设计契约卡点（I8 · 防回归）

- **单一令牌源**：`static/theme.css`；任何新页面 / 组件须引用 `--rc-*`，不得硬编码颜色。
- **发布前检查**：`grep -rn "7AA800\|C6FF1F\|B45309\|5EEAD4" static/ templates/` 应为空（无 LIME/AMBER 残留）。
- **无死 UI**：无假按钮、无常亮红点、无裸 `--` 占位（A3）。
- 完整契约见 `DESIGN_CONTRACT.md`。

---

## 8. 与实现现状的对应 & 待收口项

**已落地（本规范全部条目）**：I1 三角色身份、I2 Orb 状态环、I3 teal 左边框收敛、I4 通知点 hidden + Orb 真入口 + 空态引导、I6 pace/hr 趋势图、I7 氛围静态化、I8 设计契约。服务运行中 5 路由全 200。

**已知待收口（非阻塞，留作下一步）**：
1. **I5 密度档位**：当前 KPI 为 2→4 列，未做 560/1024 的 2→3→5 渐进（中屏 800–1100px 仍有挤压风险）。
2. **P0 #1 角色收口**：导航"评测"与首页"模型质量"卡属开发者 / 评测域，未降级到设置域——属信息架构层待办，不在本 UI 规范范围但影响整体可信度。
3. **雷达图角色**：`evalRadarChart` 仅在评测数据存在时渲染，当前面向开发者，需在角色收口时决定对跑者是否暴露。

> 本文件即当前实现的第一性原理解释文档，可作为后续开发的评审基准。如需对上述任一待收口项或其它组件做进一步细化 / 扩展，可逐条指派。
