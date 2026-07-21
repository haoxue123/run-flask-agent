# 设计契约（Design Contract）

> 依据《原型外观设计第一性原理分析.md》I8 建立，作为发布前一致性卡点。

## 1. 唯一视觉身份（Single Source of Truth）
- **官方身份 = GREEN / TEAL / ORANGE 三角色**，由 `design/running-coach-web/theme-tokens.css` 第一性原理推导。
- 线上令牌**唯一真相源** = `static/theme.css`（已接入绿青橙系统）。
- 旧的 LIME 与 AMBER 两套已弃用，禁止重新引入。
- 任何新页面 / 组件**必须**引用 `static/theme.css` 的 `--rc-*` 变量，不得硬编码颜色值。

## 2. 三角色职责（不可混淆）
| 角色 | 变量 | 语义 |
|------|------|------|
| 主色 Run Green | `--rc-brand` / `--rc-primary` | 健康 / 出发 / 正向 / 主按钮 |
| 辅助色 Data Teal | `--rc-data` | 数据 / 科学 / 恢复 / 次图表 |
| 强调色 Energy Orange | `--rc-energy` | 活力 / CTA 行动点 / 进行中 |

- 状态轴唯一映射：**绿 → 琥珀 → 红**（`--state-success` / `--state-warning` / `--state-error`）。
- 强调层级（border-led）：insight 卡左边框 **2px**，统一用 `--rc-data`；CTA 唯一用品牌绿。

## 3. 签名资产约定
- 侧边栏 **Coach Orb** = 就绪度状态环（`conic-gradient` + 中心数值），由 `window.COACH_ORB.readiness` 驱动，缺失时回落中性呼吸态。
- 氛围层 = 静态低透明度绿系渐变，**禁止**常驻 drift 重绘（成本收益原则）。

## 4. 发布前检查（CI / 人工）
- [ ] `grep -rn "7AA800\|C6FF1F\|B45309\|5EEAD4" static/ templates/` 应为空（无 LIME/AMBER 残留）。
- [ ] 所有页面 5 路由 GET 200。
- [ ] 导航项与各页 `--rc-*` 引用一致，无裸色值。
- [ ] 无"假按钮 / 常亮红点 / 裸 `--` 占位"等死 UI（A3）。
