"""
护栏模拟端到端测试（test_guard_simulation）
==========================================

目的：在 CI / 本地复刻「Windows 沙箱 safe-delete 护栏」——
任何删除文件的操作都会抛 OSError([safe-delete] ...)。
然后真实地跑一遍 /generate 路由，证明即便删除被全面拦截，
生成训练计划的关键写入路径（plan.json + 配速图）也不会崩溃。

不依赖网络：llm_agent.generate_training_plan 被打桩返回固定结构化计划。
不污染真实 data/：所有落盘都指向临时目录。
"""
import os
import json
import shutil
import tempfile
from unittest.mock import patch

import pytest
from flask import Flask


# ---- 复刻沙箱护栏：所有「删除」语义的 os / shutil 操作一律抛 OSError ----
SAFE_DELETE_MSG = (
    'OSError: [safe-delete][SAFE_DELETE_FAIL_CLOSED] '
    '{"target": "...", "reason": "windows-sandbox-recycle-bin-unavailable"}'
)


def _make_guard():
    """返回一个 context manager，激活期间所有删除操作都会抛 OSError。"""
    err = OSError(SAFE_DELETE_MSG)

    def raiser(*args, **kwargs):
        raise err

    patches = [
        patch("os.remove", side_effect=raiser),
        patch("os.unlink", side_effect=raiser),
        patch("os.replace", side_effect=raiser),
        patch("os.rename", side_effect=raiser),
        patch("os.rmdir", side_effect=raiser),
        patch("os.removedirs", side_effect=raiser),
        patch("shutil.move", side_effect=raiser),
        patch("shutil.rmtree", side_effect=raiser),
    ]
    return patches


def _enter_guard(patches):
    for p in patches:
        p.start()


def _exit_guard(patches):
    for p in reversed(patches):
        p.stop()


def _mock_llm():
    """固定的结构化计划，形状与 llm_agent.generate_training_plan 返回值一致。"""
    return {
        "plan_text": "第1天 轻松跑 5km；第2天 休息；第3天 节奏跑 8km。",
        "structured": [
            {"day": 1, "weekday": "一", "type": "轻松跑",
             "target_pace_sec": 390, "distance_km": 5.0, "is_rest": False},
            {"day": 2, "weekday": "二", "type": "休息",
             "target_pace_sec": None, "distance_km": 0.0, "is_rest": True},
            {"day": 3, "weekday": "三", "type": "节奏跑",
             "target_pace_sec": 360, "distance_km": 8.0, "is_rest": False},
        ],
        "science": {"weekly_volume_km": 13.0, "notes": "mock"},
    }


