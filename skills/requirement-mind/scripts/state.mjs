#!/usr/bin/env node
// RequirementMind 状态辅助脚本（确定性部分，不含任何 LLM 逻辑）
// 用法: node state.mjs <counters|validate|gate|snapshot|migrate> <.requirementmind 目录> [--write]

import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const cmd = process.argv[2];
const dir = process.argv[3];
if (!dir || !['counters', 'frontier', 'freeze', 'risk', 'stop', 'eval', 'validate', 'gate', 'snapshot', 'migrate'].includes(cmd)) {
  console.error('用法: node state.mjs <counters|frontier|freeze|risk|stop|eval|validate|gate|snapshot|migrate> <.requirementmind 目录> [参数]');
  console.error('  frontier : 输出当前待问批量（OPEN 的 USER_ONLY BLOCKING+IMPORTANT + OPEN 冲突）与计数；TECHNICAL 不问用户');
  console.error('  freeze   : freeze <dir> <Q-xxx|CON-xxx> "<选项字母或决定文本>" [--supersede] [--auto] [--impact "a,b"] — 冻结为 DEC（--auto=AI 自治裁决）');
  console.error('  risk     : 校验 risk.json 八维评分 → 分层 LIGHT/FOCUSED/COUNCIL + 指定专项 Reviewer');
  console.error('  stop     : 停止条件判定（R9 + 风险维度证据覆盖率）；退出码 0 = 收敛，禁止继续追问/审查');
  console.error('  eval     : 会话指标（用户自治率 / Reviewer 验真率 / 轮次），供 Eval 闭环');
  process.exit(2);
}
const writeFlag = process.argv.includes('--write');
const read = (f) => {
  const p = join(dir, f);
  if (!existsSync(p)) return [];
  const raw = readFileSync(p, 'utf8').trim();
  return raw ? JSON.parse(raw) : [];
};
const isArr = (v) => (Array.isArray(v) ? v : v ? [v] : []);

function countersData() {
  const questions = isArr(read('questions.json'));
  const conflicts = isArr(read('conflicts.json'));
  const assumptions = isArr(read('assumptions.json'));
  const challenges = isArr(read('challenges.json'));
  return {
    blocking_questions: questions.filter((q) => q.priority === 'BLOCKING' && q.status === 'OPEN').length,
    blocking_conflicts: conflicts.filter((x) => x.severity === 'BLOCKING' && x.status === 'OPEN').length,
    critical_assumptions: assumptions.filter((a) => a.risk === 'HIGH' && a.status === 'UNVERIFIED').length,
    unvalidated_high_risks: challenges.filter((x) => x.status === 'PENDING_VALIDATION' && x.severity === 'BLOCKING').length,
  };
}

function counters() {
  const c = countersData();
  for (const [k, v] of Object.entries(c)) console.log(`${k}: ${v}`);
  console.log(c.blocking_questions + c.blocking_conflicts + c.critical_assumptions + c.unvalidated_high_risks === 0
    ? '=> 关键疑问已清零 (R9 出口)' : '=> 仍有 BLOCKING 项，禁止进入下一阶段');
}

function frontier() {
  const questions = isArr(read('questions.json'));
  const conflicts = isArr(read('conflicts.json'));
  const rank = { BLOCKING: 0, IMPORTANT: 1 };
  const open = questions
    .filter((q) => q.status === 'OPEN' && q.priority !== 'OPTIONAL' && q.authority !== 'TECHNICAL')
    .sort((a, b) => (rank[a.priority] ?? 9) - (rank[b.priority] ?? 9));
  const tech = questions.filter((q) => q.status === 'OPEN' && q.priority !== 'OPTIONAL' && q.authority === 'TECHNICAL');
  const openCon = conflicts.filter((x) => x.status === 'OPEN');
  for (const q of open) {
    console.log(`${q.id} [${q.priority}] ${q.topic}`);
    console.log(`  问: ${q.question}`);
    console.log(`  缘由: ${q.reason}`);
    console.log(`  选项: ${(q.options || []).join(' | ')}`);
    console.log(`  推荐: ${q.recommended_option || '?'} — ${q.recommendation_reason || '（先补推荐再展示）'}`);
  }
  for (const c of openCon) {
    console.log(`${c.id} [CONFLICT/${c.severity}]`);
    console.log(`  左: ${c.left}`);
    console.log(`  右: ${c.right}`);
  }
  const c = countersData();
  console.log('---');
  console.log(`frontier = ${open.length} 问 + ${openCon.length} 冲突（OPTIONAL 不问走默认值；TECHNICAL 未决 ${tech.length} 条：AI 采纳推荐 freeze --auto，不问用户）`);
  console.log(`计数: blocking_questions=${c.blocking_questions} blocking_conflicts=${c.blocking_conflicts} critical_assumptions=${c.critical_assumptions} unvalidated_high_risks=${c.unvalidated_high_risks}`);
  if (open.length + openCon.length + tech.length === 0 && c.critical_assumptions === 0 && c.unvalidated_high_risks === 0) {
    console.log('=> frontier 已空且关键疑问清零（R9），可进入 Phase 4');
  } else {
    process.exitCode = 1;
    console.log('=> 仍有待决项，禁止进入 Phase 4');
  }
}

