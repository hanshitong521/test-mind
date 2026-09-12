"""看板展示层 —— 标准库 http.server，只读、只监听回环地址。

安全约束（刻意的）：
- **只绑 127.0.0.1**，不提供 `--host 0.0.0.0`。看板会暴露本机路径与报告内容，
  不该被局域网看到。
- 只读：没有任何写接口。所有数据来自 `aggregate` 的扫描结果。
- 静态资源只从本包目录读取，路径经白名单校验，不做目录穿越。
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import aggregate, playbook

_HERE = Path(__file__).resolve().parent
_STATIC = {"index.html": _HERE / "index.html"}


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")


def build_handler(root: Path):
    """按 root 闭包出 handler 类（便于测试注入临时仓）。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "TestMindDashboard/1.0"

        # ── 工具 ──
        def _send(self, code, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, _json_bytes(obj), "application/json; charset=utf-8")

        def _text(self, s, code=200):
            self._send(code, s.encode("utf-8"), "text/plain; charset=utf-8")

        def log_message(self, fmt, *args):  # 静音；需要时用 --verbose
            if getattr(self.server, "verbose", False):
                super().log_message(fmt, *args)

        # ── 路由 ──
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
            try:
                if path in ("/", "/index.html"):
                    f = _STATIC["index.html"]
                    if not f.is_file():
                        return self._text("index.html missing", 500)
                    return self._send(200, f.read_bytes(), "text/html; charset=utf-8")

                if path == "/api/health":
                    return self._json({"ok": True, "version": aggregate.version(root)})

                if path == "/api/bundle":
                    limit = _int(qs, "limit", 40)
                    return self._json(aggregate.bundle(root, limit))

                if path == "/api/summary":
                    return self._json(aggregate.summary(root))

                if path == "/api/activity":
                    limit = _int(qs, "limit", 40)
                    return self._json(aggregate.activity(root, limit))

                if path == "/api/assets":
                    return self._json(aggregate.assets(root))

                if path == "/api/playbook":
                    return self._json(playbook.playbook_payload())

                if path == "/api/route":
                    q = (qs.get("q") or [""])[0]
                    return self._json({"query": q, "hits": playbook.route(q)})

                return self._json({"error": "not_found", "path": path}, 404)
            except BrokenPipeError:
                return
            except Exception as exc:  # 看板不该因为一个坏产物就 500 一片
                return self._json({"error": "internal", "detail": repr(exc)}, 500)

    return Handler


def _int(qs, key, default):
    try:
        return int((qs.get(key) or [default])[0])
    except (TypeError, ValueError):
        return default


def create_server(root=None, host: str = "127.0.0.1", port: int = 8901, verbose: bool = False):
    """构造（不启动）服务器，便于测试里起线程。"""
    root = Path(root or aggregate.ROOT)
    httpd = ThreadingHTTPServer((host, port), build_handler(root))
    httpd.daemon_threads = True
    httpd.verbose = verbose
    return httpd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="testmind-dashboard",
                                 description="TestMind 只读看板（本机）")
    ap.add_argument("--root", default=str(aggregate.ROOT), help="test-Mind 仓根目录")
    ap.add_argument("--port", type=int, default=8901)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not (root / "reports").is_dir() and not (root / "testmind").is_dir():
        print(f"警告：{root} 看起来不是 test-Mind 仓根（缺 reports/ 与 testmind/）", file=sys.stderr)

    httpd = create_server(root, "127.0.0.1", args.port, args.verbose)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"TestMind 看板已启动：{url}")
    print(f"  数据根：{root}")
    print("  仅监听 127.0.0.1；Ctrl+C 停止")
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
