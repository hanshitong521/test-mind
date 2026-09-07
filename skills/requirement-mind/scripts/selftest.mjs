#!/usr/bin/env node
// RequirementMind 确定性自测：在 mock 会话（scripts/fixtures/mock-session，红包限时领取场景）上
// 跑 state.mjs 全部命令并断言行为，同时计量每轮 grilling 的读取开销（frontier vs 全量读 JSON）。
// 覆盖：Risk Router 分层 / Human-Only Gate（authority）/ Value Score / Stop Rule / Eval / freeze/supersede / migrate。
// 运行: node scripts/selftest.mjs   （改动 SKILL.md / state.mjs / references 后必跑）
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, mkdirSync, readFileSync, writeFileSync, copyFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const stateMjs = join(here, 'state.mjs');
const fixtureDir = join(here, 'fixtures', 'mock-session');
let pass = 0, fail = 0;

function check(name, cond, extra = '') {
  if (cond) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; console.log(`  ✗ ${name}${extra ? ` — ${extra}` : ''}`); }
}
const run = (args) => {
  try { return { code: 0, out: execFileSync(process.execPath, [stateMjs, ...args], { encoding: 'utf8' }) }; }
  catch (e) { return { code: e.status ?? -1, out: `${e.stdout || ''}${e.stderr || ''}` }; }
};
const readJ = (p) => JSON.parse(readFileSync(p, 'utf8'));
const writeJ = (p, data) => writeFileSync(p, JSON.stringify(data, null, 2) + '\n');

