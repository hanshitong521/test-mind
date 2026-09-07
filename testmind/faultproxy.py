# testmind/faultproxy.py — stdlib 反向代理故障注入（WireMock §24 矩阵的零依赖替代）
# 用法：SUT 的外部依赖 URL 指到本代理；case 的 fault.mode 切换 200/5xx/delay/refuse/bad_json
import json, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODES = {"ok", "status", "delay", "refuse", "bad_json"}


class FaultProxy:
    def __init__(self, upstream_base, port=18199):
        self.upstream = upstream_base.rstrip("/")
        self.mode = {"kind": "ok", "code": 500, "seconds": 3}
        self.calls = 0
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self): self._fwd("GET")
            def do_POST(self): self._fwd("POST")
            def do_PUT(self): self._fwd("PUT")

            def _reply(self, code, data=b"{}"):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _fwd(self, method):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                with outer.lock:
                    outer.calls += 1
                    m = dict(outer.mode)
                if m["kind"] == "refuse":
                    self.close_connection = True
                    self.connection.close()           # 连接层故障
                    return
                if m["kind"] == "delay":
                    time.sleep(m["seconds"])
                if m["kind"] == "status":
                    return self._reply(m["code"], json.dumps({"fault": m["code"]}).encode())
                if m["kind"] == "bad_json":
                    return self._reply(200, b'{"not": valid json...')
                try:                                   # ok → 透传上游
                    req = urllib.request.Request(outer.upstream + self.path, method=method,
                                                 data=body or None, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=20) as r:
                        self._reply(r.status, r.read())
                except urllib.error.HTTPError as e:
                    self._reply(e.code, e.read())
                except Exception:
                    self._reply(502, b'{"error":"upstream_down"}')

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def set_mode(self, spec):
        """spec: "ok" | "refuse" | "bad_json" | {"kind":"status","code":503} | {"kind":"delay","seconds":2}"""
        if isinstance(spec, str):
            spec = {"kind": spec}
        assert spec["kind"] in MODES, spec
        if spec["kind"] == "status":
            spec = {"kind": "status", "code": spec.get("code", 500)}
        if spec["kind"] == "delay":
            spec = {"kind": "delay", "seconds": spec.get("seconds", 3)}
        with self.lock:
            self.mode = spec

    def url(self):
        return f"http://127.0.0.1:{self.srv.server_address[1]}"

    def shutdown(self):
        self.srv.shutdown()
