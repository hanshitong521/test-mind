# testmind/rules.py — RuleMind + TemporalMind + StateMind（V10 §5/§7/§8/§9/§10）
# 规则一等公民：结构 §5.1；角色/归属必须来源于代码证据（§7 禁止凭空生成）；
# 时间谓词自动识别（§9.1）+ T-ε/T/T+ε 生成（§9.2）；状态机解析（§10）。
import datetime
import json
import os
import re

RULE_TYPES = ("range", "required", "enum", "len",
              "permission", "visibility", "ownership", "tenant", "temporal",
              "state", "aggregation", "consistency", "side_effect")

# §8 Ownership Model（关系由 actor×resource 字段推导，非角色枚举）
OWNERSHIP_RELATIONS = ("SELF", "SAME_TEAM", "SAME_COMPANY", "OTHER_COMPANY",
                       "UNASSIGNED", "SYSTEM_OWNED")

# §7 标准角色（仅当代码证据出现才可启用）
CANONICAL_ROLES = ("OWNER", "SAME_TENANT_PEER", "OTHER_TENANT", "ADMIN", "ANONYMOUS")


# ───────────────────────── 规则结构（§5.1）─────────────────────────

class Rule:
    def __init__(self, rule_id, name, resource, predicate, applies_to, surfaces,
                 rtype="visibility", source="", priority="P0"):
        self.rule_id = rule_id
        self.name = name
        self.resource = resource
        self.predicate = predicate          # {any:[...]} / {all:[...]} / 叶子表达式
        self.applies_to = list(applies_to)  # READ/WRITE/...
        self.surfaces = list(surfaces)      # order.detail / order.page ...
        self.rtype = rtype
        self.source = source                # 证据出处 path:line / RequirementMind:xxx
        self.priority = priority

    def to_dict(self):
        return {"rule_id": self.rule_id, "name": self.name, "resource": self.resource,
                "predicate": self.predicate, "applies_to": self.applies_to,
                "surfaces": self.surfaces, "type": self.rtype, "source": self.source,
                "priority": self.priority}

    @staticmethod
    def from_dict(d):
        return Rule(d["rule_id"], d.get("name", ""), d.get("resource", ""),
                    d.get("predicate", {}), d.get("applies_to", ["READ"]),
                    d.get("surfaces", []), d.get("type", "visibility"),
                    d.get("source", ""), d.get("priority", "P0"))


