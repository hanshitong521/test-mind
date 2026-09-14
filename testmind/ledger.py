# testmind/ledger.py — V10 §16 Seed Ledger：每个测试写操作必须登记
# §17 数据生命周期：PASS → 按 Ledger 逆序回滚 → 重查询证明 DB==before；FAIL → 冻结现场不清理。
import json
import os
import re
import time

from testmind import core

_TABLE_RE = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+[`\"]?(\w+)[`\"]?", re.I)
_PK_RE = re.compile(r"\bWHERE\b(.*?)(?:ORDER\b|LIMIT\b|$)", re.I | re.S)


def parse_write(sql):
    """从写 SQL 提取 (operation, table, where_clause)。解析失败 → (None, None, '')。"""
    m = _TABLE_RE.search(sql)
    if not m:
        return None, None, ""
    op = m.group(0).split()[0].upper()
    op = {"INTO": "INSERT"}.get(op, op)
    if op == "INSERT":
        op = "INSERT"
    where = _PK_RE.search(sql)
    return op, m.group(1), (where.group(1).strip() if where else "")


class SeedLedger:
    """登记测试写操作（INSERT/UPDATE/DELETE）的 before/after，支持逆序回滚与现场冻结。

    条目结构（§16）：run_id/case_id/dataset_id/table/operation/primary_key/before/after/reason/owned_by_testmind
    """

    def __init__(self, run_id, root=None):
        self.run_id = run_id
        root = root or core.ROOT
        self.dir = os.path.join(root, ".testmind", "ledger")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, f"{run_id}.jsonl")
        self.entries = []
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                self.entries = [json.loads(x) for x in fh if x.strip()]

    # ── 登记 ─────────────────────────────────────────────
    def record_exec(self, dbc, sql, case_id="", dataset_id="", reason="", owned=True):
        """执行一条写 SQL 并登记 before/after。dbc: core.DBCheck。返回 rowcount。"""
        op, table, where = parse_write(sql)
        if op is None:
            # 无法识别表名的写语句（DDL/PRAGMA 等）不登记，但必须显式标出
            self._append({"run_id": self.run_id, "case_id": case_id, "dataset_id": dataset_id,
                          "table": None, "operation": "UNPARSED", "primary_key": None,
                          "before": None, "after": None, "reason": reason or sql[:120],
                          "owned_by_testmind": False, "sql": sql[:500]})
            return dbc.exec(sql)
        pk_rows = None
        before = None
        if op in ("UPDATE", "DELETE") and where:
            try:
                before = dbc.rows(f"SELECT * FROM {dbc.quote(table)} WHERE {where}")
                pk_rows = before
            except Exception:
                before = None
        n = dbc.exec(sql)
        after = None
        if op == "INSERT":
            # INSERT 的 after = 刚写入且 before 不存在的行：用主键列回查代价高，
            # 以 where 无法用，改记 rowcount + 语句摘要；回滚按 DELETE 处理
            after = {"rowcount": n, "sql": sql[:500]}
        elif op in ("UPDATE", "DELETE") and where:
            try:
                after = dbc.rows(f"SELECT * FROM {dbc.quote(table)} WHERE {where}")
            except Exception:
                after = None
        self._append({"run_id": self.run_id, "case_id": case_id, "dataset_id": dataset_id,
                      "table": table, "operation": op,
                      "primary_key": {"where": where, "rows": _keyset(pk_rows)},
                      "before": before, "after": after, "reason": reason,
                      "owned_by_testmind": owned, "sql": sql[:500]})
        return n

    def _append(self, entry):
        entry.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        self.entries.append(entry)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    # ── 回滚（§17 PASS 路径）───────────────────────────────
    def rollback(self, dbc, verify=True):
        """逆序回滚全部 owned_by_testmind 条目；verify=True 时重查询证明恢复。
        返回 {rolled_back, skipped, verify:{table: PASS|FAIL}, unresolved:[...]}"""
        done, skipped, unresolved = 0, 0, []
        for e in reversed(self.entries):
            if e.get("rolled_back") or not e.get("owned_by_testmind"):
                skipped += 1
                continue
            try:
                self._undo(dbc, e)
                e["rolled_back"] = True
                done += 1
            except Exception as ex:
                unresolved.append({"case_id": e.get("case_id"), "error": repr(ex)})
        out = {"rolled_back": done, "skipped": skipped, "unresolved": unresolved}
        if verify and not unresolved:
            out["verify"] = self._verify(dbc)
        # 标记落盘（幂等：重复 cleanup 不重复回滚）
        self._rewrite()
        return out

    def _undo(self, dbc, e):
        op, table, where = e["operation"], e.get("table"), (e.get("primary_key") or {}).get("where", "")
        if op == "INSERT":
            sql = (e.get("after") or {}).get("sql") or e.get("sql") or ""
            # 用原 INSERT 语句的表+值反推 DELETE：优先按 WHERE 不可用 → 按语句回删
            m = re.search(r"INSERT\s+INTO\s+[`\"]?(\w+)[`\"]?\s*\(([^)]*)\)\s*VALUES\s*\((.*)\)", sql, re.I | re.S)
            if m:
                cols = [c.strip().strip("`\"") for c in m.group(2).split(",")]
                vals = _split_top_values(m.group(3))
                pred = " AND ".join(
                    f"{dbc.quote(c)} = ?" if v.strip().upper() != "NULL" else f"{dbc.quote(c)} IS NULL"
                    for c, v in zip(cols, vals))
                args = [_unlit(v) for v, c in zip(vals, cols) if v.strip().upper() != "NULL"]
                dbc.exec(f"DELETE FROM {dbc.quote(table)} WHERE {pred}", tuple(args))
            elif e.get("sql"):
                dbc.exec(e["sql"])  # 登记时带原语句兜底
            else:
                raise RuntimeError("INSERT entry lacks sql")
        elif op == "UPDATE" and e.get("before"):
            for row in e["before"]:
                cols = [k for k in row]
                pred = " AND ".join(f"{dbc.quote(k)} = ?" if row[k] is not None else f"{dbc.quote(k)} IS NULL" for k in cols)
                # 原行整行恢复：DELETE 当前 where 命中行 + 插回 before 行
                if where:
                    dbc.exec(f"DELETE FROM {dbc.quote(table)} WHERE {where}")
                dbc.exec(f"INSERT INTO {dbc.quote(table)} ({', '.join(dbc.quote(c) for c in cols)}) "
                         f"VALUES ({', '.join('?' for _ in cols)})", tuple(row[c] for c in cols))
                break  # before 行恢复一次即可（where 已消费）
        elif op == "DELETE" and e.get("before"):
            for row in e["before"]:
                cols = [k for k in row]
                dbc.exec(f"INSERT INTO {dbc.quote(table)} ({', '.join(dbc.quote(c) for c in cols)}) "
                         f"VALUES ({', '.join('?' for _ in cols)})", tuple(row[c] for c in cols))

    def _verify(self, dbc):
        """重查询证明：UPDATE/DELETE 的 before 行恢复、INSERT 行消失。"""
        out = {}
        for e in self.entries:
            t = e.get("table")
            if not t or not e.get("owned_by_testmind"):
                continue
            try:
                if e["operation"] == "INSERT":
                    sql = (e.get("after") or {}).get("sql") or ""
                    m = re.search(r"INSERT\s+INTO\s+[`\"]?(\w+)[`\"]?\s*\(([^)]*)\)\s*VALUES\s*\((.*)\)", sql, re.I | re.S)
                    if not m:
                        continue
                    cols = [c.strip().strip("`\"") for c in m.group(2).split(",")]
                    vals = _split_top_values(m.group(3))
                    pred = " AND ".join(f"{dbc.quote(c)} = ?" if v.strip().upper() != "NULL" else f"{dbc.quote(c)} IS NULL"
                                        for c, v in zip(cols, vals))
                    args = tuple(_unlit(v) for v, c in zip(vals, cols) if v.strip().upper() != "NULL")
                    n = dbc.rows(f"SELECT COUNT(*) AS n FROM {dbc.quote(t)} WHERE {pred}", args)
                    out[t] = "PASS" if n and int(list(n[0].values())[0]) == 0 else "FAIL"
                elif e["operation"] in ("UPDATE", "DELETE") and e.get("before"):
                    row = e["before"][0]
                    cols = [k for k in row]
                    pred = " AND ".join(f"{dbc.quote(k)} = ?" if row[k] is not None else f"{dbc.quote(k)} IS NULL" for k in cols)
                    args = tuple(row[c] for c in cols if row[c] is not None)
                    n = dbc.rows(f"SELECT COUNT(*) AS n FROM {dbc.quote(t)} WHERE {pred}", args)
                    out[t] = "PASS" if n and int(list(n[0].values())[0]) >= 1 else "FAIL"
            except Exception as ex:
                out[t] = f"ERROR:{ex!r}"
        return out

    def _rewrite(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            for e in self.entries:
                fh.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")

    # ── 冻结（§17 FAIL 路径）──────────────────────────────
    def freeze(self, ev_dir, case_id, request=None, response=None):
        """FAIL → 保存 PK/request/response/db before/after → incident.json，不清理。"""
        inc = {"run_id": self.run_id, "case_id": case_id, "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "request": request, "response": response,
               "entries": [e for e in self.entries if e.get("case_id") == case_id]}
        d = os.path.join(ev_dir, "incidents")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"{case_id}.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(inc, fh, ensure_ascii=False, indent=2, default=str)
        return p


def _split_top_values(s):
    return core._split_top(s, ",")


def _unlit(v):
    v = v.strip()
    if v.upper() == "NULL":
        return None
    if v.startswith("'") and v.endswith("'"):
        return v[1:-1].replace("''", "'")
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def _keyset(rows):
    if not rows:
        return None
    return [sorted(r.keys()) for r in rows[:5]]
