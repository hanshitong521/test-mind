import hashlib
import json
import os
import subprocess

from testmind import core


def git_head_short(root=None):
    root = root or core.ROOT
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=15,
    )
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def pipeline_contract_hash():
    path = os.path.join(core.ROOT, "..", "shared", "pipeline-contract.yaml")
    path = os.path.normpath(path)
    if not os.path.isfile(path):
        return ""
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def bind_pass_metadata(evidence_dir, extra=None):
    from testmind.export import build_evidence_manifest
    manifest = build_evidence_manifest(evidence_dir)
    meta = {
        "git_head": git_head_short(),
        "evidence_manifest_hash": manifest.get("manifest_hash", ""),
        "pipeline_contract_hash": pipeline_contract_hash(),
        "contract_version": 1,
        "measured_at": core.utc(),
    }
    if extra:
        meta.update(extra)
    return meta


def gate_is_stale(saved_gate: dict) -> bool:
    """INV-006：commit 或 contract_hash 变化 → STALE。"""
    if not saved_gate:
        return True
    if saved_gate.get("git_head") and saved_gate.get("git_head") != git_head_short():
        return True
    cur = pipeline_contract_hash()
    if cur and saved_gate.get("pipeline_contract_hash") and saved_gate["pipeline_contract_hash"] != cur:
        return True
    return False
