# 跑步教练 Web · 视觉系统审计与优化方案（设计令牌 v3）

> 作者：设计系统专家「彩格调」(design-system-expert)
> 范围：维度2（整体样式与配色优化）+ 维度3（跑步主题元素融入）
> 方法：第一性原理审计现有代码 → 参考业界公开设计系统（GitHub Primer / Vercel Geist / Tailwind / Radix / Linear / Stripe 及 71 套品牌系统）→ 给出可落地令牌与主题元素规范。
> 定位：运动感 · 活力 · 科学/专业 · 可信赖；受众为国内业余→进阶跑者。

---

## 一、现有视觉系统速览 + 主要问题

### 1.1 速览
当前系统（`static/theme.css` 为单一事实源，`design-system.css` 为 `-ds-*` 兼容层，`base.html` 默认 `data-theme="dark"`）是一套 **Green / Teal / Orange 三色身份**：
- 品牌主色绿 `#16C784`(亮) / `#2BD493`(暗)，次色青 `#0EA5E9`，强调橙 `#FF6A1A`；
- 中性灰带绿微染；暗色背景 `#0E1216`；
- 语义色 success/info/warning/danger 齐全；字体 Inter / Inter Tight + JetBrains Mono；
- 已有氛围层(ambient)、Coach Orb（就绪度状态环）、焦点环、`prefers-reduced-motion` 支持。

**结论**：体系已搭好、工程化扎实，但「运动感缺失、绿调偏金融/加密、语义色与跑步领域错位、明暗对比度有硬伤、部分页面脱离令牌系统」。

### 1.2 主要问题清单

#### 配色 Color
| # | 问题 | 证据 | 严重度 |
|---|------|------|--------|
| C1 | **亮色模式白字落在青色按钮上对比度不达标** | `--rc-on-data:#FFFFFF` 叠在 `--rc-data:#0EA5E9`(亮) → ≈2.78:1，**未过 WCAG AA(需≥4.5)** | 高 |
| C2 | **亮色模式 info/warning 文本对比度不达标** | `#0EA5E9` 作文字叠白底(如「AI 分析」徽章) ≈2.65:1；`#F5A623` 警告文字 ≈2.0:1，均 **未过 AA** | 高 |
| C3 | **心率区间色全是绿色，与运动科学惯例错位** | `--rc-zone-1..5` 为同色系绿(E7F9F0→0B8F5A)。真实 HR zone 应为 冷→热 梯度，单色绿不专业、不可信 | 高 |
| C4 | **品牌绿读感偏「金融/加密」** | `#16C784` 近似 Binance/Robinhood 绿，缺少户外/速度气质，与跑步定位弱相关 | 中 |
| C5 | **脱离令牌系统的游离色** | `.coach-weekly-ai-badge` 用 `rgba(46,134,199,0.12)`(非调色板蓝)；`execution.css` 焦点环 `rgba(122,168,0,0.15)`(橄榄绿)；明细分隔 `rgba(128,128,128,0.12)` | 中 |
| C6 | **onboarding.html 几乎未使用令牌，硬编码兜底** | `#222`/`#9aa`/`#fff`/`rgba(255,255,255,.04)` 等；亮色下 `color:#fff` 输入框文字**直接不可见** | 高 |

#### 排版 Typography
| # | 问题 | 证据 | 严重度 |
|---|------|------|--------|
| T1 | **字号阶梯上限过低，缺少展示级数字** | 最大仅 `2.25rem(36px)`，但 KPI 大数字页面硬写 `4rem(64px)`、移动端 `!important 48px`，无统一 token → 各页大数字尺寸不一致 | 中 |
| T2 | **KPI 图标全部同色绿，无语义映射** | `trending-up/clock/gauge/zap` 均 `var(--rc-primary)` 绿，无法一眼区分「负荷/配速/就绪度」 | 低 |
| T3 | **趋势涨跌语义与跑步领域相反** | `.kpi-trend.up`=绿 / `.down`=橙。但跑者视角「配速下降=变快=好」，纯绿涨橙跌会误导 | 中 |

