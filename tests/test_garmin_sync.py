"""
Garmin 自动同步回归测试
=======================
目标：验证两个关键修复点，且不依赖真实 Garmin 登录（沙箱无法实跑）：
  1) 根因修复：_get_garth 必须在 login/resume 之前，把 garth 默认的移动端
     UA（GCM-iOS-...）覆盖为浏览器 UA，否则 Cloudflare 拦登录返回 400。
  2) 同步状态持久化：record_sync_success / record_sync_failure 能正确写读
     data/sync_state.json，让前端判断数据是否最新。
"""
import os
import sys
import json

import pytest


def _make_fake_garth():
    """构造一个假的 garth 模块，模拟 1.1.0 的 client.session 结构。"""
    fake = type("FakeGarth", (), {})()
    session = type("FakeSession", (), {})()
    session.headers = {}
    client = type("FakeClient", (), {})()
    client.session = session
    client.domain = "garmin.com"
    fake.client = client

    calls = {"login": 0, "resume": 0}

    def configure(domain=None, **kw):
        if domain:
            client.domain = domain

    def resume(home):
        calls["resume"] += 1
        raise Exception("no saved token")  # 模拟首次运行：无令牌可恢复

    def login(email, password, **kw):
        calls["login"] += 1
        return "fake-oauth-token"

    fake.configure = configure
    fake.resume = resume
    fake.login = login
    fake._calls = calls
    return fake


def test_ua_injected_before_login(monkeypatch):
    """UA 必须在 login 之前注入到 garth.client.session.headers。"""
    fake = _make_fake_garth()
    monkeypatch.setitem(sys.modules, "garth", fake)

    import garmin_sync
    monkeypatch.setenv("GARMIN_USERNAME", "runner@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "secret")
    monkeypatch.setenv("GARMIN_IS_CN", "true")

    garmin_sync._get_garth()

    # UA 已被覆盖为浏览器 UA（不再是 GCM-iOS 移动端）
    ua = fake.client.session.headers.get("User-Agent", "")
    assert ua.startswith("Mozilla/5.0"), f"UA 未注入浏览器值，实际: {ua}"
    assert "GCM-iOS" not in ua, "默认移动端 UA 仍残留"
    # 无令牌 → 走登录分支
    assert fake._calls["login"] == 1, "应当触发登录"


def test_ua_injected_even_when_resume_ok(monkeypatch):
    """即使令牌恢复成功，UA 也应被注入（保证后续 API 调用不被拦）。"""
    fake = _make_fake_garth()
    fake.resume = lambda home: None  # 恢复成功，不抛错
    monkeypatch.setitem(sys.modules, "garth", fake)

    import garmin_sync
    monkeypatch.setenv("GARMIN_USERNAME", "runner@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "secret")
    monkeypatch.setenv("GARMIN_IS_CN", "true")

    garmin_sync._get_garth()
    ua = fake.client.session.headers.get("User-Agent", "")
    assert ua.startswith("Mozilla/5.0")
    assert fake._calls["login"] == 0  # 恢复成功不应再登录


def test_sync_state_success_and_failure(tmp_path, monkeypatch):
    """record_sync_success / record_sync_failure 写读正确。"""
    import garmin_sync
    state_path = tmp_path / "sync_state.json"
    monkeypatch.setattr(garmin_sync, "SYNC_STATE_PATH", str(state_path))

    garmin_sync.record_sync_success(["/data/2026-07-31_123.tcx"], source="auto")
    st = garmin_sync.read_sync_state()
    assert st["result"] == "success"
    assert st["count"] == 1
    assert st["files"] == ["2026-07-31_123.tcx"]
    assert "updated_at" in st

    garmin_sync.record_sync_failure("DI-OAuth2 exchange failed: 400", source="manual")
    st2 = garmin_sync.read_sync_state()
    assert st2["result"] == "failed"
    assert st2["error"] == "DI-OAuth2 exchange failed: 400"


def test_read_sync_state_missing_returns_empty(tmp_path, monkeypatch):
    """状态文件不存在时返回空 dict，不抛错。"""
    import garmin_sync
    monkeypatch.setattr(garmin_sync, "SYNC_STATE_PATH", str(tmp_path / "nope.json"))
    assert garmin_sync.read_sync_state() == {}
