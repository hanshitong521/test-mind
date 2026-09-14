# examples/logic-bug-lab/cases.py — 每个 §24 故障对应一个可执行 case。
# 判定：fixed 版必须 PASS，buggy 版必须 FAIL（run_lab 三重验证）。
# 可见性 actor：OWNER u1/c1、PEER u3/c1（同公司）、OTHER u9/c9、ADMIN。
# 时间基准 NOW=2026-01-10；id2 publish 2026-01-11（未来）；id11 publish==NOW（边界）。

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from testmind import datatruth  # noqa: F401  注册 §15 扩展 check（total_eq_rows/ids_present_n/…）

OWNER = {"X-User": "u1", "X-Company": "c1"}
PEER = {"X-User": "u3", "X-Company": "c1"}
OTHER = {"X-User": "u9", "X-Company": "c9"}
ADMIN = {"X-User": "u0", "X-Company": "c0", "X-Role": "ADMIN"}


def _c(cid, steps, expected=None):
    return {"id": cid, "category": "LOGIC_BUG", "priority": "P0",
            "reason": f"{cid}（§24 Logic Bug Lab）",
            "expected_source": "LogicBugLab:§24",
            "action": {"kind": "sequence", "steps": steps},
            "expected": expected or {}}


def _get(path, headers, checks=None, save=None, expect=200):
    s = {"method": "GET", "path": path, "headers": headers, "expect_status": expect}
    if checks:
        s["checks"] = checks
    if save:
        s["save_body"] = save
    return s


def _req(method, path, headers, body=None, expect=200, save=None, checks=None):
    s = {"method": method, "path": path, "headers": headers, "expect_status": expect}
    if body is not None:
        s["body"] = body
    if save:
        s["save"] = save
    if checks:
        s["checks"] = checks
    return s


# 每个 bug → 返回 case 的工厂
def build():
    return {
        # 1 detail 正确、list 漏时间条件
        "B01": lambda: _c("B01", [
            _get("/orders/2", PEER, expect=404),
            _get("/orders", PEER, [{"absent": {"field": "id", "value": 2}}])]),
        # 2 list 正确、export 漏条件
        "B02": lambda: _c("B02", [
            _get("/orders/export", OTHER, [{"absent": {"field": "id", "value": 1}}])]),
        # 3 count 包含不可见数据（OTHER 不可见任何行 → count 必须为 0）
        "B03": lambda: _c("B03", [
            _get("/orders/count", OTHER, [{"total_eq_rows": {}}])]),
        # 4 Owner 在发布时间前看不到自己数据
        "B04": lambda: _c("B04", [
            _get("/orders/2", OWNER, expect=200)]),
        # 5 SameCompany 在 T-1 提前可见
        "B05": lambda: _c("B05", [
            _get("/orders/2", PEER, expect=404)]),
        # 6 OtherCompany 在 T+1 错误可见
        "B06": lambda: _c("B06", [
            _get("/orders/1", OTHER, expect=404)]),
        # 7 T 使用 > 而不是 >=
        "B07": lambda: _c("B07", [
            _get("/orders/11", PEER, expect=200)]),
        # 8 删除后 search 仍出现
        "B08": lambda: _c("B08", [
            _req("DELETE", "/orders/1", OWNER, expect=200),
            _get("/orders/search?q=alpha", OWNER, [{"absent": {"field": "id", "value": 1}}])]),
        # 9 删除后 count 未减少
        "B09": lambda: _c("B09", [
            _get("/orders/count", OWNER, save="c0"),
            _req("DELETE", "/orders/1", OWNER, expect=200),
            _get("/orders/count", OWNER, [{"count_delta": {"var": "c0", "delta": -1}}])]),
        # 10 update 后 list 仍旧值
        "B10": lambda: _c("B10", [
            _req("PUT", "/orders/1", OWNER, body={"name": "alpha-renamed"}, expect=200),
            _get("/orders", OWNER, [{"present": {"field": "name", "value": "alpha-renamed"}}])]),
        # 11 page total 与 list 不一致（PEER 可见 7 行，total 不得虚报全库数）
        "B11": lambda: _c("B11", [
            _get("/orders/page?page=1&size=50", PEER, [{"total_eq_rows": {}}])]),
        # 12 pagination 重复数据
        "B12": lambda: _c("B12", [
            _get("/orders/page?page=1&size=3", OWNER, save="p1"),
            _get("/orders/page?page=2&size=3", OWNER,
                 [{"unique_across": {"vars": ["p1"], "key": "id"}}])]),
        # 13 pagination 漏数据：各页并集必须等于全量 list
        "B13": lambda: _c("B13", [
            _get("/orders/page?page=1&size=4", OWNER, save="p1"),
            _get("/orders/page?page=2&size=4", OWNER, save="p2"),
            _get("/orders/page?page=3&size=4", OWNER, save="p3"),
            _get("/orders?size=100", OWNER,
                 [{"union_eq": {"vars": ["p1", "p2", "p3"], "key": "id"}}])]),
        # 14 filter A+B 比 filter A 返回更多
        "B14": lambda: _c("B14", [
            _get("/orders?status=PUBLISHED", OWNER, save="fa"),
            _get("/orders?status=PUBLISHED&company_id=c1", OWNER,
                 [{"ids_subset_of": {"var": "fa", "key": "id"}}])]),
        # 15 sort tie 导致分页重复：并列组（amount=100）跨页边界，抖动顺序会重复/漏行
        "B15": lambda: _c("B15", [
            _get("/orders/page?page=1&size=5&sort=amount&dir=asc", OWNER, save="p1"),
            _get("/orders/page?page=2&size=5&sort=amount&dir=asc", OWNER, save="p2"),
            _get("/orders/page?page=3&size=5&sort=amount&dir=asc", OWNER,
                 [{"unique_across": {"vars": ["p1", "p2"], "key": "id"}}])]),
        # 16 状态终态仍允许更新
        "B16": lambda: _c("B16", [
            _req("PUT", "/orders/5", OWNER, body={"status": "PUBLISHED"}, expect=409)]),
        # 17 更新失败但 DB 部分落库
        "B17": lambda: _c("B17", [
            _req("PUT", "/orders/1", OWNER, body={"name": "should-not-persist", "status": "BAD"},
                 expect=400),
            _get("/orders/1", OWNER, [{"field_eq": {"path": "name", "value": "alpha"}}])]),
        # 18 幂等重复创建两条：同 Idempotency-Key 两次创建必须返回同一主键
        "B18": lambda: _c("B18", [
            {"method": "POST", "path": "/orders", "headers": {**OWNER, "Idempotency-Key": "k1"},
             "body": {"name": "idem"}, "expect_status": 201, "save": {"id1": "id"}},
            {"method": "POST", "path": "/orders", "headers": {**OWNER, "Idempotency-Key": "k1"},
             "body": {"name": "idem"}, "expect_status": 201, "save": {"id2": "id"},
             "checks": [{"saved_eq": {"a": "id1", "b": "id2"}}]}]),
        # 19 Scheduler T 边界重复执行
        "B19": lambda: _c("B19", [
            _req("POST", "/jobs/settle", OWNER, expect=200),
            _get("/orders/settle-log", OWNER, [{"unique_field": {"field": "row_id"}}])]),
        # 20 多入口共享规则只修了一处（search 漏时间条件）
        "B20": lambda: _c("B20", [
            _get("/orders/2", PEER, expect=404),
            _get("/orders/search?q=beta", PEER, [{"absent": {"field": "id", "value": 2}}])]),
    }