#### 组件 Components
| # | 问题 | 证据 | 严重度 |
|---|------|------|--------|
| P1 | **onboarding 自带 `.btn` 覆盖全局，尺寸/圆角漂移** | 全局 `.btn`=padding 8/16、radius 8；onboarding `.btn`=10/18、radius 10 → 同站按钮不一致 | 中 |
| P2 | **圆角尺度与令牌脱节** | 页面里出现 `border-radius:18px/10px`，而令牌最大 `lg=12px` | 低 |
| P3 | **图表配色与令牌重复定义** | `getChartColors()` 在 `base.html` 内联默认值，未与 `--rc-chart-*` 完全对齐管理 | 低 |
| P4 | **emoji 作装饰，违背「科学/专业」调性** | onboarding `🎉`、结果 `⚠️/✅` | 低 |

#### 主题元素 Theme Elements
| # | 问题 | 证据 | 严重度 |
|---|------|------|--------|
| E1 | **跑步主题元素近乎缺席** | 全站仅 Orb 与一层 opacity≈5% 的氛围渐变（肉眼几乎不可见），数据卡/分隔/背景无跑道、速度线、心率波形等运动语言 | 高（维度3核心） |
| E2 | **氛围层「名存实亡」** | `--rc-ambient-*` 透明度仅 0.05–0.06，且无实际图形，所谓「氛围」未传达 | 中 |

---

## 二、2–3 套配色方向对比

> 参考基准：GitHub Primer（系统化中性灰+单强调色，全 AA）、Vercel Geist（克制、单一强调、大留白）、Tailwind（50–950 完整色阶）、Radix（可导出的可访问色阶）、Linear（暗色优先、鲜亮强调、动效克制）、Stripe（鲜亮渐变、精确数据可视化）。跑步类真实产品参考：Strava(橙/黑)、Nike Run Club(volt 荧光)、Garmin Connect(蓝/青数据)、Adidas Running(蓝)、Runkeeper(绿/蓝)。

| 方向 | 主色 | 强调色(活力) | 中性色 | 语义色(成功/信息/警告/危险) | 适用理由 | 参考来源 |
|------|------|-----------|--------|-------------------------------|----------|----------|
| **A. Pulse Green 脉冲绿（推荐）** | 鲜亮春绿 `#10E08A`（保留现有品牌资产，提亮+偏青） | **Volt 荧光黄绿 `#C6F000`**（专用「进行中/在区/CTA 脉冲」） | 绿染近黑中性 `#0B0F0E`→`#1A221F` | 绿/青/琥珀/珊瑚红（修复对比度） | 运动感(Volt)+科学(青数据)+可信赖(绿身份)三者平衡；迁移成本最低，保留用户已有认知 | Primer 绿 + Radix grass + Linear 暗色 + Nike Volt |
| **B. Cobalt Pulse 电光蓝** | 电光蓝 `#3B82F6`/`#2563EB` | 霓虹青柠 `#BEF264`（活力点缀） | 近黑中性偏蓝 `#0B1020` | 蓝/青/琥珀/红 | 最强「科学/专业/可信赖」：蓝=数据&信任（Garmin/Adidas 同路）；青柠提供运动感跃动 | Vercel Geist 蓝 + Linear 蓝紫 + Radix blue/indigo |
| **C. Magma Sprint 熔岩橙** | 暖橙红 `#FB4D0A`/`#FF6A1A` | 荧光黄 `#FACC15` | 近黑暖中性 `#14110E` | 橙/青(互补对比)/琥珀/红 | 最强「运动感/活力/心率/强度」：橙=能量&心率（Strava/Nike/Spotify 同路），与青形成高能撞色 | Strava 橙 + Nike + Stripe 暖 + Brutalist 橙红 |

**选择建议**：A 为默认落地方案（兼顾品牌资产与三要素）；B 适合做「科学数据」副主题/深色仪表盘变体；C 适合作为高强度/比赛日状态的强调子品牌，不宜做全局主色（弱化「冷静可信」）。

---

## 三、推荐方案设计令牌文档（DESIGN.md · 9 段）

> 落地策略：**保留现有 `--rc-*` 命名**（避免大规模改类名），以「修正值 + 增量令牌」方式升级：
> - 修正：`--rc-on-data`(亮)白→深；`--rc-zone-1..5` 改为冷→热；`--rc-text-muted` 提亮；`--rc-chart-*` 对齐。
> - 新增：`--rc-energy`(Volt)、`--rc-glow-energy`、`--rc-radius-2xl`、`--rc-text-4xl/5xl`、`--rc-space-16/20/24`、`--rc-text-on-energy`、`--rc-info-fg`/`--rc-warning-fg`/`--rc-danger-fg`（亮色模式文字专用深色）。
> - 回填：onboarding.html / execution.css 改用令牌，移除游离硬编码色。