@pytest.fixture(autouse=True)
def _isolate_data_source(monkeypatch, tmp_path):
    """把 build_runner_profile 的数据源指向临时目录，并清缓存。

    generate() 表单数据写入 app.config["DATA_DIR"]，但 build_runner_profile()
    读的是 data_tool.DEFAULT_DATA_DIR 全局。两者都指向 tmp_path 才能对齐，
    避免测试依赖本机 data/ 下的私有数据。
    """
    import data_tool
    monkeypatch.setattr(data_tool, "DEFAULT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(data_tool, "_profile_cache", None)


@pytest.fixture
def app_client(tmp_path):
    """搭一个最小 Flask 应用，只挂 plan 蓝图，落盘全部指向 tmp_path。"""
    import routes.plan as plan_module

    app = Flask(__name__)
    app.config["DATA_DIR"] = str(tmp_path)
    app.config["UPLOAD_DIR"] = str(tmp_path / "uploads")
    app.config["CHART_DIR"] = str(tmp_path / "charts")
    app.register_blueprint(plan_module.plan_bp)

    # 确保目录存在（与真实运行一致）
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    os.makedirs(app.config["CHART_DIR"], exist_ok=True)
    return app, plan_module


def test_generate_survives_safe_delete_guard(app_client):
    """核心：在沙箱护栏全面拦截删除的情况下，/generate 仍能成功落盘。"""
    app, plan_module = app_client

    patches = _make_guard()
    form = {
        "run_date": "2026-07-30",
        "run_distance": "5",
        "plan_duration": "3",
        "instructions": "请保守一点",
    }

    with app.test_request_context("/generate", method="POST", data=form):
        # 打桩 DeepSeek 调用
        with patch.object(plan_module.llm_agent, "generate_training_plan",
                          return_value=_mock_llm()):
            _enter_guard(patches)
            try:
                resp = plan_module.generate()
            finally:
                _exit_guard(patches)

    data = json.loads(resp.get_data(as_text=True))

    # 1) 业务层必须成功，且错误信息里绝不能再出现 safe-delete
    assert data["success"] is True, f"generate 失败：{data.get('error')}"
    assert "safe-delete" not in (data.get("error") or ""), \
        "错误里出现 safe-delete，说明删除仍泄漏到了关键路径"

    # 2) plan.json 必须真实写盘且可解析
    plan_path = os.path.join(app.config["DATA_DIR"], "plan.json")
    assert os.path.isfile(plan_path), "plan.json 未生成"
    with open(plan_path, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["plan"][0]["type"] == "轻松跑", "plan.json 内容异常"

    # 3) 配速图必须生成（说明 visualize 的写入也未撞护栏）
    charts = os.listdir(app.config["CHART_DIR"])
    assert any(c.startswith("pace_chart_") and c.endswith(".png") for c in charts), \
        "配速图未生成，visualize 写入可能被护栏拦截"


def test_safe_write_json_backup_under_guard(app_client):
    """辅助：覆盖写已有 plan.json 时，.bak 备份 + 截断写 在护栏下依然可用。"""
    app, _ = app_client
    import safeio

    p = os.path.join(app.config["DATA_DIR"], "plan.json")
    # 先写一个旧版本
    safeio.safe_write_json(p, {"v": 1})

    patches = _make_guard()
    _enter_guard(patches)
    try:
        # 覆盖写：旧目标会被 os.replace 删除——但 safe_write_json 不依赖删除
        safeio.safe_write_json(p, {"v": 2})
    finally:
        _exit_guard(patches)

    assert json.load(open(p, encoding="utf-8"))["v"] == 2, "覆盖写后内容未更新"
    assert os.path.isfile(p + ".bak"), ".bak 备份未生成"
    assert json.load(open(p + ".bak", encoding="utf-8"))["v"] == 1, ".bak 应为旧值"


def test_full_app_generate_under_guard(tmp_path):
    """最高保真：用真实 create_app() 完整应用（含 token 中间件、全部蓝图），
    在沙箱护栏下跑 /generate，确认整条部署链路不崩。"""
    # 必须在 import app 之前注入强 SECRET_KEY，并关闭访问口令
    os.environ["SECRET_KEY"] = "test-secret-key-for-verification-only-1234567890"
    os.environ.pop("COACH_ACCESS_TOKEN", None)

    import app as appmod
    import llm_agent
    import data_tool

    # 与 autouse fixture 的隔离目录对齐：app 写入点和 profile 读取点必须是同一处
    tmp = str(tmp_path)
    data_tool.DEFAULT_DATA_DIR = tmp
    data_tool._profile_cache = None
    appmod.app.config["DATA_DIR"] = tmp
    appmod.app.config["UPLOAD_DIR"] = os.path.join(tmp, "uploads")
    appmod.app.config["CHART_DIR"] = os.path.join(tmp, "charts")
    os.makedirs(appmod.app.config["UPLOAD_DIR"], exist_ok=True)
    os.makedirs(appmod.app.config["CHART_DIR"], exist_ok=True)

    patches = _make_guard()
    form = {
        "run_date": "2026-07-30",
        "run_distance": "5",
        "plan_duration": "3",
        "instructions": "请保守一点",
    }

    with patch.object(llm_agent, "generate_training_plan", return_value=_mock_llm()):
        _enter_guard(patches)
        try:
            client = appmod.app.test_client()
            resp = client.post("/generate", data=form)
        finally:
            _exit_guard(patches)

    assert resp.status_code == 200, f"HTTP {resp.status_code}"
    body = json.loads(resp.get_data(as_text=True))
    assert body["success"] is True, f"generate 失败：{body.get('error')}"
    assert "safe-delete" not in (body.get("error") or "")

    plan_path = os.path.join(tmp, "plan.json")
    assert os.path.isfile(plan_path), "完整应用下 plan.json 未生成"
