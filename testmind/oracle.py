# testmind/oracle.py — Cross-Endpoint Oracle（§11）+ Metamorphic Testing（§12）
# 同一资源的 detail/list/page/search/count/statistics/export/batch/download 不能各测各的：
#   §11.1 detail 不可见 → 其余入口不得出现；§11.2 update 后各入口读回新值；
#   §11.3 delete 后各入口消失且 count 减少。
# Metamorphic：filter(A∧B)⊆filter(A)、page 并集==visible dataset、页间不重复、
#   total>=页 size、asc/desc 逆序、detail 不可见不得从 export/batch 绕过。
# 生成的 case 全部带 §22 溯源字段，action.kind=sequence（Engine 已扩展 checks/save_body）。
import re

SURFACE_KINDS = ("detail", "list", "page", "search", "count", "statistics",
                 "export", "batch", "download")
_ROW_KEYS = ("items", "data", "rows", "list", "results", "records", "content")


# ───────────────────────── 响应行提取 ─────────────────────────

def rows_of(body):
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if isinstance(body, dict):
        for k in _ROW_KEYS:
            if isinstance(body.get(k), list):
                return [r for r in body[k] if isinstance(r, dict)]
        for v in body.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def _ids(rows, key):
    return [r.get(key) for r in rows if key in r]


# ───────────────────────── check DSL（sequence step.checks）─────────────────────────

def run_checks(checks, body, ctx):
    """checks: [{check_name: args}]。返回 (all_ok, {name: {ok, ...}})。"""
    out, ok_all = {}, True
    for c in checks or []:
        (name, args), = c.items()
        fn = CHECKS.get(name)
        if fn is None:
            out[name] = {"ok": False, "error": "unknown check"}
            ok_all = False
            continue
        ok, info = fn(args or {}, body, ctx)
        out[name] = {"ok": ok, **info}
        ok_all = ok_all and ok
    return ok_all, out


CHECKS = {}


def check(fn):
    CHECKS[fn.__name__] = fn
    return fn


@check
def present(args, body, ctx):
    rows = rows_of(body)
    f, v = args["field"], str(args.get("value"))
    ok = any(str(r.get(f)) == v for r in rows)
    return ok, {"field": f, "value": v, "rows": len(rows)}


@check
def absent(args, body, ctx):
    rows = rows_of(body)
    f, v = args["field"], str(args.get("value"))
    ok = not any(str(r.get(f)) == v for r in rows)
    return ok, {"field": f, "value": v, "rows": len(rows)}


@check
def ids_subset_of(args, body, ctx):
    cur = set(map(str, _ids(rows_of(body), args["key"])))
    saved = set(map(str, _ids(rows_of(ctx.get(args["var"])), args["key"])))
    return cur <= saved, {"cur": len(cur), "saved": len(saved),
                          "extra": sorted(cur - saved)[:10]}


@check
def ids_disjoint(args, body, ctx):
    cur = set(map(str, _ids(rows_of(body), args["key"])))
    saved = set(map(str, _ids(rows_of(ctx.get(args["var"])), args["key"])))
    inter = cur & saved
    return not inter, {"overlap": sorted(inter)[:10]}


@check
def union_eq(args, body, ctx):
    cur = set(map(str, _ids(rows_of(body), args["key"])))
    union = set()
    for var in args["vars"]:
        union |= set(map(str, _ids(rows_of(ctx.get(var)), args["key"])))
    return cur == union, {"cur": len(cur), "union": len(union),
                          "missing": sorted(union - cur)[:10],
                          "extra": sorted(cur - union)[:10]}


@check
def unique_across(args, body, ctx):
    seen, dups = set(), []
    for var in args["vars"]:
        for i in map(str, _ids(rows_of(ctx.get(var)), args["key"])):
            if i in seen:
                dups.append(i)
            seen.add(i)
    for i in map(str, _ids(rows_of(body), args["key"])):
        if i in seen:
            dups.append(i)
        seen.add(i)
    return not dups, {"dups": dups[:10]}


