# testmind/impact.py — ImpactMind（V10 §3/§4）
# 纯标准库启发式 Java 解析（[DEC-003]）：Controller/Service/Mapper 调用图、
# 变更符号定位、传递闭包、Regression Radius R0-R4、失败驱动扩展（§19）。
import os
import re
import subprocess

MAPPING_VERBS = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
                 "PatchMapping": "PATCH", "DeleteMapping": "DELETE", "RequestMapping": "ANY"}
SQL_MAPPINGS = {"Select": "READS", "Insert": "WRITES", "Update": "WRITES", "Delete": "WRITES"}
WRITE_VERBS = {"POST", "PUT", "PATCH", "DELETE"}
_SKIP_NAMES = {"if", "for", "while", "switch", "catch", "try", "else", "synchronized",
               "return", "new", "sout", "assert", "super", "init", "static"}

# V10 §29 增强：Java 高风险调用点模式（静态扫描，先于运行时碰运气）。
# 每条 = (id, regex, 说明)。命中点必须进 facts/rules，每个点至少 1 正 1 负 case。
# 默认清单来自真实整改案例：公司/租户解析传 null、绕过统一解析器直查视图。
DEFAULT_JAVA_PATTERNS = [
    ("null_company_resolution", r"resolveRequired\s*\([^)]*,\s*null\s*\)",
     "公司解析传 null：resolveRequired(userId, null) 绕过请求体 companyId，多公司用户必 500"),
    ("bypass_company_support", r"selectCompanyDeptIdByUserId\s*\(",
     "绕过 OceanCompanyDeptSupport 直查视图：无 companyId fallback，归属不一致检查被跳过"),
    ("hardcoded_null_context", r"fill\w*Flags?\s*\([^)]*,\s*null\s*\)",
     "折叠/填充路径硬编码 null 上下文：与主路径解析口径不一致"),
    ("literal_null_id", r"(companyDeptId|companyId)\s*=\s*null\b",
     "公司/部门 ID 被显式置 null 后继续下游调用"),
]


def _iter_java_files(root, max_files=800):
    n = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in (".git", "target", "build", ".gradle", "node_modules")]
        for fn in fns:
            if not fn.endswith(".java"):
                continue
            if n >= max_files:
                return
            n += 1
            yield os.path.join(dp, fn)


def scan_patterns(root, patterns=None, max_files=800):
    """§29 增强：按正则清单扫描 Java 源码，返回 [{pattern_id,message,file,line,code}]。
    注释/字符串已剔除（_blank_comments_strings），避免文档示例误报。"""
    pats = [(pid, re.compile(rx), msg) for pid, rx, msg in (patterns or DEFAULT_JAVA_PATTERNS)]
    hits = []
    for path in _iter_java_files(root, max_files):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except OSError:
            continue
        clean = _blank_comments_strings(raw)
        rel = os.path.relpath(path, root).replace("\\", "/")
        raw_lines = raw.splitlines()
        for pid, rx, msg in pats:
            for m in rx.finditer(clean):
                line = clean.count("\n", 0, m.start()) + 1
                hits.append({"pattern_id": pid, "message": msg, "file": rel, "line": line,
                             "code": (raw_lines[line - 1].strip() if line <= len(raw_lines) else "")[:200]})
    return hits


def _blank_comments_strings(text):
    """注释/字符串内容替换为空格（保留换行与偏移），结构解析用；原文仍用于注解参数提取。"""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n - 2 if j == -1 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c in "\"'":
            q, i = c, i + 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == q:
                    i += 1
                    break
                if out[i] != "\n":
                    out[i] = " "
                i += 1
        else:
            i += 1
    return "".join(out)


def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def _next_code(text, pos):
    while pos < len(text) and text[pos] in " \t\r\n":
        pos += 1
    return pos


def _camel_to_snake(name):
    s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    return re.sub(r"(?<=[A-Z])([A-Z][a-z])", r"_\1", s).lower()


