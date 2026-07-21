# 代码修复 + 死代码拆除报告（交付 TRAE 平台前）

**范围**：马拉松训练 Agent Web 应用（`E:\AI\trae\agent`）
**执行**：全量修复 F1–F12 + 拆除 R1–R3，纯增强/清理，无新功能。
**验证**：`py_compile` 全通过 · `plan.js` node 检查通过 · Flask 5 页冒烟 200 · `physiology_test.py` 9/9 通过。

## 修复清单（FIX）

| ID | 严重度 | 位置 | 修复内容 |
|----|--------|------|----------|
| F1 | **P0** | `routes/pages.py` | `weekly_distance==0` 分支补 `resting_hr=None`；`index()` except 增 `NameError` → 修复新用户/停跑周首页 500 |
| F2 | **P0** | `app.py` | `debug=True` → `os.getenv("FLASK_DEBUG")=="true"`（默认 False，防 RCE） |
| F3 | P1 | `app.py` | `secret_key` 改为 `os.getenv("SECRET_KEY") or os.urandom(24).hex()`，移除硬编码默认值 |
| F4 | P1 | `routes/pages.py` + `static/plan.js` | `saved_at` 顶层读取 → `meta.generated_at`；前端 `savedAt` 改读 `data.meta.generated_at` |
| F5 | P1 | `routes/coach.py` | `_build_history_summary` 用 `entry.get` 守卫，防损坏历史 KeyError |
| F6 | P1 | `routes/coach.py` / `data_tool.py` / `llm_agent.py` | 全局缓存（`_profile_cache`/`_plan_cache`/`_prompt_cache`）读写加 `threading.Lock` |
| F7 | P1 | `llm_agent.py` | `_get_client()` 复用单例 `OpenAI` 客户端，不再每次请求新建 |
| F8 | P2 | `routes/plan.py` | `update_day` 的 `day` 归一化为 `int`，避免 int/str 不匹配 |
| F9 | P2 | `routes/data.py` | `f(x) if c else []` 空操作改为 `if saved_files:`；坏日期 run 不计入近30天 |
| F10 | P2 | `memory.py` | 伤病史存清洗标签（膝→膝盖）；去除冗余判断 |
| F11 | P2 | `requirements.txt` | 补全 `chromadb` / `sentence-transformers`（RAG 不再静默禁用） |
| F12 | P2 | `skills/__init__.py` | skill 注册 `except ImportError` 改为打印日志 |

## 拆除清单（REMOVE）

| ID | 位置 | 拆除内容 |
|----|------|----------|
| R1 | `routes/pages.py` / `visualize.py` | 移除重复 `sec_to_pace_str` 实现（`_format_pace` / `_pace_label`），统一委托 `physiology.sec_to_pace_str` |
| R2 | `visualize.py` | 删除 deprecated `parse_plan_paces` 桩函数与 `import warnings` |
| R3 | `llm_agent.py` | 彻底移除未接入任何路由的死代码 `reflect_and_adjust` / `multi_round_reflect` 及 `__main__` 自测；同步更新模块 docstring |

## 验证结果
- ✅ 全部后端模块 `python -m py_compile` 通过
- ✅ `static/plan.js` `node --check` 通过
- ✅ Flask 冒烟：`/` `/training` `/coach` `/insights` `/eval` 均 200
- ✅ **F1 定向验证**：用空近期数据（触发 `weekly_distance==0` 路径）调用 `index()` 返回 200，确认 `NameError` 已消除
- ✅ `physiology_test.py` 9/9 通过

## 残余引用（非应用代码，供参考）
- `architecture_optimization_analysis.md`、`phase2_first_principles.md`、`prototype/marathon_coach_agent.ipynb` 中仍提及 `reflect_and_adjust` / `multi_round_reflect`（历史分析/原型，不被应用执行）。如需文档同步清理可另行处理。
