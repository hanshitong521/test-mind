import hashlib
import json
import os

from testmind import secrets


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_evidence_manifest(evidence_dir):
    entries = []
    if not evidence_dir or not os.path.isdir(evidence_dir):
        return {"entries": [], "manifest_hash": ""}
    for dp, _, fns in os.walk(evidence_dir):
        for fn in fns:
            fp = os.path.join(dp, fn)
            rel = os.path.relpath(fp, evidence_dir).replace("\\", "/")
            entries.append({
                "relative_ref": rel,
                "size": os.path.getsize(fp),
                "content_hash": _sha256_file(fp),
            })
    entries.sort(key=lambda x: x["relative_ref"])
    blob = json.dumps(entries, sort_keys=True).encode()
    return {"entries": entries, "manifest_hash": hashlib.sha256(blob).hexdigest()}


def write_four_piece(task_dir, report, results, plan, facts_snapshot, evidence_dir):
    os.makedirs(task_dir, exist_ok=True)
    manifest = build_evidence_manifest(evidence_dir)
    plan_md = [
        "# TEST_PLAN",
        "",
        f"- run_id: {report.get('run_id', '')}",
        f"- facts_confirmed: {report.get('facts_confirmed', 0)}",
        f"- conflicts: {len(report.get('conflicts', []))}",
        f"- unparsed: {len(facts_snapshot.get('unparsed', []))}",
        f"- cases_planned: {len(plan)}",
        "",
        "## Risk floor",
        "",
        str(report.get("risk_floor", [])),
    ]
    paths = {
        "TEST_PLAN.md": "\n".join(plan_md),
        "TEST_CASES.yaml": json.dumps({"cases": plan}, ensure_ascii=False, indent=2),
        "EVIDENCE_MANIFEST.json": json.dumps(secrets.redact_obj(manifest), ensure_ascii=False, indent=2),
        "TEST_REPORT.md": _test_report_md(report, results),
    }
    out = {}
    for name, content in paths.items():
        p = os.path.join(task_dir, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
        out[name] = p
    fails = [r for r in results if r.get("status") == "FAIL"]
    if fails or report.get("final") in ("FAIL", "HOLD", "BLOCKED"):
        fb = secrets.redact_obj({"failures": fails, "final": report.get("final"), "why": report.get("why")})
        fp = os.path.join(task_dir, "FAILURE_BUNDLE.json")
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(fb, fh, ensure_ascii=False, indent=2)
        out["FAILURE_BUNDLE.json"] = fp
    return out


def _test_report_md(report, results):
    lines = [
        "# TEST_REPORT",
        "",
        f"**FINAL = {report.get('final', 'NOT_TESTED')}** ({report.get('why', '')})",
        "",
        f"- PASS: {sum(1 for r in results if r.get('status') == 'PASS')}",
        f"- FAIL: {sum(1 for r in results if r.get('status') == 'FAIL')}",
        f"- evidence_manifest_hash: {report.get('evidence_manifest_hash', '')}",
    ]
    return "\n".join(lines)
