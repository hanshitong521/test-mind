# examples/red-packet/sut.py — 被测系统(SUT)：红包创建/领取
# ponytail: stdlib-only demo SUT；真实项目由 TestMind 对用户代码执行，本文件只提供复杂业务靶子
import json, sqlite3, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS company (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  status INTEGER NOT NULL DEFAULT 1);          -- 1=active 0=frozen
CREATE TABLE IF NOT EXISTS influencer (
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES company(id),
  name TEXT NOT NULL, status INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS red_packet (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  idem_key TEXT UNIQUE,                        -- 幂等键
  name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 50),
  amount_cents INTEGER NOT NULL CHECK(amount_cents BETWEEN 1 AND 100000),
  quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 1000),
  influencer_id INTEGER NOT NULL REFERENCES influencer(id),
  unlimited INTEGER NOT NULL DEFAULT 0,        -- 1=无限发放
  expiry_ts INTEGER,                           -- NULL=无期限
  scheduled_at INTEGER,                        -- 定时派发时刻；NULL=非定时
  dispatched INTEGER NOT NULL DEFAULT 0,       -- 0=未派发 1=已派发（CAS 防多实例重复调度）
  status INTEGER NOT NULL DEFAULT 0,           -- 0=CREATED 1=RUNNING 2=PAUSED 3=FINISHED 4=DELETED
  remaining INTEGER NOT NULL,
  created_by INTEGER NOT NULL,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS grant_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  packet_id INTEGER NOT NULL REFERENCES red_packet(id),
  issued_at INTEGER NOT NULL);
