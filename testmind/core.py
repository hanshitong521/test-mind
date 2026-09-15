# testmind/core.py — TestMind Core：事实→问题→契约→计划→数据→执行→DB对拍→证据→Gate
# 确定性优先，零LLM(§40)。依赖：仅 stdlib + pymysql(可选，MySQL 对拍)
import datetime, itertools, json, os, re, sqlite3, subprocess, sys, time, uuid
import urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
REGISTRY = os.path.join(ROOT, "regression", "cases.json")

LEGAL_STATUS = {"PASS", "FAIL", "HOLD", "BLOCKED", "NOT_TESTED", "TOOL_ERROR", "SKIPPED_WITH_REASON", "STALE"}


def now():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ───────────────────── 1. Facts (§4-6, §44-46) ─────────────────────

class Facts:
    def __init__(self, items=None, questions=None):
        self.f = items or []
        self.questions = questions or []
        self.unparsed = []            # 看到了却没译成事实的约束：静默漏抽必须可见

    def add(self, topic, statement, source, status="CONFIRMED", derived_from=(), **meta):
        assert source, f"fact without source: {topic}"      # 无来源=拒绝，不猜
        for x in self.f:                                     # 同一约束被两处看到 ≠ 两条事实
            if (x["topic"], x["statement"], x["source"]) == (topic, statement, source):
                return x["id"]
        self.f.append({"id": f"F{len(self.f)+1:03d}", "topic": topic, "statement": statement,
                       "source": source, "status": status, "derived_from": list(derived_from), **meta})
        return self.f[-1]["id"]

    def absorb(self, other):
        self.f += other.f
        self.unparsed += other.unparsed
        self.questions += other.questions
        return self

    def gaps(self, declared=()):
        """declared = 被声明为事实来源的源。某个源零产出 = 抽取器对这个方言失效。"""
        cnt = {}
        for x in self.f:
            cnt[x["source"]] = cnt.get(x["source"], 0) + 1
        return {"sources": [{"source": s, "facts": cnt.get(s, 0)} for s in declared],
                "zero_fact_sources": [s for s in declared if not cnt.get(s)],
                "unparsed": self.unparsed}

    def add_raw(self, item):
        """宿主 AI 注入自提事实：必须带 source；derived_from 必须引用已存在 id。"""
        src = item.get("source")
        assert src, "add_facts: source required (No Requirement → No Expected)"
        for fid in item.get("derived_from", []):
            assert any(x["id"] == fid for x in self.f), f"unknown derived_from {fid}"
        extra = {k: v for k, v in item.items()
                 if k not in ("topic", "statement", "source", "status", "derived_from", "id")}
        return self.add(item["topic"], item["statement"], src,
                        item.get("status", "CONFIRMED"), item.get("derived_from", []), **extra)

    def derive(self, topic, statement, *fact_ids):
        for fid in fact_ids:
            assert any(x["id"] == fid for x in self.f), fid
        return self.add(topic, statement, "derived", "DERIVED", derived_from=fact_ids)

    def ask(self, question, why, impact, options=()):
        self.questions.append({"id": f"Q{len(self.questions)+1:03d}", "question": question,
                               "why": why, "impact": impact, "options": list(options), "state": "USER_REQUIRED"})

    def answer(self, qid, ans):
        want = "Q" + str(qid).lstrip("Qq").lstrip("0").zfill(3)   # Q1/Q001/1 统一成 Q001 口径
        for q in self.questions:
            if q["id"] == want or str(q["id"]) == str(qid):
                q["state"], q["answer"] = "CONFIRMED", ans
                return True
        return False

    def conflicts(self):
        out, by_topic = [], {}
        for x in self.f:
            by_topic.setdefault(x["topic"], []).append(x)
        for topic, xs in by_topic.items():
            for a, b in itertools.combinations(xs, 2):
                na = bool(re.search(r"不允许|禁止|not |must not", a["statement"], re.I))
                nb = bool(re.search(r"不允许|禁止|not |must not", b["statement"], re.I))
                if na != nb and _norm(a["statement"]) != _norm(b["statement"]):
                    out.append({"topic": topic, "a": a["id"], "b": b["id"]})
        return out

    def confirmed(self, topic):
        return [x for x in self.f if x["topic"] == topic and x["status"] in ("CONFIRMED", "DERIVED")]

    def snapshot(self):
        return {"facts": self.f, "questions": self.questions, "conflicts": self.conflicts()}


def _norm(s):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", s.lower())


def _split_top(s, sep=","):
    """按顶层分隔符切分：括号与引号内部不算（MySQL 的 ENUM('a','b') 里就有逗号）。"""
    out, buf, depth, q = [], [], 0, ""
    for i, c in enumerate(s):
        if q:
            buf.append(c)
            if c == q and s[i - 1] != "\\":
                q = ""
            continue
        if c in "`\"'":
            q = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == sep and depth == 0:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(c)
    out.append("".join(buf))
    return [x.strip() for x in out if x.strip()]


def _match_paren(s, i):
    """返回 s[i]=='(' 的配对 ')' 下标。CHECK 里嵌套括号+引号，正则数不准。"""
    depth, q = 0, ""
    for k in range(i, len(s)):
        c = s[k]
        if q:
            if c == q and s[k - 1] != "\\":
                q = ""
            continue
        if c in "'\"":
            q = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return k
    return None


def _col(name):
    """`db`.`t`.`qty` / [qty] / "qty" → qty。真实项目 DDL 几乎都带这些装饰。"""
    return re.sub(r"[`\[\]\"]", "", str(name)).strip().split(".")[-1]


def _cols(spec):
    return ",".join(_col(c) for c in _split_top(spec))


def _num(s):
    s = str(s)
    return int(s) if re.fullmatch(r"-?\d+", s) else float(s)


def _open(lo, hi, fmt):
    """两端都在 → fmt(lo,hi)；一端无界 → 保留 None 语义。"""
    if lo is not None and hi is not None:
        return fmt(lo, hi)
    return f"[{lo if lo is not None else 'None'},{hi if hi is not None else 'None'}]"