const ENUMS = {
  'facts.json': { category: ['stack', 'structure', 'database', 'api', 'test', 'business_rule', 'history', 'constraint', 'doc'], status: ['VERIFIED'] },
  'questions.json': { priority: ['BLOCKING', 'IMPORTANT', 'OPTIONAL'], status: ['OPEN', 'ANSWERED'], authority: ['USER_ONLY', 'TECHNICAL'], value: ['HIGH', 'MEDIUM', 'LOW'] },
  'decisions.json': { source: ['USER', 'AI_DEFAULT'], status: ['FROZEN', 'SUPERSEDED'] },
  'assumptions.json': { risk: ['HIGH', 'MEDIUM', 'LOW'], source: ['MODEL_INFERENCE'], status: ['UNVERIFIED', 'CONFIRMED_AS_FACT', 'RESOLVED'] },
  'conflicts.json': { severity: ['BLOCKING', 'IMPORTANT'], status: ['OPEN', 'RESOLVED'] },
  'challenges.json': { severity: ['BLOCKING', 'HIGH', 'MEDIUM'], status: ['PENDING_VALIDATION', 'CONFIRMED', 'PLAUSIBLE', 'REFUTED'] },
  'evidence.json': { verdict: ['CONFIRMED', 'PLAUSIBLE', 'REFUTED'] },
};
const REQUIRED = {
  'facts.json': ['id', 'category', 'statement', 'source', 'confidence', 'status'],
  'questions.json': ['id', 'topic', 'question', 'reason', 'priority', 'options', 'recommended_option', 'recommendation_reason', 'authority', 'value', 'status'],
  'decisions.json': ['id', 'question_id', 'decision', 'topic', 'source', 'status', 'created_at'],
  'assumptions.json': ['id', 'statement', 'risk', 'source', 'status'],
  'conflicts.json': ['id', 'left', 'right', 'severity', 'status'],
  'challenges.json': ['id', 'claim', 'evidence', 'validation', 'severity', 'status'],
  'evidence.json': ['id', 'challenge_id', 'verdict', 'reasoning'],
};

function validate() {
  let errors = 0;
  for (const [file, req] of Object.entries(REQUIRED)) {
    let items;
    try { items = isArr(read(file)); } catch (e) { console.log(`${file}: JSON 解析失败 - ${e.message}`); errors++; continue; }
    for (const item of items) {
      for (const k of req) if (item[k] === undefined) { console.log(`${file} ${item.id || '?'}: 缺少字段 ${k}`); errors++; }
      for (const [k, allowed] of Object.entries(ENUMS[file] || {}))
        if (item[k] !== undefined && !allowed.includes(item[k])) { console.log(`${file} ${item.id || '?'}: ${k}=${item[k]} 不在枚举内`); errors++; }
      if (file === 'questions.json' && item.options?.length) {
        const letters = ['A', 'B', 'C', 'D'].slice(0, item.options.length);
        if (item.recommended_option && !letters.includes(item.recommended_option)) {
          console.log(`${file} ${item.id || '?'}: recommended_option=${item.recommended_option} 与 options 条数不匹配`);
          errors++;
        }
        if (item.recommendation_reason !== undefined && !String(item.recommendation_reason).trim()) {
          console.log(`${file} ${item.id || '?'}: recommendation_reason 不能为空`);
          errors++;
        }
      }
    }
    if (items.length) console.log(`${file}: ${items.length} 条 OK`);
  }
  const decs = isArr(read('decisions.json'));
  for (const d of decs.filter((x) => x.status === 'SUPERSEDED'))
    if (!d.replaced_by) { console.log(`decisions.json ${d.id}: SUPERSEDED 缺 replaced_by`); errors++; }
  console.log(errors === 0 ? '=> 校验通过' : `=> ${errors} 个错误`);
  process.exitCode = errors === 0 ? 0 : 1;
}