### 1. Visual Theme
- **Philosophy**：像一块为跑者调校的运动仪表盘——克制、精确、有速度感，但绝不喧哗。
- **Direction**：Tech Utility（暗色优先、数据密集、鲜亮强调）× Modern Minimal（单一主色、严格栅格）× 运动微调。
- **Personality**：confident（自信）· precise（精确）· energetic（有活力）· trustworthy（可信赖）。
- **Reference**：GitHub Primer（中性灰体系）+ Radix Colors（可访问色阶）+ Linear（暗色/动效克制）+ Nike Run Club（Volt 活力）+ Stripe（数据可视化精度）。

### 2. Color Palette
#### Primary（品牌绿，保留资产并提亮）
| Token | HEX | OKLCh(约) | Usage |
|-------|-----|-----------|-------|
| `--rc-brand` | `#10E08A` | oklch(80% 0.18 158) | 主操作/品牌身份/激活态 |
| `--rc-brand-hover` | `#0FCB7E` | — | Hover |
| `--rc-brand-active` | `#0A9A63` | — | Active/Pressed |
| `--rc-on-brand` | `#04140D` | — | 绿底之上的文字（深，对比≥7:1） |
| `--rc-brand-soft` | `rgba(16,224,138,0.12)` | — | 绿淡底（选中/高亮区） |
| `--rc-brand-border` | `rgba(16,224,138,0.30)` | — | 绿描边 |

#### Energy（Volt 荧光，专用「活力/进行中/在区」）
| Token | HEX | OKLCh(约) | Usage |
|-------|-----|-----------|-------|
| `--rc-energy` | `#C6F000` | oklch(90% 0.20 115) | CTA 脉冲/直播中/心率在区/焦点强调 |
| `--rc-energy-hover` | `#D6FF1A` | — | Hover |
| `--rc-energy-active` | `#B2DA00` | — | Active |
| `--rc-on-energy` | `#1A2400` | — | Volt 底之上文字（深） |
| `--rc-energy-soft` | `rgba(198,240,0,0.12)` | — | Volt 淡底 |

#### Data（电光青，数据/图表专用）
| Token | HEX | OKLCh(约) | Usage |
|-------|-----|-----------|-------|
| `--rc-data`(暗) | `#2BC4E6` | oklch(75% 0.12 220) | 图表线/数据强调（暗色） |
| `--rc-data`(亮) | `#0B7FA0` | — | 亮色模式填充（保证白字 AA） |
| `--rc-on-data`(暗) | `#04222B` | — | 数据填充上文字（深，修复 C1） |
| `--rc-on-data`(亮) | `#FFFFFF` | — | 亮色模式白字 |
| `--rc-data-soft` | `rgba(43,196,230,0.12)` | — | 数据淡底 |

#### Neutral（暗色优先）
| Token | HEX | Usage |
|-------|-----|-------|
| `--rc-background` | `#0B0F0E` | 页面背景（绿染近黑） |
| `--rc-surface` | `#141A18` | 卡片/分区背景 |
| `--rc-surface-raised` | `#1A221F` | 浮起表面 |
| `--rc-surface-sunken` | `#0E1311` | 凹陷/输入底 |
| `--rc-border` | `rgba(255,255,255,0.08)` | 分隔/描边 |
| `--rc-border-strong` | `rgba(255,255,255,0.14)` | 强分隔 |
| `--rc-border-faint` | `rgba(255,255,255,0.05)` | 极弱分隔 |
| `--rc-text` | `#ECF3EF` | 主文本/标题 |
| `--rc-text-secondary` | `#9FAAA4` | 次级文本 |
| `--rc-text-muted` | `#7C887F` | 弱化文本（提亮修复，≥AA） |
| `--rc-ring` | `#10E08A` | 焦点环 |
| `--rc-ring-offset` | `#0B0F0E` | 焦点环偏移 |