def tables_from_sql(sql):
    out = []
    for m in re.finditer(r"\b(?:from|into|update|join)\s+[`\"]?([\w.]+)[`\"]?", sql, re.I):
        t = m.group(1).split(".")[-1].lower()
        if t and t not in out and not t.startswith("("):
            out.append(t)
    return out


_FIELD_DECL = re.compile(r"\b([A-Z]\w*)(?:<[^<>]*>)?\s+(\w+)\s*[;=(]")
_CALL = re.compile(r"\b(\w+)\s*\.\s*(\w+)\s*\(")
_ANN_STR = re.compile(r"@(Select|Insert|Update|Delete|SelectProvider)\s*\(\s*(?:value\s*=\s*)?\{?\s*\"((?:[^\"\\]|\\.)*)", re.S)
_MAPPING_ANN = re.compile(r"@(Get|Post|Put|Patch|Delete|Request)Mapping\s*(?:\(\s*(?:value\s*=\s*)?\"([^\"]*)\")?", re.I)


def parse_java(path, text):
    """解析单个 Java 文件 → {class, package, kind, fields, methods[], endpoints_meta}。"""
    s = _blank_comments_strings(text)
    cls_m = re.search(r"\b(class|interface)\s+(\w+)", s)
    if not cls_m:
        return None
    cname, kind = cls_m.group(2), cls_m.group(1)
    pkg = re.search(r"\bpackage\s+([\w.]+)\s*;", s)
    pkg = pkg.group(1) if pkg else ""
    body_open = s.find("{", cls_m.end())
    if body_open == -1:
        return None

    # 类头区域（class 声明到第一个成员之间）：字段注入 + 类级注解
    fields = {}
    for m in _FIELD_DECL.finditer(s[cls_m.end():body_open]):
        fields[m.group(2)] = m.group(1)
    # 构造器注入参数：SomeService(Foo foo, Bar bar)
    ctor = re.search(re.escape(cname) + r"\s*\(([^)]*)\)", s[body_open:])
    if ctor:
        for p in re.finditer(r"\b([A-Z]\w*)(?:<[^<>]*>)?\s+(\w+)", ctor.group(1)):
            fields[p.group(2)] = p.group(1)
    class_anns = s[:body_open]
    base_path = ""
    bm = re.search(r"@RequestMapping\s*(?:\(\s*(?:value\s*=\s*)?\"([^\"]*)\")?", text[:body_open])
    if bm:
        base_path = bm.group(1) or ""
    is_feign = "@FeignClient" in class_anns
    is_mapper = kind == "interface" and bool(re.search(r"@(Select|Insert|Update|Delete)\b", s[body_open:]))

    members = []
    depth, i = 1, body_open + 1
    header_start, cur = i, None
    while i < len(s):
        c = s[i]
        if c == "{":
            if depth == 1:
                cur = _member(s[header_start:i], cname)
                if cur:
                    cur["start_line"] = _line_of(s, header_start)
                    cur["open"] = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 1 and cur:
                cur["end_line"] = _line_of(s, i)
                cur["body"] = s[cur["open"] + 1:i]
                members.append(cur)
                cur = None
            header_start = _next_code(s, i + 1)
        elif c == ";" and depth == 1:
            seg = s[header_start:i]
            am = _member(seg, cname)
            if am:
                am["start_line"] = _line_of(s, header_start)
                am["end_line"] = _line_of(s, i)
                am["body"] = ""
                members.append(am)
            else:
                fm = _FIELD_DECL.search(_ANN_STRIP.sub(" ", seg) + ";")
                if fm:
                    fields[fm.group(2)] = fm.group(1)
            header_start = i + 1
            cur = None
        i += 1

    for mem in members:
        mem["calls"] = [(r, t) for r, t in _CALL.findall(mem["body"]) if r not in _SKIP_NAMES]
        mem["self_calls"] = [t for t in re.findall(r"\bthis\s*\.\s*(\w+)\s*\(", mem["body"])]
    # 注解 SQL 字符串按位置归属方法
    for m in _ANN_STR.finditer(text):
        ln = _line_of(text, m.start())
        for mem in members:
            if mem["start_line"] <= ln <= mem["end_line"]:
                mem["ann_str"][m.group(1)] = m.group(2)
                break
    # 端点注解（含路径）按位置归属方法
    for m in _MAPPING_ANN.finditer(text):
        ln = _line_of(text, m.start())
        verb = MAPPING_VERBS.get(m.group(1).capitalize() + "Mapping", "")
        for mem in members:
            if mem["start_line"] <= ln <= mem["end_line"]:
                mem["endpoint"] = (verb, (base_path + (m.group(2) or "")).rstrip("/") or "/")
                break
    # @Scheduled
    for m in re.finditer(r"@Scheduled\b", text):
        ln = _line_of(text, m.start())
        for mem in members:
            if mem["start_line"] <= ln <= mem["end_line"]:
                mem["scheduled"] = True
                break

    return {"class": cname, "package": pkg, "kind": kind, "fields": fields,
            "methods": members, "feign": is_feign, "mapper": is_mapper,
            "class_name": f"{pkg}.{cname}" if pkg else cname}


