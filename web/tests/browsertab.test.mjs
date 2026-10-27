/** What the browser tab says.
 *
 *  Every page used to carry the one title baked into index.html, so a
 *  customer with the menu, their cart and an order open had three tabs
 *  reading the same thing. The page comes first because that is what
 *  differs between them.
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
const { pageName, documentTitle } = load(path.join(root, 'features/storefront/browserTab.ts'));

const SHOP = "Jr's Corner";

test('the storefront is the menu', () => {
  assert.equal(documentTitle('/', SHOP), "Menu - Jr's Corner");
});

test('each customer page names itself', () => {
  assert.equal(documentTitle('/cart', SHOP), "Cart - Jr's Corner");
  assert.equal(documentTitle('/checkout', SHOP), "Checkout - Jr's Corner");
  assert.equal(documentTitle('/profile', SHOP), "Your account - Jr's Corner");
  assert.equal(documentTitle('/account/profile', SHOP), "Your account - Jr's Corner");
});

test('a page carrying an id is named by what it is', () => {
  assert.equal(documentTitle('/orders/7a1f-xyz', SHOP), "Your order - Jr's Corner");
  assert.equal(documentTitle('/checkout/pay/7a1f-xyz', SHOP), "Payment - Jr's Corner");
});

test('paying is not checkout', () => {
  // /checkout/pay/... starts with /checkout, so the order the prefixes are
  // tried in is load-bearing rather than incidental.
  assert.equal(pageName('/checkout/pay/abc'), 'Payment');
  assert.equal(pageName('/checkout'), 'Checkout');
});

test('a trailing slash is the same page', () => {
  assert.equal(documentTitle('/cart/', SHOP), "Cart - Jr's Corner");
  assert.equal(documentTitle('/', SHOP), "Menu - Jr's Corner");
});

test('the operator portals are named too', () => {
  assert.equal(pageName('/manage/orders'), 'Manage');
  assert.equal(pageName('/admin/restaurants/1/promotions'), 'Admin');
});

test('an unnamed page shows the restaurant rather than a wrong name', () => {
  assert.equal(pageName('/nothing-here'), null);
  assert.equal(documentTitle('/nothing-here', SHOP), "Jr's Corner");
});

test('before the restaurant is known, the page stands alone', () => {
  // A tab briefly reading "Menu" beats one reading somebody else's brand.
  assert.equal(documentTitle('/cart', null), 'Cart');
  assert.equal(documentTitle('/cart', '   '), 'Cart');
});

test('the platform name is only for pages belonging to no restaurant', () => {
  assert.equal(documentTitle('/nothing-here', null), 'ZenoEats');
});
