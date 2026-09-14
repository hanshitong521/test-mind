# examples/logic-bug-lab/sut.py — V10 §24 Logic Bug Lab 被测服务（Python 轻量 SUT）
# stdlib http.server + sqlite，单资源 orders：detail/list/page/search/count/export/
# create(幂等)/update(状态机)/delete + settle 调度任务。
# 每个 §24 故障 = 一个 bug 开关（B01..B20），bugs=set() 即 fixed 版本。
# 可见性规则（fixed）：ADMIN 全见；OWNER 恒见自己数据（含发布前）；
# 同公司见 PUBLISHED 且 now >= publish_at（T 边界含）；其他公司不可见。
import json
import sqlite3
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

NOW = "2026-01-10T00:00:00"

# id, name, company, owner, status, publish_at, amount
SEED = [
    (1, "alpha",    "c1", "u1", "PUBLISHED", "2026-01-09T00:00:00", 10),
    (2, "beta",     "c1", "u1", "PUBLISHED", "2026-01-11T00:00:00", 20),   # 未来发布
    (3, "gamma",    "c2", "u2", "PUBLISHED", "2026-01-09T00:00:00", 30),
    (4, "delta4",   "c1", "u1", "DRAFT",     "2026-01-09T00:00:00", 40),
    (5, "epsilon5", "c1", "u1", "CLOSED",    "2026-01-09T00:00:00", 50),   # 终态
    (6, "six",      "c1", "u1", "PUBLISHED", "2026-01-09T00:00:00", 100),
    (7, "seven",    "c1", "u1", "PUBLISHED", "2026-01-09T00:00:00", 100),
    (8, "eight",    "c1", "u1", "PUBLISHED", "2026-01-09T00:00:00", 100),
    (9, "nine",     "c1", "u1", "PUBLISHED", "2026-01-09T00:00:00", 100),  # amount 并列
    (10, "ten",     "c1", "u1", "PUBLISHED", "2026-01-09T00:00:00", 110),
    (11, "eleven",  "c1", "u1", "PUBLISHED", "2026-01-10T00:00:00", 120),  # publish_at == NOW（B07 边界）
]

BUG_CODES = [f"B{i:02d}" for i in range(1, 21)]


def _dt(s):
    return datetime.fromisoformat(s)


class LabState:
    def __init__(self, bugs=frozenset()):
        self.bugs = set(bugs)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        c = self.conn.cursor()
        c.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, name TEXT, company_id TEXT,"
                  " owner TEXT, status TEXT, publish_at TEXT, amount INTEGER)")
        for r in SEED:
            c.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?)", r)
        self.conn.commit()
        self.settle_log = []
        self.idem = {}
        self.nonce = 0
        # B09/B10 的"缓存/快照"：启动时定格
        self.count_cache = self._all_count()
        self.list_snapshot = [dict(r) for r in self._all_rows()]

    def _all_rows(self):
        return self.conn.execute("SELECT * FROM orders").fetchall()

    def _all_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    def rows(self):
        return [dict(r) for r in self._all_rows()]

    def search_index(self, now):
        # B08：删除后 search 仍出现——ghosts 记录"已删但索引未清理"的行
        return self.rows() + getattr(self, "_ghosts", [])

    # ── 可见性（含 20 个故障开关的"错误"分支）──
    def visible(self, r, user, comp, role, now, surface):
        b = self.bugs
        if role == "ADMIN":
            return True
        pub_ok = _dt(now) >= _dt(r["publish_at"]) if "B07" not in b \
            else _dt(now) > _dt(r["publish_at"])
        if r["owner"] == user:
            if "B04" in b and not (r["status"] == "PUBLISHED" and pub_ok):
                return False                     # 故障：owner 也被时间/状态卡住
            return True
        same_comp = r["company_id"] == comp
        published = r["status"] == "PUBLISHED"
        if "B06" in b and published:              # 故障：已发布数据全租户可见
            return True
        if "B02" in b and surface == "export":    # 故障：export 丢公司+时间条件
            return published
        if not (same_comp and published):
            return False
        if surface in ("list", "page") and "B01" in b:
            return True                          # 故障：list 漏时间条件
        if surface == "detail" and "B05" in b:
            return True                          # 故障：detail 同公司提前可见
        if surface == "search" and "B20" in b:
            return True                          # 故障：search 漏时间条件
        return pub_ok