const CHECKLIST = ['business_goal', 'scope', 'core_flow', 'business_rules', 'data_model', 'api_contract', 'state_machine', 'validation_rules', 'permission', 'concurrency', 'idempotency', 'exception_handling', 'compatibility', 'acceptance_criteria', 'testability', 'spec_consistency'];

function gate() {
  const gPath = join(dir, 'gate.json');
  const g = existsSync(gPath) ? JSON.parse(readFileSync(gPath, 'utf8')) : null;
  const hard = countersData();
  const hardSum = Object.values(hard).reduce((s, v) => s + v, 0);
  if (!g) {
    console.log(JSON.stringify({ status: hardSum === 0 ? 'HARD-GATE-OK(待检查表)' : 'BLOCKED', hard_counters: hard }, null, 2));
    process.exitCode = hardSum === 0 ? 0 : 1;
    return;
  }
  const missing = [];
  for (const k of CHECKLIST) if (g.checklist?.[k] !== 'PASS') missing.push(`checklist.${k} != PASS`);
  for (const [k, v] of Object.entries(hard)) if (v > 0) missing.push(`${k}=${v}`);
  const status = missing.length === 0 ? 'READY_FOR_DEVELOPMENT' : 'BLOCKED';
  console.log(JSON.stringify({ status, hard_counters: hard, missing }, null, 2));
  process.exitCode = status === 'READY_FOR_DEVELOPMENT' ? 0 : 1;
}

function snapshot() {
  const hDir = join(dir, 'history');
  if (!existsSync(hDir)) mkdirSync(hDir, { recursive: true });
  const n = readdirSync(hDir).length + 1;
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const files = ['session.json', 'facts.json', 'questions.json', 'decisions.json', 'assumptions.json', 'conflicts.json', 'challenges.json', 'evidence.json', 'gate.json'];
  const snap = {};
  for (const f of files) { const p = join(dir, f); if (existsSync(p)) snap[f] = JSON.parse(readFileSync(p, 'utf8')); }
  writeFileSync(join(hDir, `${String(n).padStart(3, '0')}-${stamp}.json`), JSON.stringify(snap, null, 2));
  console.log(`快照已写入 history/${String(n).padStart(3, '0')}-${stamp}.json`);
}

const OPTION_LETTERS = ['A', 'B', 'C', 'D'];

function normalizeQuestionOptions(q) {
  const raw = Array.isArray(q.options) ? q.options.filter(Boolean) : [];
  const letters = OPTION_LETTERS.slice(0, Math.min(raw.length, 4));
  const options = raw.map((opt, i) => {
    const s = String(opt).trim();
    const letter = letters[i];
    const prefixed = new RegExp(`^${letter}[.．、\\s]`, 'i').test(s);
    if (prefixed) return s;
    return `${letter}. ${s}`;
  });
  return { ...q, options };
}

function letterForOptionMatch(decision, options) {
  const d = String(decision).trim();
  if (!d) return null;
  const letterOnly = d.match(/^([A-D])\.?$/i);
  if (letterOnly) return letterOnly[1].toUpperCase();
  for (let i = 0; i < options.length; i++) {
    const letter = OPTION_LETTERS[i];
    const opt = String(options[i]);
    if (d === opt || opt.endsWith(d) || opt.includes(d)) return letter;
    const body = opt.replace(/^[A-D][.．、\s]+/i, '').trim();
    if (d === body || body.includes(d) || d.includes(body)) return letter;
  }
  return null;
}

function frozenDecisionForQuestion(decisions, questionId) {
  const list = decisions
    .filter((x) => x.question_id === questionId && x.status === 'FROZEN')
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
  return list[0] || null;
}