#### Neutral（亮色）
| Token | HEX | Usage |
|-------|-----|-------|
| `--rc-background` | `#F6FAF7` | 页面背景 |
| `--rc-surface` | `#FFFFFF` | 卡片 |
| `--rc-surface-raised` | `#EEF3F0` | 浮起 |
| `--rc-surface-sunken` | `#EAF1ED` | 凹陷 |
| `--rc-border` | `rgba(16,32,28,0.12)` | 分隔 |
| `--rc-border-strong` | `rgba(16,32,28,0.20)` | 强分隔 |
| `--rc-border-faint` | `rgba(16,32,28,0.07)` | 极弱分隔 |
| `--rc-text` | `#16201C` | 主文本 |
| `--rc-text-secondary` | `#445049` | 次级 |
| `--rc-text-muted` | `#5E6862` | 弱化 |
| `--rc-ring` | `#0A9A63` | 焦点环 |
| `--rc-ring-offset` | `#FFFFFF` | 焦点环偏移 |

#### Semantic（成功/信息/警告/危险）
| Token | HEX | Usage |
|-------|-----|-------|
| `--rc-success` | `#2BD493` | 正向状态（暗底文字用 `--rc-on-brand`） |
| `--rc-info`(暗) | `#38BDF8` | 信息填充（暗底文字 `#06283A`） |
| `--rc-info-fg`(亮文字) | `#0369A1` | **亮色模式 info 文字专用（修复 C2，≥AA）** |
| `--rc-warning` | `#FFB02E` | 警告填充（暗底文字 `#2A1500`） |
| `--rc-warning-fg`(亮文字) | `#92560A` | **亮色模式 warning 文字专用（修复 C2）** |
| `--rc-danger` | `#FF5C5C` | 危险/破坏（暗底文字 `#2A0606`） |
| `--rc-danger-fg`(亮文字) | `#B91C1C` | **亮色模式 danger 文字专用** |

#### HR Zones（冷→热，领域正确，修复 C3；亮色为加深填充，保证白字 AA）
| Token | 暗色 HEX | 亮色 HEX | 含义 |
|-------|---------|---------|------|
| `--rc-zone-1` | `#38BDF8` | `#0369A1` | Z1 恢复/轻松（冷蓝） |
| `--rc-zone-2` | `#34D399` | `#047857` | Z2 有氧（绿） |
| `--rc-zone-3` | `#FBBF24` | `#B45309` | Z3 节奏（琥珀） |
| `--rc-zone-4` | `#FB923C` | `#C2410C` | Z4 阈值（橙） |
| `--rc-zone-5` | `#FF5C5C` | `#B91C1C` | Z5 无氧/最大摄氧（红） |

#### Chart
| Token | HEX | Usage |
|-------|-----|-------|
| `--rc-chart-1` | `#10E08A` | 品牌绿（主序列） |
| `--rc-chart-2` | `#2BC4E6` | 数据青 |
| `--rc-chart-3` | `#FFB02E` | 琥珀 |
| `--rc-chart-4` | `#C6F000` | Volt |
| `--rc-chart-5` | `#FF5C5C` | 危险红 |
| `--rc-chart-grid` | 随主题 `rgba(...,0.06/0.08)` | 网格线 |

### 3. Typography
- **Heading/Display**：`'Inter Tight','Inter',-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif`
- **Body**：`'Inter',-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif`
- **Mono（数据数字专用）**：`'JetBrains Mono','SF Mono','Consolas',ui-monospace,monospace`

| Level | Size | Weight | Line-height | Usage |
|-------|------|--------|-------------|-------|
| Display-2 | 60px / 3.75rem (5xl) | 700 | 1.05 | Hero 超大数字（如里程/就绪度） |
| Display-1 | 48px / 3rem (4xl) | 700 | 1.05 | KPI 大数字（统一替换原硬写 4rem） |
| H1 | 36px / 2.25rem (3xl) | 700 | 1.2 | 页面标题 |
| H2 | 28px / 1.75rem (2xl) | 600 | 1.25 | 分区标题 |
| H3 | 22px / 1.375rem (xl) | 600 | 1.3 | 子分区 |
| Body | 15px / 0.9375rem (base) | 400 | 1.6 | 正文 |
| Small | 13px / 0.8125rem (sm) | 400 | 1.5 | 注释/元信息 |
| Micro | 12px / 0.75rem (xs) | 500 | 1.4 | 徽章/标签 |
- **数字约定**：所有数据读数（里程/配速/心率/负荷）使用 `.tnum { font-variant-numeric: tabular-nums; font-family: var(--rc-font-mono); }` 保证对齐与「仪表盘感」。

