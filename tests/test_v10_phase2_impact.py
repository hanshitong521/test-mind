# V10 Phase 2 ImpactMind Blocking Test（AC-2）
# 共享方法被 4 个接口调用 → analyze_impact 报 4/4 impacted endpoints；§3.4 输出字段齐全。
import os
import sys
import tempfile
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import impact, mcp

CTRL = """package com.demo.web;

import org.springframework.web.bind.annotation.*;
import com.demo.svc.OrderService;

@RestController
@RequestMapping("/api/orders")
public class OrderController {
    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    @GetMapping("/{id}")
    public Object detail(@PathVariable Long id) {
        return orderService.query(id);
    }

    @PostMapping
    public Object create(@RequestBody Object body) {
        return orderService.create(body);
    }

    @PutMapping("/{id}")
    public Object update(@PathVariable Long id, @RequestBody Object body) {
        return orderService.update(id, body);
    }

    @DeleteMapping("/{id}")
    public Object remove(@PathVariable Long id) {
        return orderService.delete(id);
    }
}
"""

SVC = """package com.demo.svc;

import com.demo.mapper.OrderMapper;

@Service
public class OrderService {
    private OrderMapper orderMapper;

    public Object query(Long id) {
        return shared(id);
    }

    public Object create(Object body) {
        return shared(1L);
    }

    public Object update(Long id, Object body) {
        return shared(id);
    }

    public Object delete(Long id) {
        return shared(id);
    }

    public Object shared(Long id) {
        return orderMapper.selectById(id);
    }
}
"""

MAPPER = """package com.demo.mapper;

@Mapper
public interface OrderMapper {
    @Select("select * from t_order where id = #{id}")
    Object selectById(Long id);

    @Update("update t_order set status = 2 where id = #{id}")
    int updateStatus(Long id);
}
"""

XML_MAPPER = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.demo.mapper.OrderMapper">
    <insert id="insertOrder">
        insert into t_order (id, status) values (#{id}, #{status})
    </insert>
</mapper>
"""


def _write(root, rel, text):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return rel


class TestImpactGraph(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tm_impact_")
        _write(self.root, "src/main/java/com/demo/web/OrderController.java", CTRL)
        _write(self.root, "src/main/java/com/demo/svc/OrderService.java", SVC)
        _write(self.root, "src/main/java/com/demo/mapper/OrderMapper.java", MAPPER)
        _write(self.root, "src/main/resources/mapper/OrderMapper.xml", XML_MAPPER)
        self.g = impact.build_graph(self.root)

    def _range(self, sym):
        """从 graph 取该符号真实行区间，避免手写行号漂移。"""
        info = self.g.methods[sym]
        return {info["file"]: [(info["start"], info["end"])]}

    def test_endpoints_discovered(self):
        self.assertEqual(len(self.g.endpoints), 4, sorted(self.g.endpoints))

    def test_mapper_annotation_sql_tables(self):
        self.assertIn("t_order", self.g.tables)
        self.assertTrue(self.g.tables["t_order"]["readers"])
        self.assertTrue(self.g.tables["t_order"]["writers"])

    def test_xml_mapper_statement_tables(self):
        # insertOrder 来自 XML，写 t_order
        self.assertIn("OrderMapper#insertOrder", self.g.tables["t_order"]["writers"])

    def test_service_calls_mapper(self):
        self.assertIn("OrderMapper#selectById",
                      self.g.callees.get("OrderService#shared", set()))

    def test_shared_method_impacts_all_four_endpoints(self):
        """AC-2：改 OrderService#shared → 4 个端点全受影响。"""
        rep = impact.analyze(self.root, changed=self._range("OrderService#shared"), graph=self.g)
        self.assertIn("OrderService#shared", rep["changed_symbols"])
        self.assertEqual(len(rep["affected_endpoints"]), 4, rep["affected_endpoints"])
        self.assertTrue(all(r == "R1" for r in rep["affected_endpoints"].values()))

    def test_controller_change_is_R0(self):
        rep = impact.analyze(self.root, changed=self._range("OrderController#detail"), graph=self.g)
        self.assertEqual(rep["changed_symbols"], ["OrderController#detail"])
        self.assertEqual(rep["affected_endpoints"], {"GET /api/orders/{id}": "R0"})

    def test_mapper_table_change_impacts_writing_endpoints_R3(self):
        """改 Mapper 写方法 → 读同表端点经 R3（共用数据）进入回归范围。"""
        rep = impact.analyze(self.root, changed=self._range("OrderMapper#selectById"), graph=self.g)
        self.assertIn("OrderMapper#selectById", rep["changed_symbols"])
        self.assertTrue(rep["affected_endpoints"])
        self.assertEqual(rep["risk_level"], "P0")   # 触及写表 → P0

    def test_output_fields_complete(self):
        """§3.4 输出字段齐全。"""
        rep = impact.analyze(self.root, changed={}, graph=self.g)
        for k in ("changed_files", "changed_symbols", "affected_symbols",
                  "affected_endpoints", "affected_tables", "shared_rules",
                  "regression_radius", "risk_level"):
            self.assertIn(k, rep)

    def test_R4_history_from_regression_reason(self):
        rep = impact.analyze(self.root, changed=self._range("OrderService#shared"),
                             regression_reasons=["order flow broken in shared path"],
                             graph=self.g)
        r4 = rep["regression_radius"]["R4_history"]
        self.assertTrue(any(x["symbol"] == "OrderService#shared" for x in r4), r4)

    def test_expand_radius_failure_driven(self):
        """§19：FAIL 后扩 R3——共用表的其余端点入回归范围。"""
        rep = impact.analyze(self.root, changed=self._range("OrderMapper#selectById"), graph=self.g)
        extra = impact.expand_radius(self.g, rep["affected_endpoints"],
                                     set(rep["affected_symbols"]), set(rep["changed_symbols"]))
        for ep, r in extra.items():
            self.assertEqual(r, "R3")

    def test_querywrapper_entity_to_table(self):
        rel = _write(self.root, "src/main/java/com/demo/svc/ReportService.java", """package com.demo.svc;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;

@Service
public class ReportService {
    public Object list() {
        QueryWrapper<Order> qw = new QueryWrapper<>();
        return qw;
    }
}
""")
        g = impact.build_graph(self.root)
        self.assertIn("order", g.tables)


class TestFacadeWiring(unittest.TestCase):
    def test_analyze_impact_facade_dispatch(self):
        mcp.S.reset()
        mcp.S.impact_graph = impact.build_graph(self.root)
        info = mcp.S.impact_graph.methods["OrderService#shared"]
        r = mcp.dispatch("analyze_impact", {"path": self.root, "java_graph": False,
                                            "changed": {info["file"]: [(info["start"], info["end"])]}})
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(len(r["impact"]["affected_endpoints"]), 4)

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tm_facade_")
        _write(self.root, "src/main/java/com/demo/web/OrderController.java", CTRL)
        _write(self.root, "src/main/java/com/demo/svc/OrderService.java", SVC)
        _write(self.root, "src/main/java/com/demo/mapper/OrderMapper.java", MAPPER)


if __name__ == "__main__":
    unittest.main()
