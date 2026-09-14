# testmind/datatruth.py — DataTruth Engine（V10 §13-15,17）
# 数据量由场景反推（§13.1），禁止固定"默认造 50 条"（§14）。
# 处理顺序 inventory → REUSE → REPAIR → CREATE（§13.2）：
#   REUSE 不改老数据；REPAIR 仅限 TestMind-owned 数据（§13.4 必须有 ownership 标记）；
#   CREATE 只补缺口（需 41 已有 37 → 只造 4，§13.5）。
# 所有写操作走 Seed Ledger（§16）；供给后必须重查询证明 VERIFIED（§17），不是假设。
# §15 Query Logic 专项：分页 6 情形 / Filter 6 / Sort 5 / Count 与可见性一致。

# ───────────────────────── §14 动态数据量 ─────────────────────────

def page_has_data(page, size):
    """第 page 页有数据：至少 (page-1)*size+1。"""
    return (page - 1) * size + 1


def page_full(page, size):
    """第 page 页满：至少 page*size。"""
    return page * size


def page_exists(page, size):
    """存在第 page 页：至少 (page-1)*size+1。"""
    return (page - 1) * size + 1


PAGE_SCENARIOS = {"has_data": page_has_data, "full": page_full, "exists": page_exists}


def page_min_rows(scenario, page, size):
    if scenario not in PAGE_SCENARIOS:
        raise ValueError(f"unknown page scenario {scenario!r}, use {sorted(PAGE_SCENARIOS)}")
    return PAGE_SCENARIOS[scenario](page, size)


# ───────────────────────── §13.1 数据需求结构 ─────────────────────────

def requirement(case_id, table, conditions, min_matching_rows, dataset_id="", reason="", **extra):
    """§13.1 数据需求结构；extra 透传 repair/pk 等供给提示。"""
    return {"case_id": case_id, "table": table, "conditions": dict(conditions),
            "requirements": {"min_matching_rows": int(min_matching_rows)},
            "dataset_id": dataset_id, "reason": reason, **extra}


# ───────────────────────── inventory（真实查询，非假设）─────────────────────────

def _where(conditions, ph="?"):
    """conditions: {col: val}；val=None → IS NULL。返回 (sql, args)。"""
    parts, args = [], []
    for c, v in (conditions or {}).items():
        if v is None:
            parts.append(f"{c} IS NULL")
        else:
            parts.append(f"{c} = {ph}")
            args.append(v)
    return (" WHERE " + " AND ".join(parts)) if parts else "", args


def count_matching(dbc, table, conditions):
    sql, args = _where(conditions, dbc.ph)
    rows = dbc.rows(f"SELECT COUNT(*) AS n FROM {dbc.quote(table)}{sql}", tuple(args))
    return int(list(rows[0].values())[0]) if rows else 0


def inventory(dbc, req):
    """§13.2 第一步：真实查询现有匹配行数。"""
    return {"case_id": req["case_id"], "table": req["table"],
            "conditions": req["conditions"],
            "need": req["requirements"]["min_matching_rows"],
            "have": count_matching(dbc, req["table"], req["conditions"])}


# ───────────────────────── §13.2-13.5 决策：REUSE → REPAIR → CREATE ─────────────────────────

def plan_one(dbc, req, ownership=None):
    """单需求决策。ownership={"column":..,"value":..} 是 REPAIR 的前提（§13.4：
    普通存量数据默认不可修改，仅 TestMind-owned 可 REPAIR）。
    req 可选字段：
      pk: 主键列（默认 id）
      repair: {"where": {col:val}, "set": {col:val}} — 可修复行的定位与目标值
    返回 {req, inventory, decision, gap, repair_plan, create_plan}"""
    inv = inventory(dbc, req)
    out = {"req": req, "inventory": inv, "decision": None, "gap": 0,
           "repair_plan": None, "create_plan": None}
    need, have = inv["need"], inv["have"]
    if need <= 0:
        out["decision"] = "NOT_REQUIRED"
        return out
    if have >= need:
        out["decision"] = "REUSE"                      # §13.3：直接复用，不修改
        return out
    gap = need - have
    # REPAIR：仅当有 ownership 标记 + repair 定位，且候选行确实 owned（§13.4）
    rep = req.get("repair")
    if rep and ownership:
        ocol, oval = ownership["column"], ownership["value"]
        sql, args = _where(rep["where"], dbc.ph)
        owned = dbc.rows(
            f"SELECT {req.get('pk', 'id')} AS pk FROM {dbc.quote(req['table'])}"
            f"{sql} AND {dbc.quote(ocol)} = {dbc.ph}", tuple(args) + (oval,))
        pks = [r["pk"] for r in owned][:gap]
        if pks:
            out["repair_plan"] = {"pks": pks, "set": rep["set"], "pk": req.get("pk", "id")}
            gap -= len(pks)
    if gap > 0:
        out["create_plan"] = {"rows": gap}             # §13.5：只补缺口
    out["gap"] = gap
    out["decision"] = "REPAIR+CREATE" if (out["repair_plan"] and gap) else \
        ("REPAIR" if out["repair_plan"] else "CREATE")
    return out


