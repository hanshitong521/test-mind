#!/usr/bin/env node
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SHARED_LIB = resolve(ROOT, "..", "shared", "scripts", "peak-gate-lib.mjs");
const CONTRACT_YAML = resolve(ROOT, "..", "shared", "pipeline-contract.yaml");
const ENVELOPE_OUT = join(ROOT, "reports", "peak-gate-envelope.json");

// 与 shared/scripts/peak-gate-lib.mjs **行为一致**的兜底实现。
// 关键：INV-005 的完整性校验必须同样严格，否则「有 shared 时严、没 shared 时松」= 假绿温床。
const ENVELOPE_REQUIRED = [
	"gate_id",
	"component",
	"status",
	"commit",
	"component_version",
	"contract_version",
	"contract_hash",
	"input_hash",
	"evidence",
	"measured_at",
];

function missingEnvelopeFieldsFallback(gate) {
	return ENVELOPE_REQUIRED.filter((k) => {
		const v = gate[k];
		return v === undefined || v === null || v === "";
	});
}

const FALLBACK_LIB = {
	missingEnvelopeFields: missingEnvelopeFieldsFallback,
	gateEnvelope: ({
		gate_id, component, status, summary, commit, blocking = false,
		component_version = null, contract_version = null, contract_hash = null,
		input_hash = null, evidence = null, measured_at = new Date().toISOString(),
	}) => ({
		gate_id, component, status, summary, commit, component_version, contract_version,
		contract_hash, input_hash, evidence, measured_at, blocking: Boolean(blocking),
	}),
	printGateReport: (component, gates) => {
		const incomplete = gates.filter((g) => missingEnvelopeFieldsFallback(g).length > 0);
		const blockingFail = gates.some(
			(g) => g.blocking && (g.status === "FAIL" || missingEnvelopeFieldsFallback(g).length > 0),
		);
		const anyFail = gates.some((g) => g.status === "FAIL");
		const rollup = blockingFail ? "FAIL" : anyFail ? "WARN" : "PASS";
		console.log(`# peak-gate ${component} rollup=${rollup}`);
		for (const g of gates) {
			const miss = missingEnvelopeFieldsFallback(g);
			const flag = miss.length ? `\tENVELOPE_INCOMPLETE(${miss.join(",")})` : "";
			console.log(`${g.gate_id}\t${miss.length && g.blocking ? "FAIL" : g.status}\t${g.summary}${flag}`);
		}
		return { rollup, gates, incomplete };
	},
};