@check
def reverse_of(args, body, ctx):
    cur = list(map(str, _ids(rows_of(body), args["key"])))
    saved = list(map(str, _ids(rows_of(ctx.get(args["var"])), args["key"])))
    return cur == saved[::-1], {"cur_n": len(cur), "saved_n": len(saved)}


@check
def order_sorted(args, body, ctx):
    vals = [r.get(args["by"]) for r in rows_of(body) if args["by"] in r]
    try:
        asc = all(a <= b for a, b in zip(vals, vals[1:]))
        desc = all(a >= b for a, b in zip(vals, vals[1:]))
    except TypeError:
        return False, {"error": "unorderable"}
    want = args.get("dir", "asc")
    return (asc if want == "asc" else desc), {"dir": want, "n": len(vals)}


@check
def total_gte_size(args, body, ctx):
    field = args.get("field", "total")
    total = body.get(field) if isinstance(body, dict) else None
    if isinstance(body, list):
        n = len(body)
    else:
        n = next((len(body[k]) for k in _ROW_KEYS
                  if isinstance(body, dict) and isinstance(body.get(k), list)),
                 len(rows_of(body)))
    ok = total is not None and int(total) >= n
    return ok, {"total": total, "size": n}


@check
def total_eq_len(args, body, ctx):
    """count 端点 total == 另一入口可见行数（§11.1：count 不得包含不可见数据）。"""
    field = args.get("field", "total")
    total = body.get(field) if isinstance(body, dict) else None
    n = len(rows_of(ctx.get(args["var"])))
    return total is not None and int(total) == n, {"total": total, "rows": n}


@check
def count_delta(args, body, ctx):
    """§11.3：delete 后 count 减少。current - saved == delta。"""
    field = args.get("field", "total")
    cur = body.get(field) if isinstance(body, dict) else None
    prev = ctx.get(args["var"])
    prev = prev.get(field) if isinstance(prev, dict) else None
    if cur is None or prev is None:
        return False, {"cur": cur, "prev": prev}
    return int(cur) - int(prev) == args.get("delta", -1), {"cur": cur, "prev": prev}


@check
def saved_eq(args, body, ctx):
    """两个前序保存的标量相等（如幂等两次创建返回同一主键）。"""
    a, b = ctx.get(args["a"]), ctx.get(args["b"])
    return a is not None and str(a) == str(b), {"a": a, "b": b}


@check
def unique_field(args, body, ctx):
    """响应行某字段无重复（如调度日志 row_id 不重复执行）。"""
    vals = [r.get(args["field"]) for r in rows_of(body) if args["field"] in r]
    dups = sorted({str(v) for v in vals if vals.count(v) > 1})
    return not dups, {"n": len(vals), "dups": dups[:10]}


@check
def field_eq(args, body, ctx):
    """detail.name == X（§11.2 更新后读回）。value 或 var+field 引用前序保存的标量。"""
    path = args["path"].split(".")
    cur = body
    for p in path:
        cur = cur.get(p) if isinstance(cur, dict) else None
    want = args.get("value")
    if "var" in args:
        ref = ctx.get(args["var"])
        want = ref.get(args["field"]) if isinstance(ref, dict) else ref
    return cur == want, {"got": cur, "want": want}


def match_json(expect, body):
    """dict 浅层子集匹配（与 core._subset 同语义，独立实现避免环依赖）。"""
    return isinstance(body, dict) and all(body.get(k) == v for k, v in expect.items())


# ───────────────────────── 同资源入口识别（§11）─────────────────────────

_KIND_HINT = re.compile(r"\b(detail|list|page|search|count|statistics|export|batch|download)\b", re.I)


