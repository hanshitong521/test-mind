---
name: contextmind-maintain
description: Inspect, install, and repair a ContextMind installation. Use when hooks stop firing, output is ungoverned, or you need the token ledger.
---

# ContextMind maintenance

## Commands

```bash
node .cursor/contextmind/cli.mjs doctor [dir]     # PASS/WARN/FAIL per subsystem
node .cursor/contextmind/cli.mjs status           # paths, counts, locked settings
node .cursor/contextmind/cli.mjs report --json    # three-column token ledger
node .cursor/contextmind/cli.mjs task init|validate|show|preamble [file]
node .cursor/contextmind/cli.mjs fetch <handle> --lines 1-80
node .cursor/contextmind/cli.mjs gc --days 30
node .cursor/contextmind/cli.mjs install|uninstall
node .cursor/contextmind/cli.mjs benchmark
```

No `start`/`stop`: hooks are stateless subprocesses, there is no daemon.

## Three columns, never one number

- `prevented_read_tokens` — Read Guard / dedup kept it out of the window.
- `tool_emitted_savings` — raw tool payload minus what was emitted.
- `proxy_llm_savings` — conversation proxy. **0 is expected** on Cursor subscription models (Grok/Auto/Composer), not a failure.

## Symptom → check

| Symptom | First thing to check |
|---|---|
| Hooks never fire | `doctor` → "hooks installed". Matchers are `MCP:<tool>` for MCP tools; a bare `mysql_query` matches nothing. |
| Shell output not compressed | Command is piped, redirected, multi-statement, or already wrapped. Those are deliberately skipped. |
| Output compressed but evidence lost | Look for `method=ABSTAIN` in the footer; the gate returned raw because critical lines would have been dropped. |
| `handle store unavailable` | `doctor` → handle store. Usually a read-only project dir. |
| Everything governed but slow | Check `gate_latency_ms` in the ledger; it is reported separately from `hook_latency_ms`. |
| Same FQCN explored many times | Adapter cache should skip CodeGraph (`first_layer=adapter_cache` in report). `refresh:true` to bypass. Optional Redis: `CONTEXTMIND_REDIS_URL`. |

## Do not

- Do not add a second compression layer. `shell.first_layer` is locked to `cc_balanced`; RTK has no code path.
- Do not raise budgets to make a test pass. Adjust with a bench measurement and record it.
- Do not edit `.cursor/contextmind/` in a project — it is a copy. Fix the source repo and reinstall.