_ANN_STRIP = re.compile(r"@\w+(?:\s*\([^)]*\))?")


def _member(header, cname):
    h = header.strip()
    if not h:
        return None
    anns = re.findall(r"@(\w+)", h)
    h = _ANN_STRIP.sub(" ", h)          # 去掉注解，避免把 @GetMapping(...) 误当方法名
    m = re.search(r"(\w+)\s*\(", h)
    if not m:
        return None
    name = m.group(1)
    if name == cname or name in _SKIP_NAMES:
        return None
    # 排除字段声明带初始化 lambda 等：要求 name 前有返回类型 token
    pre = h[:m.start(1)].strip()
    if not pre or re.search(r"\b(new|return|=)\b", pre.split("\n")[-1]):
        return None
    return {"name": name, "anns": anns, "endpoint": None, "scheduled": False,
            "ann_str": {}, "body": "", "calls": [], "self_calls": []}


def parse_xml_mapper(path, text):
    """*Mapper.xml：namespace → Mapper 类，statement id → SQL 表。"""
    ns = re.search(r'<mapper\s+namespace="([\w.]+)"', text)
    if not ns:
        return None
    cname = ns.group(1).split(".")[-1]
    out = []
    for m in re.finditer(r"<(select|insert|update|delete)\s+[^>]*id=\"(\w+)\"[^>]*>(.*?)</\1>", text, re.S | re.I):
        kind, mid, sql = m.group(1).lower(), m.group(2), m.group(3)
        sql = re.sub(r"<[^>]+>", " ", sql)
        out.append({"class": cname, "method": mid,
                    "op": "READS" if kind == "select" else "WRITES",
                    "tables": tables_from_sql(sql)})
    return {"class_name": ns.group(1), "class": cname, "statements": out}


class ImpactGraph:
    """节点：Endpoint/Method/Table/ScheduledJob/External；边：EXPOSES/CALLS/READS/WRITES/DEPENDS_ON。"""

    def __init__(self):
        self.methods = {}       # symbol "Class#method" → info
        self.endpoints = {}     # "GET /path" → controller symbol
        self.tables = {}        # table → {readers:set, writers:set}
        self.jobs = {}          # symbol → job node
        self.externals = {}     # class → external node
        self.callers = {}       # callee → set(callers) 反向 CALLS
        self.callees = {}       # caller → set(callees)
        self.files = {}         # rel path → [symbols]

    def add_call(self, src, dst):
        if src == dst:
            return
        self.callers.setdefault(dst, set()).add(src)
        self.callees.setdefault(src, set()).add(dst)

    def to_dict(self):
        return {"nodes": {"endpoints": sorted(self.endpoints), "methods": sorted(self.methods),
                          "tables": sorted(self.tables), "jobs": sorted(self.jobs),
                          "externals": sorted(self.externals)},
                "edges": {"calls": {k: sorted(v) for k, v in self.callees.items()},
                          "table_reads": {t: sorted(v["readers"]) for t, v in self.tables.items()},
                          "table_writes": {t: sorted(v["writers"]) for t, v in self.tables.items()}}}


