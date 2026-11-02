/** Coming back from a payment that left our page: what Stripe said, and that its
 *  client secret does not stay in the address bar or history. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';

function load() {
  const source = ts.transpileModule(fs.readFileSync('src/features/storefront/paymentReturn.ts', 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  new Function('module', 'exports', source)(module, module.exports);
  return module.exports;
}

function browserAt(href) {
  const replaced = [];
  globalThis.window = {
    location: { href },
    history: {
      state: { idx: 3 },
      replaceState: (state, _t, url) => replaced.push({ state, url }),
    },
  };
  return replaced;
}

const { takePaymentReturn } = load();
const SECRET = 'payment_intent=pi_1&payment_intent_client_secret=pi_1_secret_abc';

for (const [status, expected] of [
  ['succeeded', 'succeeded'],
  ['processing', 'processing'],
  ['failed', 'failed'],
  ['requires_payment_method', 'failed'],
]) {
  test(`redirect_status=${status} reads as ${expected}`, () => {
    browserAt(`https://jrs.zenoeats.com/orders/o1?${SECRET}&redirect_status=${status}`);
    assert.equal(takePaymentReturn(), expected);
  });
}

test('the client secret leaves the address bar, and nothing else does', () => {
  const replaced = browserAt(
    `https://jrs.zenoeats.com/orders/o1?keep=1&${SECRET}&redirect_status=succeeded#t=order-token`,
  );
  takePaymentReturn();
  assert.equal(replaced.length, 1);
  assert.equal(replaced[0].url, '/orders/o1?keep=1#t=order-token');
  assert.ok(!replaced[0].url.includes('secret'));
  // The router's own history state is kept, or Back would break.
  assert.deepEqual(replaced[0].state, { idx: 3 });
});

test('an ordinary visit to the order page is left alone', () => {
  const replaced = browserAt('https://jrs.zenoeats.com/orders/o1#t=order-token');
  assert.equal(takePaymentReturn(), null);
  assert.equal(replaced.length, 0);
});
