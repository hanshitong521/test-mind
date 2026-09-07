# scripts/mcp_smoke.py — 通过 stdio 对 MCP server 跑一发 run_pipeline（示例环境全链）
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = {"name": "smoke", "amount_cents": 100, "quantity": 10, "influencer_id": 1, "created_by": 1}
PIPE = {"env": {"example": "red-packet"},
        "openapi": "http://127.0.0.1:18102/openapi.json",
        "contract": {"contract_id": "SMOKE_RED_PACKET", "target": {"endpoint": "POST /red-packets"},
                     "inputs": CANON, "ok_status": 201},
        "plan": {"method": "POST", "path": "/red-packets", "base_input": CANON, "ok_status": 201,
                 "overrides": {"influencer_id": {"0": 400, "1": 201, "2": 404, "2147483646": 404,
                                                 "2147483647": 404, "2147483648": 400},
                               "created_by": {"0": 400, "2147483648": 400, "default": 201}}}}
lines = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "add_facts", "arguments": {"facts": [
         {"topic": "relation:influencer", "statement": "失效/删除/不存在达人创建返回 404", "source": "sut.py:create()"},
         {"topic": "idempotency:create", "statement": "idem_key 重复创建返回 200 duplicate", "source": "sut.py:create()"}]}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "run_pipeline", "arguments": PIPE}},
    {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "run_concurrency_tests",
        "arguments": {"path": "/red-packets", "count": 8, "body": CANON, "max_success": 8,
                      "id": "SMOKE-CONC"}}},
    {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "cleanup", "arguments": {}}},
]
p = subprocess.run([sys.executable, os.path.join(ROOT, "testmind", "mcp.py")],
                   input="\n".join(json.dumps(x) for x in lines),
                   capture_output=True, text=True, encoding="utf-8", timeout=900)
tools = fails = None
bad = []
for ln in p.stdout.splitlines():
    d = json.loads(ln)
    if d.get("id") == 2:
        tools = len(d["result"]["tools"])
    if d.get("id") in (3, 4, 5, 6):
        r = json.loads(d["result"]["content"][0]["text"])
        print(d["id"], r["status"], r["summary"][:160])
        if r["status"] != "PASS":
            bad.append(f"step{d['id']}={r['status']}")
print("tools:", tools)
sys.exit(0 if not bad and p.returncode == 0 and tools >= 21 else 1)
