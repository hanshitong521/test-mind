# tests/test_all.py — TestMind 自测 + 红队自测（§47/§48）
# 运行: python -m unittest discover -s tests   (从 testmind/ 目录)
import os, sys, time, unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)
sys.path.insert(0, os.path.join(TM, "examples", "red-packet"))

from testmind import core
from testmind.faultproxy import FaultProxy

DDL = "CREATE TABLE t (name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 50), amount_cents INTEGER NOT NULL CHECK(amount_cents BETWEEN 1 AND 100000));"
API = {"components": {"schemas": {"Req": {"required": ["name"], "properties": {
    "name": {"type": "string", "minLength": 1, "maxLength": 50}}}}}}


class TestFacts(unittest.TestCase):
    def test_fact_requires_source(self):
        with self.assertRaises(AssertionError):
            core.Facts().add("t", "no source", "")            # §4.1

    def test_add_raw_requires_source(self):
        with self.assertRaises(AssertionError):
            core.Facts().add_raw({"topic": "t", "statement": "s"})   # add_facts 注入通道同样强制出处

    def test_add_raw_derived_must_reference(self):
        f = core.Facts()
        f.add("a", "x", "s")
        with self.assertRaises(AssertionError):
            f.add_raw({"topic": "b", "statement": "y", "source": "s", "derived_from": ["F999"]})

    def test_conflict_detection_and_contract_block(self):
        f = core.Facts()
        f.add("qty", "quantity 最多 1000", "doc")
        f.add("qty", "quantity 不允许超过 10000", "code")
        self.assertEqual(len(f.conflicts()), 1)               # §6
        with self.assertRaises(AssertionError):
            core.build_contract("X", {}, f, {}, {})

    def test_answer_normalizes_question_id(self):
        f = core.Facts()
        f.ask("q?", "why", "impact")
        self.assertTrue(f.answer("Q1", "self-resolved"))      # Q1 == Q001
        self.assertEqual(f.questions[0]["state"], "CONFIRMED")

    def test_schema_and_openapi_extraction(self):
        f1 = core.FactResolver.from_schema_sql(DDL, "src")
        f2 = core.FactResolver.from_openapi(API, "src")
        self.assertTrue(any(x["topic"] == "len:name" for x in f1.f))
        self.assertTrue(any(x["topic"] == "range:amount_cents" for x in f1.f))
        self.assertTrue(any(x["topic"].startswith("required:") for x in f2.f))

    def test_wrong_schema_yields_nothing(self):
        self.assertEqual(len(core.FactResolver.from_schema_sql("CREATE TABLE t (id INTEGER);", "s").f), 0)


class TestPlanner(unittest.TestCase):
    def test_boundary_values(self):
        fact = {"statement": "amount_cents between 1 and 100000"}
        self.assertEqual(core.boundary_values(fact), [0, 1, 2, 99999, 100000, 100001])

    def test_open_range(self):
        self.assertEqual(core.boundary_values({"statement": "x in [1,None]"}), [0, 1, 2])

    def test_plan_emits_executable_cases_with_db_guards(self):
        f = core.FactResolver.from_schema_sql(DDL, "s")
        for x in core.FactResolver.from_openapi(API, "s").f:
            f.f.append(x)
        plan = core.plan_cases(f, {"name": "ok", "amount_cents": 100}, path="/x")
        neg = [c for c in plan if c["expected"]["http"] == 400]
        self.assertTrue(neg)
        self.assertTrue(all(c["expected"].get("db_no_write") for c in neg))   # 负例必带禁写断言
        self.assertTrue(all(c["action"]["kind"] == "http" for c in plan))
        self.assertTrue(any(c["id"].startswith("P1-TYP-amount_cents-") for c in plan))

    def test_plan_risk_without_hooks_skips_fixture_p0(self):
        f = core.FactResolver.from_schema_sql(DDL, "s")
        f.add("idempotency:create", "idem_key 重复创建返回 200", "s")
        f.add("relation:influencer", "不存在达人创建返回 404", "s")
        f.add("state:grant", "grant 仅 RUNNING 成功", "s")
        risk = core.plan_risk_cases(f, {"name": "ok", "amount_cents": 100}, path="/x", run_id="t1")
        ids = {c["id"] for c in risk}
        self.assertIn("P0-IDEM-replay", ids)
        self.assertIn("P0-CONC-create", ids)
        self.assertIn("P0-REL-missing", ids)
        self.assertNotIn("P0-CONC-grant", ids)          # 无 hooks.seed 不编造发放并发
        self.assertEqual(core.risk_floor(f), ["P0-IDEM-replay", "P0-CONC-create", "P0-REL-missing"])

    def test_risk_floor_blocks_unexecuted_p0(self):
        f = core.Facts()
        f.add("idempotency:create", "重复返回 200", "s")
        happy = {"id": "P0-HAPPY", "priority": "P0", "status": "PASS", "evidence": "e/"}
        st, why = core.Gate.evaluate([happy], [], [], floor=core.risk_floor(f))
        self.assertEqual(st, "NOT_TESTED")
        self.assertIn("P0-IDEM-replay", why)