function migrateQuestions() {
  const qPath = join(dir, 'questions.json');
  if (!existsSync(qPath)) {
    console.log('questions.json 不存在，无需迁移');
    return;
  }
  const questions = isArr(JSON.parse(readFileSync(qPath, 'utf8').trim() || '[]'));
  const decisions = isArr(read('decisions.json'));
  let changed = 0;
  const out = questions.map((q) => {
    let item = normalizeQuestionOptions(q);
    let touched = false;
    if (!item.authority) { item.authority = 'USER_ONLY'; touched = true; }
    if (!item.value) { item.value = 'MEDIUM'; touched = true; }
    const letters = OPTION_LETTERS.slice(0, item.options.length);
    const needsRec =
      !item.recommended_option ||
      !letters.includes(item.recommended_option) ||
      !String(item.recommendation_reason || '').trim();
    if (!needsRec) {
      if (touched) changed++;
      return item;
    }
    touched = true;

    const dec = frozenDecisionForQuestion(decisions, item.id);
    let recommended = item.recommended_option;
    let reason = String(item.recommendation_reason || '').trim();
    if (dec) {
      const letter = letterForOptionMatch(dec.decision, item.options);
      if (letter) {
        recommended = letter;
        reason = `与已冻结决策 ${dec.id} 一致（migrate 推断，请核对 facts 后改写推荐理由）`;
      }
    }
    if (!recommended || !letters.includes(recommended)) recommended = letters[0];
    if (!reason) {
      reason =
        item.status === 'ANSWERED'
          ? '【迁移占位】已回答但无法自动匹配选项，请 Agent 根据 facts 补充推荐理由'
          : '【迁移占位】请 Agent 根据 facts.json 补充推荐理由后再向用户展示';
    }
    changed++;
    console.log(`${item.id}: recommended_option=${recommended} (${writeFlag ? '将写入' : '预览'})`);
    return { ...item, recommended_option: recommended, recommendation_reason: reason };
  });
  if (!changed) {
    console.log('=> 所有问题字段完备，无需迁移');
    return;
  }
  if (writeFlag) {
    writeFileSync(qPath, JSON.stringify(out, null, 2) + '\n');
    console.log(`=> 已更新 questions.json（${changed} 条）`);
  } else {
    console.log(`=> 预览：${changed} 条待更新。确认后加 --write：node state.mjs migrate <dir> --write`);
  }
}

function nextId(items, prefix) {
  const max = items.reduce((m, x) => {
    const match = String(x.id || '').match(new RegExp(`^${prefix}(\\d+)$`));
    return match ? Math.max(m, Number(match[1])) : m;
  }, 0);
  return `${prefix}${String(max + 1).padStart(3, '0')}`;
}

const stripOptionLetter = (s) => String(s).replace(/^[A-D][.．、\s]+/i, '').trim();

const writeJson = (name, data) => writeFileSync(join(dir, name), JSON.stringify(data, null, 2) + '\n');