### 4. Component Styles
#### Button
- **Primary**：bg `--rc-brand`，text `--rc-on-brand`，radius `--rc-radius-md`，padding `8px 16px`；hover `--rc-brand-hover`，active 缩放 `0.97`。
- **Secondary**：透明底 + `1px solid --rc-border`，text `--rc-text`；hover `--rc-surface-raised` + `border-color:--rc-brand-border`。
- **Ghost**：透明底，text `--rc-brand`；hover `--rc-brand-soft`。
- **Energy（脉冲 CTA）**：bg `--rc-energy`，text `--rc-on-energy`，hover `--rc-energy-hover`（仅用于「进行中/直播/在区」等 ≤2 处/屏）。

#### Card
- bg `--rc-surface`；border `1px solid --rc-border`；radius `--rc-radius-lg`(14px)；padding `20px`；shadow `--rc-shadow-md`；hover 仅 `border-color:--rc-brand-border`（克制，不盲目上浮）。数据卡顶部可加「里程刻度」装饰（见第四节）。

#### Input
- height `36px`；bg `--rc-surface-sunken`；border `1px solid --rc-border`；radius `--rc-radius-md`；focus：`border-color:--rc-brand` + `box-shadow:0 0 0 3px --rc-brand-soft`（**修复 execution.css 橄榄绿游离色**）。placeholder `--rc-text-muted`。

#### Badge / Tag
- 成功：`bg rgba(43,212,147,0.14)` + `text --rc-success`；信息：`bg rgba(56,189,248,0.14)` + `text (暗) --rc-info / (亮) --rc-info-fg`；警告/危险同理用 `-fg` 文字变体（**修复 C2**）。

#### Progress / 数据展示
- 进度条 = 「跑道车道」样式：轨道 `2px dashed --rc-border-strong`（中线虚线），填充段 `--rc-brand`，末端一个「跑者圆点」(6px, `--rc-energy`)。用于训练完成度/就绪度环。
- 图表线使用 `--rc-chart-*`，折线 tension 0.4，hover 点 `--rc-brand`。

### 5. Layout
- **Grid**：容器 max-width `1200px`；主内容 padding `32px 40px`（桌面）/ `16px`（移动）；KPI 网格 4 列(≥769px) / 2 列(移动)。
- **Spacing Scale**（修正：上限扩到 96px）
| Token | Value | Usage |
|-------|-------|-------|
| `--rc-space-1..12` | 4/8/12/16/20/24/32/40/48px | 继承现有 |
| `--rc-space-16` | 64px | 大分区留白 |
| `--rc-space-20` | 80px | Hero/页脚节奏 |
| `--rc-space-24` | 96px | 整页章节间距 |

### 6. Depth & Elevation
| Level | Shadow(暗) | Shadow(亮) | Usage |
|-------|-----------|-----------|-------|
| Flat | none | none | 默认表面 |
| Sm | `0 1px 2px rgba(0,0,0,0.30)` | `0 1px 2px rgba(16,32,28,0.06)` | 卡片 |
| Md | `0 8px 24px -8px rgba(0,0,0,0.45)` | `0 6px 16px -6px rgba(16,32,28,0.12)` | 浮起卡/下拉 |
| Lg | `0 20px 40px -12px rgba(0,0,0,0.55)` | `0 16px 32px -12px rgba(16,32,28,0.18)` | Modal/浮层 |
| Glow-Brand | `0 0 24px rgba(16,224,138,0.35)` | `0 0 18px rgba(16,224,138,0.25)` | Orb/品牌焦点（**亮色也保留，修复原 `none`**） |
| Glow-Energy | `0 0 24px rgba(198,240,0,0.28)` | `0 0 18px rgba(198,240,0,0.22)` | 脉冲 CTA/直播态 |

- **Z-index**：base 0 · dropdown 100 · sticky/top-nav 100 · sidebar 90 · modal 300 · toast 400 · ambient 0(置底，不挡内容)。

### 7. Cautions
#### Never Do
- 不要用 `#fff` 硬编码输入框文字（亮色模式直接不可见，C6）。
- 不要让白字落在青/琥珀等中明度填充上（C1/C2，必须过 AA）。
- 不要用单色绿表达心率区间（C3，必须冷→热）。
- 不要把 Volt/橙红等暖色铺满整屏或每屏超过 2 个视觉焦点。
- 不要用 emoji 作装饰（🎉⚠️✅ → 改用 lucide 图标 + 文字，P4）。
- 不要引入不在令牌系统内的游离色（C5，如橄榄绿焦点环、非调色板蓝）。
- 不要真实跑者照片/大图作背景（重、易廉价、加载慢）。

