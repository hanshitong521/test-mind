#!/usr/bin/env node
/**
 * Optional Cursor MCP launcher for TestMind (on-demand only).
 * Prefer shejiuPro: node scripts/merge-mcp-snippets.mjs --add testmind
 * Standing mcp.json must NOT include this server (doctor FAIL).
 */
import { spawn } from "node:child_process";
import { existsSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = process.env.TESTMIND_ROOT || join(dirname(fileURLToPath(import.meta.url)), "..");
const MCP = join(ROOT, "testmind", "mcp.py");
const PY = process.env.TESTMIND_PYTHON || process.env.PYTHON || "python";

if (!existsSync(MCP) || statSync(MCP).size < 200) {
	console.error(`[testmind] mcp.py missing or burst-damaged: ${MCP}`);
	process.exit(2);
}

const child = spawn(PY, [MCP], {
	stdio: "inherit",
	env: { ...process.env, PYTHONUTF8: "1", TESTMIND_ROOT: ROOT },
});
child.on("exit", (code) => process.exit(code ?? 1));