def build_graph(root, max_files=800):
    """扫描 Java 源码树构建 Impact Graph。"""
    g = ImpactGraph()
    parsed = {}
    n = 0
    java_files, xml_files = [], []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in (".git", "target", "build", ".gradle", "node_modules", "test", "tests")]
        for fn in fns:
            if fn.endswith(".java"):
                java_files.append(os.path.join(dp, fn))
            elif fn.endswith("Mapper.xml"):
                xml_files.append(os.path.join(dp, fn))
    for path in java_files:
        if n >= max_files:
            break
        n += 1
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        rel = os.path.relpath(path, root).replace("\\", "/")
        info = parse_java(path, text)
        if not info:
            continue
        parsed[info["class"]] = info
        g.files[rel] = []
        for mem in info["methods"]:
            sym = f"{info['class']}#{mem['name']}"
            g.methods[sym] = {"file": rel, "start": mem["start_line"], "end": mem["end_line"],
                              "class": info["class"], "name": mem["name"], "kind": info["kind"],
                              "feign": info["feign"], "mapper": info["mapper"]}
            g.files[rel].append(sym)
            if mem["endpoint"] and not info["feign"]:
                verb, pth = mem["endpoint"]
                g.endpoints[f"{verb} {pth}"] = sym
            if mem.get("scheduled"):
                g.jobs[sym] = {"file": rel}
            # Mapper 注解 SQL → 表
            for ann, sql in mem["ann_str"].items():
                op = SQL_MAPPINGS.get(ann)
                if op:
                    for t in tables_from_sql(sql):
                        g.tables.setdefault(t, {"readers": set(), "writers": set()})[
                            "readers" if op == "READS" else "writers"].add(sym)
    for path in xml_files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        rel = os.path.relpath(path, root).replace("\\", "/")
        x = parse_xml_mapper(path, text)
        if not x:
            continue
        for st in x["statements"]:
            sym = f"{st['class']}#{st['method']}"
            g.methods.setdefault(sym, {"file": rel, "start": 0, "end": 0, "class": st["class"],
                                       "name": st["method"], "kind": "interface", "feign": False,
                                       "mapper": True})
            g.files.setdefault(rel, []).append(sym)
            for t in st["tables"]:
                g.tables.setdefault(t, {"readers": set(), "writers": set()})[
                    "readers" if st["op"] == "READS" else "writers"].add(sym)
    # 调用边：receiver 类型解析为已知类
    for info in parsed.values():
        own = {m["name"] for m in info["methods"]}
        for mem in info["methods"]:
            caller = f"{info['class']}#{mem['name']}"
            for recv, callee in mem["calls"]:
                rtype = info["fields"].get(recv)
                if rtype and rtype in parsed:
                    g.add_call(caller, f"{rtype}#{callee}")
                    if parsed[rtype].get("feign"):
                        g.externals.setdefault(rtype, {"callers": set()})["callers"].add(caller)
                elif rtype and rtype not in parsed:
                    # 未知类型但像 Feign/Mapper 命名
                    if rtype.endswith("Client") or rtype.endswith("Feign"):
                        g.externals.setdefault(rtype, {"callers": set()})["callers"].add(caller)
            for callee in mem["self_calls"]:
                g.add_call(caller, f"{info['class']}#{callee}")
            # 裸同类调用 shared(...)：前面不是 '.'，且是本类方法名
            for bm in re.finditer(r"(?<![\w.])(\w+)\s*\(", mem["body"]):
                nm = bm.group(1)
                if nm in own and nm != mem["name"]:
                    g.add_call(caller, f"{info['class']}#{nm}")
    # QueryWrapper 实体→表（在方法体内扫，归属到调用它的类）
    for info in parsed.values():
        for mem in info["methods"]:
            caller = f"{info['class']}#{mem['name']}"
            # 方法体在 stripped 文本里没保留 body？parse_java 已存 body
            for ent in re.findall(r"(?:new\s+)?(?:Query|LambdaQuery)Wrapper\s*<\s*(\w+)\s*>", mem["body"]):
                g.tables.setdefault(_camel_to_snake(ent).lower(), {"readers": set(), "writers": set()})["readers"].add(caller)
            for frm in re.findall(r"\.from\(\s*(\w+)\.class\s*\)", mem["body"]):
                g.tables.setdefault(_camel_to_snake(frm).lower(), {"readers": set(), "writers": set()})["readers"].add(caller)
    return g


