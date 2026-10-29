/** The loop breaker between a guarded page and a login page.
 *
 *  What is being guarded against is two pages that each believe the other
 *  should handle this customer: checkout sends them to sign-in, sign-in
 *  sends them straight back, and neither ever renders. The tests below are
 *  about the line between that and an ordinary day, because a breaker that
 *  trips on somebody signing in twice is its own bug.
 */
import assert from 'node:assert/strict';
import { test, beforeEach } from 'node:test';
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

/** Enough of sessionStorage to count with, and nothing more. */
function fakeStorage(impl = {}) {
  const held = new Map();
  return {
    getItem: impl.getItem ?? ((k) => (held.has(k) ? held.get(k) : null)),
    setItem: impl.setItem ?? ((k, v) => held.set(k, String(v))),
    removeItem: impl.removeItem ?? ((k) => held.delete(k)),
  };
}

globalThis.window = { sessionStorage: fakeStorage() };
const { mayBounce, loginSucceeded, MAX_BOUNCES, BOUNCE_WINDOW_MS } =
  load(path.join(root, 'components/layout/loginBounce'));

beforeEach(() => {
  globalThis.window.sessionStorage = fakeStorage();
});

const CHECKOUT = '/checkout';

test('the first redirect to sign-in goes ahead', () => {
  assert.equal(mayBounce(CHECKOUT, 1_000), true);
});

test('a page that keeps being sent back is eventually stopped', () => {
  const now = 1_000;
  for (let i = 0; i < MAX_BOUNCES; i += 1) {
    assert.equal(mayBounce(CHECKOUT, now + i * 200), true, `attempt ${i + 1}`);
  }
  assert.equal(mayBounce(CHECKOUT, now + MAX_BOUNCES * 200), false);
});

test('and stays stopped while it keeps trying', () => {
  // The window must not expire under a page that is still looping: it is the
  // loop itself that keeps moving the clock forward.
  let now = 1_000;
  for (let i = 0; i <= MAX_BOUNCES; i += 1) mayBounce(CHECKOUT, now + i * 200);
  for (let i = 0; i < 20; i += 1) {
    now += BOUNCE_WINDOW_MS / 2;
    assert.equal(mayBounce(CHECKOUT, now), false, `still looping at ${now}`);
  }
});

test('signing in clears the count', () => {
  for (let i = 0; i <= MAX_BOUNCES; i += 1) mayBounce(CHECKOUT, 1_000 + i * 200);
  assert.equal(mayBounce(CHECKOUT, 2_000), false);
  loginSucceeded();
  assert.equal(mayBounce(CHECKOUT, 2_100), true);
});

test('coming back later is not a loop', () => {
  // Sign in at lunchtime, sign in again at dinner. Nothing has gone wrong.
  // The window runs from the last redirect rather than the first, so this is
  // measured from there.
  let last = 1_000;
  for (let i = 0; i < MAX_BOUNCES; i += 1) {
    last = 1_000 + i * 200;
    mayBounce(CHECKOUT, last);
  }
  assert.equal(mayBounce(CHECKOUT, last + BOUNCE_WINDOW_MS + 1), true);
});

test('each page is counted on its own', () => {
  // Being sent to sign-in from the cart, then from checkout, then from an
  // order is three ordinary redirects, not a loop.
  for (let i = 0; i < MAX_BOUNCES; i += 1) mayBounce(CHECKOUT, 1_000 + i * 200);
  assert.equal(mayBounce('/orders/abc', 1_600), true);
});

test('the query string is part of the page', () => {
  for (let i = 0; i < MAX_BOUNCES; i += 1) mayBounce('/checkout?table=4', 1_000 + i * 200);
  assert.equal(mayBounce('/checkout?table=9', 1_600), true);
});

test('without storage to count in, nothing is ever refused', () => {
  // Private windows and embedded browsers throw on access. Losing the
  // breaker is acceptable; taking the page down with it is not.
  globalThis.window.sessionStorage = fakeStorage({
    getItem: () => { throw new Error('denied'); },
    setItem: () => { throw new Error('denied'); },
    removeItem: () => { throw new Error('denied'); },
  });
  for (let i = 0; i < MAX_BOUNCES + 5; i += 1) {
    assert.equal(mayBounce(CHECKOUT, 1_000 + i * 100), true);
  }
  assert.doesNotThrow(() => loginSucceeded());
});

test('nonsense in storage is ignored rather than trusted', () => {
  globalThis.window.sessionStorage.setItem('ze:login-bounces', 'not json');
  assert.equal(mayBounce(CHECKOUT, 1_000), true);
  globalThis.window.sessionStorage.setItem('ze:login-bounces', '{"count":"lots"}');
  assert.equal(mayBounce(CHECKOUT, 1_100), true);
});