function freeze() {
  const rest = process.argv.slice(4);
  const [target, answer] = rest;
  const flags = rest.slice(2);
  const supersede = flags.includes('--supersede');
  const auto = flags.includes('--auto');
  const impactIdx = flags.indexOf('--impact');
  const impact = impactIdx >= 0 && flags[impactIdx + 1]
    ? flags[impactIdx + 1].split(',').map((s) => s.trim()).filter(Boolean)
    : undefined;
  if (!target || !answer || !/^(Q|CON)-\d+$/.test(target)) {
    console.error('用法: node state.mjs freeze <dir> <Q-xxx|CON-xxx> "<选项字母或决定文本>" [--supersede] [--auto] [--impact "a,b"]');
    process.exit(2);
  }
  const decisions = isArr(read('decisions.json'));
  const newId = nextId(decisions, 'DEC-');
  const base = { id: newId, question_id: target, source: auto ? 'AI_DEFAULT' : 'USER', status: 'FROZEN', created_at: new Date().toISOString() };
  if (impact) base.impact = impact;

  if (target.startsWith('CON-')) {
    const conflicts = isArr(read('conflicts.json'));
    const c = conflicts.find((x) => x.id === target);
    if (!c) { console.error(`${target}: conflicts.json 中不存在`); process.exit(1); }
    if (c.status === 'RESOLVED' && !supersede) {
      console.error(`${target} 已由 ${c.resolution_decision_id || '未知 DEC'} 裁决（R5 禁止静默覆盖）。确认改判请加 --supersede`);
      process.exit(1);
    }
    if (supersede && c.resolution_decision_id) {
      const old = decisions.find((d) => d.id === c.resolution_decision_id && d.status === 'FROZEN');
      if (old) { old.status = 'SUPERSEDED'; old.replaced_by = newId; }
    }
    decisions.push({ ...base, decision: String(answer).trim(), topic: `conflict:${c.id}` });
    c.status = 'RESOLVED';
    c.resolution_decision_id = newId;
    writeJson('decisions.json', decisions);
    writeJson('conflicts.json', conflicts);
    console.log(`${newId} FROZEN ← ${target} 「${String(answer).trim()}」`);
  } else {
    const questions = isArr(JSON.parse(existsSync(join(dir, 'questions.json')) ? readFileSync(join(dir, 'questions.json'), 'utf8').trim() || '[]' : '[]'));
    const q = questions.find((x) => x.id === target);
    if (!q) { console.error(`${target}: questions.json 中不存在`); process.exit(1); }
    const frozen = decisions.filter((d) => d.question_id === q.id && d.status === 'FROZEN');
    if (frozen.length && !supersede) {
      console.error(`${q.id} 已有冻结决策 ${frozen.map((d) => d.id).join(', ')}（R5 禁止静默覆盖）。确认改判请加 --supersede`);
      process.exit(1);
    }
    const letters = OPTION_LETTERS.slice(0, (q.options || []).length);
    const m = String(answer).trim().match(/^([A-Da-d])[.．、]?$/);
    const letter = m && letters.includes(m[1].toUpperCase()) ? m[1].toUpperCase() : null;
    const decisionText = letter ? stripOptionLetter(q.options[letters.indexOf(letter)]) : String(answer).trim();
    for (const d of frozen) { d.status = 'SUPERSEDED'; d.replaced_by = newId; }
    decisions.push({ ...base, decision: decisionText, topic: q.topic });
    q.status = 'ANSWERED';
    writeJson('decisions.json', decisions);
    writeJson('questions.json', questions);
    console.log(`${newId} FROZEN ← ${q.id} 「${decisionText}」${frozen.length ? `（supersede ${frozen.map((d) => d.id).join(', ')}）` : ''}`);
  }
  snapshot();
  counters();
}

const RISK_DIMS = ['business_criticality', 'data_impact', 'concurrency_risk', 'security_risk', 'compatibility_risk', 'irreversibility', 'blast_radius', 'evidence_gap'];
const SPECIALIST_FOR = {
  security_risk: 'security',
  data_impact: 'data_integrity',
  irreversibility: 'data_integrity',
  concurrency_risk: 'concurrency',
  compatibility_risk: 'compatibility',
  evidence_gap: 'testability',
};

function risk() {
  const p = join(dir, 'risk.json');
  if (!existsSync(p)) {
    console.error('risk.json 不存在 — Phase 2 后必须先完成八维风险评分（见 references/risk-router.md）');
    process.exit(1);
  }
  let r;
  try { r = JSON.parse(readFileSync(p, 'utf8')); } catch (e) { console.error(`risk.json 解析失败: ${e.message}`); process.exit(1); }
  const dims = r.dimensions || {};
  const bad = [];
  for (const k of RISK_DIMS) {
    const d = dims[k];
    if (!d) { bad.push(`缺维度 ${k}`); continue; }
    const s = Number(d.score);
    if (!Number.isInteger(s) || s < 0 || s > 3) bad.push(`${k}.score=${JSON.stringify(d.score)} 应为 0-3 整数`);
    if (s >= 2 && !(Array.isArray(d.refs) && d.refs.length)) bad.push(`${k}.score>=2 必须给 refs（FACT/CON/DEC id）`);
  }
  if (bad.length) { for (const b of bad) console.error(`risk.json: ${b}`); process.exit(1); }
  const total = RISK_DIMS.reduce((sum, k) => sum + Number(dims[k].score), 0);
  const tier = total >= 15 ? 'COUNCIL' : total >= 8 ? 'FOCUSED' : 'LIGHT';
  const ranked = RISK_DIMS.map((k) => [k, Number(dims[k].score)]).sort((a, b) => b[1] - a[1]);
  const specialists = [];
  if (tier !== 'LIGHT') {
    for (const [k, s] of ranked) {
      if (specialists.length >= (tier === 'COUNCIL' ? 3 : 1)) break;
      if (s < 2) break;
      const sp = SPECIALIST_FOR[k];
      if (sp && !specialists.includes(sp)) specialists.push(sp);
    }
  }
  console.log(JSON.stringify({ total, tier, specialists, top_dims: ranked.filter(([, s]) => s >= 2) }, null, 2));
  console.log(tier === 'LIGHT'
    ? '=> LIGHT：现有轻量流程（主 Agent + Reviewer + Validator），禁止追加角色'
    : tier === 'FOCUSED'
      ? '=> FOCUSED：现有流程 + 1 名专项 Reviewer（references/specialists.md 对应节）'
      : '=> COUNCIL：现有流程 + 至多 3 名专项 Reviewer，全部 BLOCKING CLAIM 必须过 Validator');
}

