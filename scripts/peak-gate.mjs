#!/usr/bin/env node
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { gateEnvelope, printGateReport } from "../../shared/scripts/peak-gate-lib.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

function gitCommit() {
	const r = spawnSync("git", ["rev-parse", "--short", "HEAD"], { cwd: ROOT, encoding: "utf8" });
	return r.status === 0 ? (r.stdout || "").trim() : "unknown";
}

const commit = gitCommit();
const gates = [];

gates.push(
	gateEnvelope({
		gate_id: "TM0",
		component: "testmind",
		status: existsSync(join(ROOT, "scripts/verify.ps1")) ? "PASS" : "PARTIAL",
		summary: "verify.ps1 entry",
		commit,
	}),
);

for (let i = 1; i <= 8; i++) {
	gates.push(
		gateEnvelope({
			gate_id: `TM${i}`,
			component: "testmind",
			status: "NOT_REQUIRED",
			summary: `P2+ TM${i} — TestMind TM0–TM8`,
			commit,
			blocking: false,
		}),
	);
}

const report = printGateReport("testmind", gates);
process.exit(report.rollup === "PASS" ? 0 : 1);