#### Prefer
- 每个色相只承担一种语义：绿=身份/主操作，Volt=活力/在区，青=数据，橙红=高强度/告警，蓝=次级链接/「科学」引用。
- 主题元素用 SVG 矢量 + 低透明度，可随主题切换。
- 大数字统一走 `4xl/5xl` token + `.tnum`，不在页面硬写 px。
- 动效统一遵守 `prefers-reduced-motion`。

### 8. Responsive Behavior
| Name | Width | Behavior |
|------|-------|----------|
| Mobile | < 640px | 单列堆叠；侧栏转抽屉；底部 Tab 导航；KPI 大数字 `48px` |
| Tablet | 640–1024px | KPI 2 列；中部双列转单列 |
| Desktop | > 1024px | 完整布局（侧栏 + 主内容 + 4 列 KPI） |

- 适配规则：主内容 `margin-left` 在移动端归零；搜索框展开宽度 160px(移动)/240px(桌面)；所有装饰层在 <640px 自动降透明度或隐藏以保性能与可读性。

### 9. Agent Prompt Guide
- **Key Instructions**：
  1. 生成任何组件前先引用 `--rc-*` 令牌，禁止新硬编码 HEX（除非新增令牌并登记）。
  2. 涉及文字对比度，默认校验 AA（普通文本≥4.5:1，大字≥3:1）；亮色模式务必用 `-fg` 深色文字变体。
  3. 心率/区间/强度一律走 `--rc-zone-*` 冷→热，不得用品牌绿替代。
  4. 运动主题装饰统一调用第四节的 SVG 组件，透明度受控。
- **Quick CSS Snippet（核心令牌）**：
```css
:root{
  --rc-brand:#10E08A; --rc-on-brand:#04140D; --rc-brand-soft:rgba(16,224,138,0.12); --rc-brand-border:rgba(16,224,138,0.30);
  --rc-energy:#C6F000; --rc-on-energy:#1A2400; --rc-energy-soft:rgba(198,240,0,0.12);
  --rc-data:#2BC4E6; --rc-on-data:#04222B; --rc-data-soft:rgba(43,196,230,0.12);
  --rc-background:#0B0F0E; --rc-surface:#141A18; --rc-surface-raised:#1A221F;
  --rc-text:#ECF3EF; --rc-text-secondary:#9FAAA4; --rc-text-muted:#7C887F;
  --rc-border:rgba(255,255,255,0.08); --rc-ring:#10E08A;
  --rc-zone-1:#38BDF8; --rc-zone-2:#34D399; --rc-zone-3:#FBBF24; --rc-zone-4:#FB923C; --rc-zone-5:#FF5C5C;
  --rc-radius-sm:6px; --rc-radius-md:10px; --rc-radius-lg:14px; --rc-radius-xl:20px; --rc-radius-2xl:28px;
  --rc-shadow-md:0 8px 24px -8px rgba(0,0,0,0.45); --rc-glow-brand:0 0 24px rgba(16,224,138,0.35);
  --rc-text-4xl:3rem; --rc-text-5xl:3.75rem;
  --rc-space-16:64px; --rc-space-20:80px; --rc-space-24:96px;
}
```

---

## 四、跑步主题元素规范（维度3）