class TestGate(unittest.TestCase):
    @staticmethod
    def R(st, pr="P0", ev="e/"):
        return {"id": "x", "priority": pr, "status": st, "evidence": ev}

    def test_empty_results_never_pass(self):
        st, _ = core.Gate.evaluate([], [], [])
        self.assertEqual(st, "NOT_TESTED")                    # §4.2

    def test_pass_without_evidence_fails(self):
        st, _ = core.Gate.evaluate([self.R("PASS", ev=None)], [], [])
        self.assertEqual(st, "FAIL")                          # §4.3

    def test_conflict_blocks(self):
        self.assertEqual(core.Gate.evaluate([], [], [{}])[0], "BLOCKED")

    def test_open_unknown_holds(self):
        st, _ = core.Gate.evaluate([self.R("PASS", pr="P1")], [{"state": "USER_REQUIRED"}], [])
        self.assertEqual(st, "HOLD")

    def test_resolved_unknown_passes(self):
        st, _ = core.Gate.evaluate([self.R("PASS")], [{"state": "CONFIRMED"}], [])
        self.assertEqual(st, "PASS")

    def test_illegal_status_rejected(self):
        with self.assertRaises(AssertionError):
            core.Gate.evaluate([self.R("ASSUMED_PASS")], [], [])


class TestRegressionRegistry(unittest.TestCase):
    def test_add_and_load_dedupes_by_id(self):
        case = {"id": "T-REG-1", "action": {"kind": "http", "path": "/"}, "expected": {"http": 200}}
        try:
            n1 = core.add_regression_case(case, "test")
            core.add_regression_case(case, "test")            # 同 id 覆盖不重复
            reg = core.load_registry()
            self.assertGreaterEqual(n1, 1)
            self.assertEqual(sum(1 for c in reg if c["id"] == "T-REG-1"), 1)
        finally:
            reg = [c for c in core.load_registry() if c["id"] != "T-REG-1"]
            with open(core.REGISTRY, "w", encoding="utf-8") as fh:
                import json
                json.dump(reg, fh)


class TestSUTBusiness(unittest.TestCase):
    def setUp(self):
        from sut import SUT
        self.s = SUT(":memory:")

    def _create(self, **kw):
        body = {"name": "x", "amount_cents": 1, "quantity": 1, "influencer_id": 1, "created_by": 1}
        return self.s.create({**body, **kw})

    def test_idempotent_replay_single_row(self):
        c1, b1 = self._create(idem_key="k1")
        c2, b2 = self._create(idem_key="k1")
        self.assertEqual((c1, c2), (201, 200))
        self.assertTrue(b2["duplicate"])
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM red_packet WHERE idem_key='k1'").fetchone()[0], 1)

    def test_oversell_guard(self):
        self._create(quantity=1)
        self.s.db.execute("UPDATE red_packet SET status=1")
        self.assertEqual(self.s.grant(1)[0], 200)
        self.assertEqual(self.s.grant(1)[0], 409)
        self.assertGreaterEqual(self.s.db.execute("SELECT remaining FROM red_packet").fetchone()[0], 0)

    def test_expiry_boundary_inclusive(self):
        self._create(quantity=1, expiry_ts=2000000000)
        self.s.db.execute("UPDATE red_packet SET status=1")
        self.assertEqual(self.s.grant(1, now=2000000000)[0], 200)      # ==expiry 允许（代码事实）
        self.assertEqual(self.s.grant(1, now=2000000001)[0], 409)      # >expiry 拒绝

    def test_dependency_down_no_dirty_db(self):
        self._create(quantity=2)
        self.s.db.execute("UPDATE red_packet SET status=1")
        self.s.dependency_url = "http://127.0.0.1:9/x"                 # 死端口=拒连
        code, _ = self.s.grant(1)
        self.assertEqual(code, 502)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM grant_log").fetchone()[0], 0)