def _reverse_closure(g, seeds):
    """沿 CALLS 反向传递闭包：谁（直接或间接）调用了 seeds。"""
    seen, stack = set(seeds), list(seeds)
    while stack:
        cur = stack.pop()
        for caller in g.callers.get(cur, ()):
            if caller not in seen:
                seen.add(caller)
                stack.append(caller)
    return seen


def git_diff_hunks(root, base_sha=None, head_sha=None):
    """git diff -U0 → {file: [(start,end)]}（新增侧行区间）。"""
    args = ["git", "diff", "-U0", "--no-color"]
    if base_sha and head_sha:
        args.append(f"{base_sha}..{head_sha}")
    else:
        args += ["HEAD"]
    r = subprocess.run(args, capture_output=True, text=True, cwd=root)
    out, cur = {}, None
    if r.returncode != 0:
        return out
    for line in (r.stdout or "").splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:].strip()
            out.setdefault(cur, [])
        elif line.startswith("@@") and cur:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start, cnt = int(m.group(1)), int(m.group(2) or 1)
                out[cur].append((start, start + max(cnt, 1) - 1))
    return out


def untracked_java(root):
    r = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                       capture_output=True, text=True, cwd=root)
    return [x for x in (r.stdout or "").splitlines() if x.strip().endswith(".java")]


def changed_symbols(g, hunks, untracked=()):
    """变更行 → 所属 Class#method。untracked 文件全方法计入。"""
    syms = set()
    for rel, ranges in hunks.items():
        rel = rel.replace("\\", "/")
        for sym in g.files.get(rel, []):
            info = g.methods[sym]
            for a, b in ranges:
                if info["start"] <= b and a <= info["end"]:
                    syms.add(sym)
                    break
    for rel in untracked:
        rel = rel.replace("\\", "/")
        syms.update(g.files.get(rel, []))
    return syms