### 4.1 可使用的视觉元素 + 建议位置
| 元素 | 形态 | 建议位置 | 色彩/透明度 |
|------|------|----------|--------------|
| **跑道弧线 Lane curves** | SVG 同心/单条跑道弧，描边 | Hero 顶部背景、页脚、空状态底纹 | `--rc-brand` 描边，`opacity ≤ 0.06` |
| **速度线 Speed lines** | 3 条内收的水平/斜向细线，暗示加速 | 数据卡上边缘、「配速提升」处、CTA 旁 | `--rc-energy` 或 `--rc-brand`，`opacity ≤ 0.05` |
| **里程刻度 Distance ruler** | 卡顶一条带刻度 tick 的标尺 | KPI 卡 / Dashboard 分区卡顶部 | 中性 `--rc-border-strong`，刻度 `--rc-text-muted` |
| **心率波形 HR waveform** | 内联 SVG 心电/波形 sparkline | 数据卡内、章节分隔线、就绪度区 | 用 `--rc-zone-*`（随强度变色） |
| **进度跑道 Progress track** | 虚线中线 + 末端跑者圆点 | 训练完成度、就绪度环、周目标 | 轨道 `--rc-border-strong`，点 `--rc-energy` |
| **渐变光晕 Gradient glow** | 径向品牌光晕 | Hero 背后、Coach Orb 周围（已有，放大一档） | `--rc-glow-brand` / `--rc-glow-energy` |
| **图标语义映射** | lucide 图标按语义着色 | 全局导航/卡片图标 | zap/进行中→`--rc-energy`；gauge/负荷→`--rc-warning`；heart/心率→`--rc-zone-5`；trend→`--rc-brand`；calendar→`--rc-data`（修复 T2 全绿） |
| **空状态插画** | 极简跑者剪影线稿（lucide `person-standing`/自定义） | 列表/图表空态 | 单色 `--rc-text-muted`，`opacity 0.5` |

### 4.2 配色差异用法（一色一职）
- **绿 `--rc-brand`**：仅身份与主操作（按钮/激活态/Logo）。不铺满背景。
- **Volt `--rc-energy`**：仅「活力/进行中/在区/直播」脉冲态，每屏 ≤2 焦点。
- **青 `--rc-data`**：仅数据线与图表（科学/专业感来源）。
- **橙红 `--rc-zone-4/5` + `--rc-danger`**：仅高强度区间与告警，绝不作常驻装饰。
- **蓝 `--rc-info`**：仅次级链接与「科学引用」标注，克制使用。

### 4.3 克制原则（红线，避免喧宾夺主）
1. 背景装饰层 `z-index` 恒低于内容；透明度：速度线/弧线 ≤ 6%，且**全站同时不超过 2 处**装饰层。
2. 禁用真实跑者照片/大图背景（重量级、易显廉价、拖慢加载）。
3. 禁用 emoji 装饰（移除 🎉⚠️✅），一律图标 + 文字。
4. 暖色（Volt/橙/红）每屏视觉焦点 ≤ 2；心率红仅用于 Z5/告警。
5. 所有动效（光晕呼吸、进度点、波形）必须纳入 `prefers-reduced-motion` 关闭列表。
6. 装饰不得侵蚀文字可读性：背景装饰下，文字对比度仍须 ≥ AA。
7. 主题元素只做「暗示」，不做「叙事」——用户一眼感到「这是运动产品」，但不被图形干扰读数据。

---

## 五、为什么这样选（设计决策说明）

1. **保留绿、提亮并加 Volt，而非推倒重来**：现有产品已有绿身份与用户认知（迭代而非革命，符合 team-lead「不是从零套模板」）。绿=健康/出发/正向，天然契合跑步；但原绿偏金融/加密调性，故提亮偏青并引入 **Volt 荧光**作为专用「活力/速度」强调——这是 Nike Run Club 验证过的运动语言，用最低迁移成本补上缺失的「运动感」。

2. **青=数据**承接 Linear / Vercel Geist 的「冷色=分析/信任」纪律，让图表与数字有「科学/专业」气质；同时修复了原白字落青色按钮的 AA 硬伤（C1）与亮色 info/warning 文字对比度（C2）。

3. **心率区间改为冷→热**（蓝→绿→琥珀→橙→红）是领域正确性的关键一步：一个自称「科学教练」的产品若用单色绿表达 HR zone，会直接损害「专业/可信赖」。这是用设计语言兑现产品承诺。

4. **对比度全量过 WCAG AA**：把原亮色模式白字/info/warning 的达标缺陷（C1/C2）系统性修补，并新增 `-fg` 文字变体令牌，保证明暗双主题都可达。

5. **主题元素「克制暗示」而非「铺张」**：用 SVG 矢量（跑道弧/速度线/里程刻度/心率波形/进度跑道）以 ≤6% 透明度点缀，既传达运动身份，又不干扰数据阅读；并设 7 条红线防止过度装饰。参考 Stripe 的精确与 Apple 的克制。

6. **工程友好**：沿用 `--rc-*` 命名、以「修正值 + 增量令牌」升级，并明确要求回填 onboarding/execution 的游离硬编码（C5/C6/P1），使原型构建师可直接替换 `theme.css` 取值，无需重写类名。