class TestEngineAndProxy(unittest.TestCase):
    """假绿防线：ok 模式必须 200（对照组），故障模式必须 502 —— 缺一边即抓到接线错误。"""

    @classmethod
    def setUpClass(cls):
        from sut import serve
        cls.srv, cls.sut = serve(db_path=":memory:", port=18201)
        cls.proxy = FaultProxy("http://127.0.0.1:18201", port=18298)
        cls.sut.dependency_url = cls.proxy.url() + "/risk"
        cls.sut.create({"name": "g", "amount_cents": 1, "quantity": 3, "influencer_id": 1, "created_by": 1})
        cls.sut.db.execute("UPDATE red_packet SET status=1")
        cls.ev = core.Evidence()
        cls.db = core.DBCheck(core.open_db({"kind": "sqlite", "path": ":memory:"}), tables=())  # 占位不用

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.srv.shutdown()

    def test_engine_ok_then_fault_then_recovered(self):
        e = core.Engine("http://127.0.0.1:18201", self.ev, proxy=self.__class__.proxy)
        ok = e.run({"id": "OK-grant", "priority": "P0",
                    "action": {"kind": "http", "method": "POST", "path": "/red-packets/1/grant"},
                    "expected": {"http": 200, "json": {"granted": True}}})
        bad = e.run({"id": "FAULT-503", "priority": "P0",
                     "action": {"kind": "fault", "mode": {"kind": "status", "code": 503},
                                "path": "/red-packets/1/grant"},
                     "expected": {"http": 502}})
        rec = e.run({"id": "RECOVERED", "priority": "P0",
                     "action": {"kind": "http", "method": "POST", "path": "/red-packets/1/grant"},
                     "expected": {"http": 200}})                       # fault 后模式自动回 ok
        self.assertEqual([ok["status"], bad["status"], rec["status"]], ["PASS", "PASS", "PASS"])

    def test_engine_detects_false_expectation(self):
        r = core.Engine("http://127.0.0.1:18201", self.ev).run(
            {"id": "WRONG-EXP", "priority": "P0",
             "action": {"kind": "http", "method": "POST", "path": "/red-packets/1/grant"},
             "expected": {"http": 201}})                               # 故意错预期
        self.assertEqual(r["status"], "FAIL")                          # 不许顺从错误预期假通过

    def test_out_of_int64_negative_id_no_crash(self):
        r = core.Engine("http://127.0.0.1:18201", self.ev).run(
            {"id": "NEGI64", "priority": "P0",
             "action": {"kind": "http", "method": "POST", "path": "/red-packets/-9223372036854775809/grant"},
             "expected": {"http": 404}})     # 缺陷⑬回归：越界负 id 曾崩连接（sqlite int64 溢出）
        self.assertEqual(r["status"], "PASS")

    def test_chunked_body_no_keepalive_pollution(self):
        """缺陷⑭回归：chunked POST /scheduler/tick 后同连接的下一个请求不得被残留字节污染。"""
        import socket
        s = socket.create_connection(("127.0.0.1", 18201), timeout=10)
        req1 = (b"POST /scheduler/tick HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n"
                b"Content-Type: application/json\r\nConnection: keep-alive\r\n\r\n"
                b"2\r\n{}\r\n0\r\n\r\n")
        body2 = b'{"name":"chunk2","amount_cents":1,"quantity":1,"influencer_id":1,"created_by":1}'
        req2 = (b"POST /red-packets HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                b"Content-Length: " + str(len(body2)).encode() + b"\r\nConnection: close\r\n\r\n" + body2)
        s.sendall(req1 + req2)
        buf = b""
        while True:
            try:
                chunk = s.recv(4096)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
        s.close()
        self.assertEqual(buf.count(b"HTTP/1.1 200"), 1)          # tick 200
        self.assertIn(b"201 Created", buf)                        # 第二请求完好
        self.assertNotIn(b"missing_field", buf)                   # 未被污染

    def test_fault_without_proxy_is_skipped_not_passed(self):
        e = core.Engine("http://127.0.0.1:18201", self.ev)             # 无 proxy
        r = e.run({"id": "NOPROXY", "priority": "P1",
                   "action": {"kind": "fault", "mode": "refuse", "path": "/red-packets/1/grant"},
                   "expected": {"http": 502}})
        self.assertEqual(r["status"], "SKIPPED_WITH_REASON")


class TestMCPProtocol(unittest.TestCase):
    def test_envelope_shape(self):
        from testmind.mcp import envelope
        e = envelope("PASS", "ok")
        for k in ("status", "summary", "evidence", "facts", "unknowns", "next_actions"):
            self.assertIn(k, e)                                       # §38

    def test_unknown_tool_recovers(self):
        from testmind.mcp import dispatch
        self.assertEqual(dispatch("nope", {})["status"], "TOOL_ERROR")

    def test_no_execution_gate(self):
        from testmind.mcp import dispatch
        self.assertEqual(dispatch("final_gate", {})["status"], "NOT_TESTED")

    def test_schema_fail_blocks_gate(self):
        """§25：schemathesis 发现未解释时 final_gate 不得 PASS（门禁联动）。"""
        from testmind import mcp
        mcp.S.results = [{"id": "x", "priority": "P0", "status": "PASS", "evidence": "e/"}]
        mcp.S.schema = {"status": "FAIL"}
        try:
            self.assertEqual(mcp.dispatch("final_gate", {})["status"], "FAIL")
        finally:
            mcp.S.results, mcp.S.schema = [], None


DDL_FULL = """CREATE TABLE acct (
  id INTEGER PRIMARY KEY AUTO_INCREMENT,
  name TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK(amount_cents BETWEEN 1 AND 1000),
  memo TEXT DEFAULT '',
  UNIQUE KEY uk_name (name)
);"""


class TestStaticPrecheck(unittest.TestCase):
    """静态预检：只许抓"DDL vs 写库语句"能确定性推出的缺陷；产出永不进 Gate。"""

    def _pre(self, statements):
        return core.static_precheck(DDL_FULL, statements)

    def test_positional_insert_column_mismatch(self):
        """列错位主形态：VALUES 个数 ≠ DDL 列数（缺陷 REG-002-insert-align 同型）。"""
        r = self._pre(["INSERT INTO acct VALUES ('a', 100)"])
        rules = [f["rule"] for f in r["findings"]]
        self.assertIn("insert_count_mismatch", rules)
        self.assertEqual([f for f in r["findings"] if f["rule"] == "insert_count_mismatch"][0]["severity"], "error")

    def test_named_insert_checks(self):
        r = self._pre(["INSERT INTO acct (name, amount_cents, ghost) VALUES ('a', 1, 0)",   # 未知列
                       "INSERT INTO acct (name) VALUES ('b')",                              # 漏 NOT NULL 无默认列
                       "INSERT INTO acct (name, amount_cents) VALUES ('c', NULL)",          # 显式 NULL 进 NOT NULL
                       "INSERT INTO acct (name, amount_cents) VALUES ('d', 5000)",          # CHECK 越界
                       "INSERT INTO acct (name, amount_cents) VALUES ('e', 'x')"])          # 字符串进数值列(warn)
        by_rule = {}
        for f in r["findings"]:
            by_rule.setdefault(f["rule"], []).append(f)
        self.assertEqual(by_rule["unknown_column"][0]["severity"], "error")
        self.assertEqual(by_rule["not_null_column_missing"][0]["severity"], "error")
        self.assertEqual(by_rule["not_null_violation"][0]["severity"], "error")
        self.assertEqual(by_rule["check_range_violation"][0]["severity"], "error")
        self.assertEqual(by_rule["type_mismatch"][0]["severity"], "warn")

    def test_update_null_into_not_null(self):
        r = self._pre(["UPDATE acct SET name = NULL WHERE id = 1",
                       "UPDATE acct SET memo = 'ok', amount_cents = 3 WHERE id = 1"])   # 对照：干净
        errs = [f for f in r["findings"] if f["severity"] == "error"]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["rule"], "not_null_violation")

    def test_clean_insert_no_findings(self):
        r = self._pre(["INSERT INTO acct (name, amount_cents) VALUES ('ok', 1)",
                       "INSERT INTO acct VALUES (1, 'pos-ok', 999, 'x')"])   # 999 在 [1,1000] 内
        self.assertEqual([f for f in r["findings"] if f["severity"] == "error"], [])

    def test_unknown_table_is_warn_not_error(self):
        r = self._pre(["INSERT INTO nope (a) VALUES (1)"])
        self.assertEqual([f["severity"] for f in r["findings"]], ["warn"])

    def test_expression_values_are_not_flagged(self):
        """占位符/函数调用不许误报（静态检查只咬字面量）。"""
        r = self._pre(["INSERT INTO acct (name, amount_cents) VALUES (?, ?)",
                       "INSERT INTO acct (name, amount_cents) VALUES (UPPER('a'), NOW())"])
        self.assertEqual(r["findings"], [])