def plan(dbc, reqs, ownership=None):
    return [plan_one(dbc, r, ownership) for r in reqs]


# ───────────────────────── 供给（全部写操作走 Ledger）─────────────────────────

def provision(dbc, plans, ledger, template, case_id="", dataset_id=""):
    """执行 REPAIR/CREATE 并登记 Ledger；重查询证明 VERIFIED（§17）。
    template: {col: value} 或 callable(i, base)->{col: value}，用于 CREATE 行。
    返回 {results:[{case_id, decision, before, after, verified}], status}"""
    results, all_ok = [], True
    for p in plans:
        req, inv = p["req"], p["inventory"]
        entry = {"case_id": req["case_id"], "table": req["table"],
                 "decision": p["decision"], "before": inv["have"], "after": inv["have"],
                 "repaired": 0, "created": 0, "verified": False}
        if p["decision"] in ("NOT_REQUIRED", "REUSE"):
            entry["verified"] = inv["need"] <= 0 or inv["have"] >= inv["need"]
            results.append(entry)
            continue
        cid = case_id or req["case_id"]
        did = dataset_id or req.get("dataset_id", "")
        # REPAIR：UPDATE 指定主键行（§13.4 仅 TestMind-owned）。全部内联字面量，
        # 使 SeedLedger.parse_write 能识别表名/WHERE 并登记 before/after。
        if p["repair_plan"]:
            rp = p["repair_plan"]
            set_sql = ", ".join(f"{dbc.quote(k)} = {_lit(v)}" for k, v in rp["set"].items())
            pk_list = ", ".join(_lit(v) for v in rp["pks"])
            sql = (f"UPDATE {dbc.quote(req['table'])} SET {set_sql} "
                   f"WHERE {dbc.quote(rp['pk'])} IN ({pk_list})")
            ledger.record_exec(dbc, sql, case_id=cid, dataset_id=did,
                               reason=f"REPAIR {req['case_id']}")
            entry["repaired"] = len(rp["pks"])
        # CREATE：只补缺口
        if p["create_plan"]:
            for i in range(p["create_plan"]["rows"]):
                row = template(i, dict(req["conditions"])) if callable(template) \
                    else {**template, **req["conditions"]}
                cols = list(row)
                sql = (f"INSERT INTO {dbc.quote(req['table'])} "
                       f"({', '.join(dbc.quote(c) for c in cols)}) "
                       f"VALUES ({', '.join(_lit(row[c]) for c in cols)})")
                ledger.record_exec(dbc, sql, case_id=cid, dataset_id=did,
                                   reason=f"CREATE {req['case_id']} gap#{i + 1}")
                entry["created"] += 1
        # 重查询证明（§17：数据前置条件必须真实查询证明，不是假设）
        after = count_matching(dbc, req["table"], req["conditions"])
        entry["after"] = after
        entry["verified"] = after >= req["requirements"]["min_matching_rows"]
        all_ok = all_ok and entry["verified"]
        results.append(entry)
    return {"status": "VERIFIED" if all_ok else "BLOCKED", "results": results}


def _lit(v):
    """SQL 字面量：None→NULL，数字直出，字符串加单引号并转义。
    写 SQL 全部内联字面量，使 SeedLedger 能解析表名/WHERE 登记 before/after（§16）。"""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


# ───────────────────────── §15 Query Logic 专项 ─────────────────────────
# 生成带 §13.1 data_requirement 的 case（数据量由场景反推，非固定值）。

def _qcase(cid, reason, table, conditions, min_rows, steps, dataset_id=""):
    """带 §13.1 data_requirement 的 query case：数据量由场景（min_rows）反推。"""
    return {"id": cid, "category": "DATA_TRUTH", "priority": "P0",
            "reason": reason,
            "impact_path": [f"{table}.query", cid],
            "rule_id": None, "risk": "query logic",
            "expected_source": "DataTruth:§15",
            "data_requirement": requirement(cid, table, conditions, min_rows,
                                            dataset_id=dataset_id or f"query-{table}"),
            "action": {"kind": "sequence", "steps": steps},
            "expected": {}}


