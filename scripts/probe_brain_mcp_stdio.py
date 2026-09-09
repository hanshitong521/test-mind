"""End-to-end stdio check of the restored project-brain MCP server.

Speaks real JSON-RPC over a pipe (not an in-process call) so it exercises the
same path Cursor uses, then calls a tool with an ALIASED project id and asserts
the answer came from the canonical pool. Read-only tool: nothing is written to
memory, though the retrieval is logged to stats like any real call.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = r"E:\workA\A-skill\project-brain-agent"
PY = os.path.join(REPO, ".venv", "Scripts", "python.exe")


def send(proc: subprocess.Popen, obj: dict) -> None:
    proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
    proc.stdin.flush()


def recv(proc: subprocess.Popen) -> dict:
    line = proc.stdout.readline()
    if not line:
        raise SystemExit("server closed stdout; stderr:\n" + proc.stderr.read().decode("utf-8", "replace"))
    return json.loads(line.decode("utf-8"))


def main() -> int:
    fails: list[str] = []
    env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "src"), PYTHONIOENCODING="utf-8")
    # Isolate from any developer-local alias override so this proves the FILE.
    env["BRAIN_PROJECT_ALIASES"] = ""
    proc = subprocess.Popen(
        [PY, "-m", "brain_mcp"], cwd=REPO, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "stdio-probe", "version": "0"},
        }})
        init = recv(proc)
        si = (init.get("result") or {}).get("serverInfo") or {}
        print("initialize ->", si)
        if not si:
            fails.append("no serverInfo in initialize result: %r" % init)

        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = (recv(proc).get("result") or {}).get("tools") or []
        names = sorted(t["name"] for t in tools)
        print("tools/list -> %d %s" % (len(names), names))
        if len(names) != 5:
            fails.append("expected 5 WHY tools, got %d" % len(names))
        # The Cursor-compat fix: a row carrying outputSchema is rejected by Cursor.
        carrying = [t["name"] for t in tools if t.get("outputSchema") is not None]
        print("rows with outputSchema (must be none):", carrying)
        if carrying:
            fails.append("outputSchema leaked on %r" % carrying)

        # --- the actual requirement, over the wire, against the real pool
        for pid in ("shejiuTest", "shejiu-pro1", "shejiuPro"):
            send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "search_project_context",
                "arguments": {"project_id": pid, "query": "退款 幂等", "limit": 3},
            }})
            res = recv(proc).get("result") or {}
            text = "".join(c.get("text", "") for c in res.get("content") or [])
            try:
                payload = json.loads(text)
            except ValueError:
                fails.append("%s: tool did not return JSON: %r" % (pid, text[:120]))
                continue
            got = payload.get("project_id")
            mem = payload.get("memories") or []
            print("  %-11s -> project_id=%-10s memories=%d" % (pid, got, len(mem)))
            if got != "shejiuPro":
                fails.append("%s resolved to %r, expected the canonical shejiuPro" % (pid, got))
            if not mem:
                fails.append("%s returned no memories from the shared pool" % pid)

        # An unrelated project must NOT be pulled into the shared pool.
        send(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": "search_project_context",
            "arguments": {"project_id": "testMind", "query": "退款 幂等", "limit": 3},
        }})
        text = "".join(c.get("text", "") for c in ((recv(proc).get("result") or {}).get("content") or []))
        payload = json.loads(text) if text.strip().startswith("{") else {}
        got, mem = payload.get("project_id"), payload.get("memories") or []
        print("  %-11s -> project_id=%-10s memories=%d (must stay separate)" % ("testMind", got, len(mem)))
        if got != "testMind":
            fails.append("unrelated testMind was redirected to %r" % got)
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        err = proc.stderr.read().decode("utf-8", "replace").strip()
        if err:
            print("--- stderr ---")
            print(err[-1500:])

    print()
    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print("PASS: stdio handshake, 5 WHY tools, no outputSchema leak, "
          "aliased ids share the shejiuPro pool, unrelated id stays separate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
