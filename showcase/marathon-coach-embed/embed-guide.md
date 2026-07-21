# 跑步教练 Demo · 腾讯文档嵌入指南

本目录 `index.html` 是一个**单文件、零外部依赖**的可嵌入演示页，完整复刻产品的
`/training` + `/coach` 视觉语言与核心交互，并包含实时跑步数据（配速/距离/心率）与实时教练反馈。

## 一、本地预览（无需部署）
直接双击 `index.html` 用浏览器打开即可运行；数据全程为前端模拟，无需后端。

## 二、部署到公开地址（嵌入腾讯文档的前置条件）
腾讯文档的「嵌入网页」需要目标页面可被公网访问。任选其一（均免费）：

| 平台 | 操作 |
|------|------|
| **Gitee Pages** | 把本目录推到 Gitee 仓库 → 服务 → Gitee Pages → 部署，得到 `https://<用户名>.gitee.io/<仓库>/marathon-coach-embed/` |
| **GitHub Pages** | 推到 GitHub → Settings → Pages → 选分支根目录，得到 `https://<用户名>.github.io/<仓库>/...` |
| **Vercel / Netlify** | 拖拽本目录上传，自动分配 `*.vercel.app` 域名 |
| **EdgeOne / CloudBase 静态托管** | 上传后开启静态网站，适合国内访问（已在 `deploy/` 有 Docker 方案可参考） |

> 国内访问优先选 Gitee Pages 或 EdgeOne，延迟更低。

## 三、在腾讯文档中嵌入
1. 打开（或新建）一篇腾讯文档。
2. 菜单「**插入 → 更多 → 嵌入网页**」（部分版本为「插入 → 在线内容 / iframe」）。
3. 粘贴上一步得到的公开地址，确认插入。
4. 拖拽调整嵌入框大小——本页已做响应式：宽屏双栏（对话 + 上下文面板），
   窄屏（<900px）自动单栏堆叠，<560px 进一步压缩，适配各类嵌入容器。

等效 iframe 代码（若文档支持自定义 HTML）：
```html
<iframe
  src="https://你的托管地址/marathon-coach-embed/index.html"
  style="width:100%;height:760px;border:0;border-radius:12px;"
  allow="fullscreen"
  loading="lazy"
></iframe>
```

## 四、页面已针对嵌入环境做的适配
- `viewport` 元标签 + `body{margin:0;width:100%}`，无外边距冲突。
- 零外部依赖（图标为内联 SVG，非 lucide CDN），在沙箱 iframe 内也能正常显示。
- 顶部栏 `position:sticky` 而非 `fixed`，避免遮挡嵌入容器。
- 检测到处于 iframe 内时，自动隐藏"可嵌入提示"横幅。
- 主题切换状态存 `sessionStorage`，嵌入环境可用。
- 实时模拟引擎每秒刷新配速/距离/心率，并据心率区间自动生成教练提示。

## 五、可演示的交互点（面试 / 评审时建议点一遍）
1. 顶部 `LIVE 训练中` 脉冲 + 实时跳动的距离/配速/心率。
2. 心率趋势 sparkline 随训练实时绘制，区间徽章（Z1–Z5）自动变色。
3. 「实时教练反馈」条随心率区间切换给出建议。
4. 下方 4 个快捷问题（评估负荷 / 下周建议 / 配速分析 / 伤病预防）点击即触发打字机式流式回复。
5. 自行输入问题，教练按关键词匹配给出个性化分析，并标注 RAG 引用。
6. 右下上下文面板：跑者画像 / 最近训练 / 活跃计划进度。
7. 右上角主题切换（暗/亮）。