class FactResolver:
    """从 DDL / OpenAPI 提取 CONFIRMED 事实。译不动的约束进 facts.unparsed，绝不静默丢弃。"""

    IDENT = r"[\w`\[\]\"]+"
    NUM = r"-?\d+(?:\.\d+)?"
    LEN_FN = r"(?:length|len|char_length|character_length)"
    KNOWN_TYPES = re.compile(r"(?:BIG|SMALL|MEDIUM|TINY)?(?:INT|INTEGER|DECIMAL|NUMERIC|FLOAT|DOUBLE|REAL|BIT|"
                             r"BOOL|BOOLEAN|DATE|DATETIME|TIME|TIMESTAMP|YEAR|TEXT|TINYTEXT|MEDIUMTEXT|LONGTEXT|"
                             r"JSON|BLOB|TINYBLOB|MEDIUMBLOB|LONGBLOB|SERIAL|UUID|XML|POINT|GEOMETRY|"
                             r"VARCHAR|NVARCHAR|CHAR|NCHAR|VARBINARY|BINARY|ENUM|SET)\w*$", re.I)
    NOISE = re.compile(r"COMMENT\s+'(?:[^']|'')*'|CHARACTER\s+SET\s+\w+|COLLATE\s+\w+|UNSIGNED|SIGNED|ZEROFILL|"
                       r"AUTO_INCREMENT|ON\s+UPDATE\s+CURRENT_TIMESTAMP(?:\(\d*\))?|SRID\s+\d+|"
                       r"STORAGE\s+(?:_?DISK|MEMORY)|COLUMN_FORMAT\s+\w+|INVISIBLE|VISIBLE", re.I)
    HANDLED = re.compile(r"NOT\s+NULL|\bNULL\b|PRIMARY\s+KEY|UNIQUE(?:\s+KEY)?|DEFAULT\s+(?:\s*\([^()]*\)\s*|\S+)|"
                         r"REFERENCES\s+[\w.`\[\]\"]+(?:\s*\([^()]*\))?", re.I)

    # ────────── DDL ──────────

    @staticmethod
    def from_schema_sql(sql, source):
        facts = Facts()
        sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)          # 注释不是约束，先剥掉免得污染 unparsed
        sql = re.sub(r"^\s*#.*$", " ", sql, flags=re.M)
        sql = re.sub(r"--[^\n]*", " ", sql)
        for stmt in sql.split(";"):
            if not re.search(r"CREATE\s+TABLE", stmt, re.I):
                continue
            m = re.match(r"\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.`\[\]\"]+)\s*\((.*)\)\s*[^;]*$",
                         stmt, re.I | re.S)
            if not m:
                facts.unparsed.append({"source": source, "what": "CREATE TABLE 整体未解析", "text": stmt.strip()[:160]})
                continue
            table = _col(m.group(1))
            for part in _split_top(m.group(2)):
                FactResolver._ddl_part(facts, table, part, source)
        return facts

    @staticmethod
    def _ddl_part(facts, table, part, source):
        p = re.sub(r"\s+", " ", part).strip().rstrip(",")
        if not p:
            return
        m = re.match(r"(?:CONSTRAINT\s+\S+\s+)?FOREIGN\s+KEY\s*\((.+)\)\s*REFERENCES\s+([\w.`\[\]\"]+)\s*\((.+)\)", p, re.I)
        if m:
            return facts.add(f"fk:{table}.{_cols(m.group(1))}",
                             f"{table}.{_cols(m.group(1))} references {_col(m.group(2))}({_cols(m.group(3))})", source)
        m = re.match(r"(?:CONSTRAINT\s+\S+\s+)?UNIQUE\s+(?:KEY|INDEX|CONSTRAINT)?\s*(?:\S+\s*)?\((.+)\)", p, re.I)
        if m:
            return facts.add(f"unique:{table}.{_cols(m.group(1))}", f"{table}.{_cols(m.group(1))} is unique", source)
        m = re.match(r"(?:CONSTRAINT\s+\S+\s+)?PRIMARY\s+KEY\s*\((.+)\)", p, re.I)
        if m:
            return facts.add(f"pk:{table}", f"{table} primary key ({_cols(m.group(1))})", source)
        m = re.match(r"(?:CONSTRAINT\s+\S+\s+)?CHECK\s*\((.+)\)\s*$", p, re.I)
        if m:
            return FactResolver._check(facts, m.group(1), source, table)
        if re.match(r"(?:UNIQUE\s+)?(?:KEY|INDEX)\b", p, re.I):
            return                                      # 普通索引只影响性能，不是可测约束
        m = re.match(rf"({FactResolver.IDENT})\s+(\w+)\s*(\(([^)]*)\))?(.*)$", p, re.S)
        if not m:
            return facts.unparsed.append({"source": source, "what": f"{table} 列定义未解析", "text": p[:160]})
        col, typ, args, rest = _col(m.group(1)), m.group(2).upper(), m.group(4), m.group(5) or ""
        low = f"{table}.{col}"
        if typ in ("ENUM", "SET") and args:
            facts.add(f"enum:{col}", f"{col} in {','.join(v.strip().strip(chr(39) + chr(34)) for v in _split_top(args))}", source)
        elif typ in ("VARCHAR", "NVARCHAR", "CHAR", "NCHAR", "VARBINARY", "BINARY") and args:
            facts.add(f"len:{col}", f"{col} length between 0 and {int(args.split(',')[0])}", source)
        elif not FactResolver.KNOWN_TYPES.match(typ):
            facts.unparsed.append({"source": source, "what": f"{low} 未识别类型 {typ}", "text": p[:160]})
        if re.search(r"\bNOT\s+NULL\b", rest, re.I):
            facts.add(f"not_null:{low}", f"{low} not null", source)
        if re.search(r"\bUNIQUE\b", rest, re.I):
            facts.add(f"unique:{col}", f"{col} is unique", source)
        m2 = re.search(rf"\bREFERENCES\s+({FactResolver.IDENT}(?:\.{FactResolver.IDENT})*)", rest, re.I)
        if m2:
            facts.add(f"fk:{low}", f"{low} references {_col(m2.group(1))}", source)
        m3 = re.search(rf"\bDEFAULT\s+(\(?\s*(?:{FactResolver.IDENT}\s*\([^()]*\)|'(?:[^']|'')*'|{FactResolver.NUM}|\w+)\s*\)?)",
                       rest, re.I)
        if m3:
            facts.add(f"default:{col}", f"{col} default {m3.group(1).strip()}", source)
        left = list(rest)
        for cm in re.finditer(r"\bCHECK\s*\(", rest, re.I):
            end = _match_paren(rest, cm.end() - 1)
            if end is None:
                facts.unparsed.append({"source": source, "what": f"{low} CHECK 括号不配对", "text": rest[cm.start():][:160]})
                continue
            FactResolver._check(facts, rest[cm.end():end], source, table)
            for i in range(cm.start(), end + 1):
                left[i] = " "
        residue = FactResolver.HANDLED.sub(" ", FactResolver.NOISE.sub(" ", "".join(left)))
        if residue.strip():
            facts.unparsed.append({"source": source, "what": f"{low} 未识别约束", "text": residue.strip()[:160]})

    @staticmethod
    def _check(facts, expr, source, table=""):
        """CHECK 表达式 → len/range/enum/not_null 事实。BETWEEN、>=/<= 的 AND 组合、IN (…)、IS NOT NULL 都吃。"""
        e = expr.strip()
        hit = False
        for m in re.finditer(rf"{FactResolver.LEN_FN}\s*\(\s*({FactResolver.IDENT})\s*\)\s*BETWEEN\s+(\d+)\s+AND\s+(\d+)", e, re.I):
            c = _col(m.group(1))
            facts.add(f"len:{c}", f"{c} length between {m.group(2)} and {m.group(3)}", source)
            hit = True
        acc = {}
        for m in re.finditer(rf"(?:{FactResolver.LEN_FN}\s*\(\s*({FactResolver.IDENT})\s*\)|({FactResolver.IDENT}))"
                             rf"\s*(>=|<=|>|<)\s*({FactResolver.NUM})", e, re.I):
            is_len = bool(m.group(1))
            c = _col(m.group(1) or m.group(2))
            if not is_len and c.lower() in ("and", "or", "not", "between", "in", "is"):
                continue
            d = acc.setdefault((is_len, c), [None, None])
            op, v = m.group(3), _num(m.group(4))
            if op in (">=", ">"):
                v = v + 1 if op == ">" else v
                d[0] = v if d[0] is None else max(d[0], v)
            else:
                v = v - 1 if op == "<" else v
                d[1] = v if d[1] is None else min(d[1], v)
        for (is_len, c), (lo, hi) in acc.items():
            if is_len:
                facts.add(f"len:{c}", f"{c} length between " + _open(lo, hi, "{} and {}".format), source)
            else:
                facts.add(f"range:{c}", f"{c} between " + _open(lo, hi, "{} and {}".format), source)
            hit = True
        for m in re.finditer(rf"\b({FactResolver.IDENT})\s+BETWEEN\s+({FactResolver.NUM})\s+AND\s+({FactResolver.NUM})", e, re.I):
            c = _col(m.group(1))
            facts.add(f"range:{c}", f"{c} between {m.group(2)} and {m.group(3)}", source)
            hit = True
        for m in re.finditer(rf"\b({FactResolver.IDENT})\s+IN\s*\(([^()]*)\)", e, re.I):
            c = _col(m.group(1))
            vals = [v.strip().strip(chr(39) + chr(34)) for v in _split_top(m.group(2))]
            facts.add(f"enum:{c}", f"{c} in {','.join(vals)}", source)
            hit = True
        for m in re.finditer(rf"\b({FactResolver.IDENT})\s+IS\s+NOT\s+NULL\b", e, re.I):
            facts.add(f"not_null:{_col(m.group(1))}", f"{table + '.' if table else ''}{_col(m.group(1))} not null", source)
            hit = True
        if not hit:
            facts.unparsed.append({"source": source, "what": "CHECK 未能译成可执行约束", "text": e[:160]})

    # ────────── OpenAPI / JSON Schema ──────────

    @staticmethod
    def from_openapi(spec, source):
        facts = Facts()

        def deref(node):
            seen = 0
            while isinstance(node, dict) and "$ref" in node and seen < 10:
                seen += 1
                cur = spec
                for part in str(node["$ref"]).lstrip("#/").split("/"):
                    cur = cur.get(part, {}) if isinstance(cur, dict) else {}
                node = cur
            return node if isinstance(node, dict) else {}

        def prop(key, ps, required, **fmeta):
            ps = deref(ps)
            t = ps.get("type") or ("array" if "items" in ps else None)
            if "enum" in ps:
                facts.add(f"enum:api:{key}", f"api:{key} in {','.join(str(v) for v in ps['enum'])}", source, **fmeta)
            if t == "integer":
                lo = hi = None
                if "minimum" in ps:
                    lo = int(ps["minimum"])
                if "exclusiveMinimum" in ps:
                    lo = int(ps["exclusiveMinimum"]) + 1
                if "maximum" in ps:
                    hi = int(ps["maximum"])
                if "exclusiveMaximum" in ps:
                    hi = int(ps["exclusiveMaximum"]) - 1
                if lo is not None or hi is not None:
                    facts.add(f"range:api:{key}", f"api:{key} between " + _open(lo, hi, "{} and {}".format),
                              source, **fmeta)
            elif any(k in ps for k in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")):
                facts.unparsed.append({"source": source, "what": f"{key} 非整数边界未取样",
                                       "text": json.dumps({k: ps[k] for k in ps if k.endswith("imum") or k.endswith("al")},
                                                          default=str)[:160]})
            if "pattern" in ps:
                facts.add(f"pattern:api:{key}", f"api:{key} matches {ps['pattern']}", source, **fmeta)
            if "minLength" in ps or "maxLength" in ps:
                facts.add(f"len:api:{key}",
                          f"api:{key} length between {ps.get('minLength', 0)} and {ps.get('maxLength', 'None')}",
                          source, **fmeta)
            for k in ("oneOf", "anyOf", "allOf", "not", "const", "multipleOf", "minItems", "maxItems", "uniqueItems",
                      "format", "readOnly", "writeOnly", "additionalProperties", "contains", "propertyNames"):
                if k in ps:
                    facts.unparsed.append({"source": source, "what": f"{key}.{k} 未取样",
                                           "text": json.dumps(ps[k], default=str)[:120]})
            if required:
                facts.add(f"required:{key}", f"{key} required", source, **fmeta)
            return ps

        def walk(sch, name, prefix="", **fmeta):
            sch = deref(sch)
            req = set(sch.get("required") or [])
            for p, ps in (sch.get("properties") or {}).items():
                sub = prop(f"{name}.{prefix}{p}", ps, p in req, **fmeta)
                if sub.get("type") == "object" or "properties" in sub:
                    walk(sub, name, prefix=f"{prefix}{p}.", **fmeta)
            for r in req:
                facts.add(f"required:{name}.{prefix}{r}", f"{name}.{prefix}{r} required", source, **fmeta)

        for path, item in (spec.get("paths") or {}).items():
            if not isinstance(item, dict):
                continue
            for verb, op in item.items():
                if verb.lower() not in ("get", "put", "post", "patch", "delete", "options", "trace") or not isinstance(op, dict):
                    continue
                name = op.get("operationId") or f"{verb.upper()} {path}"
                fmeta = {"operation_id": name, "http_path": path, "http_method": verb.upper()}
                for prm in op.get("parameters") or []:
                    prm = deref(prm)
                    if prm.get("in") in ("path", "query", "header", "cookie"):
                        facts.add(f"paramrequired:{name}.{prm.get('name')}",
                                  f"{name}.{prm.get('name')} required ({prm.get('in')} param)", source, **fmeta)
                for media in ((op.get("requestBody") or {}).get("content") or {}).values():
                    walk(deref((media or {}).get("schema") or {}), name, **fmeta)
        for name, sch in (spec.get("components", {}).get("schemas") or {}).items():
            walk(sch, name, scope="components")
        return facts


# ───────────────────── 2. Contract (§8) ─────────────────────

def build_contract(contract_id, target, facts, inputs, expected):
    assert facts.conflicts() == [], "BLOCKED_BY_CONFLICT: 契约阶段禁止带冲突事实前进"
    return {"contract_id": contract_id, "target": target,
            "sources": sorted({x["source"] for x in facts.f}),
            "facts": [x["id"] for x in facts.f],
            "unknowns": [q["id"] for q in facts.questions if q["state"] == "USER_REQUIRED"],
            "conflicts": [], "preconditions": [], "inputs": inputs, "expected": expected}


# ───────────────────── 3. Planner：直接产出可执行 case (§11-15, §49) ─────────────────────

def boundary_values(fact):
    m = re.search(r"between (-?\d+) and (-?\d+)|\[(-?\d+)\s*,\s*(-?\d+|None)\]", fact["statement"])
    if not m:
        return []
    lo = int(m.group(1) or m.group(3))
    hi = m.group(2) or m.group(4)
    if hi in (None, "None"):
        return [lo - 1, lo, lo + 1]
    hi = int(hi)
    return [lo - 1, lo, lo + 1, hi - 1, hi, hi + 1] if hi - lo > 4 else sorted({lo - 1, lo, hi, hi + 1})


def string_boundaries(fact):
    m = re.search(r"len(?:gth)? \[(\d+),(\d+)\]|between (\d+) and (\d+)", fact["statement"])
    if not m:
        return []
    lo = int(m.group(1) or m.group(3)); hi = int(m.group(2) or m.group(4))
    vals = [None, "", " ", "a" * lo, "a" * hi, "a" * (hi + 1), "红包", "🧧", "a'b--", "<b>x</b>", "a b", "  a  "]
    return [v for v in vals if v is None or lo <= len(v) <= hi or len(v) in (lo - 1, hi + 1)]


def _topic_field(topic):
    return topic.split(":", 1)[-1].split(".")[-1]


def _dedupe_cases(cases):
    uniq, seen = [], set()
    for c in cases:
        if c["id"] not in seen:
            seen.add(c["id"])
            uniq.append(c)
    return uniq


def _fact_prefix(facts, prefix):
    return [x for x in facts.f if x["topic"].startswith(prefix)]


def _stmt_code(statement, default=None):
    m = re.search(r"(?:返回|=)\s*(\d{3})\b", statement)
    return int(m.group(1)) if m else default


def plan_cases(facts, base_input, method="POST", path="/", overrides=None, ok_status=201, bad_status=400):
    """从事实生成可执行 case。预期只允许来自约束事实或 overrides（overrides 必须挂事实来源）。"""
    overrides = overrides or {}
    def mk(inp, exp, cid, cat, prio, src):
        expected = {"http": exp}
        if exp >= 400:
            expected["db_no_write"] = True        # 负例禁写库（§21）；404 同样不许写
        return {"id": cid, "category": cat, "priority": prio, "source": src,
                "action": {"kind": "http", "method": method, "path": path, "body": inp},
                "expected": expected}
    cases = [mk(dict(base_input), ok_status, "P0-HAPPY", "BUSINESS", "P0", "F:canonical")]
    int_fields = set()
    for f in facts.f:
        field = _topic_field(f["topic"])
        if f["topic"].startswith("range:"):
            int_fields.add(field)
            for v in boundary_values(f):
                exp = overrides[field](v) if callable(overrides.get(field)) else \
                    (ok_status if _in_range(f, v) else bad_status)
                cases.append(mk({**base_input, field: v}, exp, f"P1-BND-{field}={v}", "BOUNDARY", "P1", f["id"]))
        if f["topic"].startswith("len:"):
            for i, v in enumerate(string_boundaries(f)):
                ok = v is not None and _len_ok(f, v) and v.strip() != ""     # §12 空格=必填负例
                exp = overrides[field](v) if callable(overrides.get(field)) else (ok_status if ok else bad_status)
                cases.append(mk({**base_input, field: v}, exp,
                                f"P1-STR-{field}-{i}-{len(v) if v is not None else 'null'}", "BOUNDARY", "P1", f["id"]))
        if f["topic"].startswith("required:"):
            for label in ("null", "missing", "empty", "blank"):
                inp = ({k: w for k, w in base_input.items() if k != field} if label == "missing"
                       else {**base_input, field: {"null": None, "empty": "", "blank": " "}[label]})
                cases.append(mk(inp, bad_status, f"P0-REQ-{field}-{label}", "NEGATIVE", "P0", f["id"]))
        if f["topic"].startswith("enum:"):
            allowed = [x.strip() for x in re.split(r",\s*", f["statement"].split(" in ", 1)[-1]) if x.strip()]
            if field in base_input and allowed:
                poison = next((x for x in ("__nope__", "99999", "not-in-enum") if x not in allowed), None)
                if poison is not None:
                    cases.append(mk({**base_input, field: poison}, bad_status,
                                    f"P1-ENUM-{field}", "NEGATIVE", "P1", f["id"]))
    poisons = (("str", "not-an-int"), ("bool", True), ("arr", []), ("obj", {"k": 1}))
    for field in sorted(int_fields):
        if field not in base_input:
            continue
        src = next((x["id"] for x in facts.f if x["topic"].startswith("range:") and _topic_field(x["topic"]) == field),
                   "F:range")
        for tag, val in poisons:
            cases.append(mk({**base_input, field: val}, bad_status,
                            f"P1-TYP-{field}-{tag}", "NEGATIVE", "P1", src))
    return _dedupe_cases(cases)


def red_packet_hooks(seed_fn):
    """红包演示 SUT 的风险规划钩子。无钩子时 plan_risk_cases 只发不需要 fixture 的 P0。"""
    return {
        "seed": seed_fn,
        "grant_path": "/red-packets/{id}/grant",
        "action_path": "/red-packets/{id}/{action}",
        "tick_path": "/scheduler/tick",
        "table": "red_packet",
        "log_table": "grant_log",
        "relation_field": "influencer_id",
        "idempotency_field": "idem_key",
        "status": {"created": 0, "running": 1, "paused": 2, "finished": 3},
    }


def _relation_field_name(rel_fact, hooks):
    if hooks.get("relation_field"):
        return hooks["relation_field"]
    tail = rel_fact["topic"].split(":", 1)[-1]
    if tail.endswith("_id"):
        return tail
    if tail in ("fk", "relation"):
        m = re.search(r"(\w+_id)", rel_fact["statement"])
        return m.group(1) if m else None
    return f"{tail}_id"


def _idempotency_field_name(facts, hooks):
    if hooks.get("idempotency_field"):
        return hooks["idempotency_field"]
    for f in _fact_prefix(facts, "idempotency:"):
        parts = f["topic"].split(":")
        if len(parts) >= 3 and parts[2]:
            return parts[2]
        low = f["statement"].lower()
        for key in ("idem_key", "request_id", "idempotency_key", "client_request_id"):
            if key in low:
                return key
    return None


def facts_for_operation(facts, operation_id=None, path=None, method=None):
    """按 operation 过滤事实；components 全局 schema 在指定 operation 时不参与规划。"""
    if not operation_id and not path:
        return facts
    scoped = []
    for x in facts.f:
        if x.get("scope") == "components":
            continue
        op = x.get("operation_id")
        if op is not None:
            if operation_id and op == operation_id:
                scoped.append(x)
            elif path and x.get("http_path") == path:
                scoped.append(x)
            continue
        scoped.append(x)
    out = Facts(items=scoped, questions=facts.questions)
    out.unparsed = list(facts.unparsed)
    return out


def plan_risk_cases(facts, base_input, method="POST", path="/", ok_status=201, bad_status=400,
                    run_id="x", hooks=None):
    """从业务事实（幂等/关系/调度/状态机/依赖/时间）展开 P0。缺事实不编造；缺 hooks 则跳过需 fixture 的 case。"""
    hooks = hooks or {}
    seed, table = hooks.get("seed"), hooks.get("table")
    log_table = hooks.get("log_table")
    grant_p = hooks.get("grant_path")
    action_p = hooks.get("action_path")
    tick_p = hooks.get("tick_path")
    stmap = hooks.get("status") or {}
    cases = []

    def gpath(i):
        return grant_p.replace("{id}", str(i))

    rel = _fact_prefix(facts, "relation:") or _fact_prefix(facts, "fk:")
    if rel:
        src = rel[0]["id"]
        miss = _stmt_code(rel[0]["statement"], 404)
        rel_field = _relation_field_name(rel[0], hooks)
        if rel_field:
            miss_val = hooks.get("relation_missing_value", 999)
            cases.append({"id": "P0-REL-missing", "category": "RELATION", "priority": "P0", "source": src,
                          "action": {"kind": "http", "method": method, "path": path,
                                     "body": {**base_input, rel_field: miss_val}},
                          "expected": {"http": miss, "db_no_write": True}})
            for f in _fact_prefix(facts, "seed:"):
                for num, label in re.findall(r"(\d+)=([^\s]+)", f["statement"]):
                    n = int(num)
                    if "禁用" in label or "已删" in label:
                        cid = "P0-REL-disabled" if "禁用" in label else "P0-REL-deleted"
                        cases.append({"id": cid, "category": "RELATION", "priority": "P0", "source": f["id"],
                                      "action": {"kind": "http", "method": method, "path": path,
                                                 "body": {**base_input, rel_field: n}},
                                      "expected": {"http": miss, "db_no_write": True}})
                    elif "他公司" in label or "carol" in label.lower():
                        cases.append({"id": "P0-REL-carol", "category": "RELATION", "priority": "P0", "source": f["id"],
                                      "action": {"kind": "http", "method": method, "path": path,
                                                 "body": {**base_input, rel_field: n}},
                                      "expected": {"http": ok_status}})

    for f in _fact_prefix(facts, "idempotency:"):
        replay = _stmt_code(f["statement"], 200)
        uniq_col = _idempotency_field_name(facts, hooks)
        if not uniq_col:
            continue
        key = f"idem-{run_id}"
        body = {**base_input, uniq_col: key}
        dbc = []
        if table:
            dbc = [{"sql": f"SELECT COUNT(*) FROM {table} WHERE {uniq_col}='{key}'", "expect": 1}]
        cases.append({"id": "P0-IDEM-replay", "category": "IDEMPOTENCY", "priority": "P0", "source": f["id"],
                      "action": {"kind": "sequence", "steps": [
                          {"path": path, "method": method, "body": body, "expect_status": ok_status},
                          {"path": path, "method": method, "body": body, "expect_status": replay}]},
                      "expected": {"db_count": dbc} if dbc else {}})
        ckey = f"conc-{run_id}"
        cbody = {**base_input, uniq_col: ckey}
        cexp = {"success_count": 8}
        if table:
            cexp["db_count"] = [{"sql": f"SELECT COUNT(*) FROM {table} WHERE {uniq_col}='{ckey}'", "expect": 1}]
        cases.append({"id": "P0-CONC-create", "category": "CONCURRENCY", "priority": "P0", "source": f["id"],
                      "action": {"kind": "concurrent", "count": 8, "method": method, "path": path, "body": cbody},
                      "expected": cexp})

    if seed and grant_p and table:
        for f in _fact_prefix(facts, "scheduler:"):
            running = stmap.get("running", 1)
            paused = stmap.get("paused", 2)
            cases += [
                {"id": "P0-SCH-notdue", "category": "SCHEDULER", "priority": "P1", "source": f["id"],
                 "setup": seed(9030, running, 5, sched=3000000000),
                 "action": {"kind": "http", "method": "POST", "path": tick_p,
                            "headers": {"X-Test-Now": "2999999999"}},
                 "expected": {"http": 200, "json": {"dispatched": 0},
                              "db_count": [{"sql": f"SELECT COUNT(*) FROM {log_table} WHERE packet_id=9030", "expect": 0}]}},
                {"id": "P0-SCH-due", "category": "SCHEDULER", "priority": "P0", "source": f["id"],
                 "setup": seed(9031, running, 5, sched=3000000000),
                 "action": {"kind": "http", "method": "POST", "path": tick_p,
                            "headers": {"X-Test-Now": "3000000000"}},
                 "expected": {"http": 200, "db_count": [
                     {"sql": f"SELECT COUNT(*) FROM {log_table} WHERE packet_id=9031", "expect": 1},
                     {"sql": f"SELECT dispatched FROM {table} WHERE id=9031", "expect": 1}]}},
                {"id": "P0-SCH-double-tick", "category": "SCHEDULER", "priority": "P0", "source": f["id"],
                 "setup": seed(9032, running, 5, sched=3000000000),
                 "action": {"kind": "sequence", "steps": [
                     {"path": tick_p, "expect_status": 200, "headers": {"X-Test-Now": "3000000000"}},
                     {"path": tick_p, "expect_status": 200, "headers": {"X-Test-Now": "3000000000"}}]},
                 "expected": {"db_count": [{"sql": f"SELECT COUNT(*) FROM {log_table} WHERE packet_id=9032", "expect": 1}]}},
                {"id": "P0-SCH-paused-nothing", "category": "SCHEDULER", "priority": "P1", "source": f["id"],
                 "setup": seed(9033, paused, 5, sched=3000000000),
                 "action": {"kind": "http", "method": "POST", "path": tick_p,
                            "headers": {"X-Test-Now": "3000000001"}},
                 "expected": {"http": 200,
                              "db_count": [{"sql": f"SELECT COUNT(*) FROM {log_table} WHERE packet_id=9033", "expect": 0}]}},
            ]
        for f in _fact_prefix(facts, "time:"):
            cases += [
                {"id": "P0-TIME-leapday-at", "category": "TIME", "priority": "P1", "source": f["id"],
                 "setup": seed(9040, stmap.get("running", 1), 5, expiry=1709251199),
                 "action": {"kind": "http", "method": "POST", "path": gpath(9040),
                            "headers": {"X-Test-Now": "1709251199"}},
                 "expected": {"http": 200, "json": {"granted": True}}},
                {"id": "P0-TIME-leapday-next", "category": "TIME", "priority": "P1", "source": f["id"],
                 "setup": seed(9041, stmap.get("running", 1), 5, expiry=1709251199),
                 "action": {"kind": "http", "method": "POST", "path": gpath(9041),
                            "headers": {"X-Test-Now": "1709251200"}},
                 "expected": {"http": 409}},
                {"id": "P0-TIME-before", "category": "TIME", "priority": "P0", "source": f["id"],
                 "setup": seed(9010, stmap.get("running", 1), 5, expiry=2000000000),
                 "action": {"kind": "http", "method": "POST", "path": gpath(9010),
                            "headers": {"X-Test-Now": "1999999999"}},
                 "expected": {"http": 200, "json": {"granted": True}}},
                {"id": "P0-TIME-at", "category": "TIME", "priority": "P0", "source": f["id"],
                 "setup": seed(9011, stmap.get("running", 1), 5, expiry=2000000000),
                 "action": {"kind": "http", "method": "POST", "path": gpath(9011),
                            "headers": {"X-Test-Now": "2000000000"}},
                 "expected": {"http": 200, "json": {"granted": True}}},
                {"id": "P0-TIME-after", "category": "TIME", "priority": "P0", "source": f["id"],
                 "setup": seed(9012, stmap.get("running", 1), 5, expiry=2000000000),
                 "action": {"kind": "http", "method": "POST", "path": gpath(9012),
                            "headers": {"X-Test-Now": "2000000001"}},
                 "expected": {"http": 409}},
            ]
        for f in _fact_prefix(facts, "state:grant"):
            cases.append({"id": "P0-CONC-grant", "category": "CONCURRENCY", "priority": "P0", "source": f["id"],
                          "setup": seed(9001, stmap.get("running", 1), 5),
                          "action": {"kind": "concurrent", "count": 16, "path": gpath(9001)},
                          "expected": {"success_count": 5, "db_count": [
                              {"sql": f"SELECT COUNT(*) FROM {log_table} WHERE packet_id=9001", "expect": 5},
                              {"sql": f"SELECT remaining FROM {table} WHERE id=9001", "expect": 0}]}})
            cases.append({"id": "P0-ST-created-grant-blocked", "category": "STATE_MACHINE", "priority": "P0",
                          "source": f["id"], "setup": seed(9022, stmap.get("created", 0), 5),
                          "action": {"kind": "http", "method": "POST", "path": gpath(9022)},
                          "expected": {"http": 409}})
        dep_http = 502
        for i, f in enumerate(_fact_prefix(facts, "dependency:")):
            dep_http = _stmt_code(f["statement"], 502)
            for cid, mode, pk in (
                ("P0-DEP-status503", {"kind": "status", "code": 503}, 9002),
                ("P0-DEP-timeout", {"kind": "delay", "seconds": 5}, 9003),
                ("P0-DEP-refuse", "refuse", 9004),
                ("P0-DEP-badjson", "bad_json", 9005),
            ):
                cases.append({"id": cid, "category": "FAILURE_INJECTION", "priority": "P0", "source": f["id"],
                              "setup": seed(pk, stmap.get("running", 1), 5),
                              "action": {"kind": "fault", "mode": mode, "path": gpath(pk)},
                              "expected": {"http": dep_http,
                                           "db_count": [{"sql": f"SELECT COUNT(*) FROM {log_table} WHERE packet_id={pk}",
                                                         "expect": 0}]}})
            break
        for f in _fact_prefix(facts, "state:transitions"):
            if action_p:
                cases.append({"id": "P0-ST-leg-chain", "category": "STATE_MACHINE", "priority": "P0", "source": f["id"],
                              "action": {"kind": "sequence", "steps": [
                                  {"path": path, "body": dict(base_input), "expect_status": ok_status, "save": {"pid": "id"}},
                                  {"path": action_p.replace("{id}", "{{pid}}").replace("{action}", "start"), "expect_status": 200},
                                  {"path": action_p.replace("{id}", "{{pid}}").replace("{action}", "pause"), "expect_status": 200},
                                  {"path": action_p.replace("{id}", "{{pid}}").replace("{action}", "start"), "expect_status": 200},
                                  {"path": action_p.replace("{id}", "{{pid}}").replace("{action}", "finish"), "expect_status": 200}]},
                              "expected": {"db_rows": [{"table": table, "key": {"id": "{{pid}}"},
                                                        "subset": {"status": stmap.get("finished", 3)}}]}})
                cases.append({"id": "P0-ST-illegal-finish-start", "category": "STATE_MACHINE", "priority": "P0",
                              "source": f["id"], "setup": seed(9020, stmap.get("finished", 3), 5),
                              "action": {"kind": "http", "method": "POST",
                                         "path": action_p.replace("{id}", "9020").replace("{action}", "start")},
                              "expected": {"http": 409}})
                cases.append({"id": "P0-ST-illegal-paused-finish", "category": "STATE_MACHINE", "priority": "P0",
                              "source": f["id"], "setup": seed(9021, stmap.get("paused", 2), 5),
                              "action": {"kind": "http", "method": "POST",
                                         "path": action_p.replace("{id}", "9021").replace("{action}", "finish")},
                              "expected": {"http": 409}})
        for f in _fact_prefix(facts, "delete:"):
            if action_p:
                cases.append({"id": "P0-ST-delete-then-read", "category": "STATE_MACHINE", "priority": "P0",
                              "source": f["id"],
                              "action": {"kind": "sequence", "steps": [
                                  {"path": path, "body": dict(base_input), "expect_status": ok_status, "save": {"pid": "id"}},
                                  {"path": action_p.replace("{id}", "{{pid}}").replace("{action}", "delete"), "expect_status": 200},
                                  {"path": gpath("{{pid}}") if grant_p else action_p.replace("{id}", "{{pid}}").replace("{action}", "start"),
                                   "expect_status": 404}]},
                              "expected": {"db_rows": [{"table": table, "key": {"id": "{{pid}}"},
                                                        "subset": {"deleted": 1}}]}})
    return _dedupe_cases(cases)


def _in_range(fact, v):
    m = re.search(r"between (-?\d+) and (-?\d+)|\[(-?\d+)\s*,\s*(-?\d+|None)\]", fact["statement"])
    lo = int(m.group(1) or m.group(3)); hi = m.group(2) or m.group(4)
    return isinstance(v, int) and v >= lo and (hi == "None" or int(hi) >= v)


def _len_ok(fact, v):
    m = re.search(r"\[(\d+),(\d+)\]|between (\d+) and (\d+)", fact["statement"])
    return int(m.group(1) or m.group(3)) <= len(v) <= int(m.group(2) or m.group(4))


# ───────────────── 3.5 Static precheck（改码未部署窗口期的诚实预检） ─────────────────
# 定位：只对拍"DDL vs 写库语句"这类从代码+SQL 就能确定性推出的缺陷（列错位/非空/约束越界）。
# 红线：本节产出绝不进 Gate/results，不产生判定 —— final_gate 没有真实执行照样 NOT_TESTED。
# 静态抓不到的（并发超发/幂等窗口/keep-alive/故障注入行为）由执行引擎负责，这里不承诺。

NUMERIC_TYPE = re.compile(r"INT|DEC|NUM|FIXED|REAL|FLOA|DOUB|SERIAL|BIT|BOOL|YEAR", re.I)


def parse_ddl_tables(sql):
    """CREATE TABLE → {table: {"columns": {col: {type,not_null,has_default}}, "order": [col...]}}。
    只服务静态对拍；与 FactResolver 的事实抽取互不干扰。"""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"^\s*#.*$", " ", sql, flags=re.M)
    sql = re.sub(r"--[^\n]*", " ", sql)
    tables = {}
    for stmt in sql.split(";"):
        if not re.search(r"CREATE\s+TABLE", stmt, re.I):
            continue
        m = re.match(r"\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.`\[\]\"]+)\s*\((.*)\)\s*[^;]*$",
                     stmt, re.I | re.S)
        if not m:
            continue
        table = _col(m.group(1))
        cols, order = {}, []
        for part in _split_top(m.group(2)):
            p = re.sub(r"\s+", " ", part).strip().rstrip(",")
            if re.match(r"(?:CONSTRAINT\s+\S+\s+)?(?:FOREIGN\s+KEY|UNIQUE\b|PRIMARY\s+KEY|CHECK\b|"
                        r"(?:UNIQUE\s+)?(?:KEY|INDEX)\b)", p, re.I):
                continue
            cm = re.match(rf"({FactResolver.IDENT})\s+(\w+)", p)
            if not cm:
                continue
            rest = p[cm.end():]
            cols[_col(cm.group(1))] = {
                "type": cm.group(2).upper(),
                "not_null": bool(re.search(r"\bNOT\s+NULL\b", rest, re.I)),
                "has_default": bool(re.search(r"\bDEFAULT\b|AUTO_?INCREMENT|GENERATED", rest, re.I)),
            }
            order.append(_col(cm.group(1)))
        tables[table] = {"columns": cols, "order": order}
    return tables


