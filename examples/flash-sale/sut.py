# examples/flash-sale/sut.py — 被测系统(SUT)：秒杀下单 / 支付 / 取消回补 / 超时自动取消
# ponytail: stdlib-only 参考实现，是 INTERFACE.md 契约的一份"可跑的影子"。
#         真实项目由宿主 AI 依 INTERFACE.md 自行实现，TestMind 对拍其 HTTP + DB。本文件让闭环今天就能跑起来。
import json, sqlite3, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS buyer (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  tenant_id INTEGER NOT NULL DEFAULT 1,
  status INTEGER NOT NULL DEFAULT 1,                        -- 1=active 0=frozen
  deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS product (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  price_cents INTEGER NOT NULL CHECK(price_cents BETWEEN 1 AND 1000000),
  stock INTEGER NOT NULL DEFAULT 0 CHECK(stock BETWEEN 0 AND 1000000),   -- 扣减下界=0：超发直接撞 CHECK
  tenant_id INTEGER NOT NULL REFERENCES buyer(id),
  status INTEGER NOT NULL DEFAULT 1,                        -- 1=on_sale 0=off_shelf
  deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sale_order (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  idem_key TEXT UNIQUE,                                     -- 幂等键
  product_id INTEGER NOT NULL REFERENCES product(id),
  buyer_id INTEGER NOT NULL REFERENCES buyer(id),
  qty INTEGER NOT NULL CHECK(qty BETWEEN 1 AND 100),
  unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents BETWEEN 1 AND 1000000),
  total_cents INTEGER NOT NULL CHECK(total_cents BETWEEN 1 AND 100000000),
  status INTEGER NOT NULL DEFAULT 0,                        -- 0=PENDING 1=PAID 2=SHIPPED 3=DONE 4=CANCELLED
  expires_at INTEGER,                                       -- PENDING 超时时刻；NULL=不过期
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS stock_log (                      -- 库存流水：扣减(-)/回补(+) 审计，幂等/超发/回补对拍靠它
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES product(id),
  order_id INTEGER,
  delta INTEGER NOT NULL,
  at INTEGER NOT NULL);
"""

OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "flash-sale", "version": "1.0"},
    "servers": [{"url": "http://127.0.0.1:18201"}],
    "paths": {
        "/orders": {
            "post": {
                "operationId": "createOrder",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CreateOrderReq"}}}},
                "responses": {"200": {"description": "idempotent replay"}, "201": {"description": "created"},
                              "400": {"description": "invalid"}, "403": {"description": "buyer frozen"},
                              "404": {"description": "buyer/product missing or deleted"},
                              "409": {"description": "off_shelf or sold_out"}},
            }
        },
        "/orders/{id}/{action}": {
            "post": {
                "operationId": "transitionOrder",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
                    {"name": "action", "in": "path", "required": True,
                     "schema": {"type": "string", "enum": ["pay", "ship", "complete", "cancel"]}},
                ],
                "responses": {"200": {"description": "transitioned"}, "404": {"description": "not found"},
                              "409": {"description": "invalid transition / expired"},
                              "502": {"description": "payment gateway down (pay only, no DB write)"}},
            }
        },
        "/scheduler/tick": {
            "post": {"operationId": "schedulerTick", "responses": {"200": {"description": "cancelled count"}}}
        },
    },
    "components": {"schemas": {
        "CreateOrderReq": {
            "type": "object", "required": ["product_id", "buyer_id", "qty"],
            "properties": {
                "product_id": {"type": "integer", "minimum": 1, "maximum": 2147483647},
                "buyer_id": {"type": "integer", "minimum": 1, "maximum": 2147483647},
                "qty": {"type": "integer", "minimum": 1, "maximum": 100},
                "idem_key": {"type": "string", "nullable": True},          # null=不启用幂等
                "ttl_seconds": {"type": "integer", "nullable": True, "minimum": 0, "maximum": 86400},  # 下单后多少秒过期；null=不过期
            },
            "additionalProperties": False,
        },
    }},
}

VALID_TRANSITIONS = {           # 状态机合法迁移图（测试断言的事实源）
    0: {"pay": 1, "cancel": 4},        # PENDING  -> PAID / CANCELLED(回补)
    1: {"ship": 2, "cancel": 4},       # PAID     -> SHIPPED / CANCELLED(回补)
    2: {"complete": 3},                # SHIPPED  -> DONE
    3: {},
    4: {},
}
CANCEL_RESTOCKS = {"cancel"}           # 触发库存回补的动作（PENDING/PAID 取消都回补）


def seed_data_sql():
    """造数据（L3 精确 fixture，禁止随机业务数据）。每条都可回答"为什么存在"。"""
    return [
        "DELETE FROM stock_log;", "DELETE FROM sale_order;", "DELETE FROM product;", "DELETE FROM buyer;",
        # 买家：1 alice有效(t1) / 2 bob冻结(t1) / 3 carol他租户有效(t2,跨租户越权靶) / 4 ghost已删(t1)
        "INSERT INTO buyer(id,name,tenant_id,status,deleted) VALUES (1,'alice',1,1,0),(2,'bob',1,0,0),(3,'carol',2,1,0),(4,'ghost',1,1,1)",
        # 商品：1 上架库存充足(price100) / 2 下架 / 3 已删 / 4 上架库存=3(price200,超发靶) / 5 上架库存=5(price100,定时回补靶)
        "INSERT INTO product(id,name,price_cents,stock,tenant_id,status,deleted) VALUES"
        " (1,'SKU-A',100,100000,1,1,0),(2,'SKU-B',100,50,1,0,0),(3,'SKU-C',100,50,1,1,1),"
        " (4,'SKU-D',200,3,1,1,0),(5,'SKU-E',100,5,1,1,0)",
    ]


def seed_order_sql(oid, product_id, buyer_id, qty, status, expiry=None, stock_deduct=True):
    """精确定位 fixture：状态机/超时/依赖 case 共用。默认按下单语义扣一次库存并记流水。"""
    i = str(int(oid))
    ex = "NULL" if expiry is None else str(int(expiry))
    stmts = [
        "DELETE FROM stock_log WHERE order_id=" + i,
        "DELETE FROM sale_order WHERE id=" + i,
        "INSERT INTO sale_order(id,idem_key,product_id,buyer_id,qty,unit_price_cents,total_cents,status,"
        "expires_at,created_at,updated_at,deleted) VALUES(" +
        f"{i},NULL,{product_id},{buyer_id},{qty},100,{100 * qty},{status},{ex},0,0,0)",
    ]
    if stock_deduct:
        stmts.append(f"UPDATE product SET stock=stock-{qty} WHERE id={product_id}")
        stmts.append(f"INSERT INTO stock_log(product_id,order_id,delta,at) VALUES({product_id},{i},-{qty},0)")
    return stmts


class SUT:
    """业务规则：全部 CONFIRMED 事实的运行时承载，Fact Resolver 从 DDL/OpenAPI 抽约束，这里实现行为。"""

    def __init__(self, db_path=":memory:"):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.isolation_level = None
        self.lock = threading.Lock()
        self.db.executescript(SCHEMA_SQL)
        for sql in seed_data_sql():
            self.db.execute(sql)
        self.now = lambda: int(time.time())
        self.dependency_url = None      # 外部支付网关（测试指向 FaultProxy）；None=不调依赖

    # ---------- 创建订单 ----------
    def create(self, body, now=None):
        now = self.now() if now is None else int(now)
        if not isinstance(body, dict):
            return 400, {"error": "invalid_body"}
        allowed = {"product_id", "buyer_id", "qty", "idem_key", "ttl_seconds"}
        if any(k not in allowed for k in body):
            return 400, {"error": "unknown_field"}
        for f in ("product_id", "buyer_id", "qty"):
            if body.get(f) is None:
                return 400, {"error": f"missing_field:{f}"}
        # 类型/范围（与 OpenAPI integer 事实一致）
        for f, hi in (("product_id", 2147483647), ("buyer_id", 2147483647), ("qty", 100)):
            v = body[f]
            if not isinstance(v, int) or isinstance(v, bool) or not (1 <= v <= hi):
                return 400, {"error": f"out_of_range:{f}"}
        idem, ttl = body.get("idem_key"), body.get("ttl_seconds")
        if idem is not None and not isinstance(idem, str):
            return 400, {"error": "invalid:idem_key"}
        if ttl is not None and (not isinstance(ttl, int) or isinstance(ttl, bool) or not (0 <= ttl <= 86400)):
            return 400, {"error": "invalid:ttl_seconds"}
        with self.lock:                                   # 共享连接：DB 访问全在锁内
            infl = self.db.execute("SELECT * FROM buyer WHERE id=?", (body["buyer_id"],)).fetchone()
            if not infl or infl["deleted"]:
                return 404, {"error": "buyer_not_found"}
            if not infl["status"]:
                return 403, {"error": "buyer_frozen"}
            prod = self.db.execute("SELECT * FROM product WHERE id=?", (body["product_id"],)).fetchone()
            if not prod or prod["deleted"]:
                return 404, {"error": "product_not_found"}
            if not prod["status"]:
                return 409, {"error": "product_off_shelf"}
            if prod["tenant_id"] != infl["tenant_id"]:
                return 403, {"error": "cross_tenant"}            # 跨租户越权：下单前拒，零写库
            if idem:                                      # 幂等：命中直接返回已有单
                row = self.db.execute("SELECT id FROM sale_order WHERE idem_key=?", (idem,)).fetchone()
                if row:
                    return 200, {"id": row["id"], "duplicate": True}
            # 原子扣减 + 超发防护：CAS，stock>=qty 才扣，撞不到=售罄（不写单）
            cur = self.db.execute("UPDATE product SET stock=stock-? WHERE id=? AND stock>=?",
                                  (body["qty"], body["product_id"], body["qty"]))
            if not cur.rowcount:
                return 409, {"error": "sold_out"}
            try:
                unit = prod["price_cents"]
                total = unit * body["qty"]
                oc = self.db.execute(
                    "INSERT INTO sale_order(idem_key,product_id,buyer_id,qty,unit_price_cents,total_cents,"
                    "status,expires_at,created_at,updated_at) VALUES(?,?,?,?,?,?,0,?,?,?)",
                    (idem, body["product_id"], body["buyer_id"], body["qty"], unit, total,
                     None if ttl is None else now + ttl, now, now))
                self.db.execute("INSERT INTO stock_log(product_id,order_id,delta,at) VALUES(?,?,?,?)",
                                (body["product_id"], oc.lastrowid, -body["qty"], now))
                return 201, {"id": oc.lastrowid, "total_cents": total}
            except sqlite3.IntegrityError:                # 并发撞 UNIQUE(idem_key) → 收敛为重放，非 409
                rb = self.db.execute("SELECT id FROM sale_order WHERE idem_key=?", (idem,)).fetchone()
                self.db.execute("UPDATE product SET stock=stock+? WHERE id=?", (body["qty"], body["product_id"]))
                if rb:
                    return 200, {"id": rb["id"], "duplicate": True}
                return 409, {"error": "concurrent_duplicate"}
            except Exception:
                self.db.execute("UPDATE product SET stock=stock+? WHERE id=?", (body["qty"], body["product_id"]))
                return 500, {"error": "internal"}

    def _gateway_ok(self):
        """外部支付网关真 HTTP 调用（2s 超时）。非 200 / ok!=true / 超时 / 拒连 / 坏 JSON = 依赖故障。"""
        import urllib.request
        try:
            req = urllib.request.Request(self.dependency_url, data=b"{}",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=2) as r:
                return r.status == 200 and json.loads(r.read()).get("ok") is True
        except Exception:
            return False

    # ---------- 状态迁移 / 支付 / 取消回补 ----------
    def transition(self, oid, action, now=None):
        now = self.now() if now is None else int(now)
        with self.lock:
            row = self.db.execute("SELECT * FROM sale_order WHERE id=? AND deleted=0", (oid,)).fetchone()
            if not row:
                return 404, {"error": "not_found"}
            if action not in VALID_TRANSITIONS.get(row["status"], {}):
                return 409, {"error": f"invalid_transition:status{row['status']}"}
            if action == "pay" and row["expires_at"] is not None and now > row["expires_at"]:
                return 409, {"error": "expired"}           # 超时单禁止支付（now==expiry 仍允许，含边界）
            if action == "pay" and self.dependency_url and not self._gateway_ok():
                return 502, {"error": "payment_gateway_down"}   # 故障点：任何写库之前
            new = VALID_TRANSITIONS[row["status"]][action]
            self.db.execute("UPDATE sale_order SET status=?, updated_at=? WHERE id=?", (new, now, oid))
            if action in CANCEL_RESTOCKS:                 # 取消：回补库存 + 流水，守恒不变
                pid = row["product_id"]
                self.db.execute("UPDATE product SET stock=stock+? WHERE id=?", (row["qty"], pid))
                self.db.execute("INSERT INTO stock_log(product_id,order_id,delta,at) VALUES(?,?,?,?)",
                                (pid, oid, row["qty"], now))
            return 200, {"status": new}

    # ---------- 定时：超时未支付自动取消并回补（CAS 防重复回补） ----------
    def tick(self, now=None):
        now = self.now() if now is None else int(now)
        with self.lock:
            rows = self.db.execute(
                "SELECT id,product_id,qty FROM sale_order WHERE status=0 AND deleted=0"
                " AND expires_at IS NOT NULL AND expires_at<=?", (now,)).fetchall()
            n = 0
            for r in rows:
                cur = self.db.execute("UPDATE sale_order SET status=4, updated_at=? WHERE id=? AND status=0",
                                      (now, r["id"]))
                if cur.rowcount:                           # 抢到才回补：重复 tick / 多实例不重复补
                    self.db.execute("UPDATE product SET stock=stock+? WHERE id=?", (r["qty"], r["product_id"]))
                    self.db.execute("INSERT INTO stock_log(product_id,order_id,delta,at) VALUES(?,?,?,?)",
                                    (r["product_id"], r["id"], r["qty"], now))
                    n += 1
            return 200, {"cancelled": n}


def make_handler(sut):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"                     # 真形态：keep-alive（响应恒带 Content-Length）
        def log_message(self, *a): pass

        def _reply(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_body(self):
            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                data = b""
                while True:
                    n = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                    if n == 0:
                        self.rfile.readline()
                        break
                    data += self.rfile.read(n)
                    self.rfile.readline()
                return data
            return self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))

        def _clock(self):
            raw = self.headers.get("X-Test-Now")
            try:
                return int(raw) if raw else None
            except ValueError:
                return "BAD"

        def do_POST(self):
            raw = self._read_body()                        # 先饮尽请求体再路由，保护 keep-alive
            if self.path == "/pay-gateway":                # 支付网关上游（FaultProxy ok 模式透传到此）
                return self._reply(200, {"ok": True})
            now = self._clock()
            if now == "BAD":
                return self._reply(400, {"error": "invalid_clock"})
            if self.path == "/scheduler/tick":
                return self._reply(*sut.tick(now=now))
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except Exception:
                return self._reply(400, {"error": "malformed_json"})
            if self.path == "/orders":
                self._reply(*sut.create(body, now=now))
            elif self.path.startswith("/orders/"):
                parts = self.path.split("/")
                if not (len(parts) == 4 and parts[3] in ("pay", "ship", "complete", "cancel")):
                    return self._reply(404, {"error": "route"})
                try:
                    oid = int(parts[2])
                    if not (0 <= oid <= 2**63 - 1):
                        raise ValueError
                except ValueError:
                    return self._reply(404, {"error": "not_found"})
                self._reply(*sut.transition(oid, parts[3], now=now))
            else:
                self._reply(404, {"error": "route"})

        def do_GET(self):
            self._read_body()
            if self.path == "/openapi.json":
                self._reply(200, OPENAPI)
            elif self.path == "/pay-gateway" or self.path.startswith("/orders") or self.path == "/scheduler/tick":
                return self._method_not_allowed("POST")
            else:
                self._reply(404, {"error": "route"})

        def _method_not_allowed(self, allow):
            data = b'{"error": "method_not_allowed"}'
            self.send_response(405)
            self.send_header("Content-Type", "application/json")
            self.send_header("Allow", allow)               # RFC 9110：405 必带 Allow
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _unsupported(self):
            self._read_body()
            self._method_not_allowed("GET" if self.path == "/openapi.json" else "POST")
        do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_TRACE = do_QUERY = _unsupported
    return H


def serve(db_path=":memory:", port=18201, sut=None):
    sut = sut or SUT(db_path)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(sut))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, sut


if __name__ == "__main__":
    import time as _t
    srv, s = serve(port=18201, db_path="sut.db")
    print("flash-sale SUT on http://127.0.0.1:18201")
    while True:
        _t.sleep(3600)
