# testmind/triage.py — 失败归因（V10 §29 增强）：把"FAIL"拆成 TEST_DEFECT 与 PRODUCT_BUG。
# 背景：真实整改案例里 68/68 全 PASS 与 4 个 BUG 同时成立，根因是判定把
# 预期拒绝 / 非预期 500 / 测试写错参数 混成一锅。本模块提供三分法机械判据：
#   EXPECTED_REJECT = 实际码命中 expected 且 msg/字段在白名单（expected.json 子集）内
#   TEST_DEFECT     = 非预期 5xx 且请求缺了事实源（OpenAPI/DDL required）声明的参数
#                     —— 先修测试（补参重跑），不许直接报 BUG
#   PRODUCT_BUG     = 参数齐全仍 5xx，或 200 但数据/DB 断言不符
# 自动修参纪律：值只允许来自事实（同 plan 内 canonical 同名字段 / fixture / 用户答案），
# 找不到出处 → 不编造，转 ask_user（No Requirement → No Expected）。
import re

_WRITE_VERBS = ("POST", "PUT", "PATCH", "DELETE")


def _endpoint_facts(facts, method, path):
    """从 Facts 里取该端点声明的 required 参数名（OpenAPI 抽取带 http_path/http_method 元数据）。"""
    if not facts or not getattr(facts, "f", None):
        return set()
    want = set()
    m = (method or "POST").upper()
    for x in facts.f:
        if x.get("http_path") and x.get("http_method") and \
                x["http_path"].rstrip("/") == (path or "").rstrip("/") and x["http_method"] == m:
            topic = x.get("topic", "")
            if topic.startswith(("required:", "paramrequired:")):
                want.add(topic.rsplit(".", 1)[-1])
    return want


def _body_keys(case):
    a = (case or {}).get("action") or {}
    body = a.get("body") or {}
    keys = set(body) if isinstance(body, dict) else set()
    for step in a.get("steps") or []:
        sb = step.get("body") or {}
        if isinstance(sb, dict):
            keys |= set(sb)
    return keys


def classify(result, case=None, facts=None):
    """输入 engine.run() 的结果 + 原 case + 事实集，返回归因 dict。
    status=PASS → 不归因；其余按三分法。"""
    st = result.get("status")
    if st == "PASS":
        return {"attribution": None}
    if st in ("TOOL_ERROR",):
        return {"attribution": "TOOL_ERROR",
                "note": "测试管线自身故障（快照读不出/引擎异常），不算产品缺陷也不许冒充通过"}
    if st == "NOT_TESTED":
        return {"attribution": "NOT_TESTED", "note": "无可执行断言或缺库无法验证"}
    detail = result.get("detail") or {}
    actual = detail.get("http")
    exp = (case or {}).get("expected") or {}
    expected_code = exp.get("http")
    a = (case or {}).get("action") or {}
    method, path = a.get("method", "POST"), a.get("path", "")

    # 5xx 且 expected 本来就是 5xx 拒绝：msg 不符 → 白名单外拒绝（仍按 FAIL 出 BUG 候选）
    if isinstance(actual, int) and actual >= 500:
        req = _endpoint_facts(facts, method, path)
        missing = sorted(req - _body_keys(case)) if req else []
        if missing:
            return {"attribution": "TEST_DEFECT", "missing_params": missing,
                    "fix_hint": f"请求缺事实源声明的必填参数 {missing}：先查源码/OpenAPI 取值补全重跑，"
                                f"补全后仍 5xx 才许报 PRODUCT_BUG",
                    "auto_fixable": True}
        return {"attribution": "PRODUCT_BUG",
                "note": "必填参数齐全仍 5xx（或预期拒绝漏成 5xx），进 BUG 清单：附请求体+源码行"}
    # 4xx 但 expected 是 2xx：先查是不是测试缺参（400 缺参是产品正确行为，测试写错）
    if isinstance(actual, int) and 400 <= actual < 500 and isinstance(expected_code, int) and expected_code < 400:
        req = _endpoint_facts(facts, method, path)
        missing = sorted(req - _body_keys(case)) if req else []
        if missing:
            return {"attribution": "TEST_DEFECT", "missing_params": missing,
                    "fix_hint": f"4xx 且缺必填 {missing}：大概率测试参数写错，补全重跑", "auto_fixable": True}
        return {"attribution": "PRODUCT_BUG", "note": "参数齐全仍 4xx 而预期 2xx"}
    # 2xx 但 json/db 断言不符：数据不对
    if detail.get("json_mismatch") or detail.get("db_row_missing") or detail.get("db_count") \
            or detail.get("db_write_on_negative"):
        return {"attribution": "PRODUCT_BUG", "note": "响应/DB 终态与预期不符（数据不对）"}
    return {"attribution": "PRODUCT_BUG", "note": "断言不符，逐条附证据归因"}


def canonical_values(cases, param):
    """从同 plan 的其它 case（含 base_input）里找该参数的既有值——自动修参唯一合法来源。"""
    for c in cases or []:
        pool = [c.get("action", {}).get("body") or {}]
        pool += [s.get("body") or {} for s in c.get("action", {}).get("steps") or []]
        pool.append(c.get("base_input") or {})
        for b in pool:
            if isinstance(b, dict) and param in b and b[param] is not None:
                return b[param]
    return None


def autofix(case, attribution_info, cases, facts=None):
    """TEST_DEFECT 自动修参：只补 canonical 有出处的值；无出处返回 None（转 ask_user）。
    返回 (new_case, fixed:[{param,value,source}]) 或 (None, [])。"""
    missing = attribution_info.get("missing_params") or []
    if not missing:
        return None, []
    body_key = "body"
    a = case.get("action") or {}
    if a.get("kind") == "sequence":
        return None, []          # 多步 case 参数归属不明，不自动改
    fixed, new_body = [], dict(a.get("body") or {})
    for p in missing:
        v = canonical_values(cases, p)
        if v is None:
            return None, []      # 任一缺参找不到出处 → 整体不自动修，问用户
        new_body[p] = v
        fixed.append({"param": p, "value": v, "source": "canonical_from_same_plan"})
    nc = {**case, "action": {**a, "body": new_body},
          "autofix_note": f"TEST_DEFECT 自动补参 {[f['param'] for f in fixed]}，出处见 source 字段"}
    return nc, fixed