def _top_groups(s):
    """s 中所有顶层括号组的内容（引号安全、按序扫描）。括号不配对返回已扫到的部分。"""
    out, i = [], 0
    while True:
        j = s.find("(", i)
        if j < 0:
            return out
        k = _match_paren(s, j)
        if k is None:
            return out
        out.append(s[j + 1:k])
        i = k + 1


def _cut_where(s):
    """砍掉 SET 子句后的 WHERE（引号/括号感知，WHERE 落在字符串里不算）。"""
    depth, q = 0, ""
    for i in range(len(s)):
        c = s[i]
        if q:
            if c == q and s[i - 1] != "\\":
                q = ""
            continue
        if c in "'\"":
            q = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and s[i:i + 5].upper() == "WHERE" and (i + 5 == len(s) or not s[i + 5].isalnum()):
            return s[:i]
    return s


def _lit(v):
    """字面量分类：(null|str|num|expr, 值)。表达式(NOW()、?占位)不检查，绝不误报。"""
    v = v.strip()
    if re.fullmatch(r"(?i)null", v):
        return ("null", None)
    if re.fullmatch(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", v):
        return ("str", v[1:-1].replace("''", "'").replace('\"\"', '"'))
    if re.fullmatch(r"[+-]?\d+", v):
        return ("num", int(v))
    if re.fullmatch(r"[+-]?\d*\.\d+", v):
        return ("num", float(v))
    return ("expr", v)


def _pf(rule, sev, table, source, stmt, msg):
    return {"rule": rule, "severity": sev, "table": table, "source": source, "message": msg,
            "statement": re.sub(r"\s+", " ", stmt).strip()[:160]}


def _check_value(col, val, table, cols, ranges, findings, source, stmt):
    """单个列=值对的三类确定性检查：非空、CHECK 范围、字符串进数值列。"""
    ddl = cols.get(col)
    kind, v = _lit(val)
    if kind == "null" and ddl and ddl["not_null"]:
        findings.append(_pf("not_null_violation", "error", table, source, stmt,
                            f"{table}.{col} 是 NOT NULL，却写入了 NULL"))
    if kind == "num" and ddl:
        for lo, hi in ranges.get(col.lower(), ()):
            if v < lo or (hi is not None and v > hi):
                findings.append(_pf("check_range_violation", "error", table, source, stmt,
                                    f"{table}.{col}={v} 越出 CHECK 范围 [{lo},{hi}]"))
    if ddl and kind == "str" and NUMERIC_TYPE.match(ddl["type"]):
        findings.append(_pf("type_mismatch", "warn", table, source, stmt,
                            f"{table}.{col} 是 {ddl['type']}，写入的是字符串字面量（部分驱动会静默转型）"))


def static_precheck(schema_sql, statements):
    """纯静态对拍：DDL vs INSERT/UPDATE 写库语句。返回 {findings, tables_checked, statements_checked}。
    findings 分 severity=error（确定性违反）/warn（可疑，需人确认）。这是前置情报，不是测试判定。"""
    tables = parse_ddl_tables(schema_sql)
    ranges = {}
    for x in FactResolver.from_schema_sql(schema_sql, "schema").f:
        if not x["topic"].startswith("range:"):
            continue
        m = re.search(r"between (-?\d+) and (-?\d+)|\[(-?\d+)\s*,\s*(-?\d+|None)\]", x["statement"])
        if not m:
            continue
        lo = int(m.group(1) or m.group(3))
        hi = m.group(2) if m.group(2) is not None else m.group(4)
        ranges.setdefault(x["topic"].split(":", 1)[1].split(".")[-1].lower(), []).append(
            (lo, None if hi in (None, "None") else int(hi)))
    findings = []
    checked = 0
    for item in statements or []:
        src = item.get("source", "?") if isinstance(item, dict) else "?"
        stmt = (item.get("sql") if isinstance(item, dict) else str(item)) or ""
        s = stmt.strip()
        if not s:
            continue
        checked += 1
        ins = re.match(r"INSERT\s+(?:OR\s+\w+\s+|IGNORE\s+)?INTO\s+([\w.`\[\]\"]+)\s*(.*)$", s, re.I | re.S)
        upd = re.match(r"UPDATE\s+([\w.`\[\]\"]+)\s+SET\s+(.*)$", s, re.I | re.S)
        if ins:
            table = _col(ins.group(1))
            t = tables.get(table)
            if t is None:
                findings.append(_pf("unknown_table", "warn", table, src, stmt,
                                    "INSERT 的表不在 DDL 中，无法对拍"))
                continue
            order, cols = t["order"], t["columns"]
            rest = ins.group(2).strip()
            named = None
            if rest.startswith("("):
                close = _match_paren(rest, 0)
                if close is None:
                    findings.append(_pf("unparsed_statement", "warn", table, src, stmt,
                                        "INSERT 括号不配对，未对拍"))
                    continue
                named = [_col(c) for c in _split_top(rest[1:close])]
                rest = rest[close + 1:].strip()
            vm = re.match(r"VALUES?\s*(.*)$", rest, re.I | re.S)
            if vm is None:
                findings.append(_pf("unparsed_statement", "warn", table, src, stmt,
                                    "INSERT ... SELECT 等形态未对拍（只支持 VALUES）"))
                continue
            unknown = [c for c in (named or []) if c not in cols]
            if unknown:
                findings.append(_pf("unknown_column", "error", table, src, stmt,
                                    f"DDL 中不存在的列: {unknown}"))
                continue
            tuples = _top_groups(vm.group(1))
            if not tuples:
                findings.append(_pf("unparsed_statement", "warn", table, src, stmt,
                                    "VALUES 元组未解析，未对拍"))
                continue
            for gi, g in enumerate(tuples):
                vals = _split_top(g)
                expect = named if named is not None else order
                if len(vals) != len(expect):
                    findings.append(_pf("insert_count_mismatch", "error", table, src, stmt,
                                        f"第{gi + 1}组 VALUES {len(vals)} 个值 vs 期望 {len(expect)} 列（列错位高危）"))
                    continue
                for c, val in zip(expect, vals):
                    _check_value(c, val, table, cols, ranges, findings, src, stmt)
            if named is not None:
                missing = [c for c in order if c not in named and cols[c]["not_null"] and not cols[c]["has_default"]]
                if missing:
                    findings.append(_pf("not_null_column_missing", "error", table, src, stmt,
                                        f"NOT NULL 且无默认值的列未被写入: {missing}"))
        elif upd:
            table = _col(upd.group(1))
            t = tables.get(table)
            if t is None:
                findings.append(_pf("unknown_table", "warn", table, src, stmt,
                                    "UPDATE 的表不在 DDL 中，无法对拍"))
                continue
            for a in _split_top(_cut_where(upd.group(2))):
                am = re.match(rf"({FactResolver.IDENT})\s*=\s*(.*)$", a.strip(), re.S)
                if am:
                    _check_value(_col(am.group(1)), am.group(2), table, t["columns"], ranges,
                                 findings, src, stmt)
        # 其余语句（SELECT/DELETE/DDL 本体）不检查
    return {"findings": findings, "tables_checked": sorted(tables), "statements_checked": checked}


# ───────────────────── 3.6 SQL 性能巡检（测试期对拍：慢 SQL + 等价更快写法） ─────────────────
# 定位：只读计时巡检 + 数据一致性对拍。发现慢 SQL；替代写法必须"结果集一致且更快"才许上报。
# 红线：与 static_precheck 同——advisory 不进 Gate/results，不改变业务判定。
# 慢 SQL 判定依赖数据量：test 库要 seed 贴近生产的量，空库上的"快"是假快。

READ_ONLY_SQL = re.compile(r"\s*(SELECT|WITH|EXPLAIN)\b", re.I)


def _fetch_capped(conn, sql, max_rows):
    """只取 max_rows+1 行：第 max_rows+1 行只用来标记截断，不参与比较。"""
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchmany(max_rows + 1)
    cur.close()
    return cols, rows, len(rows) > max_rows


def _row_keys(cols, rows):
    """多重集合比较：无 ORDER BY 时行序无意义，排序后再比。"""
    return sorted(json.dumps(dict(zip(cols, r)), sort_keys=True, default=str) for r in rows)


def sql_perf_check(dbc, queries, threshold_ms=1000, runs=1, max_rows=1000):
    """dbc: DBCheck。queries: [{id, sql, source?, slow_threshold_ms?, compare_sql?}]。
    计时取 runs 次的 min（对比写法的公平口径）。只读强制：SELECT/WITH/EXPLAIN 之外一律拒绝。
    compare_sql 是候选更快写法：结果集一致且实测更快 → faster_equivalent；不一致 → data_mismatch 禁止替换。"""
    conn = dbc.conn
    is_mysql = "pymysql" in type(conn).__module__.lower()
    findings, reports = [], []
    for q in queries or []:
        qid, sql = q.get("id", "Q"), q.get("sql", "")
        src = q.get("source", "?")
        thr = float(q.get("slow_threshold_ms", threshold_ms))
        rep = {"id": qid, "source": src, "sql": sql[:300], "threshold_ms": thr}
        if not READ_ONLY_SQL.match(sql):
            findings.append({"rule": "not_read_only", "severity": "error", "id": qid, "source": src,
                             "message": "巡检只允许 SELECT/WITH/EXPLAIN，禁写库", "sql": sql[:200]})
            reports.append(rep)
            continue
        try:
            times = []
            cols = rows = None
            trunc = False
            for _ in range(max(1, int(runs))):
                t0 = time.perf_counter()
                cols, rows, trunc = _fetch_capped(conn, sql, max_rows)
                times.append((time.perf_counter() - t0) * 1000)
            base_ms = min(times)
            rep.update({"ms": round(base_ms, 3), "runs": len(times),
                        "rows": min(len(rows), max_rows), "rows_truncated": trunc, "slow": base_ms > thr})
            if rep["slow"]:
                findings.append({"rule": "slow_sql", "severity": "warn", "id": qid, "source": src,
                                 "message": f"{qid} 耗时 {base_ms:.0f}ms > 阈值 {thr:.0f}ms"
                                            f"（{rep['rows']} 行{'，超窗口截断' if trunc else ''}）", "sql": sql[:200]})
                try:                       # 执行计划只进证据文件，不解析不判读
                    cur = conn.cursor()
                    cur.execute(("EXPLAIN " if is_mysql else "EXPLAIN QUERY PLAN ") + sql)
                    rep["explain"] = [str(r) for r in cur.fetchall()[:20]]
                    cur.close()
                except Exception:
                    pass
        except Exception as e:
            findings.append({"rule": "query_error", "severity": "error", "id": qid, "source": src,
                             "message": f"SQL 执行失败: {e!r}"[:300], "sql": sql[:200]})
            reports.append(rep)
            continue
        alt = q.get("compare_sql")
        if alt and READ_ONLY_SQL.match(alt):
            try:
                t0 = time.perf_counter()
                acols, arows, atrunc = _fetch_capped(conn, alt, max_rows)
                alt_ms = (time.perf_counter() - t0) * 1000
                same = _row_keys(cols, rows) == _row_keys(acols, arows)
                rep["compare"] = {"ms": round(alt_ms, 3), "rows": min(len(arows), max_rows),
                                  "same_result": same, "window": max_rows, "truncated": trunc or atrunc}
                if not same:
                    findings.append({"rule": "data_mismatch", "severity": "error", "id": qid, "source": src,
                                     "message": f"{qid} 替代写法结果集不一致（≤{max_rows} 行窗口内已见差异），禁止替换",
                                     "sql": alt[:200]})
                elif alt_ms < base_ms:
                    pct = round((1 - alt_ms / base_ms) * 100) if base_ms > 0 else 0
                    findings.append({"rule": "faster_equivalent", "severity": "warn", "id": qid, "source": src,
                                     "message": f"{qid} 存在等价更快写法: {base_ms:.0f}ms → {alt_ms:.0f}ms（−{pct}%），"
                                                f"结果集一致（{max_rows} 行窗口内；换写法前须业务确认）", "sql": alt[:200]})
            except Exception as e:
                findings.append({"rule": "query_error", "severity": "error", "id": qid, "source": src,
                                 "message": f"compare_sql 执行失败: {e!r}"[:300], "sql": alt[:200]})
        reports.append(rep)
    return {"findings": findings, "queries": reports, "threshold_ms": threshold_ms, "runs": runs}


# ───────────────────── 4. DB (§21-23) ─────────────────────

def open_db(spec):
    """spec: {"kind":"sqlite","path":...} | {"kind":"mysql","args":{...}} | None(API-only)"""
    if not spec or spec.get("kind") in (None, "none"):
        return None
    if spec["kind"] == "sqlite":
        c = sqlite3.connect(spec["path"], check_same_thread=False, timeout=15)
        c.row_factory = sqlite3.Row
        return c
    if spec["kind"] == "mysql":
        import pymysql
        return pymysql.connect(charset="utf8mb4", autocommit=True, **spec["args"])
    raise ValueError(f"db kind {spec['kind']} not supported (NOT_TESTED beats fake)")


def _placeholder(conn):
    """PEP249 各家实现不一致（sqlite3 只在模块级声明 qmark），按驱动模块判定最稳。"""
    if "pymysql" in type(conn).__module__.lower():
        return "%s"
    return "%s" if getattr(conn, "paramstyle", "qmark") == "format" else "?"


class DBCheck:
    def __init__(self, conn, tables=()):
        self.conn, self.tables = conn, list(tables)
        self.ph = _placeholder(conn)
        self.errors, self.truncated, self.count_errors = {}, set(), {}

    @staticmethod
    def quote(name):
        return f"`{name}`"                          # sqlite 与 mysql 都认反引号

    def rows(self, sql, args=()):
        cur = self.conn.cursor()
        cur.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def exec(self, sql, args=()):
        cur = self.conn.cursor()
        cur.execute(sql, args)
        self.conn.commit()
        return cur.rowcount

    def snapshot(self):
        """表读不出来 = 记录错误而不是返回空集：空快照会让所有 db_no_write 静默通过。"""
        self.errors, self.truncated = {}, set()
        out = {}
        for t in self.tables:
            # ponytail: 每表快照上限500行；db_no_write 另用 counts() 的权威增量兜底
            try:
                r = self.rows(f"SELECT * FROM {self.quote(t)} ORDER BY 1 LIMIT 500")
                out[t] = _mask(r)
                if len(r) >= 500:
                    self.truncated.add(t)
            except Exception as e:
                out[t], self.errors[t] = [], repr(e)
        return out

    def counts(self):
        self.count_errors = {}
        out = {}
        for t in self.tables:
            try:
                out[t] = list(self.rows(f"SELECT COUNT(*) FROM {self.quote(t)}")[0].values())[0]
            except Exception as e:
                out[t], self.count_errors[t] = None, repr(e)
        return out

    @staticmethod
    def diff(before, after):
        d = {}
        for t in sorted(set(before) | set(after)):
            b = {json.dumps(x, sort_keys=True, default=str) for x in before.get(t, [])}
            a = {json.dumps(x, sort_keys=True, default=str) for x in after.get(t, [])}
            d[t] = {"added": [json.loads(x) for x in sorted(a - b)], "removed": [json.loads(x) for x in sorted(b - a)]}
        return d


def _mask(rows):
    for r in rows:
        for k in list(r):
            if re.search(r"secret|password|token", str(k), re.I):
                r[k] = "***"                          # §22 脱敏
    return rows


# ───────────────────── 5. Engine：通用 case 执行器 (§32) ─────────────────────

def _tpl(obj, ctx):
    if isinstance(obj, str):
        for k, v in ctx.items():
            obj = obj.replace("{{" + k + "}}", str(v))
        return obj
    if isinstance(obj, dict):
        return {k: _tpl(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_tpl(v, ctx) for v in obj]
    return obj


DB_ASSERTIONS = ("db_rows", "db_count", "db_no_write")
KV_ASSERTIONS = ("kv_rows", "kv_count", "kv_no_write")   # §29 增强：Redis/KV 副作用断言


def _kv_watch_keys(exp, kv):
    """KV 断言声明的监控 key 全集（显式 key + pattern）；kv_no_write 无显式 key 时全库扫。"""
    keys = []
    for spec in exp.get("kv_rows", []):
        if spec.get("key"):
            keys.append(spec["key"])
    for spec in exp.get("kv_count", []):
        if spec.get("key"):
            keys.append(spec["key"])
        elif spec.get("pattern"):
            keys += list(kv.keys(spec["pattern"]))
    if exp.get("kv_no_write") and not keys:
        return None                                   # None = 全库快照
    return sorted(set(keys)) or None


L3_REQUIRED_FIELDS = ("case_id", "layer", "preconditions", "steps", "assertions",
                      "evidence_requirements", "source")
L3_STORE_TYPES = {"db", "database", "mysql", "sqlite", "redis", "kv"}


def is_l3_case(case):
    """L3/brandHandle 用例才启用严格字段；旧 case 保持原有契约。"""
    return (str(case.get("layer", "")).upper() == "L3"
            or "brandHandle" in case or "brand_handle" in case
            or str(case.get("category", "")).lower() == "brandhandle")


def validate_governance_case_fields(case):
    """DEC-DT-003 治理字段：empty_is_valid / actor_matrix（可选，有则校验）。"""
    errors: list[str] = []
    if case.get("empty_is_valid") is True:
        src = case.get("empty_is_valid_source") or case.get("source") or ""
        if not str(src).strip():
            errors.append("empty_is_valid:true 须附 empty_is_valid_source 或 source")
    if "actor_matrix" in case and case["actor_matrix"] is not None:
        am = case["actor_matrix"]
        if isinstance(am, list):
            if not am:
                errors.append("actor_matrix 不能为空列表")
        elif isinstance(am, dict):
            if not am:
                errors.append("actor_matrix 不能为空对象")
        else:
            errors.append("actor_matrix 须为 list 或 dict")
    return errors


def validate_l3_case(case):
    """返回 L3 case 的 schema 错误列表；非 L3 case 返回空列表。"""
    gov = validate_governance_case_fields(case)
    if not is_l3_case(case):
        return gov
    errors = [f"missing:{k}" for k in L3_REQUIRED_FIELDS if k not in case]
    if "case_id" in case and (not isinstance(case["case_id"], str) or not case["case_id"].strip()):
        errors.append("case_id must be a non-empty string")
    if case.get("layer") != "L3":
        errors.append("layer must be L3")
    for key in ("preconditions", "steps", "assertions", "evidence_requirements"):
        if key in case and not isinstance(case[key], list):
            errors.append(f"{key} must be a list")
    if "source" in case and not isinstance(case["source"], str):
        errors.append("source must be a string")
    for key in ("preconditions", "steps", "assertions", "evidence_requirements"):
        if key in case and isinstance(case[key], list) and not case[key]:
            errors.append(f"{key} must not be empty")
    if "source" in case and not str(case.get("source", "")).strip():
        errors.append("source must not be empty")
    errors.extend(gov)
    return errors


def _l3_case_id(case):
    return case.get("case_id") or case.get("id")


def _walk_values(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k).lower(), v
            yield from _walk_values(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_values(v)
    elif isinstance(value, str):
        yield "", value.lower()


def _l3_store_declarations(case):
    found = set()
    for key, value in _walk_values(case.get("assertions", [])):
        token = str(value).lower() if key in ("type", "kind", "store", "backend") else key
        if token in L3_STORE_TYPES:
            found.add("redis" if token in ("redis", "kv") else "db")
    exp = case.get("expected", {})
    if any(exp.get(k) for k in DB_ASSERTIONS):
        found.add("db")
    if any(exp.get(k) for k in KV_ASSERTIONS):
        found.add("redis")
    return found


def _l3_unmet_preconditions(case):
    unmet = []
    for i, p in enumerate(case.get("preconditions", [])):
        if isinstance(p, dict):
            if p.get("satisfied") is False or str(p.get("status", "")).upper() in {
                    "BLOCKED", "UNAVAILABLE", "NOT_READY", "MISSING"}:
                unmet.append(p.get("id") or p.get("name") or f"precondition[{i}]")
        elif p is False or (isinstance(p, str) and p.strip().upper() in {
                "BLOCKED", "UNAVAILABLE", "NOT_READY", "MISSING"}):
            unmet.append(f"precondition[{i}]")
    return unmet


def executable_assertions(case, has_db, has_kv=False):
    """返回 (真正执行的断言, 因缺库无法执行的断言)。零可执行断言 = 这个 case 什么都没验，不许它变绿。"""
    a = case.get("action") or {}
    exp = case.get("expected") or {}
    kind = a.get("kind", "http")
    ran = []
    if kind in ("http", "fault"):
        if "http" in exp:
            ran.append("http")
        if kind == "http" and exp.get("json"):
            ran.append("json")
    elif kind == "sequence":
        ran += [f"step{i}.status" for i, s in enumerate(a.get("steps") or []) if s.get("expect_status") is not None]
        ran += [f"step{i}.checks" for i, s in enumerate(a.get("steps") or []) if s.get("checks")]
    elif kind == "concurrent":
        ran += [k for k in ("success_count", "max_success") if k in exp]
    db = [k for k in DB_ASSERTIONS if exp.get(k)]
    kv = [k for k in KV_ASSERTIONS if exp.get(k)]
    if has_db:
        ran += db
    else:
        ran += []
    untested = [] if has_db else [f"{k}(no db connection)" for k in db]
    if has_kv:
        ran += kv
    else:
        untested += [f"{k}(no kv connection)" for k in kv]
    return ran, untested


class Engine:
    """case: {id, category, priority, source, setup:[sql], action:{kind:http|sequence|concurrent|fault},
              expected:{http|json|success_count|max_success|db_rows|db_count|db_no_write}}
    setup/step 支持 {{var}} 模板（step.save 从响应取变量）。未执行的 case 永远不会变成 PASS。"""

    def __init__(self, base_url, evidence, db=None, proxy=None, default_headers=None, ledger=None, kv=None):
        self.base_url = base_url.rstrip("/")
        self.ev = evidence
        self.db = db                      # DBCheck or None（API-only）
        self.proxy = proxy                # FaultProxy or None
        self.default_headers = default_headers or {}   # 全局鉴权头（case 级可覆盖）
        self.ledger = ledger              # V10 §16 SeedLedger or None：setup 写操作登记
        self.kv = kv                      # §29 增强 KVCheck or None：Redis/KV 副作用断言（before/after diff）

    def http(self, method, path, body=None, headers=None, timeout=15):
        data = json.dumps(body).encode() if body is not None else None
        hdr = {"Content-Type": "application/json", "Connection": "close"}  # 禁 keep-alive，避免脏连接串案
        hdr.update(self.default_headers)
        hdr.update(headers or {})
        req = urllib.request.Request(self.base_url + path, method=method, data=data, headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, _safe_json(r.read())
        except urllib.error.HTTPError as e:
            return e.code, _safe_json(e.read())
        except Exception as e:
            return -1, {"error": repr(e)}

    def run(self, case, snapshot=True):
        case = dict(case)
        if is_l3_case(case):
            case.setdefault("id", case.get("case_id", "L3-INVALID"))
        case.setdefault("id", case.get("case_id", "case-unknown"))
        l3_errors = validate_l3_case(case)
        ctx, detail, ok, err = {}, {}, False, None
        before = after = diff = {}
        kv_before = kv_after = kv_diff = {}
        delta = None
        phase = "setup"
        if l3_errors:
            detail["l3_schema_errors"] = l3_errors
            cid = re.sub(r'[^A-Za-z0-9._=\\[\\]-]', "_", case["id"])
            self.ev.write(f"cases/{cid}/request.json", case)
            self.ev.write(f"cases/{cid}/response.json", detail)
            r = {"id": case["id"], "case_id": case.get("case_id", case["id"]),
                 "layer": case.get("layer"), "source": case.get("source"),
                 "priority": case.get("priority", "P1"), "status": "NOT_TESTED",
                 "store_declarations": sorted(_l3_store_declarations(case)),
                 "evidence": f"cases/{cid}/", "evidence_refs": [f"cases/{cid}/request.json",
                 f"cases/{cid}/response.json"], "assertions": [], "detail": detail}
            self.ev.write(f"cases/{cid}/result.json", r)
            return r
        take = bool(self.db) and snapshot
        kv_take = bool(self.kv) and snapshot and any(
            k in case.get("expected", {}) for k in KV_ASSERTIONS)
        try:
            for sql in case.get("setup", []):
                if self.ledger is not None and self.db is not None:
                    self.ledger.record_exec(self.db, sql, case_id=case["id"],
                                            dataset_id=case.get("dataset_id", ""),
                                            reason=case.get("reason", "case setup"))
                else:
                    self.db.exec(sql)                      # fixture 先于快照：不算入被测写操作
            phase = "action"
            before, bc = (self.db.snapshot(), self.db.counts()) if take else ({}, {})
            if kv_take:
                kkeys = _kv_watch_keys(case.get("expected", {}), self.kv)
                kv_before = self.kv.snapshot(kkeys)
            ok, detail = self._dispatch(case, ctx, detail)
            phase = "db_check"
            if kv_take:
                kv_after = self.kv.snapshot(kkeys)
                kv_diff = type(self.kv).diff(kv_before, kv_after)
                detail["kv_diff_summary"] = {"added": len(kv_diff["added"]),
                                             "removed": len(kv_diff["removed"]),
                                             "changed": len(kv_diff["changed"])}
            if take:
                after, ac = self.db.snapshot(), self.db.counts()
                diff = DBCheck.diff(before, after)
                delta = {t: (None if bc.get(t) is None or ac.get(t) is None
                             else ac[t] - bc[t]) for t in diff}
                detail["db_diff_summary"] = {t: {"added": len(v["added"]), "removed": len(v["removed"])}
                                             for t, v in diff.items()}
                if self.db.errors or self.db.count_errors:
                    detail["db_snapshot_errors"] = {**self.db.errors, **self.db.count_errors}
                if self.db.truncated:
                    detail["db_snapshot_window"] = sorted(self.db.truncated)
            elif self.db:
                detail["db_snapshot"] = "skipped (parallel-safe: case asserts nothing on db)"
            if ok:
                ok, detail = self._assert_db(_tpl(case.get("expected", {}), ctx), after, diff, detail, delta)
            if ok and kv_take:
                ok, detail = self._assert_kv(_tpl(case.get("expected", {}), ctx), kv_diff, detail)
        except Exception as e:
            ok, err = False, {"phase": phase, "error": repr(e)}
        if ok is None:                    # fault 无 proxy → 诚实标注，不算假通过
            return {"id": case["id"], "priority": case.get("priority", "P1"),
                    "status": "SKIPPED_WITH_REASON", "evidence": "no fault proxy configured", "detail": detail}
        ran, untested = executable_assertions(case, self.db is not None, self.kv is not None)
        if untested:
            detail["untested_assertions"] = untested
        if err:
            status, detail["exception"] = "TOOL_ERROR", err        # 我们的管线坏了 ≠ SUT 坏了，也不许冒充通过
        elif is_l3_case(case) and _l3_unmet_preconditions(case):
            status = "BLOCKED"
            detail["unmet_preconditions"] = _l3_unmet_preconditions(case)
        elif take is False and any(k in case.get("expected", {}) for k in DB_ASSERTIONS):
            status = "NOT_TESTED"                                  # 跳过快照的批不该带库断言，带了就认栽
            detail["db_snapshot_required"] = True
        elif not kv_take and any(k in case.get("expected", {}) for k in KV_ASSERTIONS):
            status = "NOT_TESTED"                                  # 没接 KV / 跳过快照，却带 KV 断言
            detail["kv_snapshot_required"] = True
        elif take and (self.db.errors or self.db.count_errors) and any(k in case.get("expected", {}) for k in DB_ASSERTIONS):
            status = "TOOL_ERROR"                                  # 表读不出来就没资格谈 db 断言
        elif not ok:
            status = "FAIL"
        elif not ran or detail.get("unverifiable"):
            status = "NOT_TESTED"                                  # 零断言 / 断言根本没法验证
            detail.setdefault("no_executable_assertion", not ran)
        else:
            status = "PASS"
        cid = re.sub(r'[^A-Za-z0-9._=\[\]-]', "_", case["id"])
        self.ev.write(f"cases/{cid}/request.json", case)
        self.ev.write(f"cases/{cid}/response.json", detail)
        if take:
            self.ev.write(f"cases/{cid}/db-before.json", before)
            self.ev.write(f"cases/{cid}/db-after.json", after)
            self.ev.write(f"cases/{cid}/db-diff.json", diff)
        if kv_take:
            self.ev.write(f"cases/{cid}/kv-before.json", kv_before)
            self.ev.write(f"cases/{cid}/kv-after.json", kv_after)
            self.ev.write(f"cases/{cid}/kv-diff.json", kv_diff)
        evidence_refs = [f"cases/{cid}/request.json", f"cases/{cid}/response.json"]
        if take:
            evidence_refs += [f"cases/{cid}/db-before.json", f"cases/{cid}/db-after.json",
                              f"cases/{cid}/db-diff.json"]
        if kv_take:
            evidence_refs += [f"cases/{cid}/kv-before.json", f"cases/{cid}/kv-after.json",
                              f"cases/{cid}/kv-diff.json"]
        if is_l3_case(case) and status == "PASS":
            strict_errors = []
            strict_blocked = False
            if not str(case.get("source", "")).strip():
                strict_errors.append("source required")
            if not case.get("assertions") or not ran:
                strict_errors.append("assertions must be declared and executed")
            if not detail.get("http") and not any(k.startswith("step") for k in detail):
                strict_errors.append("request/response evidence required")
            if not evidence_refs or not all(os.path.exists(os.path.join(self.ev.dir, x)) for x in evidence_refs):
                strict_errors.append("traceable evidence refs required")
            for store in _l3_store_declarations(case):
                if store == "db":
                    if not self.db:
                        strict_blocked = True
                        strict_errors.append("DB declaration requires connection, assertion, and diff evidence")
                    elif not take or not any(case.get("expected", {}).get(k) for k in DB_ASSERTIONS):
                        strict_errors.append("DB declaration requires connection, assertion, and diff evidence")
                    elif not all(os.path.exists(os.path.join(self.ev.dir, f"cases/{cid}/{x}"))
                                 for x in ("db-before.json", "db-after.json", "db-diff.json")):
                        strict_errors.append("DB diff evidence missing")
                if store == "redis":
                    if not self.kv:
                        strict_blocked = True
                        strict_errors.append("Redis declaration requires connection, assertion, and diff evidence")
                    elif not kv_take or not any(case.get("expected", {}).get(k) for k in KV_ASSERTIONS):
                        strict_errors.append("Redis declaration requires connection, assertion, and diff evidence")
                    elif not all(os.path.exists(os.path.join(self.ev.dir, f"cases/{cid}/{x}"))
                                 for x in ("kv-before.json", "kv-after.json", "kv-diff.json")):
                        strict_errors.append("Redis diff evidence missing")
            required = case.get("evidence_requirements", [])
            aliases = {"request": "request.json", "response": "response.json", "db": "db-diff.json",
                       "database": "db-diff.json", "redis": "kv-diff.json", "kv": "kv-diff.json"}
            missing = []
            for req in required:
                name = req if isinstance(req, str) else req.get("path") or req.get("kind")
                rel = aliases.get(str(name).lower(), name)
                if not rel or not os.path.exists(os.path.join(self.ev.dir, f"cases/{cid}/{rel}")):
                    missing.append(name)
            if missing:
                strict_errors.append(f"evidence requirements missing: {missing}")
            if strict_errors:
                status = "BLOCKED" if strict_blocked else "NOT_TESTED"
                detail["l3_gate_errors"] = strict_errors
        r = {"id": case["id"], "case_id": case.get("case_id", case["id"]),
             "layer": case.get("layer"), "source": case.get("source"),
             "priority": case.get("priority", "P1"), "status": status,
             "store_declarations": sorted(_l3_store_declarations(case)) if is_l3_case(case) else [],
             "evidence": f"cases/{cid}/", "evidence_refs": evidence_refs,
             "assertions": ran, "detail": detail}
        self.ev.write(f"cases/{cid}/result.json", r)
        return r

    def _dispatch(self, case, ctx, detail):
        a = case["action"]
        kind = a["kind"]
        exp = case.get("expected", {})
        if kind == "http":
            st, body = self.http(a.get("method", "POST"), _tpl(a["path"], ctx), a.get("body"),
                                 {**self.default_headers, **(a.get("headers") or {})})
            for var, src in a.get("save", {}).items():
                ctx[var] = body.get(src) if isinstance(body, dict) else None
            detail.update({"http": st, "body": body})
            if "http" in exp and 400 <= exp["http"] < 500 and st >= 500:
                detail["server_error_on_client_expect"] = True   # 契约负例漏成 5xx：仍 FAIL，标出来方便修 SUT
            ok = st == exp["http"] if "http" in exp else True
            if ok and exp.get("json"):
                ok = _subset(exp["json"], body)
                if not ok:
                    detail["json_mismatch"] = {"want": exp["json"], "got": body}
            return ok, detail
        if kind == "sequence":
            unasserted = 0
            for i, step in enumerate(a["steps"]):
                st, body = self.http(step.get("method", "POST"), _tpl(step["path"], ctx),
                                     _tpl(step.get("body"), ctx),
                                     {**self.default_headers, **(_tpl(step.get("headers"), ctx) or {})})
                for var, src in step.get("save", {}).items():
                    ctx[var] = body.get(src) if isinstance(body, dict) else None
                if step.get("save_body"):
                    ctx[step["save_body"]] = body
                detail[f"step{i}"] = (st, body)
                if step.get("expect_status") is None and not step.get("checks"):
                    unasserted += 1
                    continue
                if step.get("expect_status") is not None and st != step["expect_status"]:
                    return False, detail
                if step.get("checks"):
                    from testmind.oracle import run_checks
                    okc, cinfo = run_checks(_tpl(step["checks"], ctx), body, ctx)
                    detail[f"step{i}.checks"] = cinfo
                    if not okc:
                        return False, detail
            if unasserted:
                detail["steps_unasserted"] = unasserted
            return True, detail
        if kind == "concurrent":
            import concurrent.futures
            n = a.get("count", 10)
            body, path = _tpl(a.get("body"), ctx), _tpl(a["path"], ctx)
            hdr = {**self.default_headers, **(a.get("headers") or {})}
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(n, 32)) as ex:
                rs = list(ex.map(lambda _: self.http(a.get("method", "POST"), path, body, hdr), range(n)))
            success = sum(1 for st, _ in rs if 200 <= st < 300)
            detail.update({"responses": [r[0] for r in rs], "success": success})
            if "success_count" in exp:
                return success == exp["success_count"], detail
            if exp.get("max_success") is not None:
                return success <= exp["max_success"], detail
            return True, detail
        if kind == "fault":
            if not self.proxy:
                return None, detail
            self.proxy.set_mode(a["mode"])
            try:
                st, body = self.http(a.get("method", "POST"), _tpl(a["path"], ctx), a.get("body"),
                                     {**self.default_headers, **(a.get("headers") or {})})
            finally:
                self.proxy.set_mode("ok")
            detail.update({"http": st, "body": body})
            return st == exp["http"] if "http" in exp else True, detail
        raise ValueError(f"unknown action kind {kind!r} (harness-side case error, not an SUT failure)")

    def _assert_db(self, exp, after, diff, detail, delta=None):
        if self.db is None:
            return True, detail                      # 缺库导致的未验断言由 executable_assertions 记 untested
        for spec in exp.get("db_rows", []):
            key = spec.get("key") or {}
            if not spec.get("table") or not key:
                return False, {**detail, "db_rows_spec_invalid": spec}
            rows = self.db.rows("SELECT * FROM {} WHERE {}".format(
                self.db.quote(spec["table"]),
                " AND ".join(f"{self.db.quote(k)}={self.db.ph}" for k in key)), tuple(key.values()))
            want = spec.get("subset")
            if not rows or (want and not any(_subset(want, r) for r in rows)):
                return False, {**detail, "db_row_missing": {**spec, "got": rows[:5]}}
            if not want:
                detail.setdefault("weak_assertions", []).append(f"db_rows:{spec['table']}(existence only)")
        for spec in exp.get("db_count", []):
            if "expect" not in spec:
                return False, {**detail, "db_count_spec_invalid": spec}
            got = self.db.rows(spec["sql"])
            v = list(got[0].values())[0] if got else None
            if v != spec["expect"]:
                return False, {**detail, "db_count": {"spec": spec, "got": v}}
        if exp.get("db_no_write"):
            grew = sorted({t for t, d in diff.items() if d["added"] or d["removed"]} |
                          {t for t, v in (delta or {}).items() if v})
            if grew:
                return False, {**detail, "db_write_on_negative": grew}
            blind = [t for t in diff if (delta or {}).get(t) is None or t in getattr(self.db, "truncated", set())]
            if blind:      # 计数拿不到 / 命中 500 行窗口 = 证不了"没写"，宁可 NOT_TESTED 也不许假绿
                return True, {**detail, "unverifiable": [f"db_no_write:{t}(row count not observable)" for t in blind]}
        return True, detail

    def _assert_kv(self, exp, kv_diff, detail):
        """§29 增强：KV 断言在 before/after diff 上判定（与 _assert_db 同构）。
        kv_rows: [{key, value|contains|absent}]；kv_count: [{pattern, expect}]；
        kv_no_write: true → 监控窗口内不许有任何 added/removed/changed。"""
        added, removed, changed = kv_diff["added"], kv_diff["removed"], kv_diff["changed"]
        for spec in exp.get("kv_rows", []):
            k = spec.get("key")
            if not k:
                return False, {**detail, "kv_rows_spec_invalid": spec}
            if spec.get("absent"):
                if self.kv.get(k) is not None:      # 现读为准：diff 只含变化 key，未变的存在要直接证伪
                    return False, {**detail, "kv_should_be_absent": {"key": k}}
                continue
            if k in removed:
                return False, {**detail, "kv_row_missing": {**spec, "note": "key 被删除"}}
            cur = changed.get(k, {}).get("after") if k in changed else added.get(k)
            if cur is None:
                cur = self.kv.get(k)           # 前态已存在且未变：现读一次证明值
            if "value" in spec and str(cur) != str(spec["value"]):
                return False, {**detail, "kv_value_mismatch": {"key": k, "want": spec["value"], "got": cur}}
            if "contains" in spec and (cur is None or str(spec["contains"]) not in str(cur)):
                return False, {**detail, "kv_value_mismatch": {"key": k, "want_contains": spec["contains"], "got": cur}}
            if "value" not in spec and "contains" not in spec and not spec.get("absent"):
                detail.setdefault("weak_assertions", []).append(f"kv_rows:{k}(existence only)")
        for spec in exp.get("kv_count", []):
            if "expect" not in spec or not spec.get("pattern"):
                return False, {**detail, "kv_count_spec_invalid": spec}
            got = len(self.kv.keys(spec["pattern"]))
            if got != spec["expect"]:
                return False, {**detail, "kv_count": {"spec": spec, "got": got}}
        if exp.get("kv_no_write"):
            grew = sorted(set(added) | set(removed) | set(changed))
            if grew:
                return False, {**detail, "kv_write_on_negative": grew}
            if getattr(self.kv, "truncated", False):   # 全库扫被截断 = 证不了"没写"，宁可 NOT_TESTED
                return True, {**detail, "unverifiable": ["kv_no_write(snapshot truncated)"]}
        return True, detail


def _subset(expected, actual):
    if not isinstance(actual, dict):
        return False
    return all(actual.get(k) == v for k, v in expected.items())


def _safe_json(b):
    try:
        return json.loads(b or b"null")
    except Exception:
        return {"_raw": (b or b"")[:500].decode("utf-8", "replace")}


# ───────────────────── 6. Evidence + Gate (§54, §56) ─────────────────────

class Evidence:
    def __init__(self, run_id=None):
        self.run_id = run_id or now() + "-" + uuid.uuid4().hex[:6]
        self.dir = os.path.join(REPORTS, self.run_id)
        os.makedirs(self.dir, exist_ok=True)

    def write(self, rel, obj):
        from testmind.secrets import redact_obj
        return self.text(rel, json.dumps(redact_obj(obj), ensure_ascii=False, indent=2, default=str))

    def text(self, rel, s):
        p = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(s)
        return p


class Gate:
    @staticmethod
    def evaluate(results, unknowns, conflicts, floor=(), runner_states=None, required_runners=(), plan=()):
        """地板：只要有一条 case 什么都没验，整体终判就不是 PASS。
        required_runners 中 SKIPPED/UNAVAILABLE → NOT_TESTED（不许假绿）。
        plan 用于逐条核对；缺 result 的计划 case 不得被已执行结果掩盖。"""
        runner_states = runner_states or {}
        for name in required_runners or ():
            st = runner_states.get(name, "NOT_RUN")
            if st in ("SKIPPED_WITH_REASON", "UNAVAILABLE", "NOT_RUN", "BLOCKED", "TOOL_ERROR"):
                return "NOT_TESTED", f"required runner {name} not satisfied: {st}"
        for r in results:
            assert r["status"] in LEGAL_STATUS, f"illegal status {r['status']}"
        if conflicts:
            return "BLOCKED", f"conflicts: {len(conflicts)}"
        if not results:
            return "NOT_TESTED", "no executed case"            # §4.2 没跑就是没跑
        result_ids = {r.get("case_id") or r.get("id") for r in results}
        planned_ids = {_l3_case_id(c) for c in (plan or []) if _l3_case_id(c)}
        missing_plan = sorted(planned_ids - result_ids)
        if missing_plan:
            return "NOT_TESTED", f"planned cases not executed: {missing_plan}"
        invalid_plan = [_l3_case_id(c) for c in (plan or []) if validate_l3_case(c)]
        if invalid_plan:
            return "NOT_TESTED", f"invalid L3 case schema: {invalid_plan}"
        no_ev = [r["id"] for r in results if r["status"] == "PASS" and not r.get("evidence")]
        if no_ev:
            return "FAIL", f"PASS without evidence: {no_ev}"   # §4.3
        strict_missing = []
        for r in results:
            if str(r.get("layer", "")).upper() != "L3" or r.get("status") != "PASS":
                continue
            refs = set(r.get("evidence_refs") or ())
            if not r.get("source") or not r.get("assertions"):
                strict_missing.append(r["id"] + ":source/assertions")
            if not any(x.endswith("request.json") for x in refs) or not any(x.endswith("response.json") for x in refs):
                strict_missing.append(r["id"] + ":request/response evidence")
            if "db" in (r.get("store_declarations") or ()) and not any(x.endswith("db-diff.json") for x in refs):
                strict_missing.append(r["id"] + ":DB diff evidence")
            if "redis" in (r.get("store_declarations") or ()) and not any(x.endswith("kv-diff.json") for x in refs):
                strict_missing.append(r["id"] + ":Redis diff evidence")
            if r.get("detail", {}).get("l3_gate_errors"):
                strict_missing.append(r["id"] + ":strict evidence gate")
        if strict_missing:
            return "NOT_TESTED", f"L3 evidence gate not satisfied: {strict_missing}"
        broken = [r["id"] for r in results if r["status"] == "TOOL_ERROR"]
        if broken:
            return "TOOL_ERROR", f"harness broke, verdict invalid: {broken}"
        fails = [r["id"] for r in results if r["status"] == "FAIL"]
        if fails:
            return "FAIL", f"failures: {fails}"
        if any(r["status"] == "BLOCKED" for r in results):
            return "BLOCKED", "blocked cases exist"
        nts = [r["id"] for r in results if r["status"] == "NOT_TESTED"]
        if nts:
            return "NOT_TESTED", f"no executable assertion: {nts}"   # 不再只管 P0
        missing = sorted(set(floor) - {r["id"] for r in results})
        if missing:
            return "NOT_TESTED", f"required but never executed: {missing}"
        pend = [q for q in unknowns if q.get("state", "USER_REQUIRED") == "USER_REQUIRED"]
        if pend:
            return "HOLD", f"USER_REQUIRED open: {len(pend)}"  # §35/§56
        return "PASS", "all gates satisfied"


def risk_floor(facts, hooks=None):
    """有事实就必须出现对应 case id。hooks 不足时只要求不需要 fixture 的 P0。"""
    need = []
    if _fact_prefix(facts, "idempotency:"):
        need += ["P0-IDEM-replay", "P0-CONC-create"]
    if _fact_prefix(facts, "relation:") or _fact_prefix(facts, "fk:"):
        need.append("P0-REL-missing")
    if hooks and hooks.get("seed"):
        if _fact_prefix(facts, "scheduler:"):
            need += ["P0-SCH-due", "P0-SCH-double-tick"]
        if _fact_prefix(facts, "state:grant"):
            need.append("P0-CONC-grant")
        if _fact_prefix(facts, "dependency:"):
            need.append("P0-DEP-refuse")
    return need


# ───────────────────── 7. Regression Registry (§27) ─────────────────────

def registry_path(consumer_root=None):
    if consumer_root:
        p = os.path.join(consumer_root, ".testmind", "regression", "cases.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p
    return REGISTRY


def load_registry(consumer_root=None, path=None):
    if path is None:
        path = registry_path(consumer_root)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return []


def add_regression_case(case, reason, consumer_root=None, path=None):
    if path is None:
        path = registry_path(consumer_root)
    cases = load_registry(consumer_root=None, path=path)
    prev = next((c for c in cases if c["id"] == case["id"]), None)
    # 幂等：同一 case 重复登记不刷新 added —— 否则每次跑 e2e 都会在 regression/cases.json 里
    # 制造"只有时间戳变了"的假脏 diff，污染工作区并干扰"变更即证据"的判读。
    case = {**case, "regression_reason": reason, "added": (prev or {}).get("added") or utc()}
    cases = [c for c in cases if c["id"] != case["id"]] + [case]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cases, fh, ensure_ascii=False, indent=2)
    return len(cases)


def run_regression(engine, consumer_root=None, path=None):
    if path is None:
        cases = load_registry(consumer_root)
    else:
        cases = load_registry(path=path)
    return [engine.run(c) for c in cases]


# ───────────────────── 8. Runner availability + Schemathesis + Karate + Docker ─────────────────────
# 本机 Docker 口径（E:\workA\本人电脑环境\AI-环境手册.md §2.8）：
#   CLI=C:\Docker\bin\docker\docker.exe，daemon=VirtualBox docker-vm 经 NAT 转发 tcp://127.0.0.1:2375，
#   VM 不随开机自启（探测失败先 VBoxManage startvm "docker-vm" --type headless）。

DOCKER_CLI = os.environ.get("TESTMIND_DOCKER_CLI", "")
DOCKER_HOST = os.environ.get("DOCKER_HOST", "tcp://127.0.0.1:2375")
JDK17 = os.environ.get("TESTMIND_JDK17", r"E:\jdk17\extracted\jdk-17.0.18+8\bin\java.exe")
KARATE_LIB = os.path.join(ROOT, "tools", "karate", "lib")

# 手册口径的 CLI 落点（可能装了但不在 PATH）：探测失败时按此兜底，
# 否则会出现"明明装了却报 docker cli not found"的误导性诊断（定位问题被带偏）。
DOCKER_CLI_CANDIDATES = (
    r"C:\Docker\bin\docker\docker.exe",
    r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
)


def resolve_docker_cli():
    """解析 docker CLI：env 显式配置 → PATH → 手册固定落点。
    探测与执行共用同一解析（与 resolve_schemathesis 同纪律），杜绝"报 not found 其实装了"。"""
    from shutil import which

    cli = (DOCKER_CLI or "").strip()
    if cli in ("false", "/bin/false"):
        return cli
    if cli:
        return cli if (os.path.isfile(cli) or cli == "docker") else "docker"
    found = which("docker")
    if found:
        return found
    for cand in DOCKER_CLI_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    return "docker"


def docker(*args, timeout=600):
    cli = resolve_docker_cli()
    if cli in ("false", "/bin/false"):
        return subprocess.CompletedProcess(args=cli, returncode=1, stdout="", stderr="docker disabled")
    env = {**os.environ, "DOCKER_HOST": DOCKER_HOST}
    try:
        return subprocess.run([cli, *args], capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args=cli, returncode=124, stdout="", stderr="docker timeout")
    except FileNotFoundError:
        # 明确区分「CLI 不存在」与「daemon 不可达」：127 + not found → SKIPPED_WITH_REASON 而非 BLOCKED
        return subprocess.CompletedProcess(args=cli, returncode=127, stdout="", stderr="docker cli not found")
    except OSError as e:   # 本机没装 docker（CI/macOS 常态）：fail-open，探测=UNAVAILABLE，绝不崩闭环
        return subprocess.CompletedProcess([cli, *args], returncode=1, stdout="", stderr=repr(e))


def java11plus():
    for j in (JDK17, "java"):
        try:
            p = subprocess.run(f'"{j}" -version', capture_output=True, timeout=20, shell=True)
            out = ((p.stderr or b"") + (p.stdout or b"")).decode("utf-8", "replace")
            m = re.search(r'version "(\d+)', out)
            if m and int(m.group(1)) >= 11:
                return j
        except Exception:
            continue
    return None


def docker_provision(image, name, host_port, container_port=None, env=None):
    """Testcontainers 语义（create→use→destroy）的 stdlib 实现：幂等起一次性测试容器。"""
    dv = docker("version", timeout=25)
    if dv.returncode != 0:
        reason = (dv.stderr or dv.stdout or "unavailable").strip()
        if "not found" in reason or dv.returncode == 127:
            return {"status": "SKIPPED_WITH_REASON", "reason": reason, "runner": "docker"}
        return {"status": "BLOCKED", "reason": f"docker daemon unreachable at {DOCKER_HOST}: {reason[-200:]}"}
    exist = docker("ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}")
    if name in (exist.stdout or ""):
        docker("rm", "-f", name)
    cp = container_port or host_port
    args = ["run", "-d", "--name", name, "-p", f"{host_port}:{cp}"]
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    r = docker(*(args + [image]), timeout=1800)
    if r.returncode != 0:
        return {"status": "BLOCKED", "reason": (r.stderr or r.stdout)[-300:], "image": image}
    return {"status": "AVAILABLE", "name": name, "host_port": host_port, "image": image}


def docker_release(name):
    docker("rm", "-f", name, timeout=60)


def run_karate(feature, evidence):
    """Karate adapter：真实场景 runner。tools/karate/lib 由 mvn 预取（pom.xml 在 tools/karate）。"""
    j = java11plus()
    have_jars = os.path.isdir(KARATE_LIB) and any(f.endswith(".jar") for f in os.listdir(KARATE_LIB))
    if not (j and have_jars):
        return {"status": "SKIPPED_WITH_REASON", "runner": "karate",
                "reason": f"java={'ok' if j else 'missing'} jars={'ok' if have_jars else 'missing'}"}
    cmd = f'"{j}" -cp "{KARATE_LIB}\\*" com.intuit.karate.Main "{feature}"'
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600, shell=True)
    except subprocess.TimeoutExpired:
        return {"status": "TOOL_ERROR", "runner": "karate", "reason": "timeout"}
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    evidence.text("karate-raw.log", out[-20000:])
    return {"status": "PASS" if r.returncode == 0 else "FAIL", "runner": "karate",
            "exit_code": r.returncode, "evidence": evidence.dir + "/karate-raw.log"}


SCHEMATHESIS_ENV = "TESTMIND_SCHEMATHESIS"


def resolve_schemathesis():
    """解析 schemathesis 的命令前缀（list）。**探测与执行共用同一解析**。

    以前 scan_runners 用 `py -3` 探测、run_schema_tests 却用一个写死的
    `C:\\Users\\...\\Python311\\Scripts\\schemathesis.exe` 执行 —— 探针说 AVAILABLE、
    执行却可能落在另一条路径/另一个解释器上，是"假绿"的温床（P1-02 / INV-001）。

    优先级：
      1. TESTMIND_SCHEMATHESIS —— 显式配置（指解释器 → `-m schemathesis`，指 exe → 直接跑）
      2. PATH 上的 schemathesis
      3. 当前解释器旁边的 console script（pip 装出的入口，最可靠）
      4. 当前解释器可 import → `-m schemathesis` / console entry point 函数
    全不可用 → None（诚实三态：UNAVAILABLE → SKIPPED_WITH_REASON）。本函数不含任何绝对路径。

    NOTE: schemathesis 4.x 删掉了 `__main__.py`（`python -m schemathesis` 直接
    ModuleNotFoundError），入口只在 console script `schemathesis`（entry point
    `schemathesis.cli:schemathesis`）。因此"当前解释器可 import"必须退到 entry point 函数，
    并优先解析解释器同级的 Scripts/bin，否则装了 schemathesis 的机器仍会被误判 UNAVAILABLE。
    """
    from shutil import which

    cands = []
    env = (os.environ.get(SCHEMATHESIS_ENV) or "").strip()
    if env:
        if os.path.basename(env).lower().startswith("python"):
            cands.append([env, "-m", "schemathesis"])
        else:
            cands.append([env])
    found = which("schemathesis")
    if found:
        cands.append([found])
    # 当前解释器同级的 console script：Windows <prefix>/Scripts/schemathesis.exe，
    # POSIX <prefix>/bin/schemathesis。用 sys.executable 推导，不写死绝对路径。
    bindir = "Scripts" if os.name == "nt" else "bin"
    ext = ".exe" if os.name == "nt" else ""
    sibling = os.path.join(os.path.dirname(sys.executable), bindir, "schemathesis" + ext)
    if os.path.exists(sibling):
        cands.append([sibling])
    # 退路：schemathesis 4.x 无 __main__，直接调 console entry point 函数
    # （argv 语义与 `-m` 一致：Click 读 sys.argv[1:]）。
    cands.append([sys.executable, "-c",
                  "from schemathesis.cli import schemathesis as _s; _s()"])

    for c in cands:
        try:
            r = subprocess.run(c + ["--version"], capture_output=True, timeout=60)
        except Exception:
            continue
        if r.returncode == 0:
            return c
    return None


def schemathesis_status():
    c = resolve_schemathesis()
    if not c:
        return f"UNAVAILABLE(no schemathesis for {os.path.basename(sys.executable)}; set {SCHEMATHESIS_ENV})"
    return "AVAILABLE(" + " ".join(c) + ")"


def scan_runners():
    def probe(cmd, note):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60, shell=True)
            return "AVAILABLE" if r.returncode == 0 else f"UNAVAILABLE({note})"
        except Exception as e:
            return f"UNAVAILABLE({e.__class__.__name__})"
    j = java11plus()
    jars = os.path.isdir(KARATE_LIB) and any(f.endswith(".jar") for f in os.listdir(KARATE_LIB))
    d = docker("version", timeout=25).returncode == 0
    return {
        "schemathesis": schemathesis_status(),
        "karate": "AVAILABLE(jdk17+tools/karate/lib)" if (j and jars) else
                  "UNAVAILABLE(" + ("no jars" if j else "no java11+ under E:/jdk17; jars pending") + ")",
        "testcontainers": f"AVAILABLE(docker@{DOCKER_HOST}) via docker_provision" if d
                          else f"UNAVAILABLE(daemon at {DOCKER_HOST} down — VBoxManage startvm docker-vm)",
        "jacoco": ("AVAILABLE(cli " + ("jdk17" if j else "?") + ", 需 Java SUT 才有被测对象)")
                  if j and jars and any("jacoco.cli" in f for f in os.listdir(KARATE_LIB)) else
                  "UNAVAILABLE(java11+ or cli jar missing)",
        "db_rider": "UNAVAILABLE(needs JUnit harness; DBCheck 已覆盖 §21 DB 对拍职能)",
        "wiremock": "UNAVAILABLE(no jar; FaultProxy 已原生覆盖 §24 故障矩阵，YAGNI 不重复接)",
        "keploy": "UNAVAILABLE(windows kernel not supported)",
        "evomaster": "UNAVAILABLE(需 Java SUT 做白盒被测对象)" if j else "UNAVAILABLE(java11+ missing)",
        "cats": probe("cats --version", "not installed"),
        "restler": probe("restler --version", "not installed"),
    }


def run_schema_tests(openapi_url, evidence, max_examples=30, path_params=None, runner_prefix=None):
    """Schemathesis 契约/负例测试。

    runner_prefix 可显式注入命令前缀（测试/CI 用），默认走 resolve_schemathesis()——
    与 scan_runners 同一解析，确保"探测怎么说、执行就怎么跑"。
    """
    prefix = runner_prefix if runner_prefix is not None else resolve_schemathesis()
    if not prefix:
        return {"status": "SKIPPED_WITH_REASON", "runner": "schemathesis",
                "reason": schemathesis_status()}
    params = path_params or {"id": 1, "action": "start"}
    cfg = os.path.join(evidence.dir, "schemathesis.toml")
    lines = ["headers = { Connection = \"close\" }", "", "[parameters]"]
    for k, v in params.items():
        lines.append(f'{k} = {v!r}' if isinstance(v, str) else f"{k} = {v}")
    evidence.text("schemathesis.toml", "\n".join(lines) + "\n")
    # list 形式（不经 shell）：跨平台、无盘符、无引号转义歧义（INV-001）
    cmd = [*prefix, "--config-file", cfg, "run", openapi_url,
           "--max-examples", str(max_examples), "--workers", "1",
           "--header", "Connection: close", "--generation-allow-x00", "false"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600, env=env)
    except subprocess.TimeoutExpired:
        return {"status": "TOOL_ERROR", "runner": "schemathesis", "reason": "timeout"}
    except OSError as e:   # 配置的 runner 不可执行：三态降级，绝不崩闭环
        return {"status": "SKIPPED_WITH_REASON", "runner": "schemathesis",
                "reason": f"runner not executable: {e}"}
    out = (r.stdout or b"").decode("utf-8", "replace")
    evidence.text("schema-tests-raw.log", out + "\n" + (r.stderr or b"").decode("utf-8", "replace"))
    return {"status": "PASS" if r.returncode == 0 else "FAIL", "runner": "schemathesis",
            "exit_code": r.returncode, "path_params": params,
            "evidence": evidence.text("schema-tests-summary.txt", out[-4000:])}