"""

OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "red-packet", "version": "1.0"},
    "servers": [{"url": "http://127.0.0.1:18101"}],
    "paths": {
        "/red-packets": {
            "post": {
                "operationId": "createRedPacket",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CreateReq"}}}},
                "responses": {"200": {"description": "idempotent replay"}, "201": {"description": "created"}, "400": {"description": "invalid"}, "404": {"description": "no influencer"}, "409": {"description": "duplicate idem"}},
            }
        },
        "/scheduler/tick": {
            "post": {
                "operationId": "schedulerTick",
                "responses": {"200": {"description": "dispatched count"}},
            }
        },
        "/red-packets/{id}/grant": {
            "post": {
                "operationId": "grantRedPacket",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "granted"}, "400": {"description": "invalid id"}, "404": {"description": "not found"}, "409": {"description": "exhausted/paused/expired"}},
            }
        },
        "/red-packets/{id}/{action}": {
            "post": {
                "operationId": "transitionRedPacket",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
                    {"name": "action", "in": "path", "required": True,
                     "schema": {"type": "string", "enum": ["start", "pause", "finish", "delete"]}},
                ],
                "responses": {"200": {"description": "transitioned"}, "404": {"description": "not found"}, "409": {"description": "invalid transition"}},
            }
        },
    },
    "components": {"schemas": {
        "CreateReq": {
            "type": "object", "required": ["name", "amount_cents", "quantity", "influencer_id", "created_by"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 50,
                         "pattern": "^[^\\u0000-\\u001f\\u007f]*\\S[^\\u0000-\\u001f\\u007f]*$"},
                "amount_cents": {"type": "integer", "minimum": 1, "maximum": 100000},
                "quantity": {"type": "integer", "minimum": 1, "maximum": 1000},
                "influencer_id": {"type": "integer", "minimum": 1, "maximum": 2147483647},
                "created_by": {"type": "integer", "minimum": 1, "maximum": 2147483647},
                "unlimited": {"type": "boolean", "default": False, "nullable": True},
                "expiry_ts": {"type": "integer", "nullable": True,
                              "minimum": -2**63, "maximum": 2**63 - 1},   # 存储上限即契约下限，否则 schema 与实现不一致
                "scheduled_at": {"type": "integer", "nullable": True,
                                 "minimum": -2**63, "maximum": 2**63 - 1},
                "idem_key": {"type": "string", "nullable": True},   # null=不启用幂等（真实行为）
            },
            "additionalProperties": False,
        },
    }},
}

VALID_TRANSITIONS = {  # 状态机：合法转换图（测试断言用）
    0: {"start": 1, "delete": 4},      # CREATED -> RUNNING / DELETED
    1: {"pause": 2, "finish": 3, "delete": 4},
    2: {"start": 1, "delete": 4},
}


def seed_packet_sql(pk, status, remaining, expiry=None, sched=None):
    """精确定位 fixture：调度/发放/状态机 case 共用，禁止随机业务数据。"""
    i = str(int(pk))
    ex = "NULL" if expiry is None else str(int(expiry))
    sc = "NULL" if sched is None else str(int(sched))
    return [
        "DELETE FROM grant_log WHERE packet_id=" + i,
        "DELETE FROM red_packet WHERE id=" + i,
        "INSERT INTO red_packet(id,name,amount_cents,quantity,influencer_id,unlimited,expiry_ts,"
        "scheduled_at,status,remaining,created_by,created_at,updated_at,deleted) VALUES("
        + f"{i},'ANCHOR-{i}',100,{remaining},1,0,{ex},{sc},{status},{remaining},1,0,0,0)",
    ]


class SUT:
    """业务规则（全部 CONFIRMED 事实源，供 Fact Resolver 解析）"""

    def __init__(self, db_path=":memory:"):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.isolation_level = None
        self.lock = threading.Lock()
        self.db.executescript(SCHEMA_SQL)
        self.db.execute("INSERT OR IGNORE INTO company(id,name,status) VALUES (1,'ACME',1),(2,'OTHER',1)")
        self.db.execute("INSERT OR IGNORE INTO influencer(id,company_id,name,status,deleted) VALUES"
                        " (1,1,'alice',1,0),(2,1,'bob',0,0),(3,2,'carol',1,0),(4,1,'ghost',1,1)")
        self.now = lambda: int(time.time())
        self.dependency_url = None     # 外部风控服务（测试指向 FaultProxy）；None=不调依赖

    # ---- 业务 ----
    def create(self, body):
        if not isinstance(body, dict):
            return 400, {"error": "invalid_body"}
        allowed = {"name", "amount_cents", "quantity", "influencer_id", "created_by",
                   "unlimited", "expiry_ts", "scheduled_at", "idem_key"}
        if any(k not in allowed for k in body):
            return 400, {"error": "unknown_field"}
        for f in ("name", "amount_cents", "quantity", "influencer_id", "created_by"):
            v = body.get(f)
            if v is None or (isinstance(v, str) and v.strip() == ""):
                return 400, {"error": f"missing_field:{f}"}
        name = body["name"]
        if not isinstance(name, str) or not (1 <= len(name) <= 50):
            return 400, {"error": "invalid_name"}
        if any(ord(c) <= 0x1F or ord(c) == 0x7F for c in name):
            return 400, {"error": "invalid_name"}          # 与 OpenAPI pattern 一致：控制字符拒收
        for f in ("amount_cents", "quantity", "influencer_id", "created_by"):
            v = body[f]
            if not isinstance(v, int) or isinstance(v, bool) or not (1 <= v <= (100000 if f == "amount_cents" else 1000 if f == "quantity" else 2**31 - 1)):
                return 400, {"error": f"out_of_range:{f}"}
        idem, expiry = body.get("idem_key"), body.get("expiry_ts")
        sched = body.get("scheduled_at")
        if idem is not None and not isinstance(idem, str):
            return 400, {"error": "invalid:idem_key"}
        for k, v in (("expiry_ts", expiry), ("scheduled_at", sched)):
            if v is not None and (not isinstance(v, int) or isinstance(v, bool)
                                  or not (-2**63 <= v <= 2**63 - 1)):   # 越界整数会在 INSERT 时 OverflowError
                return 400, {"error": f"invalid:{k}"}
        unlimited = body.get("unlimited")
        if unlimited is not None and not isinstance(unlimited, bool):
            return 400, {"error": "invalid:unlimited"}
        with self.lock:                                  # 共享连接：全部DB访问必须在锁内
            infl = self.db.execute("SELECT * FROM influencer WHERE id=?", (body["influencer_id"],)).fetchone()
            if not infl or infl["deleted"] or not infl["status"]:
                return 404, {"error": "influencer_unavailable"}
            idem = body.get("idem_key")
            if idem:
                row = self.db.execute("SELECT id FROM red_packet WHERE idem_key=?", (idem,)).fetchone()
                if row:
                    return 200, {"id": row["id"], "duplicate": True}   # 幂等重放
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                unlim = 1 if unlimited else 0
                remaining = 2147483647 if unlim else body["quantity"]
                cur = self.db.execute(
                    "INSERT INTO red_packet(idem_key,name,amount_cents,quantity,influencer_id,unlimited,"
                    "expiry_ts,scheduled_at,status,remaining,created_by,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (idem, name, body["amount_cents"], body["quantity"], body["influencer_id"], unlim,
                     expiry, sched, 0, remaining, body["created_by"], self.now(), self.now()))
                self.db.execute("COMMIT")
                return 201, {"id": cur.lastrowid}
            except sqlite3.IntegrityError:
                self.db.execute("ROLLBACK")
                if idem:   # 缺陷⑪：并发窗口撞了 UNIQUE → 按幂等语义收敛为重放，而非 409
                    row = self.db.execute("SELECT id FROM red_packet WHERE idem_key=?", (idem,)).fetchone()
                    if row:
                        return 200, {"id": row["id"], "duplicate": True}
                return 409, {"error": "concurrent_duplicate"}
            except Exception:
                self.db.rollback()      # 共享连接上残留未结束事务会毒化此后每一次写入
                return 500, {"error": "internal"}

    def _risk_ok(self):
        """外部风控真 HTTP 调用（2s 超时）。非 200 / 超时 / 拒连 / JSON 不符 = 依赖故障。"""
        import urllib.request, urllib.error
        try:
            req = urllib.request.Request(self.dependency_url, data=b"{}",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=2) as r:
                return r.status == 200 and json.loads(r.read()).get("ok") is True
        except Exception:
            return False

    def grant(self, packet_id, now=None):
        now = int(time.time()) if now is None else int(now)   # X-Test-Now 注入的可控时钟
        with self.lock:                                  # 共享连接：全部DB访问必须在锁内
            row = self.db.execute("SELECT * FROM red_packet WHERE id=? AND deleted=0", (packet_id,)).fetchone()
        if not row:
            return 404, {"error": "not_found"}
        if row["status"] != 1:
            return 409, {"error": f"invalid_status:{row['status']}"}
        if row["expiry_ts"] and now > row["expiry_ts"]:
            return 409, {"error": "expired"}
        if self.dependency_url and not self._risk_ok():  # 故障注入点（写库之前）
            return 502, {"error": "risk_control_down"}
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = self.db.execute("UPDATE red_packet SET remaining=remaining-1 WHERE id=? AND remaining>0", (packet_id,))
                if cur.rowcount:
                    self.db.execute("INSERT INTO grant_log(packet_id,issued_at) VALUES(?,?)", (packet_id, now))
                    self.db.execute("COMMIT")
                    return 200, {"granted": True}
                self.db.execute("ROLLBACK")
                return 409, {"error": "exhausted"}
            except Exception:
                self.db.execute("ROLLBACK")
                return 502, {"error": "internal"}

    def tick(self, now=None):
        """定时派发：RUNNING 且 scheduled_at<=now 且未派发 → CAS 抢占，恰好一次。
        返回 {dispatched: n}。重复 tick / 多实例并发 tick 均不重派（dispatched 守卫）。"""
        now = int(time.time()) if now is None else int(now)
        with self.lock:
            rows = self.db.execute(
                "SELECT id FROM red_packet WHERE status=1 AND deleted=0 AND scheduled_at IS NOT NULL"
                " AND scheduled_at<=? AND dispatched=0", (now,)).fetchall()
            n = 0
            for r in rows:
                cur = self.db.execute("UPDATE red_packet SET dispatched=1, updated_at=? WHERE id=? AND dispatched=0",
                                      (now, r["id"]))
                if cur.rowcount:
                    self.db.execute("INSERT INTO grant_log(packet_id,issued_at) VALUES(?,?)", (r["id"], now))
                    n += 1
            return 200, {"dispatched": n}

    def transition(self, packet_id, action, now=None):
        """状态机迁移：只认 VALID_TRANSITIONS 事实表，非法转换 409。"""
        now = int(time.time()) if now is None else int(now)
        with self.lock:
            row = self.db.execute("SELECT * FROM red_packet WHERE id=? AND deleted=0", (packet_id,)).fetchone()
            if not row:
                return 404, {"error": "not_found"}
            allowed = VALID_TRANSITIONS.get(row["status"], {})
            if action not in allowed:
                return 409, {"error": f"invalid_transition:status{row['status']}"}
            new = allowed[action]
            if action == "delete":
                self.db.execute("UPDATE red_packet SET status=4, deleted=1, updated_at=? WHERE id=?", (now, packet_id))
            else:
                self.db.execute("UPDATE red_packet SET status=?, updated_at=? WHERE id=?", (new, now, packet_id))
            return 200, {"status": new}


def make_handler(sut):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"        # 真实形态：启用 keep-alive（响应恒带 Content-Length，安全）
        def log_message(self, *a): pass

        def _reply(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_body(self):
            """完整消费请求体（Content-Length 或 chunked），保护同连接下一个请求不被污染。"""
            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                data = b""
                while True:
                    n = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                    if n == 0:
                        self.rfile.readline()          # chunk trailer CRLF
                        break
                    data += self.rfile.read(n)
                    self.rfile.readline()              # 块后 CRLF
                return data
            return self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))

        def do_POST(self):
            raw = self._read_body()                    # 先读尽请求体再路由，防破坏 keep-alive
            if self.path == "/risk":
                return self._reply(200, {"ok": True})   # 风控上游 = POST（FaultProxy ok 模式透传目标）
            if self.path == "/scheduler/tick":
                now = self.headers.get("X-Test-Now")
                try:
                    return self._reply(*sut.tick(now=int(now) if now else None))
                except ValueError:
                    return self._reply(400, {"error": "invalid_clock"})
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be object")
            except Exception:
                return self._reply(400, {"error": "malformed_json"})
            if self.path == "/red-packets":
                self._reply(*sut.create(body))
            elif self.path.startswith("/red-packets/"):
                parts = self.path.split("/")
                ok_route = len(parts) == 4 and parts[3] in ("grant", "start", "pause", "finish", "delete")
                if not ok_route:
                    return self._reply(404, {"error": "route"})
                try:
                    pid = int(parts[2])
                    if not (0 <= pid <= 2**63 - 1):          # 负数/超界 id 资源不存在（sqlite int64 边界）
                        raise ValueError
                except ValueError:
                    return self._reply(404, {"error": "not_found"})
                now = self.headers.get("X-Test-Now")                   # 可控时钟测试缝
                try:
                    now = int(now) if now else None
                except ValueError:
                    return self._reply(400, {"error": "invalid_clock"})
                if parts[3] == "grant":
                    self._reply(*sut.grant(pid, now=now))
                else:
                    self._reply(*sut.transition(pid, parts[3], now=now))
            else:
                self._reply(404, {"error": "route"})

        def do_GET(self):
            self._read_body()                         # GET-with-body 也要饮尽（fuzz 实测发过 -d 的 GET）
            if self.path == "/openapi.json":
                self._reply(200, OPENAPI)
            elif self.path == "/risk" or self.path.startswith("/red-packets") or self.path == "/scheduler/tick":
                self.send_response(405)                  # 声明过/已知资源只允许 POST
                self.send_header("Content-Type", "application/json")
                self.send_header("Allow", "POST")             # RFC 9110: 405 必带 Allow
                data = b'{"error": "method_not_allowed"}'
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._reply(404, {"error": "route"})

        # 未在 OpenAPI 声明的方法必须 405 + 精确 Allow 头（RFC 9110，schemathesis 抓过缺失/不匹配）
        def _unsupported(self):
            self._read_body()                         # 任何带 body 的方法都先饮尽，防污染 keep-alive 连接
            allow = "GET" if self.path == "/openapi.json" else "POST"
            self.send_response(405)
            self.send_header("Content-Type", "application/json")
            self.send_header("Allow", allow)
            data = b'{"error": "method_not_allowed"}'
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":              # HTTP/1.1 keep-alive 下 HEAD 响应禁 body
                self.wfile.write(data)
        do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_TRACE = do_QUERY = _unsupported
    return H


def serve(db_path=":memory:", port=18101, sut=None):
    sut = sut or SUT(db_path)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(sut))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, sut


if __name__ == "__main__":
    srv, s = serve(port=18101, db_path="sut.db")
    print("SUT on http://127.0.0.1:18101")
    while True:
        time.sleep(3600)
