/**
 * Node tests for the Cloudflare Worker's StreamGenerate response parsing.
 * Run: node tests/worker_parsing.test.js
 *
 * Mirrors tests/test_response_parsing.py: the parser must return only the
 * primary candidate of the main answer frame, never thought summaries,
 * alternative drafts or follow-up chip frames.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'cloudflare', 'worker.js'), 'utf8');

// Extract just the parsing helpers and load them in a sandbox.
// (The full worker registers a fetch handler / uses Cloudflare APIs, so we
// slice out the functions we need by evaluating the whole file in a context
// where such APIs are stubbed.)
const sandbox = {
  console: { log: () => {} },
  addEventListener: () => {},
  exportDefault: undefined,
  TextDecoder: require('util').TextDecoder,
  TextEncoder: require('util').TextEncoder,
  URLSearchParams,
  Map,
  crypto: { getRandomValues: (arr) => { for (let i = 0; i < arr.length; i++) arr[i] = i + 1; return arr; } },
  fetch: () => Promise.reject(new Error('no network in tests')),
  setTimeout, clearTimeout,
};
vm.createContext(sandbox);
// The worker uses an ES module `export default`; rewrite it to a global
// assignment so the file can be evaluated in a plain script context.
const script = source.replace(/^export\s+default\s*\{/m, 'globalThis.__workerDefault = {');
vm.runInContext(script, sandbox);

const { iterFrames, candidateTexts, isAnswerFrame, bestMainAnswer, extractResponseText, nextReqid } = sandbox;

// ─── wire-format builders (mirror the Python tests) ─────────────────────────
function makeLine(...inners) {
  return JSON.stringify(inners.map((inner, i) => ['wrb.fr', 'f' + i, JSON.stringify(inner)]));
}
function makeRaw(...lines) {
  return ")]}'\n\n" + lines.map((l) => `${l.length}\n${l}\n`).join('');
}
function answerInner(segments, drafts = []) {
  const inner = new Array(6).fill(null);
  inner[1] = 'c_abc123';
  inner[2] = 'r_def456';
  inner[4] = [[null, [...segments]], ...drafts.map((d) => [null, [...d]])];
  return inner;
}
function noiseInner(segments) {
  const inner = new Array(6).fill(null);
  inner[4] = [[null, [...segments]]];
  return inner;
}

const ANATOMY_NOISE =
  'The pharynx, larynx, and trachea form a continuous vertical pathway connecting your nasal cavity and mouth down to your lungs. '.repeat(8);
const SHORT_ANSWER = "I'm not sure what you mean. Could you clarify?";

let passed = 0;
let failed = 0;
function check(name, actual, expected) {
  const ok = actual === expected;
  if (ok) { passed++; console.log(`ok   - ${name}`); }
  else { failed++; console.log(`FAIL - ${name}\n  expected: ${JSON.stringify(expected)}\n  actual:   ${JSON.stringify(actual)}`); }
}

// 1. thought/noise frame longer than answer
check(
  'thought frame longer than answer is ignored',
  extractResponseText(makeRaw(makeLine(noiseInner([ANATOMY_NOISE])), makeLine(answerInner([SHORT_ANSWER])))),
  SHORT_ANSWER
);

// 2. follow-up chip frame after answer
const chip = 'Would you like a closer look at how the epiglottis prevents choking during swallowing? '.repeat(6);
check(
  'chip frame after answer is ignored',
  extractResponseText(makeRaw(makeLine(answerInner([SHORT_ANSWER])), makeLine(noiseInner([chip])))),
  SHORT_ANSWER
);

// 3. multi-segment answer joined
check(
  'multi-segment answer is joined',
  extractResponseText(makeRaw(makeLine(answerInner(['你好，', '我是一段', '被切開的答案', '請重新組合'])))),
  '你好，我是一段被切開的答案請重新組合'
);

// 4. longer alternative draft does not override primary
const draft = 'This alternative draft is much longer than the primary answer '.repeat(5);
check(
  'longer alternative draft does not override primary',
  extractResponseText(makeRaw(makeLine(answerInner([SHORT_ANSWER], [draft])))),
  SHORT_ANSWER
);

// 5. progressive updates
check(
  'progressive updates return final text',
  extractResponseText(makeRaw(makeLine(answerInner(['Hel'])), makeLine(answerInner(['Hello'])), makeLine(answerInner(['Hello, world!'])))),
  'Hello, world!'
);

// 6. fallback when no frame carries ids
check(
  'fallback when no frame carries ids',
  extractResponseText(makeRaw(makeLine(noiseInner(['short'])), makeLine(noiseInner([SHORT_ANSWER])))),
  SHORT_ANSWER
);

// 7. multi-frame line
check(
  'multi-frame line all frames parsed',
  extractResponseText(makeRaw(makeLine(noiseInner([ANATOMY_NOISE]), answerInner([SHORT_ANSWER])))),
  SHORT_ANSWER
);

// 8. bestMainAnswer returns null for noise-only lines
check('bestMainAnswer ignores noise lines', bestMainAnswer(makeLine(noiseInner([ANATOMY_NOISE]))), null);
check('bestMainAnswer finds answer lines', bestMainAnswer(makeLine(answerInner(['abc']))), 'abc');

// 9. BardErrorInfo raises
let raised = false;
try { extractResponseText(")]}'\n\n[3,[\"er\",null,\"BardErrorInfo [1037]\"]]\n"); }
catch (e) { raised = /BardErrorInfo/.test(e.message); }
check('bard error raises', raised, true);

// 10. reqid uniqueness
const ids = [];
for (let i = 0; i < 50; i++) ids.push(nextReqid());
check('reqids unique', new Set(ids).size, 50);
check('reqids increasing', JSON.stringify(ids), JSON.stringify([...ids].sort((a, b) => a - b)));

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
