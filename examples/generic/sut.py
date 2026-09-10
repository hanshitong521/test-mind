"""三领域最小靶场：库存关系 / GET 分页 / 幂等写。"""
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SCHEMA = """
CREATE TABLE warehouse(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE stock(sku TEXT, warehouse_id INTEGER, qty INTEGER);
CREATE TABLE coupon(code TEXT PRIMARY KEY, request_id TEXT UNIQUE);
INSERT INTO warehouse VALUES (1,'main'),(2,'backup');
"""


class SUT:
    def __init__(self, db=":memory:"):
        self.db = sqlite3.connect(db, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def create_stock(self, body):
        if not isinstance(body, dict):
            return 400, {"error": "body"}
        for k in ("sku", "warehouse_id", "qty"):
            if k not in body or body[k] is None:
                return 400, {"error": k}
            if isinstance(body[k], str) and body[k].strip() == "":
                return 400, {"error": k}
        try:
            wid, qty = int(body["warehouse_id"]), int(body["qty"])
        except (TypeError, ValueError):
            return 400, {"error": "type"}
        if not isinstance(body["sku"], str):
            return 400, {"error": "sku"}
        if qty < 1 or qty > 100:
            return 400, {"error": "qty"}
        if not self.db.execute("SELECT 1 FROM warehouse WHERE id=?", (wid,)).fetchone():
            return 404, {"error": "warehouse"}
        self.db.execute("INSERT INTO stock VALUES (?,?,?)", (body["sku"], wid, qty))
        self.db.commit()
        return 201, {"ok": True}

    def list_items(self, page, size):
        page, size = int(page), int(size)
        if page < 1 or size < 1 or size > 50:
            return 400, {"error": "bad page/size"}
        return 200, {"page": page, "size": size, "items": []}

    def create_coupon(self, body):
        rid = body.get("request_id")
        if rid:
            row = self.db.execute("SELECT code FROM coupon WHERE request_id=?", (rid,)).fetchone()
            if row:
                return 200, {"code": row["code"], "duplicate": True}
        code = body.get("code", "")
        if len(code) < 3:
            return 400, {"error": "code"}
        try:
            self.db.execute("INSERT INTO coupon(code,request_id) VALUES (?,?)", (code, rid))
            self.db.commit()
            return 201, {"code": code}
        except sqlite3.IntegrityError:
            return 200, {"code": code, "duplicate": True}


def serve(port=18310, db_path=":memory:"):
    sut = SUT(db_path)
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if urlparse(self.path).path != "/items":
                return self._json(404, {"error": "nf"})
            q = parse_qs(urlparse(self.path).query)
            with lock:
                c, o = sut.list_items(q.get("page", ["1"])[0], q.get("size", ["10"])[0])
            self._json(c, o)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            path = urlparse(self.path).path
            with lock:
                if path == "/stock":
                    c, o = sut.create_stock(body)
                elif path == "/coupons":
                    c, o = sut.create_coupon(body)
                else:
                    c, o = 404, {"error": "nf"}
            self._json(c, o)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, sut
