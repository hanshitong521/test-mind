# V10 Phase 3 Blocking Test：RuleMind / Actor / Ownership / Temporal / State / ScenarioMind
import datetime
import os
import sys
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import mcp
from testmind import rules as R
from testmind import scenario as SC

VIS_RULE = {
    "rule_id": "VIS-001",
    "name": "数据可见性规则",
    "resource": "order",
    "predicate": {"any": [
        "actor.id == resource.created_by",
        "actor.role == ADMIN",
        {"all": ["actor.company_id == resource.company_id",
                 "now >= resource.publish_at"]},
    ]},
    "applies_to": ["READ"],
    "surfaces": ["order.detail", "order.page", "order.count"],
    "type": "visibility",
    "source": "RequirementMind:VIS-001",
}

ORDER_ENUM = """
public enum OrderStatus {
    CREATED, PAID, SHIPPED, FINISHED, CANCELLED
}
class Svc {
    void pay() { order.setStatus(OrderStatus.PAID.getCode()); }
}
"""


class TestRuleStructure(unittest.TestCase):
    def test_load_and_roundtrip(self):
        (rule,) = R.load_rules([VIS_RULE])
        self.assertEqual(rule.rule_id, "VIS-001")
        self.assertEqual(rule.rule_id, R.Rule.from_dict(rule.to_dict()).rule_id)

    def test_visibility_matrix(self):
        """§9.3 示例矩阵：Owner 恒可见；同公司 T-1 不可见/T 可见；外公司全不可见。"""
        (rule,) = R.load_rules([VIS_RULE])
        pub = datetime.datetime(2026, 1, 1, 0, 0, 0)
        res = {"created_by": "u1", "company_id": "c1", "publish_at": pub}
        owner = {"id": "u1", "role": "USER", "company_id": "c1"}
        peer = {"id": "u2", "role": "USER", "company_id": "c1"}
        other = {"id": "u3", "role": "USER", "company_id": "c2"}
        admin = {"id": "u9", "role": "ADMIN", "company_id": "c9"}
        self.assertTrue(R.visible(rule, owner, res, now=pub - datetime.timedelta(days=1)))
        self.assertTrue(R.visible(rule, admin, res, now=pub))
        self.assertFalse(R.visible(rule, peer, res, now=pub - datetime.timedelta(seconds=1)))
        self.assertTrue(R.visible(rule, peer, res, now=pub))
        self.assertFalse(R.visible(rule, other, res, now=pub + datetime.timedelta(days=1)))
        self.assertIsNone(R.visible(rule, owner, res, operation="WRITE"))  # 不适用


class TestActorEvidence(unittest.TestCase):
    def test_roles_from_code(self):
        java = 'x.hasRole("FINANCE"); @PreAuthorize("hasRole(\'BRAND_ADMIN\')") y.equals("OPERATOR");'
        roles = {r for r, _ in R.extract_roles(java)}
        self.assertEqual(roles, {"FINANCE", "BRAND_ADMIN", "OPERATOR"})

    def test_no_evidence_no_roles(self):
        """§7：查不到角色 = 空（UNKNOWN），禁止凭空生成。"""
        self.assertEqual(R.extract_roles("class A { void b() {} }"), [])


class TestOwnership(unittest.TestCase):
    def test_relations(self):
        res = {"created_by": "u1", "company_id": "c1"}
        self.assertEqual(R.ownership_of({"id": "u1"}, res), "SELF")
        self.assertEqual(R.ownership_of({"id": "u2", "company_id": "c1"}, res), "SAME_COMPANY")
        self.assertEqual(R.ownership_of({"id": "u3", "company_id": "c2"}, res), "OTHER_COMPANY")
        self.assertEqual(R.ownership_of({"id": "u4"}, res), "UNASSIGNED")
        self.assertEqual(R.ownership_of({"id": "u1"}, {"created_by": None}), "SYSTEM_OWNED")