def query_logic_cases(table, endpoint, size=20, key="id", total_field="total",
                      sort_by=None, filters=None, count_endpoint=None,
                      conditions=None, actors_headers=None):
    """§15：分页 6 情形 + Filter 6 情形 + Sort 5 情形 + Count 一致性。
    endpoint: "GET /orders/page"；size: 分页 size（数据量由此反推，非固定值）。
    filters: [{name, params:{col:val}}]；conditions: 分页/排序用例的基础 DB 条件（dict）。
    count_endpoint: 缺省由 endpoint 路径推导。"""
    method, _, path = endpoint.partition(" ")
    hdr = actors_headers or {}
    base_cond = dict(conditions or {})
    cases = []

    def get(qs="", checks=None, save=None):
        s = {"method": method, "path": path + (f"?{qs}" if qs else ""),
             "headers": hdr, "expect_status": 200}
        if checks:
            s["checks"] = checks
        if save:
            s["save_body"] = save
        return s

    # —— 分页 6 情形（§15，数据量由 §14 反推）——
    cases.append(_qcase(f"DT-PAGE-{table}-EMPTY", "0 行：空结果 total=0 且无行（§15）",
                        table, base_cond, 0,
                        [get(f"page=1&size={size}", [{"total_eq_rows": {}}])]))
    cases.append(_qcase(f"DT-PAGE-{table}-ONE", "1 行：恰好 1 条（§15）",
                        table, base_cond, 1,
                        [get(f"page=1&size={size}", [{"total_eq_rows": {}}])]))
    cases.append(_qcase(f"DT-PAGE-{table}-EXACT", f"刚好一页：total=={size}（§15）",
                        table, base_cond, size,
                        [get(f"page=1&size={size}", [{"total_eq_rows": {}}])]))
    cases.append(_qcase(f"DT-PAGE-{table}-PLUS1", f"多 1 行：第 2 页有 1 条，共 {size}+1（§15/§14）",
                        table, base_cond, size + 1,
                        [get(f"page=2&size={size}",
                             [{"ids_present_n": {"n": 1, "key": key}},
                              {"total_gte_size": {"field": total_field}}])]))
    cases.append(_qcase(f"DT-PAGE-{table}-LAST", f"最后一页：page=2 size={size} 共 {size}+1 行，页间不重复（§15）",
                        table, base_cond, size + 1,
                        [get(f"page=1&size={size}", save="p1"),
                         get(f"page=2&size={size}",
                             [{"unique_across": {"vars": ["p1"], "key": key}},
                              {"total_gte_size": {"field": total_field}}])]))
    cases.append(_qcase(f"DT-PAGE-{table}-EMPTYLAST", f"空最后页：total 恰为 {size}，第 2 页无行（§15）",
                        table, base_cond, size,
                        [get(f"page=2&size={size}", [{"absent_all": {}}])]))

    # —— Filter 6 情形（hit/miss/single/multiple/null/empty）——
    flist = filters or []
    for f in flist:
        fname, fparams = f["name"], f["params"]
        qs = "&".join(f"{k}={v}" for k, v in fparams.items())
        cases.append(_qcase(f"DT-FILTER-{table}-{fname}-HIT", f"filter {fname} hit：仅返回匹配行（§15）",
                            table, fparams, 1,
                            [get(qs, [{"filter_only": {"match": fparams}}])]))
        miss = {k: (f"{v}-nomatch" if isinstance(v, str) else -999999)
                for k, v in fparams.items()}
        mqs = "&".join(f"{k}={v}" for k, v in miss.items())
        cases.append(_qcase(f"DT-FILTER-{table}-{fname}-MISS", f"filter {fname} miss → 空（§15）",
                            table, miss, 0,
                            [get(mqs, [{"absent_all": {}}])]))
    if flist:
        single = flist[0]["params"]
        multi = {k: v for f in flist for k, v in f["params"].items()}
        sqs = "&".join(f"{k}={v}" for k, v in single.items())
        mqs = "&".join(f"{k}={v}" for k, v in multi.items())
        cases.append(_qcase(f"DT-FILTER-{table}-SINGLE", "single condition 结果 ⊇ multiple（§15）",
                            table, single, 1,
                            [get(sqs, save="fa"),
                             get(mqs, [{"ids_subset_of": {"var": "fa", "key": key}}])]))
        cases.append(_qcase(f"DT-FILTER-{table}-MULTI", "multiple condition：仅返回同时满足全部条件行（§15）",
                            table, multi, 1,
                            [get(mqs, [{"filter_only": {"match": multi}}])]))
        nullf = list(single)[0]
        cases.append(_qcase(f"DT-FILTER-{table}-NULL", f"null 条件：{nullf} 为 NULL 的行（§15）",
                            table, {nullf: None}, 1,
                            [get(f"{nullf}=NULL", [{"field_is_null": {"field": nullf}}])]))
        cases.append(_qcase(f"DT-FILTER-{table}-EMPTY", "empty 条件：空串参数不误匹配（§15）",
                            table, {nullf: ""}, 0,
                            [get(f"{nullf}=", [{"absent_all": {}}])]))

    # —— Sort 5 情形（min/max/duplicate/null/tie-breaker→reverse）——
    if sort_by:
        def asc_step():
            return get(f"sort={sort_by}&dir=asc&size=1000", save="asc")
        cases.append(_qcase(f"DT-SORT-{table}-MIN", f"asc 首行为 {sort_by} 最小值（§15）",
                            table, base_cond, 2, [asc_step(),
                                get(f"sort={sort_by}&dir=asc&size=1",
                                    [{"first_eq_saved_of": {"var": "asc", "key": key, "pos": "first"}}])]))
        cases.append(_qcase(f"DT-SORT-{table}-MAX", f"desc 首行为 {sort_by} 最大值（§15）",
                            table, base_cond, 2, [asc_step(),
                                get(f"sort={sort_by}&dir=desc&size=1",
                                    [{"first_eq_saved_of": {"var": "asc", "key": key, "pos": "last"}}])]))
        cases.append(_qcase(f"DT-SORT-{table}-REVERSE", "asc 与 desc 逆序（§15/§12）",
                            table, base_cond, 2, [asc_step(),
                                get(f"sort={sort_by}&dir=desc&size=1000",
                                    [{"reverse_of": {"var": "asc", "key": key}}])]))
        cases.append(_qcase(f"DT-SORT-{table}-DUP", "重复值排序仍单调（§15）",
                            table, base_cond, 2, [asc_step(),
                                get(f"sort={sort_by}&dir=asc&size=1000",
                                    [{"order_sorted": {"by": sort_by, "dir": "asc"}}])]))
        cases.append(_qcase(f"DT-SORT-{table}-NULL", "NULL 排序值固定末端（§15）",
                            table, base_cond, 1, [asc_step(),
                                get(f"sort={sort_by}&dir=asc&size=1000",
                                    [{"nulls_at_end": {"by": sort_by}}])]))

    # —— Count 与可见性一致（§15：count 必须和 list/page/detail visibility 一致）——
    ce = count_endpoint or ("GET " + path.rsplit("/", 1)[0] + "/count")
    cm, cpath = ce.split(" ", 1)
    cases.append(_qcase(f"DT-COUNT-{table}", "count.total == list 可见行数（§15/§11.1）",
                        table, base_cond, 1,
                        [get("size=1000", save="vis"),
                         {"method": cm, "path": cpath, "headers": hdr, "expect_status": 200,
                          "checks": [{"total_eq_len": {"var": "vis"}}]}]))
    return cases