def group_surfaces(endpoints):
    """endpoints: ["GET /api/orders/{id}", ...] → {resource: {kind: "METHOD /path"}}。
    kind ∈ SURFACE_KINDS + update/delete（写入口）。"""
    groups = {}
    for ep in endpoints:
        method, _, path = ep.partition(" ")
        method = method.upper()
        segs = [s for s in path.split("/") if s]
        if not segs:
            continue
        last = segs[-1]
        has_id = last.startswith("{") and last.endswith("}")
        kind = None
        hm = _KIND_HINT.search(last)
        if hm:
            kind = hm.group(1).lower()
        elif has_id:
            kind = {"GET": "detail", "PUT": "update", "PATCH": "update",
                    "POST": "create", "DELETE": "delete"}.get(method)
        elif method == "GET":
            kind = "list"
        if kind is None:
            continue
        res = segs[0] if not has_id and not hm else (segs[-2] if len(segs) > 1 else segs[0])
        if hm:
            res = segs[-2] if len(segs) > 1 else segs[0]
        groups.setdefault(res.lower(), {})[kind] = ep
        if has_id and method in ("PUT", "PATCH", "DELETE"):
            groups[res.lower()].setdefault("update" if method in ("PUT", "PATCH") else "delete", ep)
    return groups


# ───────────────────────── case 生成 ─────────────────────────
# spec: {key: "id", sample_id: 42, name_field: "name", new_value: "X-1",
#        invisible_status: 404, page_size: 2, filter_a: {...}, filter_b: {...},
#        sort_by: "amount"}
# actors: {"OTHER_COMPANY": {"headers": {...}}, ...} —— 必须来自证据（§7），无 actor 不出可见性 case。

def _case(cid, reason, risk, expected_source, steps, rule_id=None, impact_path=None,
          category="CROSS_ENDPOINT"):
    return {"id": cid, "reason": reason,
            "impact_path": impact_path or [rule_id or expected_source, cid],
            "rule_id": rule_id, "risk": risk, "expected_source": expected_source,
            "category": category, "priority": "P0",
            "action": {"kind": "sequence", "steps": steps},
            "expected": {}}


