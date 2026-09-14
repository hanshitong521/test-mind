# V10 Phase 4 Blocking Test：Cross-Endpoint Oracle + Metamorphic
# 关键验收：detail 不可见 → list/count/export 泄漏时，oracle case 必须 FAIL（抓得住）。
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import core, oracle as O, scenario as SC

ROWS = [
    {"id": 1, "name": "alpha", "amount": 10, "company_id": "c1", "owner": "u1", "status": "PUBLISHED"},
    {"id": 2, "name": "beta", "amount": 20, "company_id": "c1", "owner": "u1", "status": "DRAFT"},
    {"id": 3, "name": "gamma", "amount": 30, "company_id": "c2", "owner": "u2", "status": "PUBLISHED"},
]


def make_handler(bug="none"):
    """bug=none 正确实现；bug=list_leak：list/count/export 不过滤不可见数据（§11.1 缺陷）。"""
    state = {"rows": [dict(r) for r in ROWS]}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _who(self):
            return self.headers.get("X-User", "anon"), self.headers.get("X-Company", "")

        def _visible(self, r, user, comp, role):
            if role == "ADMIN" or r["owner"] == user:
                return True
            if r["status"] != "PUBLISHED":
                return False
            return comp and r["company_id"] == comp

        def _send(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _query(self, rows, q):
            """等值过滤 + 排序（供 list/page 入口共用）。"""
            out = [r for r in rows
                   if all(str(r.get(k)) == v for k, v in q.items()
                          if k not in ("page", "size", "sort", "dir") and r.get(k) is not None)]
            sb = q.get("sort")
            if sb:
                out = sorted(out, key=lambda r: r.get(sb), reverse=q.get("dir") == "desc")
            return out

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            user, comp = self._who()
            role = self.headers.get("X-Role", "USER")
            seg = [s for s in u.path.split("/") if s]
            if seg in (["orders"], ["orders", "page"]):
                vis = [r for r in state["rows"] if self._visible(r, user, comp, role)]
                if bug == "list_leak":
                    vis = list(state["rows"])          # 缺陷：泄漏不可见行
                vis = self._query(vis, q)
                if "page" in q:
                    size = int(q.get("size", 2))
                    pg = int(q["page"])
                    self._send(200, {"total": len(vis),
                                     "items": vis[(pg - 1) * size:pg * size]})
                else:
                    self._send(200, {"items": vis, "total": len(vis)})
            elif seg == ["orders", "count"]:
                vis = [r for r in state["rows"] if self._visible(r, user, comp, role)]
                self._send(200, {"total": len(state["rows"]) if bug == "list_leak" else len(vis)})
            elif seg == ["orders", "export"]:
                vis = [r for r in state["rows"] if self._visible(r, user, comp, role)]
                self._send(200, {"items": vis})
            elif len(seg) == 2 and seg[0] == "orders":
                r = next((x for x in state["rows"] if str(x["id"]) == seg[1]), None)
                if r and self._visible(r, user, comp, role):
                    self._send(200, r)
                else:
                    self._send(404, {"error": "not found"})
            else:
                self._send(404, {})

        def do_PUT(self):
            seg = [s for s in urlparse(self.path).path.split("/") if s]
            user, _ = self._who()
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"]) or 0) or b"{}")
            if len(seg) == 2 and seg[0] == "orders":
                r = next((x for x in state["rows"] if str(x["id"]) == seg[1]), None)
                if r and (r["owner"] == user or self.headers.get("X-Role") == "ADMIN"):
                    r.update({k: v for k, v in body.items() if k != "id"})
                    self._send(200, r)
                else:
                    self._send(403, {})
            else:
                self._send(404, {})

        def do_DELETE(self):
            seg = [s for s in urlparse(self.path).path.split("/") if s]
            user, _ = self._who()
            if len(seg) == 2 and seg[0] == "orders":
                r = next((x for x in state["rows"] if str(x["id"]) == seg[1]), None)
                if r and r["owner"] == user:
                    state["rows"].remove(r)
                    self._send(200, {"ok": True})
                else:
                    self._send(404, {})
            else:
                self._send(404, {})
    return H


class _Srv:
    def __init__(self, bug="none"):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(bug))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


ENDPOINTS = ["GET /orders/{id}", "GET /orders", "GET /orders/page", "GET /orders/count",
             "GET /orders/export", "PUT /orders/{id}", "DELETE /orders/{id}"]
ACTORS = {
    "OWNER": {"headers": {"X-User": "u1", "X-Company": "c1"}},
    "OTHER_COMPANY": {"headers": {"X-User": "u9", "X-Company": "c9"}},
}
SPEC = {"key": "id", "sample_id": 2, "name_field": "name", "new_value": "beta-x",
        "invisible_status": 404, "page_size": 1, "pages": 3, "sort_by": "amount",
        "filter_a": {"status": "PUBLISHED"}, "filter_b": {"company_id": "c1"},
        "allow_delete": True, "delete_id": 1}


def _run(engine, cases):
    return [engine.run(c) for c in cases]


