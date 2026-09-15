"""Verify-run API、store 与 client 的最小安全契约测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

VERIFY_ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = VERIFY_ROOT.parent / "UI"
sys.path.insert(0, str(UI_ROOT / "src"))
sys.path.insert(0, str(VERIFY_ROOT / "scripts"))

from brain_api.app import app
from brain_services import handoff_store
from handoff_client import (
    HandoffClient,
    HandoffClientError,
    assert_no_hints,
    build_run_summary,
    contract_facts,
    strip_hints,
)


PROJECT_ID = "__verify_run_test__"

# 带 hints 的 handoff 样例：hints 里的内容永远不该出现在 plan facts 里（AC-2/AC-3）
HANDOFF_WITH_HINTS = {
    "handoff_id": "DH-HINTS",
    "project_id": PROJECT_ID,
    "status": "PENDING_TEST",
    "endpoints": [{"method": "GET", "path": "/x", "origin": "submitted"}],
    "contract": {
        "version": "v1",
        "sections": {
            "3_api": "GET /x → 200 {id,name}",
            "2_ddl": "CREATE TABLE t (id INTEGER, name TEXT);",
        },
    },
    "hints": {
        "disclaimer": "非判定依据；VerifyMind 禁止据此写 expected",
        "why": "解决公海看板折叠与公司维度切换问题",
        "dev_notes": "companyId 为空走默认视图，不要断言 400",
        "sql_perf_notes": [{"sql_id": "Q1", "observed_ms": 120, "threshold_ms": 1000}],
    },
    "dev_self_check": {"compile": "PASS", "local_probe": "NOT_RUN"},
    "work_items": [{"item_id": "WI-1", "kind": "scope_add", "status": "PROPOSED"}],
}


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(handoff_store, "HANDOFF_DIR", tmp_path / "handoffs")
    client = TestClient(app)
    created = client.post(
        "/api/handoffs",
        json={"project_id": PROJECT_ID, "title": "verify run", "task_id": "T-1"},
    )
    assert created.status_code == 200
    handoff_id = created.json()["handoff"]["handoff_id"]
    assert client.patch(
        f"/api/handoffs/{handoff_id}",
        params={"project_id": PROJECT_ID},
        json={
            "endpoints": [{"method": "GET", "path": "/x", "origin": "submitted"}],
            "contract": {"version": "v1", "sections": {"3_api": "GET /x"}},
        },
    ).status_code == 200
    assert client.post(
        f"/api/handoffs/{handoff_id}/submit",
        params={"project_id": PROJECT_ID},
        json={"actor_role": "dev"},
    ).status_code == 200
    assert client.post(
        f"/api/handoffs/{handoff_id}/claim",
        params={"project_id": PROJECT_ID},
        json={"actor_role": "verify-mind"},
    ).status_code == 200
    return client, handoff_id


def _run():
    return {
        "run_id": "run-1",
        "gate_final": "PASS",
        "report_dir": "reports/run-1",
        "cases": [
            {
                "case_id": "C1",
                "layer": "L2",
                "title": "健康检查",
                "status": "PASS",
                "summary": "ok",
                "evidence_refs": ["cases/C1/request.json"],
                "assertions": ["http"],
                "observations": {"http": 200},
            }
        ],
        "progress": {"total": 1, "passed": 1, "failed": 0, "blocked": 0, "not_tested": 0},
        "evidence_summary": {
            "request": ["cases/C1/request.json"],
            "response": ["cases/C1/response.json"],
        },
        "endpoint_coverage": {"status": "PASS"},
    }


def test_valid_case_and_idempotent(monkeypatch, tmp_path):
    client, handoff_id = _client(monkeypatch, tmp_path)
    url = f"/api/handoffs/{handoff_id}/verify-run"
    first = client.post(url, params={"project_id": PROJECT_ID}, json=_run())
    second = client.post(url, params={"project_id": PROJECT_ID}, json=_run())
    assert first.status_code == second.status_code == 200
    got = second.json()["handoff"]
    assert len(got["verify"]["runs"]) == 1
    assert got["verify"]["runs"][0]["progress"]["passed"] == 1


def test_duplicate_case_rejected(monkeypatch, tmp_path):
    client, handoff_id = _client(monkeypatch, tmp_path)
    body = _run()
    body["cases"].append(dict(body["cases"][0], title="重复"))
    body["progress"] = {"total": 2, "passed": 2, "failed": 0, "blocked": 0, "not_tested": 0}
    response = client.post(
        f"/api/handoffs/{handoff_id}/verify-run",
        params={"project_id": PROJECT_ID},
        json=body,
    )
    assert response.status_code == 400
    assert "duplicate case_id" in response.json()["detail"]["error"]


def test_progress_mismatch_rejected(monkeypatch, tmp_path):
    client, handoff_id = _client(monkeypatch, tmp_path)
    body = _run()
    body["progress"]["failed"] = 1
    response = client.post(
        f"/api/handoffs/{handoff_id}/verify-run",
        params={"project_id": PROJECT_ID},
        json=body,
    )
    assert response.status_code == 400
    assert "progress" in response.json()["detail"]["error"]


def test_non_in_test_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(handoff_store, "HANDOFF_DIR", tmp_path / "handoffs")
    created = handoff_store.create_handoff(PROJECT_ID)
    response = TestClient(app).post(
        f"/api/handoffs/{created['handoff_id']}/verify-run",
        params={"project_id": PROJECT_ID},
        json=_run(),
    )
    assert response.status_code == 409


def test_pass_without_evidence_rejected(monkeypatch, tmp_path):
    client, handoff_id = _client(monkeypatch, tmp_path)
    body = _run()
    body["cases"][0]["evidence_refs"] = []
    body["evidence_summary"] = {}
    response = client.post(
        f"/api/handoffs/{handoff_id}/verify-run",
        params={"project_id": PROJECT_ID},
        json=body,
    )
    assert response.status_code == 400
    assert "evidence_refs" in response.json()["detail"]["error"]


def test_client_builds_structured_summary_and_keeps_legacy_call():
    run = build_run_summary(
        "run-1",
        "PASS",
        "/tmp/verify-mind/reports/run-1",
        [{
            "case_id": "C1",
            "layer": "L2",
            "status": "PASS",
            "evidence_refs": ["cases/C1/request.json", "cases/C1/response.json"],
            "assertions": ["http"],
            "detail": {"http": 200},
        }],
        root="/tmp/verify-mind",
    )
    assert run["progress"] == {"total": 1, "passed": 1, "failed": 0, "blocked": 0, "not_tested": 0}
    assert run["evidence_summary"]["request"] == ["cases/C1/request.json"]

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({"ok": True}).encode()

    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = HandoffClient("http://127.0.0.1:18787").submit_run_id("DH-1", "legacy-1")
    assert result == {"ok": True}


# ─────────────── AC-2 / AC-3：hints 绝不进 plan facts ───────────────

_HINTS_NEEDLES = ("companyId 为空走默认视图", "公海看板折叠", "禁止据此写 expected", "Q1")


class _HandoffResponse:
    """只服务于 handoff 拉取的最小 urlopen 替身。"""

    def __init__(self, payload):
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self._body


def test_schema_declares_handoff_id():
    """additionalProperties:False —— 不登记字段，宿主 AI 传参会被拒。"""
    from testmind.tool_schemas import input_schema

    props = input_schema("plan_verification")["properties"]
    assert props["handoff_id"] == {"type": "string"}


def test_contract_facts_only_takes_contract_and_drops_hints():
    facts = contract_facts(HANDOFF_WITH_HINTS, handoff_id="DH-HINTS")
    blob = json.dumps(facts, ensure_ascii=False)

    assert [f["topic"] for f in facts] == ["contract:2_ddl", "contract:3_api"]
    assert all(f["source"].startswith("handoff:DH-HINTS:contract.sections.") for f in facts)
    for needle in _HINTS_NEEDLES:
        assert needle not in blob, f"hints 泄漏进 facts: {needle}"
    # work_items / dev_self_check 同样不是判定依据（AC-13）
    assert "WI-1" not in blob and "local_probe" not in blob


def test_strip_hints_removes_nested_hints():
    cleaned = strip_hints({"contract": {"sections": {"3_api": "GET /x"}, "hints": {"why": "深层"}},
                           "hints": {"why": "顶层"}})
    assert "hints" not in cleaned and "hints" not in cleaned["contract"]


def test_assert_no_hints_blocks_leak():
    leaked = [{"topic": "contract:3_api", "statement": "companyId 为空走默认视图，不要断言 400"}]
    try:
        assert_no_hints(leaked, HANDOFF_WITH_HINTS)
    except HandoffClientError as exc:
        assert "hints leaked" in str(exc)
    else:
        raise AssertionError("hints 泄漏未被拦截（AC-2/AC-3 失效）")


def test_plan_verification_with_handoff_id_keeps_hints_out_of_facts():
    """AC-3 端到端：即使 Hub 响应里带 hints，dispatch 后 S.facts 里也不含 hints 内容。"""
    from testmind import core, mcp

    old_facts = mcp.S.facts
    mcp.S.facts = core.Facts()
    try:
        with patch("urllib.request.urlopen",
                   return_value=_HandoffResponse({"project_id": PROJECT_ID,
                                                  "handoff": HANDOFF_WITH_HINTS})):
            result = mcp.dispatch("plan_verification", {"handoff_id": "DH-HINTS"})
        assert result["status"] == "PASS", result
        assert ("handoff_facts", 2) in result["steps"]

        blob = json.dumps(mcp.S.facts.f, ensure_ascii=False)
        for needle in _HINTS_NEEDLES:
            assert needle not in blob, f"hints 泄漏进 S.facts: {needle}"
        assert sorted(f["topic"] for f in mcp.S.facts.f) == ["contract:2_ddl", "contract:3_api"]
    finally:
        mcp.S.facts = old_facts


def test_plan_verification_without_handoff_id_unchanged():
    """向后兼容：不传 handoff_id 时不发起任何 HTTP 请求，行为与以前一致。"""

    def _boom(*_a, **_kw):
        raise AssertionError("未传 handoff_id 却访问了 Hub")

    from testmind import core, mcp

    old_facts = mcp.S.facts
    mcp.S.facts = core.Facts()
    try:
        with patch("urllib.request.urlopen", side_effect=_boom):
            result = mcp.dispatch("plan_verification", {"facts": [
                {"topic": "t", "statement": "s", "source": "manual:1"}]})
        assert result["status"] == "PASS", result
        assert [f["topic"] for f in mcp.S.facts.f] == ["t"]
    finally:
        mcp.S.facts = old_facts