def consistency_cases(resource, surfaces, spec, actors=None, rules=None):
    """§11.1/11.2/11.3 三类一致性 case。surfaces = group_surfaces()[resource]。"""
    out = []
    key = spec.get("key", "id")
    rid = spec.get("sample_id")
    inv = spec.get("invisible_status", 404)
    detail = surfaces.get("detail")
    if not detail or rid is None:
        return out
    dmethod, dpath = detail.split(" ", 1)

    # §11.1 不可见传播：detail 不可见 → list/search/count/export 均不得出现
    for actor, cfg in sorted((actors or {}).items()):
        if actor in ("OWNER", "SELF"):
            continue                       # owner 应可见，另测
        steps = [{"method": dmethod, "path": dpath.replace("{" + key + "}", str(rid)),
                  "headers": cfg.get("headers", {}), "expect_status": inv}]
        for kind in ("list", "search", "export", "batch", "download"):
            ep = surfaces.get(kind)
            if not ep:
                continue
            m, p = ep.split(" ", 1)
            steps.append({"method": m, "path": p, "headers": cfg.get("headers", {}),
                          "expect_status": 200,
                          "checks": [{"absent": {"field": key, "value": rid}}]})
        lst = surfaces.get("list")
        cnt = surfaces.get("count")
        if lst and cnt:
            lm, lp = lst.split(" ", 1)
            cm, cp = cnt.split(" ", 1)
            steps.append({"method": lm, "path": lp, "headers": cfg.get("headers", {}),
                          "expect_status": 200, "save_body": "vis_rows"})
            steps.append({"method": cm, "path": cp, "headers": cfg.get("headers", {}),
                          "expect_status": 200,
                          "checks": [{"total_eq_len": {"var": "vis_rows"}}]})
        out.append(_case(f"CE-VIS-{resource}-{actor}",
                         f"detail invisible to {actor} must propagate to all read surfaces（§11.1）",
                         "cross-endpoint inconsistency",
                         f"CrossEndpointOracle:{resource}.detail",
                         steps, rule_id=(rules or [None])[0],
                         impact_path=[f"{resource}.detail", f"{resource}.list"]))

    # §11.2 update 后各入口读回新值
    upd = surfaces.get("update")
    if upd and spec.get("name_field"):
        um, upath = upd.split(" ", 1)
        nf, nv = spec["name_field"], spec.get("new_value", "TM-X")
        owner_headers = (actors or {}).get("OWNER", {}).get("headers", {}) or \
            (actors or {}).get("ADMIN", {}).get("headers", {})
        steps = [{"method": um, "path": upath.replace("{" + key + "}", str(rid)),
                  "headers": owner_headers, "body": {nf: nv}, "expect_status": 200}]
        steps.append({"method": dmethod, "path": dpath.replace("{" + key + "}", str(rid)),
                      "headers": owner_headers, "expect_status": 200,
                      "checks": [{"field_eq": {"path": nf, "value": nv}}]})
        for kind in ("list", "search", "export"):
            ep = surfaces.get(kind)
            if not ep:
                continue
            m, p = ep.split(" ", 1)
            steps.append({"method": m, "path": p, "headers": owner_headers,
                          "expect_status": 200,
                          "checks": [{"present": {"field": key, "value": rid}}]})
        out.append(_case(f"CE-UPD-{resource}",
                         f"update {nf}={nv} must be readable from detail/list/search/export（§11.2）",
                         "stale read across endpoints", f"CrossEndpointOracle:{resource}.update",
                         steps))

    # §11.3 delete 后各入口消失且 count 减少
    dele = surfaces.get("delete")
    cnt = surfaces.get("count")
    if dele and spec.get("allow_delete"):
        dm, dpath2 = dele.split(" ", 1)
        del_rid = spec.get("delete_id", rid)
        owner_headers = (actors or {}).get("OWNER", {}).get("headers", {})
        steps = []
        if cnt:
            cm, cp = cnt.split(" ", 1)
            steps.append({"method": cm, "path": cp, "headers": owner_headers,
                          "expect_status": 200, "save_body": "c0"})
        steps.append({"method": dm, "path": dpath2.replace("{" + key + "}", str(del_rid)),
                      "headers": owner_headers, "expect_status": spec.get("delete_status", 200)})
        steps.append({"method": dmethod, "path": dpath.replace("{" + key + "}", str(del_rid)),
                      "headers": owner_headers, "expect_status": inv})
        lst = surfaces.get("list")
        if lst:
            lm, lp = lst.split(" ", 1)
            steps.append({"method": lm, "path": lp, "headers": owner_headers,
                          "expect_status": 200,
                          "checks": [{"absent": {"field": key, "value": del_rid}}]})
        if cnt:
            cm, cp = cnt.split(" ", 1)
            steps.append({"method": cm, "path": cp, "headers": owner_headers,
                          "expect_status": 200,
                          "checks": [{"count_delta": {"var": "c0", "delta": -1}}]})
        out.append(_case(f"CE-DEL-{resource}",
                         "delete must remove from detail/list and decrement count（§11.3）",
                         "ghost row after delete", f"CrossEndpointOracle:{resource}.delete",
                         steps))
    return out