def analyze(root, changed=None, untracked=(), base_sha=None, head_sha=None,
            regression_reasons=None, graph=None, patterns=None, scan_hits=None):
    """§3.4 输出：changed_symbols/affected_symbols/affected_endpoints/shared_rules/risk_level
    + §4 Regression Radius。changed: {file: [(start,end)]}（显式传入优先于 git）。
    patterns 非 None 时附带高风险调用点扫描（§29 增强）：scan_hits 命中点按 file 归属到端点/类。"""
    g = graph or build_graph(root)
    if changed is None:
        changed = git_diff_hunks(root, base_sha, head_sha)
        untracked = tuple(untracked) or tuple(untracked_java(root))
    changed = {k.replace("\\", "/"): v for k, v in (changed or {}).items()}
    seeds = changed_symbols(g, changed, untracked)
    affected = _reverse_closure(g, seeds)
    # 受影响端点
    endpoints = {}
    for ep, sym in g.endpoints.items():
        if sym in affected:
            endpoints[ep] = {"symbol": sym, "radius": "R0" if sym in seeds else "R1"}
    # 变更触及的表（seeds 直接读写）
    changed_tables = set()
    for t, v in g.tables.items():
        if seeds & v["readers"] or seeds & v["writers"]:
            changed_tables.add(t)
    for ep, sym in g.endpoints.items():
        if ep in endpoints or sym in seeds:
            continue
        ep_tables = _tables_of(g, {sym})
        if ep_tables & changed_tables:
            endpoints[ep] = {"symbol": sym, "radius": "R3"}
    # R4：历史缺陷关联——registry reason 文本命中受影响 symbol
    r4 = []
    for reason in (regression_reasons or []):
        for sym in affected:
            cls, _, meth = sym.partition("#")
            if meth and meth.lower() in reason.lower():
                r4.append({"reason": reason[:120], "symbol": sym})
    # 受影响表 / 任务
    aff_tables = sorted(_tables_of(g, affected) | changed_tables)
    aff_jobs = sorted(j for j in g.jobs if j in affected)
    # §29 增强：命中点归属到端点（文件→类→端点），无归属的标 UNATTRIBUTED 仍要补 case
    hits = scan_hits if scan_hits is not None else (scan_patterns(root, patterns) if patterns is not None else [])
    file_eps = {}
    for ep, sym in g.endpoints.items():
        f = _class_file(g, sym.partition("#")[0])
        if f:
            file_eps.setdefault(f, []).append(ep)
    attributed = []
    for h in hits:
        eps = sorted(set(file_eps.get(h["file"], [])))
        attributed.append({**h, "endpoints": eps or ["UNATTRIBUTED"],
                           "required_cases": max(2, len(eps) * 2)})   # 每命中点 ≥1 正 1 负
    # 风险分级：写端点/任务/表写入 → P0；读端点 → P1；仅内部 → P2
    write_eps = [e for e in endpoints if e.split(" ", 1)[0] in WRITE_VERBS]
    if write_eps or aff_jobs or any(t in changed_tables for t in
                                    [k for k, v in g.tables.items() if v["writers"] & affected]):
        risk = "P0"
    elif endpoints:
        risk = "P1"
    else:
        risk = "P2"
    return {"changed_files": sorted(changed.keys()) + [u.replace("\\", "/") for u in untracked],
            "changed_symbols": sorted(seeds),
            "affected_symbols": sorted(affected),
            "affected_endpoints": {k: v["radius"] for k, v in sorted(endpoints.items())},
            "affected_tables": aff_tables,
            "affected_jobs": aff_jobs,
            "shared_rules": [],   # Phase 3 RuleMind 接入后填充（§3.2 USES_RULE）
            "regression_radius": {"R4_history": r4},
            "pattern_findings": attributed,          # §29 增强：高风险调用点清单
            "risk_level": risk,
            "graph_nodes": len(g.methods) + len(g.endpoints) + len(g.tables),
            "graph_edges_calls": sum(len(v) for v in g.callees.values())}


def _class_file(g, cls):
    """类全名 → 相对文件路径（从已解析方法的 file 反推；未解析的类返回 None）。"""
    for sym, v in g.methods.items():
        if sym.partition("#")[0] == cls:
            return v["file"]
    return None


def _tables_of(g, symbols):
    out = set()
    for t, v in g.tables.items():
        if symbols & v["readers"] or symbols & v["writers"]:
            out.add(t)
    return out


def expand_radius(g, endpoints, affected, seeds):
    """§19 失败驱动扩展：FAIL 后把 R2/R3 候选端点并入回归范围。
    R2 待 RuleMind；当前扩展 = 与受影响方法共用表的其余端点。"""
    aff_tables = _tables_of(g, affected)
    extra = {}
    for ep, sym in g.endpoints.items():
        if ep in endpoints:
            continue
        if _tables_of(g, {sym}) & aff_tables:
            extra[ep] = "R3"
    return extra