class TestOraclePlanning(unittest.TestCase):
    def test_group_surfaces(self):
        g = O.group_surfaces(ENDPOINTS)
        self.assertEqual(g["orders"]["detail"], "GET /orders/{id}")
        self.assertEqual(g["orders"]["list"], "GET /orders")
        self.assertEqual(g["orders"]["count"], "GET /orders/count")
        self.assertEqual(g["orders"]["export"], "GET /orders/export")
        self.assertEqual(g["orders"]["update"], "PUT /orders/{id}")
        self.assertEqual(g["orders"]["delete"], "DELETE /orders/{id}")

    def test_cases_have_provenance(self):
        """§22：oracle 生成的每个 case 溯源齐全。"""
        cases = O.plan(ENDPOINTS, SPEC, actors=ACTORS)
        self.assertTrue(cases)
        for c in cases:
            ok, missing = SC.validate_case_provenance(c)
            self.assertTrue(ok, (c["id"], missing))
            self.assertEqual(c["category"], "CROSS_ENDPOINT")
        kinds = {c["id"].split("-")[1] for c in cases}
        self.assertTrue({"VIS", "UPD", "DEL", "FILTER", "PAGE", "SORT", "BYPASS"} <= kinds, kinds)

    def test_no_actors_no_visibility_cases(self):
        """§7：无角色证据 → 不出可见性 case（禁止凭空）。"""
        cases = O.plan(ENDPOINTS, SPEC, actors=None)
        self.assertFalse([c for c in cases if "-VIS-" in c["id"]])


class TestOracleExecution(unittest.TestCase):
    def setUp(self):
        self.srv = _Srv("none")
        self.ev = core.Evidence()
        self.engine = core.Engine(self.srv.base, self.ev)

    def tearDown(self):
        self.srv.stop()

    def test_correct_sut_all_pass(self):
        cases = O.plan(ENDPOINTS, SPEC, actors=ACTORS)
        results = _run(self.engine, cases)
        bad = [r["id"] for r in results if r["status"] != "PASS"]
        self.assertEqual(bad, [], {r["id"]: r.get("detail") for r in results if r["status"] != "PASS"})

    def test_list_leak_caught(self):
        """§11.1 核心：list/count 泄漏不可见行 → oracle 必须 FAIL。"""
        leaky = _Srv("list_leak")
        try:
            eng = core.Engine(leaky.base, core.Evidence())
            cases = [c for c in O.plan(ENDPOINTS, SPEC, actors=ACTORS) if "-VIS-" in c["id"]]
            self.assertTrue(cases)
            results = _run(eng, cases)
            self.assertTrue(any(r["status"] == "FAIL" for r in results),
                            [r["status"] for r in results])
        finally:
            leaky.stop()


class TestCheckDSL(unittest.TestCase):
    def test_present_absent(self):
        body = {"items": [{"id": 1}, {"id": 2}]}
        self.assertTrue(O.run_checks([{"present": {"field": "id", "value": 1}}], body, {})[0])
        self.assertFalse(O.run_checks([{"absent": {"field": "id", "value": 1}}], body, {})[0])

    def test_subset_and_reverse(self):
        a = {"items": [{"id": 1}, {"id": 2}, {"id": 3}]}
        b = {"items": [{"id": 1}, {"id": 2}]}
        self.assertTrue(O.run_checks([{"ids_subset_of": {"var": "a", "key": "id"}}], b, {"a": a})[0])
        asc = {"items": [{"id": 1}, {"id": 2}]}
        desc = {"items": [{"id": 2}, {"id": 1}]}
        self.assertTrue(O.run_checks([{"reverse_of": {"var": "asc", "key": "id"}}], desc, {"asc": asc})[0])

    def test_total_and_count_delta(self):
        self.assertTrue(O.run_checks([{"total_gte_size": {}}], {"total": 5, "items": [1, 2]}, {})[0])
        self.assertFalse(O.run_checks([{"total_gte_size": {}}], {"total": 1, "items": [1, 2]}, {})[0])
        self.assertTrue(O.run_checks([{"count_delta": {"var": "c0", "delta": -1}}],
                                     {"total": 4}, {"c0": {"total": 5}})[0])

    def test_field_eq_nested(self):
        self.assertTrue(O.run_checks([{"field_eq": {"path": "a.b", "value": 1}}],
                                     {"a": {"b": 1}}, {})[0])

    def test_rows_of_shapes(self):
        self.assertEqual(len(O.rows_of([{"id": 1}])), 1)
        self.assertEqual(len(O.rows_of({"items": [{"id": 1}]})), 1)
        self.assertEqual(len(O.rows_of({"data": {"list": [{"id": 1}]}})), 0)  # 嵌套 data.list 不猜
        self.assertEqual(O.rows_of({"error": "x"}), [])


class TestFacadeWiring(unittest.TestCase):
    def test_plan_verification_oracle(self):
        from testmind import mcp
        mcp.S.reset()
        r = mcp.dispatch("plan_verification", {"oracle": {"endpoints": ENDPOINTS,
                                                          "spec": SPEC, "actors": ACTORS}})
        self.assertEqual(r["status"], "PASS")
        self.assertGreater(r["plan"], 0)
        self.assertTrue(all(c.get("reason") and c.get("expected_source") for c in mcp.S.plan))


if __name__ == "__main__":
    unittest.main()