def make_handler(state):
    b = state.bugs

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _actor(self):
            return (self.headers.get("X-User", "anon"),
                    self.headers.get("X-Company", ""),
                    self.headers.get("X-Role", "USER"))

        def _now(self):
            return self.headers.get("X-Now", NOW)

        def _send(self, code, obj):
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        # ── 查询视图（list/page/search 共用，含 filter/sort/pagination 故障）──
        def _view(self, rows, q, surface):
            # B14：多条件用 OR 而不是 AND
            conds = [(k, v) for k, v in q.items()
                     if k in ("status", "company_id", "owner") and v not in (None, "")]
            if conds:
                if "B14" in b:
                    rows = [r for r in rows if any(str(r.get(k)) == v for k, v in conds)]
                else:
                    rows = [r for r in rows if all(str(r.get(k)) == v for k, v in conds)]
            sb = q.get("sort")
            if sb:
                with state.lock:
                    state.nonce += 1
                    n = state.nonce
                # B15：并列值顺序每次请求抖动 → 分页重复/漏行；fixed 版 tie 恒为 id 升序
                tie = (lambda r: (-r["id"] if n % 2 else r["id"])) if "B15" in b \
                    else (lambda r: r["id"])
                rows = sorted(rows, key=lambda r: (r.get(sb) is None, r.get(sb), tie(r)),
                              reverse=q.get("dir") == "desc")
            return rows

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            user, comp, role = self._actor()
            now = self._now()
            seg = [s for s in u.path.split("/") if s]
            if seg == ["health"]:
                return self._send(200, {"ok": True})
            if seg[0] != "orders":
                return self._send(404, {"error": "not found"})
            if len(seg) == 1 or seg[1] == "page":
                rows = state.list_snapshot if "B10" in b else state.rows()
                vis = [r for r in rows if state.visible(r, user, comp, role, now, "list")]
                vis = self._view(vis, q, "list")
                if "page" in q:
                    size = int(q.get("size", 20))
                    pg = int(q["page"])
                    if "B12" in b:
                        start = max(0, (pg - 1) * size - 1)        # 故障：页首重叠
                    elif "B13" in b:
                        start = pg * size                            # 故障：页首跳过
                    else:
                        start = (pg - 1) * size
                    total = state._all_count() if "B11" in b else len(vis)
                    return self._send(200, {"total": total, "items": vis[start:start + size]})
                return self._send(200, {"items": vis, "total": len(vis)})
            kind = seg[1]
            if kind == "count":
                if "B03" in b:
                    return self._send(200, {"total": state._all_count()})
                if "B09" in b:
                    return self._send(200, {"total": state.count_cache})
                vis = [r for r in state.rows() if state.visible(r, user, comp, role, now, "count")]
                return self._send(200, {"total": len(vis)})
            if kind == "export":
                vis = [r for r in state.rows() if state.visible(r, user, comp, role, now, "export")]
                return self._send(200, {"items": vis})
            if kind == "search":
                term = q.get("q", "")
                src = state.rows()
                if "B08" in b:                                       # 故障：search 仍含已删除
                    src = state.search_index(now)
                hits = [r for r in src if term and term in r["name"]
                        and state.visible(r, user, comp, role, now, "search")]
                return self._send(200, {"items": hits, "total": len(hits)})
            if kind == "settle-log":
                return self._send(200, {"runs": list(state.settle_log)})
            try:
                rid = int(kind)
            except ValueError:
                return self._send(404, {"error": "not found"})
            r = next((x for x in state.rows() if x["id"] == rid), None)
            if r and state.visible(r, user, comp, role, now, "detail"):
                return self._send(200, r)
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            seg = [s for s in urlparse(self.path).path.split("/") if s]
            user, comp, role = self._actor()
            now = self._now()
            if seg == ["jobs", "settle"]:
                with state.lock:
                    movable = state.conn.execute(
                        "SELECT * FROM orders WHERE status='DRAFT' AND publish_at<=?",
                        (now,)).fetchall()
                    for r in movable:
                        state.conn.execute("UPDATE orders SET status='PUBLISHED' WHERE id=?",
                                           (r["id"],))
                        state.settle_log.append({"row_id": r["id"], "at": now})
                    if "B19" in b:                                   # 故障：T 边界重复执行
                        again = state.conn.execute(
                            "SELECT * FROM orders WHERE status='PUBLISHED' AND publish_at<=?",
                            (now,)).fetchall()
                        for r in again:
                            state.settle_log.append({"row_id": r["id"], "at": now})
                    state.conn.commit()
                return self._send(200, {"settled": len(movable), "runs": len(state.settle_log)})
            if seg == ["orders"]:
                body = self._body()
                key = self.headers.get("Idempotency-Key")
                with state.lock:
                    if key and "B18" not in b and key in state.idem:  # 故障：幂等键被忽略
                        return self._send(201, dict(state.conn.execute(
                            "SELECT * FROM orders WHERE id=?", (state.idem[key],)).fetchone()))
                    cur = state.conn.execute(
                        "INSERT INTO orders (name,company_id,owner,status,publish_at,amount) "
                        "VALUES (?,?,?,?,?,?)",
                        (body.get("name", "x"), body.get("company_id", comp),
                         body.get("owner", user), body.get("status", "DRAFT"),
                         body.get("publish_at", now), int(body.get("amount", 0))))
                    state.conn.commit()
                    if key:
                        state.idem[key] = cur.lastrowid
                    row = dict(state.conn.execute(
                        "SELECT * FROM orders WHERE id=?", (cur.lastrowid,)).fetchone())
                return self._send(201, row)
            return self._send(404, {"error": "not found"})

        def do_PUT(self):
            seg = [s for s in urlparse(self.path).path.split("/") if s]
            user, comp, role = self._actor()
            if len(seg) != 2 or seg[0] != "orders":
                return self._send(404, {"error": "not found"})
            rid = int(seg[1])
            body = self._body()
            with state.lock:
                r = state.conn.execute("SELECT * FROM orders WHERE id=?", (rid,)).fetchone()
                if not r or (r["owner"] != user and role != "ADMIN"):
                    return self._send(403, {"error": "forbidden"})
                new_status = body.get("status")
                terminal = r["status"] == "CLOSED"
                valid_status = new_status in (None, "DRAFT", "PUBLISHED", "CLOSED")
                if terminal and "B16" not in b:                      # 故障：终态仍可更新
                    return self._send(409, {"error": "terminal state"})
                if new_status is not None and not valid_status:
                    if "B17" in b:                                   # 故障：先写 name 再失败（部分落库）
                        state.conn.execute("UPDATE orders SET name=? WHERE id=?",
                                           (body.get("name", r["name"]), rid))
                        state.conn.commit()
                    return self._send(400, {"error": "bad status"})
                cols = {k: v for k, v in body.items() if k in ("name", "status", "amount")}
                if cols:
                    sets = ", ".join(f"{k}=?" for k in cols)
                    state.conn.execute(f"UPDATE orders SET {sets} WHERE id=?",
                                       tuple(cols.values()) + (rid,))
                    state.conn.commit()
                row = dict(state.conn.execute("SELECT * FROM orders WHERE id=?", (rid,)).fetchone())
            return self._send(200, row)

        def do_DELETE(self):
            seg = [s for s in urlparse(self.path).path.split("/") if s]
            user, _, role = self._actor()
            if len(seg) != 2 or seg[0] != "orders":
                return self._send(404, {"error": "not found"})
            rid = int(seg[1])
            with state.lock:
                r = state.conn.execute("SELECT * FROM orders WHERE id=?", (rid,)).fetchone()
                if not r or (r["owner"] != user and role != "ADMIN"):
                    return self._send(404, {"error": "not found"})
                if "B08" in b:                                       # 故障：删除后 search 仍出现
                    if not hasattr(state, "_ghosts"):
                        state._ghosts = []
                    state._ghosts.append(dict(r))
                state.conn.execute("DELETE FROM orders WHERE id=?", (rid,))
                state.conn.commit()
            return self._send(200, {"ok": True})
    return H


def serve(bugs=frozenset()):
    state = LabState(bugs)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}", state
