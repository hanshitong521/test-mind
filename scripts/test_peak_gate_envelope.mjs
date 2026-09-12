#!/usr/bin/env node
// INV-005 门禁信封契约测试（node --test）。
// 跑法：node --test scripts/test_peak_gate_envelope.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SHARED_LIB = resolve(ROOT, "..", "shared", "scripts", "peak-gate-lib.mjs");

if (!existsSync(SHARED_LIB)) {
	// 独立克隆场景（P1-04 / F9 未完成）：不红，但必须把缺口说清楚。
	console.log(`SKIP: ${SHARED_LIB} 不存在 —— 独立克隆无法校验 INV-005（P1-04/F9 待 vendor/submodule）`);
	process.exit(0);
}

const { gateEnvelope, missingEnvelopeFields, printGateReport, ENVELOPE_REQUIRED } = await import(
	pathToFileURL(SHARED_LIB).href
);

const FULL = {
	gate_id: "TMx",
	component: "testmind",
	status: "PASS",
	summary: "s",
	commit: "abc1234",
	blocking: true,
	component_version: "1.2.0",
	contract_version: 1,
	contract_hash: "deadbeefdeadbeef",
	input_hash: "cafebabecafebabe",
	evidence: "reports/peak-gate-envelope.json",
	measured_at: "2026-09-10T00:00:00.000Z",
};

test("INV-005: gateEnvelope 覆盖全部必填字段", () => {
	const e = gateEnvelope(FULL);
	for (const k of ENVELOPE_REQUIRED) {
		assert.ok(e[k] !== undefined && e[k] !== null && e[k] !== "", `缺字段 ${k}`);
	}
	assert.equal(missingEnvelopeFields(e).length, 0);
});

test("INV-005: 缺任一必填字段都能被检出（不静默补默认值）", () => {
	for (const k of ENVELOPE_REQUIRED) {
		const e = gateEnvelope(FULL);
		e[k] = null;
		assert.deepEqual(missingEnvelopeFields(e), [k], `${k} 未被检出`);
	}
});

test("INV-005: blocking gate 信封不完整 → rollup FAIL（信封是真门禁）", () => {
	const good = gateEnvelope(FULL);
	const bad = gateEnvelope({ ...FULL, gate_id: "TMbad", contract_hash: null });
	const r = printGateReport("testmind", [good, bad]);
	assert.equal(r.rollup, "FAIL");
	assert.equal(r.incomplete.length, 1);
	assert.equal(r.incomplete[0].gate_id, "TMbad");
});

test("INV-005: 信封完整且全 PASS → rollup PASS", () => {
	const r = printGateReport("testmind", [gateEnvelope(FULL)]);
	assert.equal(r.rollup, "PASS");
	assert.equal(r.incomplete.length, 0);
});

test("rollup: 非 blocking 的 FAIL → WARN；blocking FAIL → FAIL", () => {
	const soft = gateEnvelope({ ...FULL, gate_id: "TMs", status: "FAIL", blocking: false });
	assert.equal(printGateReport("c", [soft]).rollup, "WARN");
	const hard = gateEnvelope({ ...FULL, gate_id: "TMh", status: "FAIL", blocking: true });
	assert.equal(printGateReport("c", [hard]).rollup, "FAIL");
});
