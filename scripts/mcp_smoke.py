# scripts/mcp_smoke.py — 通过 stdio 对 MCP server 跑一发 7 门面全链（示例环境，§29/§30）
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = {"name": "smoke", "amount_cents": 100, "quantity": 10, "influencer_id": 1, "created_by": 1}
FACTS = [
    {"topic": "relation:influencer", "statement": "失效/删除/不存在达人创建返回 404", "source": "sut.py:create()"},
    {"topic": "idempotency:create", "statement": "idem_key 重复创建返回 200 duplicate", "source": "sut.py:create()"},
]
lines = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    # prepare 先行：red-packet example 的 hooks 必须在 plan 前就位（risk case 才有出处）
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "prepare_verification",
        "arguments": {"env": {"example": "red-packet", "openapi_url": "http://127.0.0.1:18102/openapi.json"}}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "plan_verification", "arguments": {
        "openapi": "http://127.0.0.1:18102/openapi.json",   # §29：建立声明端点全集，final_gate 覆盖台账才有依据
        "facts": FACTS,
        "contract": {"contract_id": "SMOKE_RED_PACKET", "target": {"endpoint": "POST /red-packets"},
                     "inputs": CANON, "ok_status": 201},
        "plan": {"method": "POST", "path": "/red-packets", "base_input": CANON, "ok_status": 201,
                 "overrides": {"influencer_id": {"0": 400, "1": 201, "2": 404, "2147483646": 404,
                                                 "2147483647": 404, "2147483648": 400},
                               "created_by": {"0": 400, "2147483648": 400, "default": 201}}}}}},
    {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "run_verification", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "run_verification",
        "arguments": {"concurrency": {"path": "/red-packets", "count": 8, "body": CANON,
                                      "max_success": 8, "id": "SMOKE-CONC"}}}},
    # §29 覆盖台账：补齐 tick/grant/action 三个写端点（setup 精确 fixture，禁随机业务数据）
    {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "plan_verification", "arguments": {
        "cases": [
            {"id": "SMOKE-TICK", "category": "SCHEDULER", "priority": "P0", "source": "smoke:coverage",
             "setup": ["DELETE FROM grant_log WHERE packet_id=9052", "DELETE FROM red_packet WHERE id=9052",
                       "INSERT INTO red_packet(id,name,amount_cents,quantity,influencer_id,unlimited,expiry_ts,"
                       "scheduled_at,status,remaining,created_by,created_at,updated_at,deleted) "
                       "VALUES(9052,'SMOKE-9052',100,5,1,0,NULL,2000000000,1,5,1,0,0,0)"],
             "action": {"kind": "http", "method": "POST", "path": "/scheduler/tick",
                        "headers": {"X-Test-Now": "2000000001"}},
             "expected": {"http": 200, "json": {"dispatched": 1}}},
            {"id": "SMOKE-GRANT", "category": "BUSINESS", "priority": "P0", "source": "smoke:coverage",
             "setup": ["DELETE FROM grant_log WHERE packet_id=9050", "DELETE FROM red_packet WHERE id=9050",
                       "INSERT INTO red_packet(id,name,amount_cents,quantity,influencer_id,unlimited,expiry_ts,"
                       "scheduled_at,status,remaining,created_by,created_at,updated_at,deleted) "
                       "VALUES(9050,'SMOKE-9050',100,3,1,0,NULL,NULL,1,3,1,0,0,0)"],
             "action": {"kind": "http", "method": "POST", "path": "/red-packets/9050/grant"},
             "expected": {"http": 200, "json": {"granted": True},
                          "db_count": [{"sql": "SELECT COUNT(*) FROM grant_log WHERE packet_id=9050",
                                        "expect": 1}]}},
            {"id": "SMOKE-ACTION", "category": "STATE", "priority": "P0", "source": "smoke:coverage",
             "setup": ["DELETE FROM red_packet WHERE id=9051",
                       "INSERT INTO red_packet(id,name,amount_cents,quantity,influencer_id,unlimited,expiry_ts,"
                       "scheduled_at,status,remaining,created_by,created_at,updated_at,deleted) "
                       "VALUES(9051,'SMOKE-9051',100,5,1,0,NULL,NULL,0,5,1,0,0,0)"],
             "action": {"kind": "http", "method": "POST", "path": "/red-packets/9051/start"},
             "expected": {"http": 200, "json": {"status": 1}}},
        ]}}},
    {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "run_verification",
        "arguments": {"case_ids": ["SMOKE-TICK", "SMOKE-GRANT", "SMOKE-ACTION"]}}},
    {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "final_gate", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "cleanup", "arguments": {}}},
]
p = subprocess.run([sys.executable, os.path.join(ROOT, "testmind", "mcp.py")],
                   input="\n".join(json.dumps(x) for x in lines),
                   capture_output=True, text=True, encoding="utf-8", timeout=900)
tools = None
bad = []
for ln in p.stdout.splitlines():
    d = json.loads(ln)
    if d.get("id") == 2:
        tools = [t["name"] for t in d["result"]["tools"]]
    if d.get("id") in (3, 4, 5, 6, 7, 8, 9, 10):
        r = json.loads(d["result"]["content"][0]["text"])
        print(d["id"], r["status"], r["summary"][:160])
        if r["status"] != "PASS":
            bad.append(f"step{d['id']}={r['status']}")
# §29/AC-7：tools/list 必须恰好是 7 门面
EXPECT = {"analyze_impact", "plan_verification", "prepare_verification",
          "run_verification", "replay_failure", "final_gate", "cleanup"}
if set(tools or []) != EXPECT:
    bad.append(f"tools/list={sorted(tools or [])}")
print("tools:", sorted(tools or []))
sys.exit(0 if not bad and p.returncode == 0 else 1)