class TestMCPPrecheckGateIsolation(unittest.TestCase):
    def test_precheck_never_enters_gate(self):
        """红线：静态发现哪怕 error 满天飞，final_gate 没执行照样 NOT_TESTED，不许借静态翻绿。"""
        from testmind import mcp
        try:
            r = mcp.dispatch("static_precheck", {
                "schema_sql": DDL_FULL,
                "statements": [{"sql": "INSERT INTO acct VALUES ('only', 1)", "source": "diff"}]})
            self.assertEqual(r["status"], "FAIL")                      # findings 存在 → 工具层 FAIL
            self.assertTrue(mcp.S.precheck and mcp.S.precheck["findings"])
        finally:
            mcp.S.precheck = None
            mcp.S.ev = None
        self.assertEqual(mcp.dispatch("final_gate", {})["status"], "NOT_TESTED")
        self.assertEqual(mcp.S.results, [])                            # results 未被污染

    def test_precheck_carries_fact_conflicts(self):
        from testmind import mcp
        old_facts = mcp.S.facts
        mcp.S.facts = core.Facts()                       # 冲突事实不能泄漏给后续测试
        try:
            mcp.S.facts.add("amount", "amount 最多 1000", "doc")
            mcp.S.facts.add("amount", "amount 不允许超过 10000", "code")
            r = mcp.dispatch("static_precheck", {"schema_sql": DDL_FULL, "statements": []})
            self.assertEqual(r["status"], "PASS")
            self.assertEqual(len(r["fact_conflicts"]), 1)
        finally:
            mcp.S.facts = old_facts
            mcp.S.reset()


