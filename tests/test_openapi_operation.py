import json
import os
import sys
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)
sys.path.insert(0, os.path.join(TM, "examples", "red-packet"))

from testmind.core import FactResolver, facts_for_operation, plan_cases


class TestOpenAPIOperationScope(unittest.TestCase):
    def test_grant_does_not_pull_create_body_facts(self):
        from sut import OPENAPI
        facts = FactResolver.from_openapi(OPENAPI, "red-packet/openapi")
        scoped = facts_for_operation(facts, operation_id="grantRedPacket")
        topics = {x["topic"] for x in scoped.f}
        self.assertFalse(any(t.startswith("required:createRedPacket") or "CreateReq" in t for t in topics))

    def test_create_has_amount_facts(self):
        from sut import OPENAPI
        facts = FactResolver.from_openapi(OPENAPI, "red-packet/openapi")
        scoped = facts_for_operation(facts, operation_id="createRedPacket")
        plan = plan_cases(scoped, {"name": "n", "amount_cents": 1, "quantity": 1,
                                   "influencer_id": 1, "created_by": 1},
                          path="/red-packets", method="POST")
        self.assertTrue(any("amount_cents" in str(c) for c in plan))

    def test_generic_openapi_components_excluded(self):
        spec = json.load(open(os.path.join(TM, "examples", "generic", "openapi.json"), encoding="utf-8"))
        facts = FactResolver.from_openapi(spec, "generic/openapi.json")
        scoped = facts_for_operation(facts, operation_id="createStock")
        self.assertFalse(any("CreateRedPacketReq" in x["topic"] for x in scoped.f))
        self.assertTrue(any("warehouse_id" in x["statement"] or "sku" in x["topic"] for x in scoped.f))


if __name__ == "__main__":
    unittest.main()