def metamorphic_cases(resource, surfaces, spec, actors=None):
    """§12 变形关系 case。"""
    out = []
    key = spec.get("key", "id")
    headers = ((actors or {}).get("OWNER") or {}).get("headers", {}) or {}
    lst = surfaces.get("list")
    page = surfaces.get("page")
    export = surfaces.get("export")
    detail = surfaces.get("detail")
    inv = spec.get("invisible_status", 404)
    rid = spec.get("sample_id")

    # filter(A∧B) ⊆ filter(A)
    fa, fb = spec.get("filter_a"), spec.get("filter_b")
    if lst and fa and fb:
        m, p = lst.split(" ", 1)
        qa = "&".join(f"{k}={v}" for k, v in fa.items())
        qb = "&".join(f"{k}={v}" for k, v in fb.items())
        out.append(_case(f"MT-FILTER-{resource}",
                         "filter(A AND B) must be subset of filter(A)（§12）",
                         "filter logic inconsistency", "Metamorphic:filter_subset",
                         [{"method": m, "path": f"{p}?{qa}", "headers": headers,
                           "expect_status": 200, "save_body": "fa"},
                          {"method": m, "path": f"{p}?{qa}&{qb}", "headers": headers,
                           "expect_status": 200,
                           "checks": [{"ids_subset_of": {"var": "fa", "key": key}}]}]))

    # 分页：页间不重复 + 并集==全量 + total>=size
    ps = spec.get("page_size", 2)
    pages = spec.get("pages", 2)
    if page and lst:
        m, p = page.split(" ", 1)
        steps = []
        for i in range(1, pages + 1):
            steps.append({"method": m, "path": f"{p}?page={i}&size={ps}", "headers": headers,
                          "expect_status": 200, "save_body": f"pg{i}",
                          "checks": [{"total_gte_size": {"field": spec.get("total_field", "total")}}]})
        steps[-1]["checks"].append({"unique_across": {"vars": [f"pg{i}" for i in range(1, pages)],
                                                      "key": key}})
        lm, lp = lst.split(" ", 1)
        steps.append({"method": lm, "path": lp, "headers": headers, "expect_status": 200,
                      "checks": [{"union_eq": {"vars": [f"pg{i}" for i in range(1, pages + 1)],
                                               "key": key}}]})
        out.append(_case(f"MT-PAGE-{resource}",
                         f"pages 1..{pages} must be disjoint and their union == full visible set（§12）",
                         "pagination inconsistency", "Metamorphic:page_union",
                         steps))

    # 排序：asc 与 desc 逆序
    sb = spec.get("sort_by")
    if lst and sb:
        m, p = lst.split(" ", 1)
        out.append(_case(f"MT-SORT-{resource}",
                         f"sort {sb} asc and desc must be exact reverse（§12）",
                         "sort inconsistency", "Metamorphic:sort_reverse",
                         [{"method": m, "path": f"{p}?sort={sb}&dir=asc", "headers": headers,
                           "expect_status": 200, "save_body": "asc"},
                          {"method": m, "path": f"{p}?sort={sb}&dir=desc", "headers": headers,
                           "expect_status": 200,
                           "checks": [{"reverse_of": {"var": "asc", "key": key}},
                                      {"order_sorted": {"by": sb, "dir": "desc"}}]}]))

    # detail 不可见不得从 export/batch 绕过
    other = {k: v for k, v in (actors or {}).items() if k not in ("OWNER", "SELF")}
    if detail and export and other and rid is not None:
        dm, dp = detail.split(" ", 1)
        em, ep = export.split(" ", 1)
        hdr = list(other.values())[0].get("headers", {})
        out.append(_case(f"MT-BYPASS-{resource}",
                         "detail invisible to non-owner must not be bypassable via export（§12）",
                         "permission bypass via export", "Metamorphic:no_bypass",
                         [{"method": dm, "path": dp.replace("{" + key + "}", str(rid)),
                           "headers": hdr, "expect_status": inv},
                          {"method": em, "path": ep, "headers": hdr, "expect_status": 200,
                           "checks": [{"absent": {"field": key, "value": rid}}]}]))
    return out


def plan(endpoints, spec, actors=None, rules=None):
    """主入口：端点清单 → 分组 → 一致性 + 变形 case（§22 溯源齐全）。"""
    cases = []
    for res, surfaces in sorted(group_surfaces(endpoints).items()):
        cases += consistency_cases(res, surfaces, spec, actors, rules)
        cases += metamorphic_cases(res, surfaces, spec, actors)
    return cases