class TestSutProvision(unittest.TestCase):
    """泛化自动拉起：TestMind 替你起被测服务，health 探活才放行；cleanup 必须回收进程树。"""

    PORT = 18211

    @classmethod
    def setUpClass(cls):
        import tempfile, textwrap
        cls.tmp = tempfile.mkdtemp(prefix="tm_sut_")
        cls.script = os.path.join(cls.tmp, "mini_sut.py")
        with open(cls.script, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(f'''
                import json
                from http.server import BaseHTTPRequestHandler, HTTPServer
                class H(BaseHTTPRequestHandler):
                    def _ok(self):
                        b = json.dumps({{"ok": True}}).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(b)))
                        self.end_headers()
                        self.wfile.write(b)
                    do_GET = do_POST = _ok
                    def log_message(self, *a):
                        pass
                HTTPServer(("127.0.0.1", {cls.PORT}), H).serve_forever()
            '''))

    def _prepare(self, **sut):
        from testmind import mcp
        return mcp.dispatch("prepare_environment", {
            "sut": {"command": f'"{sys.executable}" "{self.script}"', "health_check": {
                "url": f"http://127.0.0.1:{self.PORT}/health", "timeout_s": 20}, **sut}})

    def test_spawn_health_and_real_request(self):
        from testmind import mcp
        r = self._prepare()
        try:
            self.assertEqual(r["status"], "PASS")
            self.assertEqual(mcp.S.engine.base_url, f"http://127.0.0.1:{self.PORT}")  # 健康路径不进 base_url
            probe = mcp.S.engine.run({"id": "PROBE", "priority": "P0",
                                      "action": {"kind": "http", "method": "GET", "path": "/health"},
                                      "expected": {"http": 200}})
            self.assertEqual(probe["status"], "PASS")                  # 拉起的服务真能被打
        finally:
            mcp.dispatch("cleanup", {})
        with self.assertRaises(Exception):
            import socket
            socket.create_connection(("127.0.0.1", self.PORT), timeout=2).close()   # 进程树已回收

    def test_early_exit_is_blocked_not_fake_ready(self):
        from testmind import mcp
        r = mcp.dispatch("prepare_environment", {
            "sut": {"command": f'"{sys.executable}" -c "import sys; sys.exit(3)"',
                    "health_check": {"url": f"http://127.0.0.1:{self.PORT + 1}/health", "timeout_s": 15}}})
        self.assertEqual(r["status"], "BLOCKED")
        self.assertIn("exited early", r["summary"])
        self.assertIsNone(mcp.S.engine)                                # 不带死服务前进

    def test_health_timeout_is_blocked_and_reaped(self):
        from testmind import mcp
        r = self._prepare(health_check={"port": 1, "timeout_s": 2})    # 端口1必拒连 → 超时
        self.assertEqual(r["status"], "BLOCKED")
        self.assertIsNone(mcp.S.engine)
        self.assertIsNone(mcp.S.sut_proc)                              # 超时路径也回收了进程