def load_rules(path_or_text):
    """规则注入：JSON 文件路径 / JSON 字符串 / list[dict]。YAML 不支持（纯 stdlib），
    RequirementMind 侧以 JSON 投喂。"""
    if isinstance(path_or_text, list):
        data = path_or_text
    elif os.path.exists(str(path_or_text)):
        with open(path_or_text, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = json.loads(path_or_text)
    if isinstance(data, dict):
        data = data.get("rules", [data])
    return [Rule.from_dict(d) for d in data]


# ───────────────────── 谓词求值（any/all + 叶子比较）─────────────────────

_LEAF = re.compile(r"^\s*([\w.]+)\s*(>=|<=|==|!=|>|<)\s*(.+?)\s*$")


def _resolve(path, ctx):
    cur = ctx
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _coerce(v):
    if isinstance(v, str):
        s = v.strip().strip("'\"")
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in s else (
                "%Y-%m-%dT%H:%M:%S" if "T" in s else "%Y-%m-%d")
            try:
                return datetime.datetime.fromisoformat(s) if "T" in s or ":" in s \
                    else datetime.datetime.strptime(s, fmt)
            except ValueError:
                return s
        if re.match(r"^-?\d+(\.\d+)?$", s):
            return float(s) if "." in s else int(s)
        return s
    return v


def eval_predicate(pred, ctx):
    """pred: {"all": [...]} | {"any": [...]} | "actor.id == resource.created_by"
    ctx: {"actor": {...}, "resource": {...}, "now": datetime}。未知路径 → None → 比较为 False。"""
    if isinstance(pred, dict):
        if "all" in pred:
            return all(eval_predicate(p, ctx) for p in pred["all"])
        if "any" in pred:
            return any(eval_predicate(p, ctx) for p in pred["any"])
        return False
    m = _LEAF.match(str(pred))
    if not m:
        return False
    left, op, right = m.group(1), m.group(2), m.group(3).strip()
    lv = _resolve(left, ctx)
    # 右值：含点号视为路径解析；裸词（ADMIN / 数字 / 引号串）当字面量
    rv = _resolve(right, ctx) if "." in right else _coerce(right)
    if lv is None:
        return False
    lv, rv = _coerce(lv) if not isinstance(lv, (dict, list)) else lv, _coerce(rv) if not isinstance(rv, (dict, list)) else rv
    if isinstance(lv, datetime.datetime) and isinstance(rv, str):
        rv = _coerce(rv)
    try:
        if op == "==":
            return lv == rv
        if op == "!=":
            return lv != rv
        if lv is None or rv is None:
            return False
        return {"<": lambda: lv < rv, "<=": lambda: lv <= rv,
                ">": lambda: lv > rv, ">=": lambda: lv >= rv}[op]()
    except TypeError:
        return False


def visible(rule, actor, resource, operation="READ", now=None):
    ctx = {"actor": actor, "resource": resource,
           "now": now or datetime.datetime.now()}
    if operation not in rule.applies_to:
        return None          # 规则不适用该操作
    return eval_predicate(rule.predicate, ctx)


# ───────────────────── Actor Model（§7：证据驱动，禁止凭空）─────────────────────

_ROLE_PATTERNS = (
    r"hasRole\s*\(\s*[\"'](\w+)[\"']",
    r"hasAuthority\s*\(\s*[\"'](\w+)[\"']",
    r"@RolesAllowed\s*\(\s*\{?\s*[\"'](\w+)[\"']",
    r"@PreAuthorize\s*\([\"'][^\"']*?hasRole\s*\(\s*'(\w+)'",
    r"\.equals\s*\(\s*[\"'](\w+)[\"']\s*\)",
    r"==\s*[\"'](\w+)[\"']",
    r"ROLE_(\w+)",
)


def extract_roles(java_text):
    """从代码文本提取角色字面量（带行号证据）。查不到 → []（UNKNOWN 挂起，不编造）。"""
    out = []
    for pat in _ROLE_PATTERNS:
        for m in re.finditer(pat, java_text):
            role = m.group(1).upper()
            line = java_text.count("\n", 0, m.start()) + 1
            if role and role not in (r.upper() for r, _ in out):
                out.append((role, line))
    return sorted(out, key=lambda x: x[1])


def actors_from_evidence(evidence):
    """evidence: [(role, source)] → actors dict。无证据 = {}，禁止默认四角色。"""
    return {role: {"source": f"evidence:line:{ln}"} for role, ln in evidence}


# ───────────────────── Ownership Model（§8）─────────────────────

def ownership_of(actor, resource):
    """actor/resource 是 dict；按字段推导归属关系。"""
    res_owner = resource.get("created_by") or resource.get("owner_id")
    res_team = resource.get("team_id")
    res_company = resource.get("company_id")
    if res_owner in (None, ""):
        return "SYSTEM_OWNED"
    if actor.get("id") == res_owner:
        return "SELF"
    if res_team and actor.get("team_id") == res_team:
        return "SAME_TEAM"
    if res_company and actor.get("company_id") == res_company:
        return "SAME_COMPANY"
    if res_company and actor.get("company_id") not in (None, "", res_company):
        return "OTHER_COMPANY"
    return "UNASSIGNED"


# ───────────────────── TemporalMind（§9）─────────────────────

# Java: now >= publishAt / now.isAfter(expireAt) / startTime <= now / expireAt > currentTimeMillis()
_NOWISH = r"(?:now|LocalDateTime\.now\(\)|LocalDate\.now\(\)|Instant\.now\(\)|System\.currentTimeMillis\(\)|currentTimeMillis\(\)|new\s+Date\(\))"
_BOUND = r"(?:\w*[Tt]ime\w*|\w*(?:publish|expire|start|end|deadline|due|at)\w*)"
# 一侧是 now、另一侧是边界字段（或字面量/epoch），双向匹配
_TEMPORAL_JAVA = re.compile(
    r"(?:(?P<lhs_now>" + _NOWISH + r")\s*(?P<op1>>=|<=|>|<)\s*(?P<rhs_bound>" + _BOUND + r"))"
    r"|(?:(?P<lhs_bound>" + _BOUND + r")\s*(?P<op2>>=|<=|>|<)\s*(?P<rhs_now>" + _NOWISH + r"))"
    r"|(?:(?P<recv_now>" + _NOWISH + r")\s*\.\s*(?P<mop>isAfter|isBefore)\s*\(\s*(?P<marg>" + _BOUND + r")\s*\))",
    re.I)
_TEMPORAL_SQL = re.compile(
    r"(?P<lhs>[\w.]+)\s*(?P<op>>=|<=|>|<)\s*NOW\(\)", re.I)
_TEMPORAL_SQL_REV = re.compile(
    r"NOW\(\)\s*(?P<op>>=|<=|>|<)\s*(?P<lhs>[\w.]+)", re.I)

EPS = {"milliseconds": datetime.timedelta(milliseconds=1),
       "seconds": datetime.timedelta(seconds=1),
       "minutes": datetime.timedelta(minutes=1),
       "days": datetime.timedelta(days=1)}


def detect_temporal_predicates(text, source=""):
    """自动识别时间谓词（Java + SQL）→ [{field, op, boundary, precision, source}]。
    约定：op 一律表达 `field <op> now`（字段在左，SQL WHERE 口径）。"""
    out = []
    _FLIP = {">=": "<=", "<=": ">=", ">": "<", "<": ">"}
    for m in _TEMPORAL_JAVA.finditer(text):
        if m.group("mop"):
            # now.isAfter(field) ⇔ field < now ; now.isBefore(field) ⇔ field > now
            direction = "<" if m.group("mop").lower() == "isafter" else ">"
            field = m.group("marg")
        elif m.group("lhs_now"):
            # now <op> field  →  field <flip(op)> now
            op = _norm_op(m.group("op1"))
            field, direction = m.group("rhs_bound"), _FLIP.get(op, op)
        else:
            # field <op> now  →  原样
            op = _norm_op(m.group("op2"))
            field, direction = m.group("lhs_bound"), op
        out.append({"field": _field_name(field), "op": direction,
                    "boundary": None, "precision": _precision_of(field),
                    "source": f"{source}:{m.start()}"})
    for rx, flip in ((_TEMPORAL_SQL, False), (_TEMPORAL_SQL_REV, True)):
        for m in rx.finditer(text):
            op = m.group("op")
            if flip:
                op = _FLIP.get(op, op)
            out.append({"field": m.group("lhs").split(".")[-1], "op": op,
                        "boundary": None, "precision": "seconds",
                        "source": f"{source}:{m.start()}"})
    return out


def _norm_op(op):
    return {"isAfter": ">", "isBefore": "<"}.get(op, op)


def _field_name(x):
    x = re.sub(r"\(\)$", "", x)
    return x.split(".")[-1]


def _precision_of(hint):
    h = hint.lower()
    if "millis" in h:
        return "milliseconds"
    if "minute" in h:
        return "minutes"
    if re.search(r"(date|day)", h) and "time" not in h:
        return "days"
    return "seconds"


def precision_of_value(v):
    """按边界值格式自动决定 ε 精度：epoch 毫秒/秒、ISO 带毫秒/秒、纯日期。"""
    if isinstance(v, (int, float)):
        return "milliseconds" if v >= 1e12 else "seconds"
    s = str(v)
    if re.match(r"^\d{13,}$", s):
        return "milliseconds"
    if re.match(r"^\d{10}$", s):
        return "seconds"
    if "." in s:
        return "milliseconds"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return "days"
    return "seconds"


def _to_datetime(v):
    if isinstance(v, datetime.datetime):
        return v
    if isinstance(v, (int, float)):
        ts = v / 1000.0 if abs(v) >= 1e12 else float(v)
        return datetime.datetime.fromtimestamp(ts)
    s = str(v).strip().strip("'\"")
    if re.match(r"^\d{13,}$", s):
        return _to_datetime(int(s) / 1000.0)
    if re.match(r"^\d{10}$", s):
        return _to_datetime(int(s))
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(s, fmt)
            except ValueError:
                pass
    return None


def time_points(boundary, precision=None):
    """§9.2：T-ε / T / T+ε。ε 按精度自动决定。支持 datetime/ISO 串/epoch 秒/毫秒。"""
    b = _to_datetime(boundary)
    if b is None:
        raise ValueError(f"unparsable boundary: {boundary!r}")
    prec = precision or precision_of_value(boundary)
    e = EPS[prec]
    return {"T-ε": b - e, "T": b, "T+ε": b + e, "precision": prec}


# ───────────────────── StateMind（§10）─────────────────────

_ENUM = re.compile(r"enum\s+(\w+)\s*\{([^}]*)\}")
_SETTER = re.compile(r"\.set(?:Status|State)\s*\(\s*(?:(?:\w+)\.)?([A-Z][A-Z0-9_]*)\s*(?:\.getCode\(\))?\s*\)")


def parse_state_machine(java_text):
    """解析枚举状态 + 从 setStatus(X) 推断迁移。返回 {states, transitions, terminal}。
    无枚举证据 → states=[]（UNKNOWN，不编造状态机）。"""
    m = _ENUM.search(java_text)
    if not m:
        return {"states": [], "transitions": [], "terminal": [], "source": ""}
    name = m.group(1)
    states = [s.strip().split("(")[0].strip() for s in m.group(2).split(",")
              if re.match(r"^[A-Z][A-Z0-9_]*", s.strip())]
    # 声明顺序视为生命周期顺序（启发式）：X → 下一个状态
    transitions = [(a, b) for a, b in zip(states, states[1:])]
    # setter 证据：只确认状态确实被赋值过（不额外推断迁移，避免把越级当合法）
    assigned = {mm.group(1) for mm in _SETTER.finditer(java_text)} & set(states)
    return {"states": states, "transitions": transitions,
            "terminal": states[-1:] if states else [], "source": name,
            "assigned_states": sorted(assigned)}


def state_case_kinds(sm):
    """§10 五类边：合法/非法/重复/越级/终态再操作。返回 [(kind, from, to)]。"""
    states, legal = sm["states"], {tuple(t) for t in sm["transitions"]}
    if not states:
        return []
    out = []
    for a, b in sm["transitions"]:
        out.append(("legal", a, b))
    terminal = set(sm["terminal"])
    for a in states:
        for b in states:
            if a == b or (a, b) in legal:
                continue
            # 越级 = 同向但跨步；非法 = 反向/无关；终态再操作 = 从终态出发
            if a in terminal:
                out.append(("terminal_reop", a, b))
            elif states.index(b) - states.index(a) > 1:
                out.append(("skip", a, b))
            else:
                out.append(("illegal", a, b))
    for a, b in sm["transitions"]:
        out.append(("repeat", a, b))       # 重复执行同一迁移
    return out