function stop() {
  const questions = isArr(read('questions.json'));
  const conflicts = isArr(read('conflicts.json'));
  const c = countersData();
  const openQ = questions.filter((q) => q.status === 'OPEN' && q.priority !== 'OPTIONAL');
  const openCon = conflicts.filter((x) => x.status === 'OPEN');
  const checks = {
    open_questions: openQ.length,
    open_conflicts: openCon.length,
    critical_assumptions: c.critical_assumptions,
    unvalidated_high_risks: c.unvalidated_high_risks,
  };
  const known = new Set([
    ...isArr(read('facts.json')).map((x) => x.id),
    ...isArr(read('decisions.json')).map((x) => x.id),
    ...isArr(read('conflicts.json')).map((x) => x.id),
  ]);
  const gaps = [];
  const rp = join(dir, 'risk.json');
  if (!existsSync(rp)) gaps.push('risk.json 缺失（先完成 Phase 2.5 风险评分）');
  else {
    const dims = (JSON.parse(readFileSync(rp, 'utf8')).dimensions) || {};
    for (const k of RISK_DIMS) {
      const d = dims[k];
      if (!d || Number(d.score) < 2) continue;
      if (!Array.isArray(d.refs) || !d.refs.length) { gaps.push(`${k}: score>=2 但无 refs`); continue; }
      for (const ref of d.refs) if (!known.has(ref)) gaps.push(`${k}: ref ${ref} 无法解析为 FACT/DEC/CON`);
    }
  }
  checks.coverage_gaps = gaps.length;
  const blocked = Object.values(checks).reduce((s, v) => s + v, 0);
  for (const [k, v] of Object.entries(checks)) console.log(`${k}: ${v}`);
  for (const g of gaps) console.log(`  覆盖缺口: ${g}`);
  if (blocked === 0) console.log('=> STOP 条件满足（R9 + 覆盖率）：禁止继续追问或“为了严谨”追加审查');
  else console.log('=> 未满足停止条件');
  process.exitCode = blocked === 0 ? 0 : 1;
}

function evalMetrics() {
  const questions = isArr(read('questions.json'));
  const decisions = isArr(read('decisions.json'));
  const challenges = isArr(read('challenges.json'));
  const evidence = isArr(read('evidence.json'));
  const answered = questions.filter((q) => q.status === 'ANSWERED');
  const userDec = decisions.filter((d) => d.status === 'FROZEN' && d.source === 'USER');
  const aiDec = decisions.filter((d) => d.status === 'FROZEN' && d.source === 'AI_DEFAULT');
  const verdicts = { CONFIRMED: 0, PLAUSIBLE: 0, REFUTED: 0 };
  for (const e of evidence) if (verdicts[e.verdict] !== undefined) verdicts[e.verdict]++;
  for (const ch of challenges)
    if (verdicts[ch.status] !== undefined && !evidence.some((e) => e.challenge_id === ch.id)) verdicts[ch.status]++;
  const reviewed = verdicts.CONFIRMED + verdicts.PLAUSIBLE + verdicts.REFUTED;
  const m = {
    questions_total: questions.length,
    should_ask_by_policy: questions.filter((q) => q.priority !== 'OPTIONAL' && q.authority !== 'TECHNICAL' && q.value !== 'LOW').length,
    answered_total: answered.length,
    user_decisions: userDec.length,
    ai_decisions: aiDec.length,
    autonomy_rate: answered.length ? `${Math.round(((answered.length - userDec.length) / answered.length) * 100)}%` : 'n/a',
    review: {
      ...verdicts,
      refuted_rate: reviewed ? `${Math.round((verdicts.REFUTED / reviewed) * 100)}%` : 'n/a',
    },
    snapshots: existsSync(join(dir, 'history')) ? readdirSync(join(dir, 'history')).length : 0,
  };
  console.log(JSON.stringify(m, null, 2));
}

({ counters, frontier, freeze, risk, stop, eval: evalMetrics, validate, gate, snapshot, migrate: migrateQuestions })[cmd]();