class TestSQLPerf(unittest.TestCase):
    """SQL 性能巡检：只读强制 / 阈值告警 / 对拍一致 / gate 隔离。"""

    def setUp(self):
        self.conn = core.open_db({"kind": "sqlite", "path": ":memory:"})
        self.dbc = core.DBCheck(self.conn, tables=("t",))
        self.dbc.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        self.dbc.exec("INSERT INTO t (id, v) VALUES (1,'a'),(2,'b')")

    def tearDown(self):
        self.conn.close()

    def test_slow_sql_flagged_by_threshold(self):
        r = core.sql_perf_check(self.dbc, [{"id": "Q1", "sql": "SELECT * FROM t"}], threshold_ms=0)
        f = [x for x in r["findings"] if x["rule"] == "slow_sql"][0]
        self.assertEqual(f["severity"], "warn")
        self.assertTrue(r["queries"][0]["slow"])
        self.assertTrue(r["queries"][0].get("explain"))          # 慢 SQL 附执行计划进证据

    def test_read_only_enforced_and_no_write(self):
        r = core.sql_perf_check(self.dbc, [{"id": "W", "sql": "DELETE FROM t"}])
        f = [x for x in r["findings"] if x["rule"] == "not_read_only"][0]
        self.assertEqual(f["severity"], "error")
        self.assertEqual(list(self.dbc.counts().values())[0], 2)  # 库没被动过

    def test_equivalent_compare_no_mismatch(self):
        r = core.sql_perf_check(self.dbc, [{"id": "Q", "sql": "SELECT * FROM t ORDER BY id",
                                            "compare_sql": "SELECT id, v FROM t ORDER BY id"}])
        self.assertEqual([f for f in r["findings"] if f["rule"] == "data_mismatch"], [])
        self.assertTrue(r["queries"][0]["compare"]["same_result"])

    def test_data_mismatch_blocks_replacement(self):
        r = core.sql_perf_check(self.dbc, [{"id": "Q", "sql": "SELECT * FROM t WHERE id > 0",
                                            "compare_sql": "SELECT * FROM t WHERE id > 1"}])
        f = [x for x in r["findings"] if x["rule"] == "data_mismatch"][0]
        self.assertEqual(f["severity"], "error")                  # 不一致=禁止替换，不许报"更快"

    def test_query_error_is_error(self):
        r = core.sql_perf_check(self.dbc, [{"id": "E", "sql": "SELECT * FROM nope"}])
        self.assertEqual([f for f in r["findings"] if f["rule"] == "query_error"][0]["severity"], "error")

    def test_mcp_perf_never_enters_gate(self):
        from testmind import mcp
        try:
            r = mcp.dispatch("sql_perf_check", {
                "db": {"kind": "sqlite", "path": ":memory:"},
                "queries": [{"id": "Q1", "sql": "SELECT 1 AS x", "source": "dao.py:12",
                             "compare_sql": "SELECT 1 AS x"}], "threshold_ms": 0})
            self.assertEqual(r["status"], "PASS")
            self.assertTrue(mcp.S.sqlperf["queries"])
        finally:
            mcp.S.sqlperf = None
            mcp.S.ev = None
        self.assertEqual(mcp.dispatch("final_gate", {})["status"], "NOT_TESTED")
        self.assertEqual(mcp.S.results, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
