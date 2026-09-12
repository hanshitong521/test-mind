"""看板单测：知识层不变式 + 数据层容错 + 服务层端点。

重点不变式：**7 组入口必须恰好覆盖 tool_schemas 里的全部 MCP 工具**。
新增工具却忘了归类 → 这里直接红，防止看板悄悄漏掉一个入口。
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from testmind.dashboard import aggregate, playbook, server  # noqa: E402
from testmind import tool_schemas  # noqa: E402


# ───────────────────────── 知识层 ─────────────────────────

class TestPlaybook(unittest.TestCase):
    def test_entry_groups_cover_every_mcp_tool(self):
        declared = set(tool_schemas.SCHEMAS)
        covered = playbook.covered_tools()
        missing = declared - covered
        extra = covered - declared
        self.assertEqual(missing, set(), f"有工具没归到任何入口：{sorted(missing)}")
        self.assertEqual(extra, set(), f"入口引用了不存在的工具：{sorted(extra)}")

    def test_no_tool_is_in_two_groups(self):
        seen = {}
        dup = []
        for g in playbook.ENTRY_GROUPS:
            for t in g["tools"]:
                if t in seen:
                    dup.append((t, seen[t], g["id"]))
                seen[t] = g["id"]
        self.assertEqual(dup, [], f"工具被分到多个入口：{dup}")

    def test_entry_groups_returns_deep_copy(self):
        a = playbook.entry_groups()
        a[0]["tools"].append("__poison__")
        a[0]["extrapolate"][0]["expand"].append("__poison__")
        b = playbook.entry_groups()
        self.assertNotIn("__poison__", b[0]["tools"])
        self.assertNotIn("__poison__", b[0]["extrapolate"][0]["expand"])

    def test_route_matches_payment_case(self):
        hits = playbook.route("用户付款成功但订单停在待支付")
        self.assertTrue(hits, "支付/订单类需求应命中入口")
        self.assertIn("change", [h["entry"] for h in hits])

    def test_route_empty_and_unrelated(self):
        self.assertEqual(playbook.route(""), [])
        self.assertEqual(playbook.route(None), [])
        self.assertEqual(playbook.route("今天天气不错"), [])

    def test_payload_shape(self):
        p = playbook.playbook_payload()
        for k in ("hard_rules", "status_vocab", "quality_dimensions",
                  "defect_classes", "fault_modes", "pipeline", "entries", "routing"):
            self.assertIn(k, p)
        self.assertEqual(len(p["hard_rules"]), 4)
        self.assertEqual(len(p["quality_dimensions"]), 5)
        self.assertEqual(len(p["defect_classes"]), 10)
        self.assertEqual(len(p["fault_modes"]), 5)
        self.assertIn("PASS", p["status_vocab"])


# ───────────────────────── 数据层 ─────────────────────────

def _fake_repo(tmp: Path):
    """造一个最小仓：2 个报告 + 1 条回归锚 + 状态文件。"""
    (tmp / "reports" / "20260911-101010-aaaaaa").mkdir(parents=True)
    (tmp / "reports" / "20260911-101010-aaaaaa" / "gate.json").write_text(
        json.dumps({"status": "PASS", "why": "ok", "git_head": "abcdef1234567890",
                    "contract_version": 1, "measured_at": "2026-09-11T02:10:10+00:00"}),
        encoding="utf-8")
    (tmp / "reports" / "20260911-101010-aaaaaa" / "results.json").write_text(
        json.dumps([{"id": "C1", "status": "PASS"}, {"id": "C2", "status": "PASS"}]),
        encoding="utf-8")

    (tmp / "reports" / "20260911-111111-bbbbbb").mkdir(parents=True)
    (tmp / "reports" / "20260911-111111-bbbbbb" / "results.json").write_text(
        json.dumps([{"id": "C3", "status": "FAIL"}, {"id": "C4", "status": "PASS"}]),
        encoding="utf-8")

    (tmp / "regression").mkdir()
    (tmp / "regression" / "cases.json").write_text(json.dumps([
        {"id": "REG-1", "priority": "P0", "category": "REGRESSION",
         "source": "缺陷:x", "action": {"kind": "http"}},
        {"id": "REG-2", "priority": "P1", "category": "REGRESSION",
         "source": "缺陷:y", "action": {"kind": "sequence"}},
    ]), encoding="utf-8")

    (tmp / ".agent" / "state").mkdir(parents=True)
    (tmp / ".agent" / "state" / "project_state.json").write_text(json.dumps({
        "goal": "g", "current": "c", "blocked": ["b1"], "constraints": ["k1"],
        "completed": [], "updated": "2026-09-04"}), encoding="utf-8")
    (tmp / ".agent" / "state" / "task_state.json").write_text(json.dumps({
        "phase": "verify", "open_risks": ["r1"], "last_verified": ""}), encoding="utf-8")
    (tmp / ".agent" / "state" / "decision_state.json").write_text(
        json.dumps({"decisions": []}), encoding="utf-8")
    return tmp


class TestAggregate(unittest.TestCase):
    def setUp(self):
        aggregate.reset_cache()
        self._td = tempfile.TemporaryDirectory()
        self.root = _fake_repo(Path(self._td.name))

    def tearDown(self):
        self._td.cleanup()
        aggregate.reset_cache()

    def test_scan_reports_parses_gate_and_results(self):
        rows = aggregate.scan_reports(self.root, 10)
        self.assertEqual(len(rows), 2)
        by_id = {r["id"]: r for r in rows}
        a = by_id["20260911-101010-aaaaaa"]
        self.assertEqual(a["gate"], "PASS")
        self.assertEqual(a["cases"], 2)
        self.assertEqual(a["git_head"], "abcdef12")
        b = by_id["20260911-111111-bbbbbb"]
        # 没有 gate.json 时按结果推：有 FAIL → FAIL
        self.assertEqual(b["gate"], "FAIL")
        self.assertEqual(b["failed"], 1)
        self.assertEqual(b["failed_ids"], ["C3"])

    def test_summarize(self):
        s = aggregate.summarize_reports(aggregate.scan_reports(self.root))
        self.assertEqual(s["total_dirs"], 2)
        self.assertEqual(s["effective_runs"], 2)
        self.assertEqual(s["empty_shells"], 0)
        self.assertEqual(s["gate_pass"], 1)
        self.assertEqual(s["gate_fail"], 1)
        self.assertEqual(s["judged"], 2)
        self.assertEqual(s["pass_rate"], 50.0)
        self.assertEqual(s["pass_rate_basis"], "PASS/(PASS+FAIL)")
        self.assertEqual(s["cases_total"], 4)
        self.assertEqual(s["cases_failed"], 1)

    def test_empty_shell_is_counted_but_excluded_from_verdict(self):
        """空壳目录（只建了目录没产物）不能进通过率分母——这是上一版的真实缺陷。"""
        (self.root / "reports" / "20260911-131313-dddddd").mkdir(parents=True)
        rows = aggregate.scan_reports(self.root)
        s = aggregate.summarize_reports(rows)
        self.assertEqual(s["total_dirs"], 3)
        self.assertEqual(s["empty_shells"], 1)
        self.assertEqual(s["effective_runs"], 2)
        self.assertEqual(s["judged"], 2)
        self.assertEqual(s["pass_rate"], 50.0, "空壳不得稀释通过率")
        self.assertEqual(len(aggregate.activity(self.root)["items"]), 2, "时间线不列空壳")

    def test_non_verdict_states_are_separate(self):
        d = self.root / "reports" / "20260911-141414-eeeeee"
        d.mkdir(parents=True)
        (d / "gate.json").write_text(json.dumps({"status": "NOT_TESTED"}), encoding="utf-8")
        s = aggregate.summarize_reports(aggregate.scan_reports(self.root))
        self.assertEqual(s["not_tested"], 1)
        self.assertEqual(s["judged"], 2, "NOT_TESTED 不进判定分母")
        self.assertEqual(s["pass_rate"], 50.0)

    def test_regression_assets(self):
        reg = aggregate.load_regression(self.root)
        self.assertEqual(reg["count"], 2)
        self.assertEqual(reg["by_priority"], {"P0": 1, "P1": 1})

    def test_state_todo(self):
        st = aggregate.load_state(self.root)
        kinds = [t["kind"] for t in st["todo"]]
        self.assertIn("当前目标", kinds)
        self.assertIn("阻塞", kinds)
        self.assertIn("未决风险", kinds)
        self.assertEqual(st["task"]["phase"], "verify")

    def test_missing_dirs_do_not_raise(self):
        empty = Path(self._td.name) / "empty"
        empty.mkdir()
        self.assertEqual(aggregate.scan_reports(empty, 10), [])
        self.assertEqual(aggregate.load_regression(empty)["count"], 0)
        self.assertEqual(aggregate.load_state(empty)["todo"], [])
        self.assertEqual(aggregate.load_assets(empty)["skills"], [])

    def test_corrupt_json_degrades(self):
        (self.root / "regression" / "cases.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(aggregate.load_regression(self.root)["count"], 0)

    def test_bundle_is_cached(self):
        d1 = aggregate.bundle(self.root)
        d2 = aggregate.bundle(self.root)
        self.assertIs(d1, d2, "签名未变时应命中缓存")
        self.assertIn("playbook", d1)

    def test_bundle_cache_invalidates_on_new_report(self):
        d1 = aggregate.bundle(self.root)
        (self.root / "reports" / "20260911-121212-cccccc").mkdir(parents=True)
        d2 = aggregate.bundle(self.root)
        self.assertIsNot(d1, d2, "新增报告目录后应重扫")
        self.assertEqual(d2["reports"]["total_dirs"], 3)

    def test_bundle_cache_key_includes_limit(self):
        """缓存键漏了 limit 会让 limit=1 的结果被 limit=5 复用（已修的缺陷）。"""
        a = aggregate.bundle(self.root, limit=1)
        b = aggregate.bundle(self.root, limit=5)
        self.assertEqual(len(a["activity"]), 1)
        self.assertEqual(len(b["activity"]), 2)
        self.assertIsNot(a, b)

    def test_trend_has_30_buckets(self):
        self.assertEqual(len(aggregate.daily_trend(aggregate.scan_reports(self.root))), 30)


# ───────────────────────── 服务层 ─────────────────────────

class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        aggregate.reset_cache()
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _fake_repo(Path(cls._td.name))
        cls.httpd = server.create_server(cls.root, "127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        cls.t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._td.cleanup()
        aggregate.reset_cache()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
            return r.status, r.read()

    def test_health(self):
        code, body = self._get("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_bundle(self):
        code, body = self._get("/api/bundle?limit=5")
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual(d["reports"]["total_dirs"], 2)
        self.assertEqual(len(d["playbook"]["entries"]), 7)

    def test_activity_and_assets_and_playbook(self):
        for p in ("/api/activity?limit=5", "/api/assets", "/api/playbook"):
            code, _ = self._get(p)
            self.assertEqual(code, 200, p)

    def test_route_endpoint(self):
        code, body = self._get("/api/route?q=" + urllib.parse.quote("并发会不会超发"))
        self.assertEqual(code, 200)
        hits = json.loads(body)["hits"]
        self.assertTrue(hits)
        self.assertIn("execute", [h["entry"] for h in hits])

    def test_index_html_served(self):
        code, body = self._get("/")
        self.assertEqual(code, 200)
        self.assertIn("TestMind 看板", body.decode("utf-8"))

    def test_unknown_path_404(self):
        try:
            self._get("/api/nope")
            self.fail("应返回 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


if __name__ == "__main__":
    unittest.main()
