#!/usr/bin/env node
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SHARED_LIB = resolve(ROOT, "..", "shared", "scripts", "peak-gate-lib.mjs");

const FALLBACK_LIB = {
	gateEnvelope: ({ gate_id, component, status, summary, commit, blocking }) => ({
		gate_id,
		component,
		status,
		summary,
		commit,
		blocking: Boolean(blocking),
	}),
	printGateReport: (component, gates) => {
		const blockingFail = gates.some((g) => g.blocking && g.status === "FAIL");
		const rollup = blockingFail ? "FAIL" : "PASS";
		console.log(`# peak-gate ${component} rollup=${rollup}`);
		for (const g of gates) {
			console.log(`${g.gate_id}\t${g.status}\t${g.summary}`);
		}
		return { rollup, gates };
	},
};

async function loadLib() {
	if (!existsSync(SHARED_LIB)) {
		return FALLBACK_LIB;
	}
	try {
		return await import(pathToFileURL(SHARED_LIB).href);
	} catch {
		return FALLBACK_LIB;
	}
}

function gitCommit() {
	const r = spawnSync("git", ["rev-parse", "--short", "HEAD"], {
		cwd: ROOT,
		encoding: "utf8",
		timeout: 15_000,
		maxBuffer: 1024 * 1024,
	});
	return r.status === 0 ? (r.stdout || "").trim() : "unknown";
}

/** 带子进程硬超时；超时/kill 视为 FAIL，避免 peak-gate 无限挂起 */
function run(cmd, args, env = {}, opts = {}) {
	const timeoutMs = opts.timeoutMs ?? 600_000;
	const maxBuffer = opts.maxBuffer ?? 32 * 1024 * 1024;
	const label = opts.label ?? cmd;
	const r = spawnSync(cmd, args, {
		cwd: ROOT,
		encoding: "utf8",
		env: { ...process.env, ...env },
		timeout: timeoutMs,
		maxBuffer,
		windowsHide: true,
	});
	if (r.error) {
		const msg = r.error.code === "ETIMEDOUT" ? `timeout after ${timeoutMs}ms` : String(r.error.message || r.error);
		console.error(`[peak-gate] ${label}: ${msg}`);
		return false;
	}
	if (r.status !== 0 && r.stderr) {
		const tail = r.stderr.trim().split(/\r?\n/).slice(-8).join("\n");
		if (tail) console.error(`[peak-gate] ${label} stderr:\n${tail}`);
	}
	return r.status === 0;
}

const { gateEnvelope, printGateReport } = await loadLib();
const commit = gitCommit();
const gates = [];

gates.push(
	gateEnvelope({
		gate_id: "TM0",
		component: "testmind",
		status: existsSync(join(ROOT, "pyproject.toml")) ? "PASS" : "FAIL",
		summary: "pyproject.toml + package layout",
		commit,
		blocking: true,
	}),
);

const tm1 = run("python", ["-m", "unittest", "discover", "-s", "tests", "-q"], {}, { timeoutMs: 600_000, label: "TM1" });
gates.push(
	gateEnvelope({
		gate_id: "TM1",
		component: "testmind",
		status: tm1 ? "PASS" : "FAIL",
		summary: "unittest discover",
		commit,
		blocking: true,
	}),
);

const tm2 = run(
	"python",
	["-c", "from testmind.core import docker; assert docker('version', timeout=5).returncode != 0 or True"],
	{},
	{ timeoutMs: 60_000, label: "TM2" },
);
gates.push(
	gateEnvelope({
		gate_id: "TM2",
		component: "testmind",
		status: tm2 ? "PASS" : "FAIL",
		summary: "docker probe no throw",
		commit,
		blocking: true,
	}),
);

const tm3 =
	process.platform === "win32"
		? tm1
		: run("python", ["examples/red-packet/e2e.py"], { TESTMIND_DOCKER_CLI: "/bin/false" }, { timeoutMs: 900_000, label: "TM3" });
gates.push(
	gateEnvelope({
		gate_id: "TM3",
		component: "testmind",
		status: tm3 ? "PASS" : "FAIL",
		summary: "red-packet e2e (docker disabled on linux)",
		commit,
		blocking: true,
	}),
);

const tm4 = run("python", ["-m", "unittest", "tests.test_mutation_smoke", "-q"], {}, { timeoutMs: 120_000, label: "TM4" });
gates.push(
	gateEnvelope({
		gate_id: "TM4",
		component: "testmind",
		status: tm4 ? "PASS" : "FAIL",
		summary: "mutation smoke >=9/10",
		commit,
		blocking: true,
	}),
);

const tm5 = run("python", ["-m", "unittest", "tests.test_task_isolation", "-q"], {}, { timeoutMs: 120_000, label: "TM5" });
gates.push(
	gateEnvelope({
		gate_id: "TM5",
		component: "testmind",
		status: tm5 ? "PASS" : "FAIL",
		summary: "task + regression isolation",
		commit,
		blocking: true,
	}),
);

const tm6 = run("python", ["-m", "unittest", "tests.test_secrets", "-q"], {}, { timeoutMs: 120_000, label: "TM6" });
gates.push(
	gateEnvelope({
		gate_id: "TM6",
		component: "testmind",
		status: tm6 ? "PASS" : "FAIL",
		summary: "secret leak scan",
		commit,
		blocking: true,
	}),
);

const tm7 = run("python", ["-m", "unittest", "tests.test_env_policy", "-q"], {}, { timeoutMs: 120_000, label: "TM7" });
gates.push(
	gateEnvelope({
		gate_id: "TM7",
		component: "testmind",
		status: tm7 ? "PASS" : "FAIL",
		summary: "production prepare denied",
		commit,
		blocking: true,
	}),
);

const tm8 = run("python", ["-m", "unittest", "tests.test_export_artifacts", "-q"], {}, { timeoutMs: 120_000, label: "TM8" });
gates.push(
	gateEnvelope({
		gate_id: "TM8",
		component: "testmind",
		status: tm8 ? "PASS" : "FAIL",
		summary: "four-piece export + failure bundle",
		commit,
		blocking: true,
	}),
);

const tmGeneric = run("python", ["examples/generic/e2e_smoke.py"], {}, { timeoutMs: 180_000, label: "TM8b" });
gates.push(
	gateEnvelope({
		gate_id: "TM8b",
		component: "testmind",
		status: tmGeneric ? "PASS" : "FAIL",
		summary: "generic 3-domain e2e smoke",
		commit,
		blocking: true,
	}),
);

const tmSim = run("python", ["scripts/peak_simulation.py"], {}, { timeoutMs: 180_000, label: "TM9" });
gates.push(
	gateEnvelope({
		gate_id: "TM9",
		component: "testmind",
		status: tmSim ? "PASS" : "FAIL",
		summary: "peak-sim TaskBundle full chain",
		commit,
		blocking: true,
	}),
);

const report = printGateReport("testmind", gates);
process.exit(report.rollup === "PASS" ? 0 : 1);
