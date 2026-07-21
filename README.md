# 🏃 马拉松训练计划 Agent

从第一性原理出发，结合吴恩达 Agentic 设计模式中的**工具使用**与**反思**，构建一个能读取真实跑步数据、生成训练计划、并自我审查调整的 AI Agent。

**无需注册任何 API 账号**——从手表 App 导出运动文件或手动填写数据即可。

## 项目结构

```
马拉松训练计划 Agent/
├── app.py                 # Flask 入口：注册路由、初始化 RAG 与记忆
├── llm_agent.py           # DeepSeek 双模型封装（生成 chat + 反思 reasoner）
├── rag.py                 # RAG 检索（ChromaDB + data/knowledge 知识库）
├── memory.py              # 长期记忆：用户画像与记忆增删 / 否定
├── physiology.py          # 确定性科学内核：生理指标与配速计算
├── plan_science.py        # 训练计划科学内核（计划生成与校验）
├── training_science.py    # 训练负荷 / 长期趋势科学内核
├── workflow.py            # 教练对话流程编排（被 routes/coach 调用）
├── visualize.py           # 图表生成（配速曲线等）
├── data_tool.py           # 数据接入（运动文件 / 手动输入双模式）
├── routes/                # Web 路由：pages · data · plan · coach · eval · onboarding
├── skills/                # 工具执行器：data_query（双引擎）· chart_generate
├── parsers/               # GPX / TCX / FIT 解析
├── data/
│   ├── knowledge/         # RAG 知识库（25 篇 Markdown，部署必需）
│   └── manual_input.example.json  # 手动输入模板
├── prompts/               # LLM 提示词模板
├── templates/ static/     # 前端页面与静态资源
├── tests/                 # 测试套件（test_rag_cache.py）
├── deploy/                # Docker / Nginx / Compose 部署配置
├── docs/                  # GitHub Pages 架构文档（index.html）
├── eval*.py offline_eval.py gen_eval_data.py skill_eval.py  # 离线评测 harness
├── requirements.txt · .env.example · .gitignore · .dockerignore
└── ARCHITECTURE.md · DEPLOY_CHECKLIST.md   # 架构文档 / 部署清单
```

## 核心闭环

```
真实跑步数据（文件/手动）→ AI 生成计划 → AI 反思审查 → 输出调整后计划
      (工具使用)              (推理)          (反思)         (最终输出)
```

## 技术栈

| 组件 | 技术 | 角色 |
|------|------|------|
| 数据源 | GPX/TCX/FIT 文件解析（gpxpy/fitparse）+ 手动输入 JSON | 工具使用：获取真实跑步数据 |
| 生成模型 | DeepSeek deepseek-chat | 推理：生成初版训练计划 |
| 反思模型 | DeepSeek deepseek-reasoner | 反思：带思维链审查并调整 |
| 运行环境 | Flask Web 应用 | Web 界面交互 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置密钥

将 `.env.example` 复制为 `.env`，填入 DeepSeek API Key：

```bash
# Windows PowerShell
Copy-Item .env.example .env
```

只需填写 `DEEPSEEK_API_KEY`（在 [platform.deepseek.com](https://platform.deepseek.com) 申请，新用户有免费额度）。

### 3. 准备数据（二选一）

**方式 A：导入运动文件（推荐）**

从 Garmin Connect App 或网页版导出运动文件，放入 `data/` 目录：
1. 打开 Garmin Connect → 活动 → 选择跑步记录
2. 菜单 → 「导出为 GPX」或「导出为 TCX」
3. 将文件放入项目的 `data/` 目录

支持 GPX、TCX、FIT 三种格式，放多个文件会自动按日期排序取最近 5 次。

**方式 B：手动填写数据**

```bash
Copy-Item data/manual_input.example.json data/manual_input.json
```

编辑 `data/manual_input.json`，填写你最近的跑步数据。配速支持 `"6:17"` 这样的人类可读写法。

### 4. 启动 Flask 服务器

```bash
python app.py
```

打开浏览器访问 http://127.0.0.1:5000 即可使用。

## 页面功能说明

| 页面 | 路由 | 功能 |
|------|------|------|
| 数据总览 | `/` | KPI 卡片、最近跑步、教练洞察、趋势图表 |
| 深度分析 | `/analysis` | 单次跑步配速曲线、心率区间、分段表 |
| 训练计划 | `/training` | 月历编辑、课表管理、完成率追踪 |
| 长期趋势 | `/trends` | 配速/跑量/VO2max/训练负荷趋势 |
| 教练对话 | `/coach` | AI 教练对话、自适应建议 |

## 常见问题

### 健康指标和体能指标为 None？

运动文件（GPX/TCX/FIT）记录的是单次跑步数据，不包含日常健康数据（静息心率、睡眠、压力）和高级体能指标（VO2max、训练准备度）。这些字段在文件模式下会置为 None。

如需补充，编辑 `data/manual_input.json` 中的「健康指标」和「体能指标」部分即可，文件模式下会自动合并。

### 文件解析失败？

- 确认文件格式正确（GPX/TCX/FIT）
- 单个文件解析失败不会中断流程，会显示警告并跳过
- 尝试用其他格式导出（如 GPX 代替 TCX）

### DeepSeek API 报错？

- 确认 API Key 正确（以 `sk-` 开头）
- 确认账户有余额（新用户赠送 10 元）
- reasoner 模型不支持 `temperature` 参数，代码已自动处理

### 没有运动文件也不会手动填？

直接复制模板即可开始：
```bash
Copy-Item data/manual_input.example.json data/manual_input.json
```
模板里已有示例数据，可以直接跑通闭环体验效果。

## 设计理念

**第一性原理**：Agent = 感知 → 推理 → 行动 → 反馈的闭环系统。「工具使用」模式的本质是让 AI 通过外部数据感知真实世界——Garmin API 只是手段之一，文件解析同样实现了「感知」环节，且无需注册任何 API。

**吴恩达的洞察**：精心设计的 Agentic 工作流，能让弱模型超越强模型。本项目用 deepseek-chat（生成）+ deepseek-reasoner（反思）的双模型分工，对应工具使用与反思两种模式，跑通最小闭环。

## 架构文档

从第一性原理出发的完整架构梳理（前端 / 后端 / 项目目标 / 选型理由），见 [ARCHITECTURE.md](./ARCHITECTURE.md)。