async function loadLib() {
	if (!existsSync(SHARED_LIB)) {
		console.error("[peak-gate] shared/scripts/peak-gate-lib.mjs 缺失 → 使用 in-file 兜底（P1-04/F9 未完成）");
		return FALLBACK_LIB;
	}
	try {
		return await import(pathToFileURL(SHARED_LIB).href);
	} catch (e) {
		console.error(`[peak-gate] shared lib 载入失败 → 兜底: ${e.message}`);
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

/** pyproject [project] version —— component_version 的来源（INV-005）。 */
function componentVersion() {
	try {
		const txt = readFileSync(join(ROOT, "pyproject.toml"), "utf8");
		const m = txt.match(/^version\s*=\s*"([^"]+)"/m);
		return m ? m[1] : "unknown";
	} catch {
		return "unknown";
	}
}

/** 契约版本 + hash。shared 缺失时返回 null —— 让 INV-005 校验把它暴露成不完整信封，而不是静默补值。 */
function contractMeta() {
	if (!existsSync(CONTRACT_YAML)) return { version: null, hash: null };
	try {
		const raw = readFileSync(CONTRACT_YAML, "utf8");
		const m = raw.match(/^contract_version:\s*(\d+)/m);
		return {
			version: m ? Number(m[1]) : null,
			hash: createHash("sha256").update(raw, "utf8").digest("hex").slice(0, 16),
		};
	} catch {
		return { version: null, hash: null };
	}
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

const { gateEnvelope, printGateReport, missingEnvelopeFields } = await loadLib();
const commit = gitCommit();
const version = componentVersion();
const contract = contractMeta();
const measuredAt = new Date().toISOString();
const envRef = "reports/peak-gate-envelope.json"; // INV-010：相对 component root 的 relative_ref
const gates = [];

/** 每个 gate 的 input_hash：该门实际执行的输入（命令/判据）指纹。 */
const inputHash = (parts) =>
	createHash("sha256").update(Array.isArray(parts) ? parts.join("\u0000") : String(parts), "utf8").digest("hex").slice(0, 16);

function push(gate_id, status, summary, inputs, blocking = true) {
	gates.push(
		gateEnvelope({
			gate_id,
			component: "testmind",
			status,
			summary,
			commit,
			blocking,
			component_version: version,
			contract_version: contract.version,
			contract_hash: contract.hash,
			input_hash: inputHash(inputs),
			evidence: envRef,
			measured_at: measuredAt,
		}),
	);
}

const TM0_INPUTS = ["pyproject.toml", "package layout", ENVELOPE_REQUIRED.join(",")];
push(
	"TM0",
	existsSync(join(ROOT, "pyproject.toml")) ? "PASS" : "FAIL",
	"pyproject.toml + package layout + INV-005 envelope schema",
	TM0_INPUTS,
);

function runCmd(cmdArr, env = {}, opts = {}) {
	return run(cmdArr[0], cmdArr.slice(1), env, opts);
}

const TM1_CMD = ["python", "-m", "unittest", "discover", "-s", "tests", "-q"];
push("TM1", runCmd(TM1_CMD, {}, { timeoutMs: 600_000, label: "TM1" }) ? "PASS" : "FAIL",
	"unittest discover", TM1_CMD);

const TM2_CMD = ["python", "-c", "from testmind.core import docker; assert docker('version', timeout=5).returncode != 0 or True"];
push("TM2", runCmd(TM2_CMD, {}, { timeoutMs: 60_000, label: "TM2" }) ? "PASS" : "FAIL",
	"docker probe no throw", TM2_CMD);

// TM3：红包 e2e 全链。**所有平台真跑**——旧实现 Win 上用 TM1 结果代位（P1-20 机制性缺口），
// 等于拿"单测过了"冒充"端到端过了"。现在起不来/docker 缺失都是 e2e 自己的三态，不再有代位。
const TM3_CMD = ["python", "examples/red-packet/e2e.py"];
push("TM3", runCmd(TM3_CMD, { TESTMIND_DOCKER_CLI: "/bin/false" }, { timeoutMs: 900_000, label: "TM3" })
	? "PASS" : "FAIL", "red-packet e2e 114 cases (docker disabled)", TM3_CMD);

const TM4_CMD = ["python", "-m", "unittest", "tests.test_mutation_smoke", "-q"];
push("TM4", runCmd(TM4_CMD, {}, { timeoutMs: 120_000, label: "TM4" }) ? "PASS" : "FAIL",
	"mutation smoke >=9/10", TM4_CMD);

const TM5_CMD = ["python", "-m", "unittest", "tests.test_task_isolation", "-q"];
push("TM5", runCmd(TM5_CMD, {}, { timeoutMs: 120_000, label: "TM5" }) ? "PASS" : "FAIL",
	"task + regression isolation", TM5_CMD);

const TM6_CMD = ["python", "-m", "unittest", "tests.test_secrets", "-q"];
push("TM6", runCmd(TM6_CMD, {}, { timeoutMs: 120_000, label: "TM6" }) ? "PASS" : "FAIL",
	"secret leak scan", TM6_CMD);

const TM7_CMD = ["python", "-m", "unittest", "tests.test_env_policy", "-q"];
push("TM7", runCmd(TM7_CMD, {}, { timeoutMs: 120_000, label: "TM7" }) ? "PASS" : "FAIL",
	"production prepare denied", TM7_CMD);

const TM8_CMD = ["python", "-m", "unittest", "tests.test_export_artifacts", "-q"];
push("TM8", runCmd(TM8_CMD, {}, { timeoutMs: 120_000, label: "TM8" }) ? "PASS" : "FAIL",
	"four-piece export + failure bundle", TM8_CMD);

const TM8B_CMD = ["python", "examples/generic/e2e_smoke.py"];
push("TM8b", runCmd(TM8B_CMD, {}, { timeoutMs: 180_000, label: "TM8b" }) ? "PASS" : "FAIL",
	"generic 3-domain e2e smoke", TM8B_CMD);

const TM9_CMD = ["python", "scripts/peak_simulation.py"];
push("TM9", runCmd(TM9_CMD, {}, { timeoutMs: 180_000, label: "TM9" }) ? "PASS" : "FAIL",
	"peak-sim TaskBundle full chain", TM9_CMD);

const report = printGateReport("testmind", gates);

// 落盘信封证据（INV-010：可移植相对引用 + 内容可直接校验）
try {
	mkdirSync(dirname(ENVELOPE_OUT), { recursive: true });
	writeFileSync(
		ENVELOPE_OUT,
		JSON.stringify(
			{
				component: "testmind",
				commit,
				component_version: version,
				contract_version: contract.version,
				contract_hash: contract.hash,
				measured_at: measuredAt,
				rollup: report.rollup,
				incomplete: report.incomplete.map((g) => ({ gate_id: g.gate_id, missing: missingEnvelopeFields(g) })),
				gates,
			},
			null,
			2,
		) + "\n",
		"utf8",
	);
	console.log(`# envelope → ${envRef} (rollup=${report.rollup}, incomplete=${report.incomplete.length})`);
} catch (e) {
	console.error(`[peak-gate] 信封落盘失败: ${e.message}`);
	process.exit(1);
}

process.exit(report.rollup === "PASS" ? 0 : 1);
