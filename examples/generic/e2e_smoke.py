#!/usr/bin/env python3
"""三领域通用样例烟雾：关系 404 / GET 分页 / request_id 幂等（精选 case 须全 PASS）。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from testmind import core
from sut import serve

PORT = 18310


def main():
    srv, sut = serve(PORT)
    ev = core.Evidence()
    db = core.DBCheck(sut.db, tables=("stock", "coupon", "warehouse"))
    eng = core.Engine(f"http://127.0.0.1:{PORT}", ev, db=db)
    results = [
        eng.run({
            "id": "SMOKE-stock-happy", "priority": "P0",
            "action": {"kind": "http", "method": "POST", "path": "/stock",
                       "body": {"sku": "A", "warehouse_id": 1, "qty": 2}},
            "expected": {"http": 201},
        }),
        eng.run({
            "id": "SMOKE-stock-relation", "priority": "P0",
            "action": {"kind": "http", "method": "POST", "path": "/stock",
                       "body": {"sku": "B", "warehouse_id": 999, "qty": 1}},
            "expected": {"http": 404, "db_no_write": True},
        }),
        eng.run({
            "id": "SMOKE-list-items", "priority": "P0",
            "action": {"kind": "http", "method": "GET", "path": "/items?page=1&size=10"},
            "expected": {"http": 200},
        }),
        eng.run({
            "id": "SMOKE-idem-replay", "priority": "P0",
            "action": {"kind": "sequence", "steps": [
                {"path": "/coupons", "method": "POST",
                 "body": {"code": "SAVE10", "request_id": "rid-smoke-1"}, "expect_status": 201},
                {"path": "/coupons", "method": "POST",
                 "body": {"code": "SAVE10", "request_id": "rid-smoke-1"}, "expect_status": 200},
            ]},
            "expected": {"db_count": [{"sql": "SELECT COUNT(*) FROM coupon WHERE request_id='rid-smoke-1'", "expect": 1}]},
        }),
    ]
    st, why = core.Gate.evaluate(results, [], [], floor=[])
    srv.shutdown()
    srv.server_close()
    try:
        sut.db.close()
    except Exception:
        pass
    out = {"final": st, "why": why, "pass": sum(1 for r in results if r["status"] == "PASS"),
           "domains": ["stock/relation", "GET/pagination", "coupon/idempotency"]}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if st == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