const tmp = mkdtempSync(join(tmpdir(), 'rm-selftest-'));
try {
  const dir = join(tmp, '.requirementmind');
  mkdirSync(dir, { recursive: true });
  for (const f of readdirSync(fixtureDir)) copyFileSync(join(fixtureDir, f), join(dir, f));
  const riskPath = join(dir, 'risk.json');

  console.log('▶ validate / counters');
  const v = run(['validate', dir]);
  check('validate 退出码 0', v.code === 0 && v.out.includes('校验通过'), v.out);
  const c0 = run(['counters', dir]);
  check('初始 blocking_questions=4', c0.out.includes('blocking_questions: 4'), c0.out);
  check('初始 blocking_conflicts=1', c0.out.includes('blocking_conflicts: 1'));
  check('初始 critical_assumptions=1', c0.out.includes('critical_assumptions: 1'));
  check('初始 unvalidated_high_risks=1', c0.out.includes('unvalidated_high_risks: 1'));

  console.log('▶ Risk Router（评分校验 → 分层 → 专项路由）');
  const r0 = run(['risk', dir]);
  check('risk 校验通过', r0.code === 0, r0.out);
  check('总分 13 → FOCUSED', r0.out.includes('"total": 13') && r0.out.includes('FOCUSED'), r0.out);
  check('最高分维度路由专项 concurrency', r0.out.includes('"specialists": [\n    "concurrency"\n  ]') || /"specialists":\s*\[\s*"concurrency"/.test(r0.out), r0.out);
  const riskOrig = readFileSync(riskPath, 'utf8');
  const badRisk = JSON.parse(riskOrig);
  badRisk.dimensions.concurrency_risk.refs = [];
  writeJ(riskPath, badRisk);
  const r1 = run(['risk', dir]);
  check('score>=2 无 refs 被拒（评分必须有证据）', r1.code === 1 && r1.out.includes('必须给 refs'), r1.out);
  const r2 = run(['stop', dir]);
  check('覆盖率缺证据 → stop 拦截', r2.code === 1 && r2.out.includes('无 refs'), r2.out);
  writeFileSync(riskPath, riskOrig);

  console.log('▶ frontier（USER_ONLY 批量 + token 计量）');
  const fullBytes = Buffer.byteLength(readFileSync(join(dir, 'questions.json'), 'utf8'))
    + Buffer.byteLength(readFileSync(join(dir, 'conflicts.json'), 'utf8'));
  const f0 = run(['frontier', dir]);
  const fBytes = Buffer.byteLength(f0.out, 'utf8');
  for (const id of ['Q-001', 'Q-002', 'Q-005', 'CON-001'])
    check(`frontier 含 USER_ONLY 待决项 ${id}`, f0.out.includes(id));
  for (const id of ['Q-003', 'Q-009', 'Q-006'])
    check(`frontier 排除 TECHNICAL ${id}（AI 自治，不打扰用户）`, !f0.out.includes(id));
  for (const id of ['Q-004', 'Q-007', 'Q-008', 'Q-010'])
    check(`frontier 排除已答/OPTIONAL ${id}`, !f0.out.includes(id));
  check('frontier 提示 TECHNICAL 自治通道', f0.out.includes('freeze --auto'));
  check('frontier 退出码 1（仍有待决项）', f0.code === 1, `code=${f0.code}`);
  check(`frontier 输出 ${fBytes}B < 全量读取 ${fullBytes}B`, fBytes < fullBytes);
  const savedPct = Math.max(0, 100 - Math.round((fBytes / fullBytes) * 100));
  console.log(`  ⛽ 每轮读取: frontier ${fBytes}B vs 全量 questions+conflicts ${fullBytes}B（省 ${savedPct}%）`);

  console.log('▶ freeze（用户裁决 / R5 / supersede）');
  const fr1 = run(['freeze', dir, 'Q-001', 'A']);
  check('freeze Q-001 A 退出码 0', fr1.code === 0, fr1.out);
  const d3 = readJ(join(dir, 'decisions.json')).find((d) => d.id === 'DEC-003');
  check('生成 DEC-003 FROZEN，decision=选项正文（非字母）', d3 && d3.status === 'FROZEN' && d3.decision === '请求到达服务端时间', JSON.stringify(d3));
  check('Q-001 置 ANSWERED', readJ(join(dir, 'questions.json')).find((q) => q.id === 'Q-001').status === 'ANSWERED');
  check('冻结后回打印数 blocking_questions=3', fr1.out.includes('blocking_questions: 3'));
  check('自动快照 history/', readdirSync(join(dir, 'history')).length >= 1);
  const fr2 = run(['freeze', dir, 'Q-001', 'B']);
  check('重复冻结被拒（R5，提示 --supersede）', fr2.code === 1 && fr2.out.includes('--supersede'), fr2.out);
  const fr3 = run(['freeze', dir, 'Q-001', 'B', '--supersede']);
  check('--supersede 改判成功', fr3.code === 0, fr3.out);
  const decs = readJ(join(dir, 'decisions.json'));
  check('DEC-003 置 SUPERSEDED + replaced_by=DEC-004', decs.find((d) => d.id === 'DEC-003')?.status === 'SUPERSEDED'
    && decs.find((d) => d.id === 'DEC-003')?.replaced_by === 'DEC-004');

  console.log('▶ freeze 冲突裁决');
  const fc = run(['freeze', dir, 'CON-001', '不复用 status=2：新增 status=3=EXPIRED，status=2 保持 STOPPED 语义']);
  check('freeze CON-001 退出码 0', fc.code === 0, fc.out);
  const con = readJ(join(dir, 'conflicts.json')).find((x) => x.id === 'CON-001');
  check('CON-001 RESOLVED + 回填 resolution_decision_id', con.status === 'RESOLVED' && con.resolution_decision_id === 'DEC-005', JSON.stringify(con));

  console.log('▶ Human-Only Gate（TECHNICAL → freeze --auto 自治裁决）');
  // 模拟主 Agent 的合法动作：assumption 取证升级、validator 销案 challenge
  const asm = readJ(join(dir, 'assumptions.json'));
  asm.find((a) => a.id === 'ASM-001').status = 'RESOLVED';
  writeJ(join(dir, 'assumptions.json'), asm);
  const chs = readJ(join(dir, 'challenges.json'));
  chs.find((x) => x.id === 'CH-001').status = 'REFUTED';
  writeJ(join(dir, 'challenges.json'), chs);
  const restAnswers = [
    ['Q-002', 'D', '--impact', 'sql/red_packet.sql:18'],
    ['Q-003', 'A', '--auto'],
    ['Q-009', 'B', '--auto'],
    ['Q-005', 'A'],
    ['Q-006', 'B', '--auto'],
  ];
  for (const args of restAnswers) {
    const r = run(['freeze', dir, ...args]);
    check(`freeze ${args.slice(0, 2).join(' ')}${args.includes('--auto') ? '（--auto）' : ''}`, r.code === 0, r.out);
  }
  const decAll = readJ(join(dir, 'decisions.json'));
  check('TECHNICAL 冻结 source=AI_DEFAULT', decAll.find((d) => d.question_id === 'Q-009')?.source === 'AI_DEFAULT');
  check('USER 冻结 source=USER', decAll.find((d) => d.question_id === 'Q-002')?.source === 'USER');
  check('impact 落盘', decAll.some((d) => d.question_id === 'Q-002' && Array.isArray(d.impact) && d.impact[0] === 'sql/red_packet.sql:18'));
  const f1 = run(['frontier', dir]);
  check('frontier 空 → 退出码 0（R9 达成）', f1.code === 0 && f1.out.includes('frontier 已空'), f1.out);

  console.log('▶ Stop Rule（R9 + 覆盖率）');
  const s0 = run(['stop', dir]);
  check('全部清零 → stop 退出码 0', s0.code === 0 && s0.out.includes('STOP 条件满足'), s0.out);
  const riskObj = JSON.parse(riskOrig);
  riskObj.dimensions.concurrency_risk.refs = ['FACT-999'];
  writeJ(riskPath, riskObj);
  const s1 = run(['stop', dir]);
  check('风险维度证据失效 → stop 退出码 1', s1.code === 1 && s1.out.includes('无法解析'), s1.out);
  writeFileSync(riskPath, riskOrig);
  const s2 = run(['stop', dir]);
  check('证据恢复 → stop 退出码 0', s2.code === 0, s2.out);

  console.log('▶ Gate');
  const g0 = run(['gate', dir]);
  check('gate 无检查表 → HARD-GATE-OK', g0.code === 0 && g0.out.includes('HARD-GATE-OK'), g0.out);
  const checklist = ['business_goal', 'scope', 'core_flow', 'business_rules', 'data_model', 'api_contract', 'state_machine', 'validation_rules', 'permission', 'concurrency', 'idempotency', 'exception_handling', 'compatibility', 'acceptance_criteria', 'testability', 'spec_consistency'];
  writeJ(join(dir, 'gate.json'), {
    status: 'READY_FOR_DEVELOPMENT',
    checklist: Object.fromEntries(checklist.map((k) => [k, 'PASS'])),
    hard_counters: { blocking_questions: 0, blocking_conflicts: 0, critical_assumptions: 0, unvalidated_high_risks: 0 },
    missing: [],
    checked_at: new Date().toISOString(),
  });
  const g1 = run(['gate', dir]);
  check('gate 全 PASS → READY_FOR_DEVELOPMENT', g1.code === 0 && g1.out.includes('READY_FOR_DEVELOPMENT'), g1.out);

  console.log('▶ Eval 闭环（自治率 / 验真率）');
  const ev = run(['eval', dir]);
  check('eval 退出码 0', ev.code === 0, ev.out);
  let m = null;
  try { m = JSON.parse(ev.out); } catch { /* ignore */ }
  check('eval 输出可解析 JSON', !!m, ev.out.slice(0, 120));
  if (m) {
    check('user_decisions=6', m.user_decisions === 6, JSON.stringify(m));
    check('ai_decisions=3（TECHNICAL 自治）', m.ai_decisions === 3);
    check('answered_total=8', m.answered_total === 8);
    check('autonomy_rate=25%', m.autonomy_rate === '25%');
    check('refuted=2（伪问题被销案）', m.review.REFUTED === 2);
    check('refuted_rate=100%', m.review.refuted_rate === '100%');
  }

  console.log('▶ migrate（旧版 questions 兼容 + authority/value 回填）');
  const dir2 = join(tmp, 'legacy');
  mkdirSync(dir2, { recursive: true });
  copyFileSync(join(here, 'fixtures', 'questions-legacy.json'), join(dir2, 'questions.json'));
  copyFileSync(join(here, 'fixtures', 'decisions-legacy.json'), join(dir2, 'decisions.json'));
  const m0 = run(['migrate', dir2]);
  check('migrate 预览不写盘', m0.code === 0 && m0.out.includes('预览'), m0.out);
  check('预览模式下 questions.json 未变', !readJ(join(dir2, 'questions.json'))[0].options[0].startsWith('A. '));
  const m1 = run(['migrate', dir2, '--write']);
  check('migrate --write 成功', m1.code === 0, m1.out);
  const migrated = readJ(join(dir2, 'questions.json'));
  check('Q-001 选项规范化 A. 前缀', migrated[0].options[0].startsWith('A. '));
  check('Q-001 占位推荐 A', migrated[0].recommended_option === 'A' && migrated[0].recommendation_reason.includes('迁移占位'));
  check('Q-002 从 DEC-002 推断推荐 C', migrated[1].recommended_option === 'C', JSON.stringify(migrated[1]));
  check('缺 authority/value 回填为安全默认', migrated[0].authority === 'USER_ONLY' && migrated[0].value === 'MEDIUM');
  const v2 = run(['validate', dir2]);
  check('迁移后 validate 通过', v2.code === 0, v2.out);
} finally {
  rmSync(tmp, { recursive: true, force: true });
}
console.log(`\n结果: ${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
