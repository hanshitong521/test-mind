"""Peak 模拟环境：拉起 SUT、跑 MCP 闭环、产出 Evidence + 四件套。"""
import json
import os
import socket
import sys
import tempfile

from testmind import core
from testmind import env_registry


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _import_generic_sut():
    """Load examples/generic/sut.py without clobbering sys.modules['sut'] (e.g. red-packet)."""
    import importlib.util

    path = os.path.join(core.ROOT, "examples", "generic", "sut.py")
    mod_name = "testmind_examples_generic_sut"
    mod = sys.modules.get(mod_name)
    if mod is None or getattr(mod, "__file__", "") != path:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[mod_name] = mod
    return mod.serve


def run_peak_simulation(consumer_root=None, port=None, task_bundle_path=None):
    port = port or _free_port()
    consumer_root = consumer_root or os.path.join(core.ROOT, "examples", "peak-sim")
    bundle_path = task_bundle_path or os.path.join(consumer_root, "task-bundle.json")
    with open(bundle_path, encoding="utf-8") as fh:
        bundle = json.load(fh)

    verify = (bundle.get("spec") or {}).get("verify") or {}
    env_ref = verify.get("environment_ref") or "env://peak-sim/local"
    profile = env_registry.resolve_ref(env_ref, consumer_root)
    if not profile:
        return {"status": "BLOCKED", "summary": f"profile missing: {env_ref}"}
    ok, reason = env_registry.check_profile(profile)
    if not ok:
        return {"status": "BLOCKED", "summary": reason}

    secrets_dir = os.path.join(consumer_root, "env", "secrets")
    profile = env_registry.materialize_secrets(profile, secrets_dir)

    db_path = os.path.join(tempfile.gettempdir(), f"testmind-sim-{port}.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    serve = _import_generic_sut()
    srv, sut = serve(port=port, db_path=db_path)
    from testmind import mcp

    try:
        mcp.S.reset()
        mcp.S.facts = core.Facts()
        mcp.S.consumer_root = consumer_root
        steps = []

        openapi_path = os.path.join(consumer_root, profile.get("openapi_path", "openapi.json"))
        r = mcp.dispatch("plan_verification", {
            "task_bundle": bundle, "consumer_root": consumer_root, "openapi": openapi_path})
        steps.append(("plan_verification", r["status"]))
        if r["status"] == "BLOCKED":
            return {"status": r["status"], "summary": r["summary"], "steps": steps}

        api_key = profile.get("_resolved_secrets", {}).get("api_key", "")
        base_url = f"http://127.0.0.1:{port}"
        prep = {
            "env_class": profile.get("env_class", "test"),
            "base_url": base_url,
            "db": {"kind": "sqlite", "path": db_path},
            "tables": ["stock", "coupon", "warehouse"],
        }
        r = mcp.dispatch("prepare_verification", {"env": prep})
        steps.append(("prepare_verification", r["status"]))
        if r["status"] != "PASS":
            return {"status": r["status"], "summary": r["summary"], "steps": steps}

        cases = [
            {"id": "SIM-stock", "priority": "P0", "category": "BUSINESS",
             "action": {"kind": "http", "method": "POST", "path": "/stock",
                        "body": {"sku": "SIM", "warehouse_id": 1, "qty": 1},
                        "headers": {"Authorization": f"Bearer {api_key}"}},
             "expected": {"http": 201}},
            {"id": "SIM-relation", "priority": "P0", "category": "RELATION",
             "action": {"kind": "http", "method": "POST", "path": "/stock",
                        "body": {"sku": "X", "warehouse_id": 999, "qty": 1}},
             "expected": {"http": 404, "db_no_write": True}},
            {"id": "SIM-list", "priority": "P0",
             "action": {"kind": "http", "method": "GET", "path": "/items?page=1&size=5"},
             "expected": {"http": 200}},
            {"id": "SIM-idem", "priority": "P0", "category": "IDEMPOTENCY",
             "action": {"kind": "sequence", "steps": [
                 {"path": "/coupons", "method": "POST",
                  "body": {"code": "SIM10", "request_id": "sim-rid-1"}, "expect_status": 201},
                 {"path": "/coupons", "method": "POST",
                  "body": {"code": "SIM10", "request_id": "sim-rid-1"}, "expect_status": 200},
             ]},
             "expected": {"db_count": [{"sql": "SELECT COUNT(*) FROM coupon WHERE request_id='sim-rid-1'", "expect": 1}]}},
        ]
        # curated case 注入 plan（等价旧 generate_cases→run_suite），再走门面执行
        mcp.S.plan += cases
        r = mcp.dispatch("run_verification", {})
        steps.append(("run_verification", r["status"]))

        # 模拟验收集 curated case，不按 risk_floor 强制 P0-IDEM-*（无红包 hooks）
        mcp.S.facts = core.Facts()
        r = mcp.dispatch("final_gate", {})
        steps.append(("final_gate", r["status"]))

        rep = {"final": r["status"], "why": r.get("summary"), "steps": steps,
               "evidence": mcp.S.ev.dir if mcp.S.ev else None,
               "environment_fingerprint": profile.get("fingerprint"),
               "task_id": bundle["task_id"]}

        # §29：四件套随 final_gate 终判自动导出
        steps.append(("handoff", "auto"))
        rep["artifacts"] = r.get("artifacts", [])
        rep["task_dir"] = os.path.join(consumer_root, ".testmind", "tasks", bundle["task_id"])

        leak = False
        if mcp.S.ev and api_key:
            for dp, _, fns in os.walk(mcp.S.ev.dir):
                for fn in fns:
                    with open(os.path.join(dp, fn), encoding="utf-8", errors="ignore") as fh:
                        txt = fh.read()
                    if api_key in txt:
                        leak = True
        rep["secret_leak"] = leak
        if leak:
            rep["final"] = "FAIL"
            rep["why"] = "secret leaked into evidence"

        out_path = os.path.join(consumer_root, ".testmind", "last-simulation.json")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=2)
        rep["status"] = rep["final"]
        rep["summary"] = rep.get("why", "")
        return rep
    finally:
        mcp.dispatch("cleanup", {})
        srv.shutdown()
        srv.server_close()
        try:
            sut.db.close()
        except Exception:
            pass
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass
