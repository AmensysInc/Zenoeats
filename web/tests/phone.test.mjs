/** A phone number as it is typed, and as it ends up.
 *
 *  The finished form is +1 (xxx) xxx-xxxx, because that is what a North
 *  American customer reads their own number as. What the tests below are
 *  really guarding is the half-typed case: the country is not announced
 *  until there are ten digits, or somebody four characters into a London
 *  number is told they are in Nebraska.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

const root = path.resolve('src');
const cache = new Map();
function load(filename) {
  const file = ['', '.ts'].map((ext) => filename + ext).find((p) => fs.existsSync(p) && fs.statSync(p).isFile());
  if (!file) throw new Error(`Missing test module: ${filename}`);
  if (cache.has(file)) return cache.get(file).exports;
  const module = { exports: {} };
  cache.set(file, module);
  const source = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const local = (id) =>
    id.startsWith('@/') ? load(path.join(root, id.slice(2)))
      : id.startsWith('.') ? load(path.resolve(path.dirname(file), id))
        : require(id);
  new Function('require', 'module', 'exports', source)(local, module, module.exports);
  return module.exports;
}
const { formatPhone } = load(path.join(root, 'features/storefront/contact.ts'));

test('a whole North American number reads +1 (xxx) xxx-xxxx', () => {
  assert.equal(formatPhone('5802267925'), '+1 (580) 226-7925');
});

test('however it was typed', () => {
  for (const typed of ['5802267925', '580 226 7925', '(580)2267925', '580-226-7925']) {
    assert.equal(formatPhone(typed), '+1 (580) 226-7925', typed);
  }
});

test('a country code already typed is not doubled', () => {
  for (const typed of ['15802267925', '+15802267925', '+1 (580) 226-7925']) {
    assert.equal(formatPhone(typed), '+1 (580) 226-7925', typed);
  }
});

test('formatting its own output changes nothing', () => {
  // It runs on every keystroke over the value it produced last time, so a
  // function that drifted would walk the number away a character at a time.
  const once = formatPhone('5802267925');
  assert.equal(formatPhone(once), once);
  assert.equal(formatPhone(formatPhone(once)), once);
});

test('the country is not announced while the number is half typed', () => {
  // "+1 (447) 9" for somebody starting a London number is a guess said out
  // loud. Partial input stays as it was typed until there are ten digits.
  assert.equal(formatPhone('5'), '5');
  assert.equal(formatPhone('580'), '580');
  assert.equal(formatPhone('58022'), '(580) 22');
  assert.equal(formatPhone('580226792'), '(580) 226-792');
});

test('an international number is left alone', () => {
  assert.equal(formatPhone('+447911123456'), '+447911123456');
  assert.equal(formatPhone('447911123456'), '+447911123456');
});

test('nothing in, nothing out', () => {
  assert.equal(formatPhone(''), '');
  assert.equal(formatPhone('   '), '');
});
