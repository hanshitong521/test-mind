"""看板数据层 —— 零依赖扫描 test-Mind 仓内真实产物。

数据源（全部是仓内既有文件，不新增状态）：
- `reports/<ts>-<hash>/gate.json` + `results.json`  → 项目动态 / 门禁结论
- `regression/cases.json`                            → 回归锚资产
- `.agent/state/{project,task,decision}_state.json`  → 今日待处理 / 当前阶段
- `.agent/rules/*.md`、`skills/*/SKILL.md`、`examples/*/` → 资产库
- `pyproject.toml` 版本                              → 看板抬头

设计纪律：
- **目录缺失 / 文件损坏一律降级，不抛异常**（看板不能因为某个产物坏了就打不开）。
- **带签名缓存**：reports 目录新增、cases.json 或 state 变更才重扫；否则复用。
  947 个报告目录，不做缓存会让每次请求都打满磁盘。
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_CACHE: dict = {}
_CACHE_KEY = None
# 报告扫描结果缓存：summary / activity / bundle 共享同一份，避免各扫一遍。
_SCAN_CACHE = None
_SCAN_KEY = None


# ─────────────────────────── 基础 IO ───────────────────────────

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _dir_ts(name):
    """从 `20260911-020053-f911b5` 取时间；取不到返回 None。"""
    try:
        return datetime.strptime(name[:15], "%Y%m%d-%H%M%S")
    except Exception:
        return None


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _signature(root: Path):
    """廉价签名：只读目录自身 mtime + 条目数，**不递归**。

    早期版本每次 iterdir() 全量列目录再取 max(name)，1000+ 目录时热路径仍要
    150ms+。目录 mtime 在增删条目时由文件系统自动更新，足以判定"要不要重扫"。
    """
    reports = root / "reports"
    try:
        with os.scandir(reports) as it:
            n = 0
            for _ in it:
                n += 1
    except OSError:
        n = -1
    return (
        str(root),
        n,
        _mtime(reports),
        _mtime(root / "regression" / "cases.json"),
        _mtime(root / ".agent" / "state"),
    )


# ─────────────────────────── 报告 / 项目动态 ───────────────────────────

def _report_dirs(root: Path):
    reports = root / "reports"
    if not reports.is_dir():
        return []
    try:
        dirs = [d for d in reports.iterdir() if d.is_dir()]
    except OSError:
        return []
    dirs.sort(key=lambda d: d.name, reverse=True)
    return dirs


# 目录存在但没有任何产物 = 空壳（只建了目录没跑出东西）。
EMPTY = "EMPTY"
# 有产物但 gate 明确给出非判定态的状态词（沿用 SKILL.md 词表）。
JUDGED = ("PASS", "FAIL")


def scan_reports(root=None, limit=None):
    """解析报告目录，产出时间线行（带签名缓存，全端点共享）。

    默认**全量**扫描：有签名缓存后每轮只算一次。`limit` 只截断返回的行数，
    **不影响统计口径**——早期版本只扫最近 80 个，把空壳目录算进分母，把真实
    通过率 95.5% 稀释成 21.2%，是个会误导人的假口径。
    """
    global _SCAN_CACHE, _SCAN_KEY
    root = Path(root or ROOT)
    key = _signature(root)
    if _SCAN_KEY == key and _SCAN_CACHE is not None:
        rows = _SCAN_CACHE
    else:
        rows = _scan_reports_uncached(root)
        _SCAN_CACHE, _SCAN_KEY = rows, key
    return rows[:limit] if limit else rows


def _scan_reports_uncached(root: Path):
    rows = []
    for d in _report_dirs(root):
        gate_p, res_p = d / "gate.json", d / "results.json"
        has_gate, has_res = gate_p.is_file(), res_p.is_file()
        if not has_gate and not has_res:
            rows.append({
                "id": d.name, "ts": d.name[:15], "gate": EMPTY, "empty": True,
                "why": "", "cases": 0, "passed": 0, "failed": 0,
                "contract_version": None, "git_head": "", "has_gate": False,
                "failed_ids": [],
            })
            continue
        gate = _read_json(gate_p) or {}
        results = _read_json(res_p)
        if not isinstance(results, list):
            results = []
        passed = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "PASS")
        failed = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "FAIL")
        if gate.get("status"):
            status = gate["status"]
        elif results:
            status = "PASS" if failed == 0 else "FAIL"
        else:
            status = "UNKNOWN"
        rows.append({
            "id": d.name,
            "ts": d.name[:15],
            "gate": status,
            "empty": False,
            "why": gate.get("why") or "",
            "cases": len(results),
            "passed": passed,
            "failed": failed,
            "contract_version": gate.get("contract_version"),
            "git_head": (gate.get("git_head") or "")[:8],
            "has_gate": has_gate,
            "failed_ids": [r.get("id") for r in results
                           if isinstance(r, dict) and r.get("status") == "FAIL"][:8],
        })
    return rows


def daily_trend(rows, days: int = 30):
    """按天聚合近 days 天的**有效运行**（空壳目录不计入，否则图表被噪声压平）。"""
    today = datetime.now().date()
    buckets = {}
    for i in range(days):
        buckets[(today - timedelta(days=days - 1 - i)).isoformat()] = {
            "runs": 0, "pass": 0, "fail": 0, "other": 0}
    for r in rows:
        if r.get("empty"):
            continue
        dt = _dir_ts(r["id"])
        if not dt:
            continue
        key = dt.date().isoformat()
        if key not in buckets:
            continue
        buckets[key]["runs"] += 1
        if r["gate"] == "PASS":
            buckets[key]["pass"] += 1
        elif r["gate"] == "FAIL":
            buckets[key]["fail"] += 1
        else:
            buckets[key]["other"] += 1
    return [{"date": k, **v} for k, v in buckets.items()]


def summarize_reports(rows):
    """统计口径：**通过率 = PASS/(PASS+FAIL)**，只算真正给出判定的运行。

    非判定态（NOT_TESTED / TOOL_ERROR）与空壳目录单独列，不混进分母。
    """
    total_dirs = len(rows)
    empty = sum(1 for r in rows if r.get("empty"))
    judged = [r for r in rows if r["gate"] in JUDGED]
    pass_n = sum(1 for r in judged if r["gate"] == "PASS")
    fail_n = sum(1 for r in judged if r["gate"] == "FAIL")
    not_tested = sum(1 for r in rows if r["gate"] == "NOT_TESTED")
    tool_error = sum(1 for r in rows if r["gate"] == "TOOL_ERROR")
    unknown = sum(1 for r in rows if r["gate"] == "UNKNOWN")
    effective = total_dirs - empty
    latest = next((r for r in rows if not r.get("empty")), None)
    week = datetime.now() - timedelta(days=7)
    runs_7d = sum(1 for r in rows if not r.get("empty")
                  and (_dir_ts(r["id"]) or datetime.min) >= week)
    return {
        "total_dirs": total_dirs,
        "empty_shells": empty,
        "empty_ratio": round(empty / total_dirs * 100, 1) if total_dirs else None,
        "effective_runs": effective,
        "judged": len(judged),
        "gate_pass": pass_n,
        "gate_fail": fail_n,
        "not_tested": not_tested,
        "tool_error": tool_error,
        "unknown": unknown,
        "runs_7d": runs_7d,
        "pass_rate": round(pass_n / len(judged) * 100, 1) if judged else None,
        "pass_rate_basis": "PASS/(PASS+FAIL)",
        "cases_total": sum(r["cases"] for r in rows),
        "cases_failed": sum(r["failed"] for r in rows),
        "latest": latest,
    }


# ─────────────────────────── 资产库 ───────────────────────────

def load_regression(root=None):
    root = Path(root or ROOT)
    data = _read_json(root / "regression" / "cases.json")
    if not isinstance(data, list):
        data = []
    items = []
    for c in data:
        if not isinstance(c, dict):
            continue
        action = c.get("action") or {}
        items.append({
            "id": c.get("id"),
            "priority": c.get("priority"),
            "category": c.get("category"),
            "source": c.get("source"),
            "kind": action.get("kind") if isinstance(action, dict) else None,
        })
    by_pri = Counter(i["priority"] for i in items if i["priority"])
    by_cat = Counter(i["category"] for i in items if i["category"])
    return {
        "count": len(items),
        "by_priority": dict(by_pri),
        "by_category": dict(by_cat),
        "items": items,
    }


_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _skill_meta(path: Path):
    """从 SKILL.md frontmatter 取 name/description（取不到就退化为目录名）。"""
    name, desc = path.parent.name, ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"name": name, "description": desc}
    m = _FRONT_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip().strip('"\'')
            elif line.startswith("description:"):
                desc = line.split(":", 1)[1].strip().strip('"\'')
    return {"name": name, "description": desc}


def load_assets(root=None):
    root = Path(root or ROOT)
    skills = []
    sdir = root / "skills"
    if sdir.is_dir():
        for d in sorted(sdir.iterdir()):
            skill_md = d / "SKILL.md"
            if d.is_dir() and skill_md.is_file():
                skills.append(_skill_meta(skill_md))

    rules = []
    rdir = root / ".agent" / "rules"
    if rdir.is_dir():
        rules = sorted(p.name for p in rdir.glob("*.md"))

    examples = []
    edir = root / "examples"
    if edir.is_dir():
        for d in sorted(edir.iterdir()):
            if d.is_dir():
                n = sum(1 for p in d.rglob("*") if p.is_file())
                examples.append({"name": d.name, "files": n})

    return {"skills": skills, "rules": rules, "examples": examples,
            "regression": load_regression(root)}


# ─────────────────────────── 今日待处理 / 状态 ───────────────────────────

def load_state(root=None):
    root = Path(root or ROOT)
    sdir = root / ".agent" / "state"
    project = _read_json(sdir / "project_state.json") or {}
    task = _read_json(sdir / "task_state.json") or {}
    decision = _read_json(sdir / "decision_state.json") or {}

    todo = []
    if project.get("current"):
        todo.append({"kind": "当前目标", "text": project["current"], "tone": "info"})
    for b in (project.get("blocked") or []):
        todo.append({"kind": "阻塞", "text": str(b), "tone": "warn"})
    for r in (task.get("open_risks") or []):
        todo.append({"kind": "未决风险", "text": str(r), "tone": "warn"})
    for c in (project.get("constraints") or []):
        todo.append({"kind": "约束", "text": str(c), "tone": "muted"})

    return {
        "project": {
            "goal": project.get("goal"),
            "current": project.get("current"),
            "completed": project.get("completed") or [],
            "blocked": project.get("blocked") or [],
            "constraints": project.get("constraints") or [],
            "updated": project.get("updated"),
        },
        "task": {
            "phase": task.get("phase"),
            "open_risks": task.get("open_risks") or [],
            "last_verified": task.get("last_verified"),
            "updated": task.get("updated"),
        },
        "decisions": decision.get("decisions") or [],
        "todo": todo,
    }


# ─────────────────────────── 组装 ───────────────────────────

def version(root=None):
    root = Path(root or ROOT)
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else None


def summary(root=None):
    root = Path(root or ROOT)
    rows = scan_reports(root)
    return {
        "version": version(root),
        "reports": summarize_reports(rows),
        "trend": daily_trend(rows),
        "state": load_state(root),
        "regression": load_regression(root),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def activity(root=None, limit: int = 40):
    """时间线只列**有产物**的运行；空壳目录会淹没真正的动态。"""
    root = Path(root or ROOT)
    rows = [r for r in scan_reports(root) if not r.get("empty")]
    return {"items": rows[:limit]}


def assets(root=None):
    root = Path(root or ROOT)
    return load_assets(root)


def bundle(root=None, limit: int = 40):
    """一次性打包看板首屏需要的全部数据（带签名缓存）。

    缓存键必须带上 `limit`——否则先请求 limit=10 再请求 limit=40 会拿到被截断的旧包。
    """
    global _CACHE, _CACHE_KEY
    root = Path(root or ROOT)
    key = _signature(root) + (limit,)
    if _CACHE_KEY == key and "bundle" in _CACHE:
        return _CACHE["bundle"]
    from . import playbook

    rows = scan_reports(root)
    timeline = [r for r in rows if not r.get("empty")][:limit]
    data = {
        "version": version(root),
        "reports": summarize_reports(rows),
        "trend": daily_trend(rows),
        "activity": timeline,
        "state": load_state(root),
        "assets": load_assets(root),
        "playbook": playbook.playbook_payload(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _CACHE, _CACHE_KEY = {"bundle": data}, key
    return data


def reset_cache():
    global _CACHE, _CACHE_KEY, _SCAN_CACHE, _SCAN_KEY
    _CACHE, _CACHE_KEY = {}, None
    _SCAN_CACHE, _SCAN_KEY = None, None