# ───────────────────────── 扩展 check DSL（§15 专用，注册进 oracle.CHECKS）─────────────────────────

def _extend_checks():
    from testmind import oracle as _o

    @_o.check
    def total_eq_rows(args, body, ctx):
        """body 自身：total == 行数（分页 total 与实际返回一致）。"""
        field = args.get("field", "total")
        total = body.get(field) if isinstance(body, dict) else None
        n = len(_o.rows_of(body))
        return total is not None and int(total) == n, {"total": total, "rows": n}

    @_o.check
    def absent_all(args, body, ctx):
        rows = _o.rows_of(body)
        return len(rows) == 0, {"rows": len(rows)}

    @_o.check
    def ids_present_n(args, body, ctx):
        rows = _o.rows_of(body)
        return len(rows) == args["n"], {"rows": len(rows), "want": args["n"]}

    @_o.check
    def filter_only(args, body, ctx):
        rows = _o.rows_of(body)
        m = args["match"]
        bad = [r for r in rows if not all(str(r.get(k)) == str(v) for k, v in m.items())]
        return not bad, {"violations": bad[:5]}

    @_o.check
    def field_is_null(args, body, ctx):
        rows = _o.rows_of(body)
        f = args["field"]
        return bool(rows) and all(r.get(f) is None for r in rows), {"rows": len(rows)}

    @_o.check
    def first_eq_saved_of(args, body, ctx):
        """当前首行 id == 已保存集合的首/末行 id（sort min/max）。"""
        cur = _o.rows_of(body)
        saved = _o.rows_of(ctx.get(args["var"]))
        if not cur or not saved:
            return False, {"cur": len(cur), "saved": len(saved)}
        k = args["key"]
        want = saved[-1] if args.get("pos") == "last" else saved[0]
        return str(cur[0].get(k)) == str(want.get(k)), {"first": cur[0].get(k), "want": want.get(k)}

    @_o.check
    def nulls_at_end(args, body, ctx):
        rows = _o.rows_of(body)
        by = args["by"]
        vals = [r.get(by) for r in rows]
        first_null = next((i for i, v in enumerate(vals) if v is None), len(vals))
        return all(v is None for v in vals[first_null:]), {"n": len(vals), "first_null": first_null}


_extend_checks()
