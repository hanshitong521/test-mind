#!/usr/bin/env python3
"""scripts/dashboard.py — 启动 TestMind 只读看板。

    python scripts/dashboard.py                 # 127.0.0.1:8901
    python scripts/dashboard.py --port 8902 --open
    python scripts/dashboard.py --check         # 不起服务，只自检数据层

与 `scripts/verify-all.py` 同一纪律：显式把仓根塞进 sys.path，不依赖调用时的 cwd。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from testmind.dashboard import aggregate, server  # noqa: E402


def _check() -> int:
    import json
    data = aggregate.bundle(ROOT)
    r = data["reports"]
    print(f"仓根        : {ROOT}")
    print(f"版本        : {data['version']}")
    print(f"报告目录    : 总 {r['total_dirs']} / 有效 {r['effective_runs']} / 空壳 {r['empty_shells']} ({r['empty_ratio']}%)")
    print(f"门禁        : PASS {r['gate_pass']} / FAIL {r['gate_fail']} / NOT_TESTED {r['not_tested']} / TOOL_ERROR {r['tool_error']}")
    print(f"通过率      : {r['pass_rate']}%  （口径 {r['pass_rate_basis']}，近7天 {r['runs_7d']} 次）")
    print(f"用例        : {r['cases_total']}（失败 {r['cases_failed']}）")
    print(f"回归锚      : {data['assets']['regression']['count']}")
    print(f"入口分类    : {len(data['playbook']['entries'])} 组 / "
          f"{sum(len(g['tools']) for g in data['playbook']['entries'])} 个工具")
    print(f"待处理项    : {len(data['state']['todo'])}")
    print(f"时间线      : {len(data['activity'])} 行")
    print(f"最近一次    : {r['latest']['id'] if r['latest'] else '无'} "
          f"{r['latest']['gate'] if r['latest'] else ''}")
    print("--- bundle 键 ---")
    print(json.dumps(sorted(data.keys()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(_check())
    raise SystemExit(server.main([a for a in sys.argv[1:]]))