class TestTemporal(unittest.TestCase):
    def test_detect_java(self):
        # 约定：op 表达 field <op> now
        hits = R.detect_temporal_predicates(
            "if (now.isAfter(expireAt)) {} if (startTime <= now) {} if (publishAt <= LocalDateTime.now()) {}")
        fields = {(h["field"], h["op"]) for h in hits}
        self.assertIn(("expireAt", "<"), fields)    # now.isAfter(expireAt) ⇔ expireAt < now
        self.assertIn(("startTime", "<="), fields)
        self.assertIn(("publishAt", "<="), fields)

    def test_detect_sql(self):
        hits = R.detect_temporal_predicates("select * from t where publish_time <= NOW() and expire_time > NOW()")
        ops = {(h["field"], h["op"]) for h in hits}
        self.assertEqual(ops, {("publish_time", "<="), ("expire_time", ">")})

    def test_epsilon_points(self):
        pts = R.time_points("2026-01-01T00:00:00")
        self.assertEqual(pts["T"], datetime.datetime(2026, 1, 1))
        self.assertEqual(pts["T-ε"], pts["T"] - datetime.timedelta(seconds=1))
        self.assertEqual(pts["precision"], "seconds")
        d = R.time_points("2026-01-01")
        self.assertEqual(d["T+ε"] - d["T"], datetime.timedelta(days=1))
        ms = R.time_points(1767225600123)   # epoch 毫秒
        self.assertEqual(ms["precision"], "milliseconds")


class TestStateMind(unittest.TestCase):
    def test_parse_enum(self):
        sm = R.parse_state_machine(ORDER_ENUM)
        self.assertEqual(sm["states"], ["CREATED", "PAID", "SHIPPED", "FINISHED", "CANCELLED"])
        self.assertIn(("CREATED", "PAID"), sm["transitions"])
        self.assertEqual(sm["terminal"], ["CANCELLED"])

    def test_five_edge_kinds(self):
        sm = R.parse_state_machine(ORDER_ENUM)
        kinds = {k for k, _, _ in R.state_case_kinds(sm)}
        self.assertTrue({"legal", "illegal", "repeat", "skip", "terminal_reop"} <= kinds)

    def test_no_enum_no_state_machine(self):
        self.assertEqual(R.parse_state_machine("class A {}")["states"], [])


class TestScenarioMind(unittest.TestCase):
    def setUp(self):
        (self.rule,) = R.load_rules([VIS_RULE])

    def test_pairwise_coverage(self):
        axes = {"actor": ["OWNER", "ADMIN", "ANON"], "ownership": ["SELF", "OTHER_COMPANY"],
                "time": ["T-ε", "T", "T+ε"], "entry_point": ["detail", "list"]}
        rows = SC.pairwise_axes(axes)
        self.assertLess(len(rows), 3 * 2 * 3 * 2)          # 禁止全笛卡尔积
        for a, b in [(x, y) for x in axes for y in axes if x < y]:
            combos = {(r[a], r[b]) for r in rows}
            self.assertEqual(combos, {(va, vb) for va in axes[a] for vb in axes[b]},
                             f"pairwise miss {a}×{b}")

    def test_derive_provenance_complete(self):
        """§22：每个 case 有 reason/expected_source；缺来源的拒绝。"""
        cases = SC.derive(rules=[self.rule], roles=["OWNER", "ADMIN"],
                          times=["T-ε", "T", "T+ε"], states=["CREATED", "PAID", "FINISHED"],
                          entry_points=["order.detail"], operations=["READ"])
        self.assertTrue(cases)
        for c in cases:
            ok, missing = SC.validate_case_provenance(c)
            self.assertTrue(ok, (c["id"], missing))
        bad = {"id": "X-1", "reason": "", "expected_source": ""}
        self.assertFalse(SC.validate_case_provenance(bad)[0])

    def test_rule_surface_full_coverage(self):
        """P0 全覆盖：规则 × 每个 surface 必出 case。"""
        cases = SC.derive(rules=[self.rule])
        ids = {c["id"] for c in cases}
        for surf in self.rule.surfaces:
            self.assertTrue(any(surf.replace(".", "_") in i for i in ids), surf)

    def test_unknown_axes_skipped_not_fabricated(self):
        cases = SC.derive(rules=[self.rule])   # 无 roles/states/times 证据
        self.assertFalse([c for c in cases if c["category"] == "TEMPORAL"])
        self.assertFalse([c for c in cases if c["category"] == "STATE"])


class TestFacadeWiring(unittest.TestCase):
    def test_plan_verification_with_rules(self):
        mcp.S.reset()
        r = mcp.dispatch("plan_verification", {
            "rules": [VIS_RULE],
            "scenario": {"roles": ["OWNER", "ADMIN"], "times": ["T-ε", "T", "T+ε"],
                         "entry_points": ["order.detail"]}})
        self.assertEqual(r["status"], "PASS")
        self.assertGreater(r["scenarios"], 0)
        self.assertTrue(mcp.S.rules and mcp.S.rules[0].rule_id == "VIS-001")
        # 场景 case 未物化前不进 plan（plan 仍 0），杜绝无 action 崩溃
        self.assertEqual(len(mcp.S.plan), 0)


if __name__ == "__main__":
    unittest.main()
