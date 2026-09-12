#!/usr/bin/env python
# 杀剩 2 个变异：MUT_OFF_BY_ONE（unlimited=True）+ MUT_NEGATE（duplicate body 断言）
# 直接在 e2e.py 末尾的 special_cases 列表里追加
import json
from pathlib import Path

ROOT = Path(".")
E2E  = ROOT / "examples" / "red-packet" / "e2e.py"

# 2 个新 case 的 spec（Python 字面量，每条尾部带逗号）
NEW_CASES = [
    # 杀 MUT_OFF_BY_ONE：unlimited=True 时 remaining 必须 == 2147483647
    # 变异后是 2147483646，db_count 会发现差异
    (
        '        # ── V9 §11 杀残余变异：MUT_OFF_BY_ONE & MUT_NEGATE ──\n'
        '        {"id": "P0-MUT-off-by-one-unlimited", "category": "MUTATION_KILL", "priority": "P0",\n'
        '         "source": "mutation:MUT_OFF_BY_ONE",\n'
        '         "regression_reason": "unlimited=True 时 remaining 必须 = 2147483647（变异会变 2147483646）",\n'
        '         "action": {"kind": "http", "method": "POST", "path": "/red-packets",\n'
        '                    "body": {"name": "无限包", "amount_cents": 100, "quantity": 5, "influencer_id": 1, "created_by": 1, "unlimited": True},\n'
        '                    "save": {"pid": "id"}},\n'
        '         "expected": {"http": 201,\n'
        '                      "db_rows": [{"table": "red_packet", "key": {"id": "{{pid}}"},\n'
        '                                   "subset": {"remaining": 2147483647, "unlimited": 1}}]}},\n'
    ),
    # 杀 MUT_NEGATE：duplicate 路径必须返回 body 包含 "duplicate": True
    # 变异走 error path 但也返回 duplicate=True，所以再补一条：duplicate=True 的"前置顺序"断言
    # 用一个 distinct idem_key：先发两次（确保 duplicate 路径），第二次必须 200 + duplicate=True
    (
        '        {"id": "P0-MUT-negate-idem-body", "category": "MUTATION_KILL", "priority": "P0",\n'
        '         "source": "mutation:MUT_NEGATE",\n'
        '         "regression_reason": "idempotent replay 必须返回 duplicate=True（覆盖 if idem 翻转）",\n'
        '         "action": {"kind": "sequence", "steps": [\n'
        '             {"path": "/red-packets", "body": {**base, "idem_key": "negate-kill"}, "expect_status": 201},\n'
        '             {"path": "/red-packets", "body": {**base, "idem_key": "negate-kill"}, "expect_status": 200,\n'
        '              "expect_json": {"duplicate": True}}]},\n'
        '         "expected": {"db_count": [{"sql": "SELECT COUNT(*) FROM red_packet WHERE idem_key=\'negate-kill\'", "expect": 1}]}},\n'
    ),
]

# 找到 special_cases 列表末尾前插入
import re
src = E2E.read_text(encoding="utf-8")
# 移除上一轮如果注入了
src = re.sub(
    r'\n        # ── V9 §11 杀残余变异.*?(?=\n    \]\n)',
    '\n', src, count=1, flags=re.DOTALL,
)

m = re.search(r'(\n    \]\n)\n\nREGRESSION_SEEDS', src)
if not m:
    raise SystemExit("could not find special_cases list end")
new_src = src[:m.start()] + "\n" + "".join(NEW_CASES) + m.group(1) + "\n\nREGRESSION_SEEDS" + src[m.end():]
E2E.write_text(new_src, encoding="utf-8")
print(f"OK: appended 2 mutation-killer cases into {E2E}")
print(f"file size: {len(src)} -> {len(new_src)} bytes")
