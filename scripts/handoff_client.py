"""VerifyMind -> DevTest Hub handoff client (stdlib only)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable


GATE_STATUSES = frozenset({"PASS", "FAIL", "HOLD", "BLOCKED"})
CASE_STATUSES = frozenset({"PASS", "FAIL", "BLOCKED", "NOT_TESTED"})
PROGRESS_KEYS = ("total", "passed", "failed", "blocked", "not_tested")
EVIDENCE_KEYS = ("request", "response", "db_before", "db_after", "redis_before", "redis_after")

# ── hints 隔离（AC-2 / AC-3）──
# handoff 里只有 contract 是判定依据；hints 是开发方叙事，VerifyMind 禁止据此写
# expected / 断言 / PASS 依据。这里用「白名单 + 物理摘除 + 出口校验」三道机制保证，
# 不依赖调用方自觉。
HINTS_KEY = "hints"
FACT_ALLOWED_BLOCKS = ("contract",)         # 允许转 facts 的顶层块
HINTS_LEAK_MIN_LEN = 8                      # 短于 8 字符的片段不参与泄露比对（防误伤）


class HandoffClientError(RuntimeError):
    """Raised when the handoff request cannot be built or accepted."""


def _relative_report_dir(report_dir: str | os.PathLike[str], root: str | os.PathLike[str] | None) -> str:
    raw = str(report_dir or "").strip().replace("\\", "/")
    if not raw:
        return ""
    root_raw = str(root or "").strip().replace("\\", "/").rstrip("/")
    if root_raw and raw == root_raw:
        raw = ""
    elif root_raw and raw.startswith(root_raw + "/"):
        raw = raw[len(root_raw) + 1:]
    else:
        path = Path(raw)
        if path.is_absolute() or (len(raw) > 1 and raw[1] == ":"):
            if root is None:
                raise HandoffClientError("absolute report_dir requires root")
            try:
                raw = path.resolve().relative_to(Path(root).resolve()).as_posix()
            except ValueError as exc:
                raise HandoffClientError("report_dir is outside root") from exc
    parts = raw.split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise HandoffClientError("report_dir must be a safe relative path")
    if parts[0] not in {"artifacts", ".testmind", "reports", "verify-mind"}:
        raise HandoffClientError("report_dir is outside the controlled whitelist")
    return "/".join(parts)


def _case_status(value: Any) -> str:
    status = str(value or "NOT_TESTED").strip().upper()
    if status == "HOLD" or status in {"TOOL_ERROR", "SKIPPED_WITH_REASON"}:
        return "BLOCKED" if status == "HOLD" else "NOT_TESTED"
    if status not in CASE_STATUSES:
        raise HandoffClientError(f"unsupported case status: {status}")
    return status


def _progress(cases: list[dict[str, Any]]) -> dict[str, int]:
    result = {key: 0 for key in PROGRESS_KEYS}
    result["total"] = len(cases)
    for case in cases:
        result[{"PASS": "passed", "FAIL": "failed", "BLOCKED": "blocked", "NOT_TESTED": "not_tested"}[case["status"]]] += 1
    return result


def _plan_by_id(plan: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for case in plan or ():
        case_id = str(case.get("case_id") or case.get("id") or "").strip()
        if case_id:
            out[case_id] = case
    return out


def build_run_summary(
    run_id: str,
    gate_final: str,
    report_dir: str | os.PathLike[str],
    results: Iterable[dict[str, Any]],
    plan: Iterable[dict[str, Any]] = (),
    *,
    endpoint_coverage: dict[str, Any] | None = None,
    evidence_summary: dict[str, Any] | None = None,
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    gate = str(gate_final or "").strip().upper()
    if gate not in GATE_STATUSES:
        raise HandoffClientError(f"unsupported gate_final: {gate}")
    lookup = _plan_by_id(plan)
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    summary = {key: [] for key in EVIDENCE_KEYS}
    for result in results or ():
        case_id = str(result.get("case_id") or result.get("id") or "").strip()
        if not case_id:
            raise HandoffClientError("result case_id is required")
        if case_id in seen:
            raise HandoffClientError(f"duplicate result case_id: {case_id}")
        seen.add(case_id)
        source = lookup.get(case_id, {})
        refs = [str(ref).replace("\\", "/") for ref in (result.get("evidence_refs") or [])]
        for ref in refs:
            name = ref.rsplit("/", 1)[-1].lower()
            if name == "request.json":
                summary["request"].append(ref)
            elif name == "response.json":
                summary["response"].append(ref)
            elif name in {"db-before.json", "db_before.json"}:
                summary["db_before"].append(ref)
            elif name in {"db-after.json", "db_after.json"}:
                summary["db_after"].append(ref)
            elif name in {"kv-before.json", "redis-before.json", "redis_before.json"}:
                summary["redis_before"].append(ref)
            elif name in {"kv-after.json", "redis-after.json", "redis_after.json"}:
                summary["redis_after"].append(ref)
        detail = result.get("detail") or result.get("observations") or {}
        cases.append(
            {
                "case_id": case_id,
                "layer": str(result.get("layer") or source.get("layer") or ""),
                "title": str(result.get("title") or source.get("title") or source.get("name") or case_id),
                "status": _case_status(result.get("status")),
                "summary": str(result.get("summary") or (detail.get("message") if isinstance(detail, dict) else "") or ""),
                "evidence_refs": refs,
                "assertions": result.get("assertions") or [],
                "observations": detail,
            }
        )
    if evidence_summary:
        for key in EVIDENCE_KEYS:
            if key in evidence_summary:
                summary[key] = evidence_summary[key]
    return {
        "run_id": str(run_id).strip(),
        "gate_final": gate,
        "report_dir": _relative_report_dir(report_dir, root),
        "cases": cases,
        "progress": _progress(cases),
        "evidence_summary": summary,
        "endpoint_coverage": endpoint_coverage or {},
    }


def strip_hints(obj: Any) -> Any:
    """机制一：物理摘除。任何深度上名为 `hints` 的键一律删掉，调用方拿不到就写不进去。"""
    if isinstance(obj, dict):
        return {k: strip_hints(v) for k, v in obj.items() if k != HINTS_KEY}
    if isinstance(obj, list):
        return [strip_hints(v) for v in obj]
    return obj


def _iter_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _iter_strings(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from _iter_strings(value)


def assert_no_hints(facts: Any, handoff: dict[str, Any] | None) -> Any:
    """机制二：出口校验。facts 里出现 hints 的任一片段 → 直接抛错，绝不静默放行。"""
    if not isinstance(handoff, dict):
        return facts
    hints = handoff.get(HINTS_KEY)
    if not hints:
        return facts
    needles = [s.strip() for s in _iter_strings(hints) if len(s.strip()) >= HINTS_LEAK_MIN_LEN]
    if not needles:
        return facts
    blob = json.dumps(facts, ensure_ascii=False, default=str)
    leaked = [s for s in needles if s in blob]
    if leaked:
        raise HandoffClientError(
            "hints leaked into plan facts (AC-2/AC-3): " + " | ".join(repr(s) for s in leaked[:3])
        )
    return facts


def contract_facts(handoff: dict[str, Any], handoff_id: str | None = None) -> list[dict[str, Any]]:
    """机制三：白名单。只从 contract.sections 建 facts，其余块（hints/dev_self_check/
    work_items/bugs/comments）连看都不看。返回的每条 fact 都带 source，可被 Facts.add_raw 接收。"""
    if not isinstance(handoff, dict):
        raise HandoffClientError("handoff payload must be an object")
    clean = strip_hints(handoff)
    contract = clean.get("contract") or {}
    if not isinstance(contract, dict):
        raise HandoffClientError("handoff.contract must be an object")
    sections = contract.get("sections") or {}
    if not isinstance(sections, dict):
        raise HandoffClientError("handoff.contract.sections must be an object")
    hid = str(handoff_id or clean.get("handoff_id") or "").strip()
    prefix = f"handoff:{hid}:" if hid else "handoff:"
    facts: list[dict[str, Any]] = []
    for key in sorted(sections):
        text = str(sections[key]).strip()
        if not text:
            continue
        facts.append({
            "topic": f"contract:{key}",
            "statement": text,
            "source": f"{prefix}contract.sections.{key}",
        })
    return assert_no_hints(facts, handoff)


def _get_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise HandoffClientError(f"DevTest Hub handoff fetch failed ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HandoffClientError(f"DevTest Hub handoff request failed: {exc}") from exc


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise HandoffClientError(f"DevTest Hub rejected verify-run ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HandoffClientError(f"DevTest Hub verify-run request failed: {exc}") from exc


class HandoffClient:
    def __init__(self, base_url: str, project_id: str = "shejiuPro", timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.project_id = project_id
        self.timeout = timeout

    def fetch_handoff(self, handoff_id: str, *, project_id: str | None = None) -> dict[str, Any]:
        """GET /api/handoffs/{id} → handoff 对象（自动脱掉 {"handoff": ...} 外壳）。
        返回体仍含 hints 原样；要建 facts 必须过 contract_facts()。"""
        query = urllib.parse.urlencode({"project_id": project_id or self.project_id})
        escaped_id = urllib.parse.quote(str(handoff_id), safe="")
        url = f"{self.base_url}/api/handoffs/{escaped_id}?{query}"
        data = _get_json(url, self.timeout)
        if isinstance(data, dict) and isinstance(data.get("handoff"), dict):
            return data["handoff"]
        return data

    def submit_run(self, handoff_id: str, run: dict[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode({"project_id": self.project_id})
        escaped_id = urllib.parse.quote(str(handoff_id), safe="")
        url = f"{self.base_url}/api/handoffs/{escaped_id}/verify-run?{query}"
        return _post_json(url, run, self.timeout)

    def submit_run_id(self, handoff_id: str, run_id: str) -> dict[str, Any]:
        """Compatibility entry point for the historical run_id-only call."""
        return self.submit_run(handoff_id, {"run_id": run_id})


def submit_run(base_url: str, handoff_id: str, run: dict[str, Any], *, project_id: str = "shejiuPro", timeout: float = 15.0) -> dict[str, Any]:
    return HandoffClient(base_url, project_id=project_id, timeout=timeout).submit_run(handoff_id, run)


def fetch_handoff(base_url: str, handoff_id: str, *, project_id: str = "shejiuPro", timeout: float = 15.0) -> dict[str, Any]:
    return HandoffClient(base_url, project_id=project_id, timeout=timeout).fetch_handoff(handoff_id)


if __name__ == "__main__":
    raise SystemExit("import HandoffClient or call submit_run()")
