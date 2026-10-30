/**
 * Browser regression checks for the real app with deterministic API fixtures.
 * No fixture is bundled into production. Backend authorization is verified
 * separately against PostgreSQL. Live provider checks remain separate.
 *
 * PLAYWRIGHT_MODULE may point to an existing Playwright installation.
 * Run with the Vite preview on 127.0.0.1:3100.
 */
import fs from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const root = path.resolve(import.meta.dirname, "../..");
const output = path.join(root, "artifacts/redesign");
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const results = [];
const stamp = new Date().toISOString();
// A returning customer, which includes having agreed to the terms. Without
// terms_accepted the guard shows the consent form instead of the page, which
// is correct behaviour and would fail every checkout check below for the
// wrong reason.
const session = { email: "customer@example.com", full_name: "Sam Regular", phone: "3125550123",
  address: "20 Oak Avenue, Chicago", email_pending: false, is_guest: false,
  terms_accepted: true };
const portal = { restaurant_id: "r1", slug: "qa-kitchen", name: "Spice House", tagline: "Bold flavors. Good company.",
  currency: "USD", is_orderable: true, accepting_orders: true, delivery_offered: true,
  stripe_publishable_key: "", stripe_account_id: "acct_fixture", maps_browser_key: null, maps_map_id: null };
const option = (id, name, delta = 0) => ({ id, name, price_delta_minor: delta, is_available: true, image_url: null });
const item = { id: "i1", name: "House cheeseburger", item_type_id: "t1", description: "Grilled burger, cheddar and house sauce.",
  base_price_minor: 1295, currency: "USD", is_available: true, image_url: null,
  included_option_ids: ["o1"], modifier_groups: [{ id: "g1", name: "Cheese", selection_type: "SINGLE",
    is_required: true, min_select: 1, max_select: 1, options: [option("o1","Cheddar",100),option("o2","Swiss",150)] }] };
const simpleItem = { ...item, id: "i2", name: "Hand-cut fries", base_price_minor: 450, included_option_ids: [], modifier_groups: [] };
const combo = { id: "c1", name: "House lunch", description: "Your favourites together.", discount_kind: "PERCENT", discount_value: 1250,
  slots: [{ id: "s1", item_type_id: "t1", label: "Main", items: [item] }, { id: "s2", item_type_id: "t1", label: "Side", items: [simpleItem] }] };
const menu = { meals: [{ id: "m1", name: "Lunch", starts_at: "11:00", ends_at: "15:00", combos: [combo],
  sections: [{ item_type_id: "t1", label: "Burgers", items: [item,simpleItem], groups: [] }] },
{ id: "m2", name: "Dinner", starts_at: "17:00", ends_at: "02:00", combos: [],
  sections: [{ item_type_id: "t1", label: "Burgers", items: [item], groups: [] }] }] };
const amounts = { subtotal_minor: 1295, discount_minor: 0, delivery_fee_minor: 0, tax_minor: 107, total_minor: 1402 };
const order = { order_id: "order-1", order_number: 1042, status: "PREPARING", payment_status: "PAID", currency: "USD",
  amounts, items: [{ name: item.name, quantity: 1, unit_price_minor: 1295, line_total_minor: 1295,
    item_note: null, modifiers: [], combo_name: null, combo_group: null }],
  fulfillment_type: "PICKUP", pickup_pin: "4286", delivery_address: null, tracking: null, expires_at: null, created_at: stamp };
const board = { order_id: "order-1", order_number: 1042, status: "PREPARING", payment_status: "PAID", currency: "USD",
  total_minor: 1402, created_at: stamp, paid_at: stamp, customer_note: "No cutlery please.", pin_locked: false,
  fulfillment_type: "PICKUP", delivery_address: null, delivery_fee_minor: 0, driver: null,
  contact_name: "Sam Regular", contact_phone: "3125550123",
  items: [{ name: item.name, quantity: 1, note: null, modifiers: ["Cheddar"], combo_name: null, combo_group: null }] };
const profile = { ...portal, status: "ACTIVE", timezone: "America/Chicago", tax_mode: "FLAT", tax_rate_bps: 825,
  tax_code: null, address_line1: "100 Main Street", address_line2: null, address_city: "Chicago", address_state: "IL",
  address_postal_code: "60601", address_country: "US", stripe_connected: true, charges_enabled: true };
const restaurant = { ...profile, id: "r1", created_at: stamp, deleted_at: null };
const delivery = { delivery_enabled: true, delivery_available: true, blockers: [], latitude: 41.88, longitude: -87.63,
  geocoded_address: "100 Main Street, Chicago, IL 60601, US", pickup_address: "100 Main Street, Chicago, IL 60601, US",
  origin_is_current: true, geocoding_configured: true, currency: "USD", delivery_fee_taxable: false,
  tax_mode: "FLAT", zones: [{ id: "z1", max_miles: 3, fee_minor: 400 }] };
const report = { currency: "USD", timezone: "America/Chicago", today: "2026-09-17", from: "2026-09-17", to: "2026-09-17",
  orders_paid: 1, orders_completed: 0, orders_cancelled: 0, orders_refunded: 0, gross_sales_minor: 1402,
  refunds_minor: 0, net_sales_minor: 1402, tax_collected_minor: 107, combo_discounts_minor: 0,
  average_order_value_minor: 1402, orders_delivery: 0, orders_delivered: 0, delivery_sales_minor: 0, by_driver: [],
  orders_pending_payment: 0, orders_expired: 0, by_day: [], top_items: [{ name: item.name, units: 1, revenue_minor: 1295 }] };
const cart = { lines: [{ key: "fixture-item", menu_item_id: "i1", name: item.name, quantity: 1,
  unitPreviewMinor: 1295, modifiers: [] }], combos: [] };

async function setup(options = {}) {
  const context = await browser.newContext({ viewport: { width: options.width ?? 390, height: 900 },
    reducedMotion: options.reduced ? "reduce" : "no-preference" });
  const state = {
    session: options.guest ? { ...session, is_guest: true } : { ...session },
    portal: { ...portal, ...options.portal }, menu: structuredClone(menu), profile: { ...profile },
    staff: { user_id: "u1", email: "operator@example.com", full_name: "Alex Cook", role_code: options.role ?? "ADMIN",
      must_change_password: false, restaurant_name: "Spice House", membership_status: options.invited ? "INVITED" : "ACTIVE" },
    order: { ...structuredClone(order), ...options.order }, board: options.empty ? [] : [structuredClone(board)],
    stock: [{ id: "i1", name: item.name, type: "Burgers", is_available: true }],
    errors: options.errors ?? {}, delay: options.delay ?? {}, calls: [], quoteDelay: 0, profileFail: false, logoutFail: false,
  };
  if (options.emptyMenu) state.menu = { meals: [] };
  await context.addInitScript(({ session, cart, withCart, anonymous }) => {
    if (withCart) localStorage.setItem("zenoeats.cart.v2.qa-kitchen", JSON.stringify(cart));
  }, { session: state.session, cart, withCart: options.cart ?? false, anonymous: options.anonymous ?? false });
  if (options.mapsFixture || options.placesFixture) await context.addInitScript(({ failFirst }) => {
    const calls = window.__qaMapCalls = [];
    let first = failFirst;
    class MapFixture {
      constructor() { this.zoom = 14; calls.push(["create"]); }
      fitBounds() { calls.push(["fitBounds"]); }
      panTo(point) { calls.push(["panTo", point]); }
      setCenter(point) { calls.push(["setCenter", point]); }
      getZoom() { return this.zoom; }
      setZoom(value) { this.zoom = value; calls.push(["setZoom", value]); }
    }
    class Bounds { extend() { return this; } }
    class Marker { constructor(options) { Object.assign(this, options); } }
    class Autocomplete {
      constructor(input) { this.input = input; this.listeners = []; window.__qaAutocomplete = this; }
      addListener(_name, handler) {
        this.listeners.push(handler);
        return { remove: () => { this.listeners = this.listeners.filter(item => item !== handler); } };
      }
      getPlace() { return this.place ?? {}; }
      select(address) {
        this.place = { formatted_address: address };
        for (const handler of this.listeners) handler();
      }
    }
    window.google = { maps: { async importLibrary(name) {
      if (first) { first = false; throw new Error("Map provider fixture unavailable"); }
      return name === "maps" ? { Map: MapFixture } :
        name === "marker" ? { AdvancedMarkerElement: Marker } :
          name === "places" ? { Autocomplete } : { LatLngBounds: Bounds };
    } } };
  }, { failFirst: options.mapsFixture === "fail-first" });
  const page = await context.newPage();
  page.setDefaultNavigationTimeout(90000);
  const jsErrors = [];
  page.on("pageerror", e => jsErrors.push(e.message));
  await page.route("**/api/v1/**", async route => {
    const request = route.request(), url = new URL(request.url());
    const key = url.pathname.replace("/api/v1", ""), method = request.method();
    const body = method === "GET" || method === "DELETE" ? null : request.postDataJSON();
    state.calls.push({ path: key, method, body, key: request.headers()["idempotency-key"] });
    const fail = async (code, message, status = 503) => route.fulfill({ status, json: { detail: { code, message } } });
    if (state.delay[key]) await new Promise(resolve => setTimeout(resolve, state.delay[key]));
    if (state.errors[key]) return fail("FIXTURE_ERROR", state.errors[key]);
    let data;
    if (key === "/portal") data = state.portal;
    else if (key === "/menu") data = state.menu;
    else if (key === "/orders/session") {
      if (method === "DELETE") {
        if (state.logoutFail) return fail("LOGOUT_FAILED", "Could not end your session. Try again.");
        return route.fulfill({ status: 204 });
      }
      if (options.anonymous) return fail("UNAUTHENTICATED", "Sign in to continue.", 401);
      data = state.session;
    }
    else if (key === "/orders/quote") {
      if (state.quoteDelay) await new Promise(resolve => setTimeout(resolve, state.quoteDelay));
      data = { currency: "USD", amounts: { ...amounts }, delivery_miles: null };
      if (body.fulfillment_type === "DELIVERY") {
        if (body.delivery_address.includes("Outside")) return fail("OUT_OF_DELIVERY_RANGE","That address is outside our delivery area.",409);
        data.amounts.delivery_fee_minor = 400; data.amounts.total_minor += 400; data.delivery_miles = 1.2;
      }
    }
    else if (key === "/orders" && method === "POST") data = { ...state.order, status: "PENDING_PAYMENT" };
    else if (key.endsWith("/payment-intent")) return fail("PAYMENT_PROVIDER_UNAVAILABLE","Card payments are unavailable. Try again.");
    else if (key === "/orders/order-1") data = state.order;
    else if (key === "/customer/profile") {
      if (state.profileFail) return fail("SAVE_FAILED", "Could not save your details. Try again.");
      Object.assign(state.session, body); data = state.session;
    }
    else if (key === "/customer/orders") data = { orders: [], next_before: null };
    else if (key === "/customer/favourites") data = [];
    else if (key.startsWith("/customer/favourites/")) return route.fulfill({ status: 204 });
    else if (key === "/restaurant/login" || key === "/restaurant/me") data = state.staff;
    else if (key === "/admin/login") data = { email: "platform@example.com" };
    else if (key === "/restaurant/orders") data = state.board;
    else if (key === "/restaurant/orders/order-1/ready") { state.board[0].status = "READY_FOR_PICKUP"; data = {}; }
    else if (key === "/restaurant/orders/order-1/complete") {
      if (body.pin !== "428642") return fail("INVALID_PIN", "That PIN is not correct.", 409);
      state.board = []; data = {};
    }
    else if (key === "/restaurant/orders/order-1/cancel") { state.board = []; data = { refund_needed: true }; }
    else if (key === "/restaurant/orders/history") data = { date: "2026-09-17", timezone: "America/Chicago", orders: [] };
    else if (key === "/restaurant/drivers") data = [{ membership_id: "driver1", name: "Jamie Driver" }];
    else if (key === "/restaurant/deliveries") data = options.empty ? [] : [{ ...board, fulfillment_type: "DELIVERY", status: "READY_FOR_DELIVERY",
      delivery_address: session.address, driver: "Jamie Driver", mine: true }];
    else if (key === "/restaurant/stock") data = state.stock;
    else if (key.endsWith("/availability")) { state.stock[0].is_available = url.searchParams.get("is_available") === "true"; data = state.stock[0]; }
    else if (key === "/restaurant/menu") data = { meals: [] };
    else if (["/restaurant/items", "/restaurant/item-types", "/restaurant/modifier-groups", "/restaurant/combos", "/restaurant/staff"].includes(key)) data = [];
    else if (key === "/restaurant/profile") { if (method === "PATCH") Object.assign(state.profile, body); data = state.profile; }
    else if (key === "/restaurant/delivery") data = delivery;
    else if (key === "/restaurant/reports") data = report;
    else if (key === "/admin/me") data = { email: "platform@example.com" };
    else if (key === "/admin/restaurants") data = [restaurant];
    else if (key === "/admin/reports") data = [{ restaurant_id: "r1", slug: portal.slug, name: portal.name, status: "ACTIVE", currency: "USD",
      orders_paid: 1, gross_revenue_minor: 1402, tax_collected_minor: 107, average_order_value_minor: 1402,
      orders_pending_payment: 0, orders_expired: 0, last_order_at: stamp }];
    else if (key === "/admin/restaurants/r1/orders") data = { restaurant_id: "r1", slug: portal.slug, total: 1,
      orders: [{ order_id: order.order_id, order_number: order.order_number, status: "PREPARING", currency: "USD",
        total_minor: 1402, tax_minor: 107, created_at: stamp, paid_at: stamp, expires_at: null,
        payment_status: "PAID", stripe_payment_intent_id: "pi_fixture" }] };
    else return fail("UNMOCKED_ENDPOINT", method + " " + key, 500);
    await route.fulfill({ status: 200, json: data });
  });
  return { page, context, state, jsErrors };
}
async function textIncludes(page, value) {
  await page.getByText(value, { exact: false }).filter({ visible: true }).first().waitFor({ state: "visible", timeout: 12000 });
}
async function capture(page, name) {
  await page.screenshot({ path: path.join(output, name + ".png"), fullPage: true, animations: "disabled" });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
  assert.equal(overflow, false, name + " overflows horizontally");
}
async function check(name, options, fn) {
  if (process.env.QA_FILTER && !new RegExp(process.env.QA_FILTER).test(name)) return;
  const c = await setup(options);
  try {
    await fn(c);
    assert.deepEqual(c.jsErrors, [], "Uncaught browser errors");
    results.push({ name, status: "passed" });
  } catch (error) {
    results.push({ name, status: "failed", message: error.message });
    await c.page.screenshot({ path: path.join(output, "FAIL-" + name.replace(/[^a-z0-9-]/gi,"-") + ".png"), fullPage: true }).catch(() => {});
  } finally {
    await c.context.close();
    await fs.writeFile(path.join(output,"browser-results.json"), JSON.stringify(results,null,2));
    console.log(results.at(-1).status.toUpperCase() + " " + name + (results.at(-1).message ? ": " + results.at(-1).message : ""));
  }
}
const origin = process.env.ZENOEATS_QA_URL ?? "http://127.0.0.1:3100";
for (const endpoint of ["/portal", "/menu"]) {
  await check("A1-retry-" + endpoint.slice(1), {
    errors: { [endpoint]: "The server took too long to respond." },
  }, async ({page,state}) => {
    await page.goto(origin);
    await textIncludes(page, "The server took too long to respond.");
    delete state.errors[endpoint];
    await page.getByRole("button", {name: "Try again", exact: true}).click();
    await textIncludes(page, item.name);
    assert.equal(await page.getByRole("button", {name: "Try again", exact: true}).count(), 0);
    assert(state.calls.filter(c => c.path === endpoint).length >= 2);
    assert.equal(state.calls.some(c => c.method === "POST" && c.path === "/orders"), false);
  });
}
for (const width of [390,1024,1440]) {
  await check("A1-home-" + width, { width }, async ({page}) => {
    await page.goto(origin); await textIncludes(page, item.name);
    await capture(page,"A1-app-"+width);
  });
}
await check("A1-public-empty", { anonymous: true, emptyMenu: true }, async ({page,state}) => {
  await page.goto(origin); await textIncludes(page,"Nothing on the menu");
  assert.equal(state.calls.some(c=>c.path==="/orders"), false);
});
await check("A1-closed-A4-disabled", { portal: { is_orderable: false, accepting_orders: false }, cart: true }, async ({page}) => {
  await page.goto(origin); await textIncludes(page,"Not taking orders");
  const items = page.getByRole("button",{name: /House cheeseburger/});
  assert(await items.first().isDisabled());
});
await check("A2-item-sheet-keyboard-A4-cart", {}, async ({page}) => {
  await page.goto(origin); await page.getByRole("button",{name: /House cheeseburger/}).first().click();
  await page.getByRole("dialog").waitFor(); await capture(page,"A2-app-390");
  await page.keyboard.press("Escape"); assert.equal(await page.getByRole("dialog").count(),0);
  await page.getByRole("button",{name: /House cheeseburger/}).first().click();
  await page.getByRole("button",{name: /^Add 1/}).click();
  await textIncludes(page,"Review order"); await capture(page,"A4-app-390");
});
await check("A3-combo-sheet", {}, async ({page}) => {
  await page.goto(origin); await page.getByRole("button",{name: /House lunch/}).first().click();
  await page.getByRole("dialog").waitFor(); await capture(page,"A3-app-390");
  await page.keyboard.press("Escape");
});
await check("A9-guest-validation-draft-and-retry", { guest: true, cart: true }, async ({page,state}) => {
  await page.goto(origin+"/checkout"); await textIncludes(page,"Your items");
  assert.equal(await page.locator("#contact-address").count(),0);
  assert.equal(await page.locator("#contact-phone").inputValue(),"(312) 555-0123");
  await page.locator("#contact-full_name").fill("Guest Draft");
  await page.locator("#contact-phone").fill("15736472362");
  assert.equal(await page.locator("#contact-phone").inputValue(),"+1 (573) 647-2362");
  await page.locator("#contact-email").fill("Corrected@Example.COM ");
  await page.locator("#contact-email").blur();
  assert.equal(await page.locator("#contact-email").inputValue(),"corrected@example.com");
  await page.getByRole("radio",{name:/Delivery/}).check();
  await page.locator("#contact-address").fill("30 Draft Street");
  await textIncludes(page,"$4.00"); await capture(page,"A9-delivery-app-390");
  await page.reload(); await textIncludes(page,"Your items");
  assert.equal(await page.locator("#contact-full_name").inputValue(),"Guest Draft");
  assert.equal(await page.locator("#contact-email").inputValue(),"corrected@example.com");
  assert(await page.getByRole("radio",{name:/Delivery/}).isChecked());
  await page.getByRole("button",{name:/Continue to payment/}).click();
  await textIncludes(page,"Card payments are unavailable");
  const created = state.calls.find(c=>c.path==="/orders"&&c.method==="POST");
  assert.equal(created.body.guest_email,"corrected@example.com");
  await page.getByRole("button",{name:/Continue to payment/}).click();
  await page.waitForTimeout(250);
  const attempts = state.calls.filter(c=>c.path==="/orders"&&c.method==="POST");
  assert.equal(attempts.at(-1).key,created.key);
});
await check("A9-quote-invalidated-on-address-edit", { cart:true }, async ({page,state}) => {
  await page.goto(origin+"/checkout"); await textIncludes(page,"Your items");
  await page.getByRole("radio",{name:/Delivery/}).check();
  await textIncludes(page,"$4.00");
  state.quoteDelay = 900;
  await page.locator("#contact-address").fill("Outside delivery area");
  assert(await page.getByRole("button",{name:/Continue to payment/}).isDisabled());
  await textIncludes(page,"outside our delivery area");
});
await check("A9-google-address-suggestion", {
  cart:true, placesFixture:true, portal:{maps_browser_key:"test-browser-key"}
}, async ({page,state}) => {
  await page.goto(origin+"/checkout");
  await page.getByRole("radio",{name:/Delivery/}).check();
  const address=page.locator("#contact-address");
  await address.fill("6542 N Maple");
  await page.waitForFunction(()=>!!window.__qaAutocomplete);
  await page.evaluate(()=>window.__qaAutocomplete.select("6542 N Maplewood Ave, Chicago, IL 60645, USA"));
  await page.waitForFunction(()=>document.querySelector("#contact-address")?.value.includes("60645"));
  await textIncludes(page,"$4.00");
  const quote=state.calls.filter(c=>c.path==="/orders/quote"&&c.body?.fulfillment_type==="DELIVERY").at(-1);
  assert.equal(quote.body.delivery_address,"6542 N Maplewood Ave, Chicago, IL 60645, USA");
});
await check("A13-save-failure-retains-draft-and-verified-email", { cart:true }, async ({page,state}) => {
  await page.goto(origin+"/account/profile"); await textIncludes(page,"Personal details");
  state.profileFail = true;
  await page.locator("#contact-full_name").fill("Changed Name");
  await page.getByRole("button",{name:"Save changes",exact:true}).click();
  await textIncludes(page,"Could not save your details");
  assert.equal(await page.locator("#contact-full_name").inputValue(),"Changed Name");
  state.profileFail = false;
  await page.getByRole("button",{name:"Save changes",exact:true}).click(); await textIncludes(page,"Saved.");
  await page.getByRole("button",{name:"Change email address",exact:true}).click();
  await page.getByLabel("New email address").fill("new@example.com");
  await page.getByRole("button",{name:"Send verification code"}).click();
  await page.getByLabel("Verification code").fill("000000");
  await page.getByRole("button",{name:"Verify and save"}).click();
  await textIncludes(page,"That code is incorrect");
  assert(!state.calls.some(c=>c.path.endsWith("email-sync")));
  await page.getByLabel("Verification code").fill("654321");
  await page.getByRole("button",{name:"Verify and save"}).click();
  await textIncludes(page,"Your verified email is saved"); await capture(page,"A13-app-390");
});
const roleLinks = { ADMIN: ["/manage","/manage/deliveries","/manage/stock","/manage/menu","/manage/staff","/manage/reports","/manage/settings"],
  MANAGER:["/manage","/manage/deliveries","/manage/stock","/manage/menu","/manage/reports"],
  KITCHEN:["/manage","/manage/stock"], CASHIER:["/manage","/manage/stock"], DRIVER:["/manage/deliveries"],
  IT_SUPPORT:["/manage","/manage/stock","/manage/menu","/manage/settings"] };
for (const [role,links] of Object.entries(roleLinks)) {
  await check("B0-role-"+role,{role,width:1024},async({page})=>{
    await page.goto(origin+(role==="DRIVER"?"/manage/deliveries":"/manage"));
    await textIncludes(page,role==="DRIVER"?"Deliveries":"Kitchen");
    for(const href of roleLinks.ADMIN) assert.equal(await page.locator('a[href="'+href+'"]').count()>0,links.includes(href),role+" "+href);
    await capture(page,"B0-"+role+"-1024");
  });
}
for (const [id,route,label] of [
  ["B5","/manage","Kitchen"],["B6","/manage/stock","Stock"],["B7","/manage/menu","Your menu"],
  ["B8","/manage/staff","Your team"],["B9","/manage/reports","Reports"],["B10","/manage/deliveries","Deliveries"],
  ["B11","/manage/settings","Restaurant settings"],["C2","/admin","Restaurants"],
  ["C3","/admin/restaurants/r1/orders","Orders"],["G3","/unknown-route","Page not found"]]) {
  for(const width of [390,1024,1440]) await check(id+"-layout-"+width,{width},async({page})=>{
    await page.goto(origin+route); await textIncludes(page,label); await page.waitForTimeout(350); await capture(page,id+"-app-"+width);
  });
}
await check("B7-all-builder-tabs",{},async({page})=>{
  await page.goto(origin+"/manage/menu"); await textIncludes(page,"Your menu");
  for(const [id,label] of [["B7a","Preview"],["B7b","Items"],["B7c","Meal periods"],["B7d","Combos"],["B7e","Modifier library"]]) {
    await page.getByRole("navigation",{name:"Menu builder"}).getByRole("button",{name:label,exact:true}).click();
    await capture(page,id+"-app-390");
  }
});
for(const status of ["PENDING_PAYMENT","AUTO_ACCEPTED","PREPARING","READY_FOR_PICKUP","COMPLETED","CANCELLED","EXPIRED","UNKNOWN"]) {
  await check("A11-pickup-"+status,{order:{status,pickup_pin:["AUTO_ACCEPTED","PREPARING","READY_FOR_PICKUP"].includes(status)?"4286":null}},async({page})=>{
    await page.goto(origin+"/orders/order-1"); await textIncludes(page,"1042"); await capture(page,"A11-"+status+"-390");
    if(["COMPLETED","CANCELLED","EXPIRED"].includes(status)) assert.equal(await page.getByText("4286",{exact:true}).count(),0);
  });
}
for(const status of ["PREPARING","READY_FOR_DELIVERY","OUT_FOR_DELIVERY","COMPLETED","CANCELLED"]) {
  await check("A11-delivery-"+status,{order:{status,fulfillment_type:"DELIVERY",pickup_pin:null,delivery_address:session.address,
    tracking:{steps:[{step:"PAID",at:stamp}],driver_name:"Jamie",restaurant:{latitude:41.88,longitude:-87.63},destination:{latitude:41.89,longitude:-87.62},
      driver_location:null,eta_seconds:null,eta_computed_at:null}}},async({page})=>{
    await page.goto(origin+"/orders/order-1"); await textIncludes(page,"1042"); await capture(page,"A11-delivery-"+status+"-390");
    assert.equal(await page.getByText("4286",{exact:true}).count(),0);
  });
}
await check("A12-customer-network-error",{errors:{"/orders/session":"Session service is unavailable."}},async({page})=>{
  await page.goto(origin+"/checkout"); await textIncludes(page,"Can't reach sign-in"); await capture(page,"A12-app-390");
});
await check("G2-staff-network-error",{errors:{"/restaurant/me":"Session service is unavailable."}},async({page})=>{
  await page.goto(origin+"/manage"); await page.getByRole("button",{name:"Reload"}).waitFor(); await capture(page,"G2-app-390");
});
await check("B3-invitation",{invited:true},async({page})=>{
  await page.goto(origin+"/manage"); await textIncludes(page,"invitation"); await capture(page,"B3-app-390");
});
await check("B4-driver-access-denied",{role:"DRIVER"},async({page})=>{
  await page.goto(origin+"/manage/settings"); await textIncludes(page,"role"); await capture(page,"B4-app-390");
});
await check("A11-reduced-motion",{reduced:true},async({page})=>{
  await page.goto(origin+"/orders/order-1"); await textIncludes(page,"1042");
  assert(await page.evaluate(()=>matchMedia("(prefers-reduced-motion: reduce)").matches));
  const animations = await page.evaluate(()=>document.getAnimations().filter(a=>a.playState==="running").map(a=>a.effect.getComputedTiming()));
  for (const timing of animations) {
    assert(timing.duration <= 1, "Reduced motion must settle within one millisecond");
    assert.equal(timing.iterations,1,"Reduced motion must not loop");
  }
});

for(const [id,route] of [["A5","/account/sign-in"],["A6","/account/sign-up"],["A7","/account/forgot-password"],
    ["B1","/manage/login"],["B2","/manage/change-password"],["C1","/admin/login"]]) {
  for(const width of [390,1024,1440]) await check(id+"-auth-layout-"+width,{anonymous:true,width},async({page})=>{
    await page.goto(origin+route); await page.locator("h1").waitFor(); await page.waitForTimeout(200);
    await capture(page,id+"-app-"+width);
  });
}
for(const [id,route,target] of [["B1","/manage/login","/manage"],["C1","/admin/login","/admin"]]) {
  await check(id+"-safe-return",{anonymous:true},async({page})=>{
    await page.goto(origin+route+"?next="+encodeURIComponent("/\\untrusted.example"));
    await page.locator("#email").fill("qa@example.com"); await page.locator("#password").fill("test-password-only");
    await page.locator("#submit").click(); await page.waitForURL(origin+target);
  });
}
await check("B2-password-length-and-mismatch",{anonymous:true},async({page,state})=>{
  await page.goto(origin+"/manage/change-password");
  await page.locator("#current").fill("current-test-password");
  await page.locator("#next").fill("short"); await page.locator("#confirm").fill("different");
  assert(await page.locator("#submit").isDisabled());
  await page.locator("#next").fill("a-new-long-password"); await page.locator("#confirm").fill("a-new-long-password");
  assert(await page.locator("#submit").isEnabled());
  assert(!state.calls.some(c=>c.path==="/restaurant/change-password"));
});
await check("A9-delivery-unavailable",{cart:true,portal:{delivery_offered:false,pickup_address:"100 Main Street, Chicago"}},async({page})=>{
  await page.goto(origin+"/checkout"); await textIncludes(page,"Your items");
  assert(await page.getByRole("radio",{name:/Delivery/}).isDisabled());
  assert(await page.getByRole("radio",{name:/Pick-up/}).isChecked());
  await textIncludes(page,"100 Main Street, Chicago");
});
await check("A9-guest-end-failure-keeps-draft",{cart:true,guest:true},async({page,state})=>{
  await page.goto(origin+"/checkout"); await textIncludes(page,"Your items");
  await page.locator("#contact-full_name").fill("Draft stays here");
  state.logoutFail=true;
  await page.getByRole("button",{name:"End guest session",exact:true}).first().click();
  await page.getByRole("button",{name:"End guest session",exact:true}).last().click();
  await textIncludes(page,"Could not end your session");
  assert.equal(await page.locator("#contact-full_name").inputValue(),"Draft stays here");
  assert((await page.evaluate(()=>Object.keys(sessionStorage))).some(k=>k.startsWith("zenoeats:checkout-draft:")));
});
await check("B11-independent-profile-and-ring-drafts",{},async({page,state})=>{
  await page.goto(origin+"/manage/settings"); await textIncludes(page,"Restaurant settings");
  await page.getByLabel("Fee · USD",{exact:false}).fill("");
  await page.getByRole("button",{name:"Save rings",exact:true}).click();
  await textIncludes(page,"Every ring needs a distance and a fee");
  assert(!state.calls.some(c=>c.path==="/restaurant/delivery/zones"));
  await page.getByLabel("Fee · USD",{exact:false}).fill("5.00");
  await page.getByLabel(/^Tagline/).fill("An updated tagline");
  await page.getByRole("button",{name:"Save changes",exact:true}).click();
  await page.waitForTimeout(350);
  const patch=state.calls.find(c=>c.path==="/restaurant/profile" && c.method==="PATCH");
  assert.deepEqual(patch.body,{tagline:"An updated tagline"});
  assert.equal(await page.getByLabel("Fee · USD",{exact:false}).inputValue(),"5.00");
});

await check("A2-limits-merge-and-focus-restoration",{},async({page})=>{
  await page.goto(origin);
  const trigger=page.getByRole("button",{name:/House cheeseburger/}).first();
  await trigger.click();
  assert(await page.getByRole("button",{name:/Fewer/}).isDisabled());
  for(let i=1;i<20;i++) await page.getByRole("button",{name:/More items/}).click();
  assert(await page.getByRole("button",{name:/More items/}).isDisabled());
  await page.getByRole("button",{name:/^Add 20/}).click();
  assert(await trigger.evaluate(el=>el===document.activeElement));
  await trigger.click(); await page.getByRole("button",{name:/^Add 1/}).click();
  const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem("zenoeats.cart.v2.qa-kitchen")));
  assert.equal(saved.lines.length,1); assert.equal(saved.lines[0].quantity,21);
});
await check("A3-independent-shared-item-slots",{},async({page,state})=>{
  state.menu.meals[0].combos[0].slots[1].items=[structuredClone(item)];
  await page.goto(origin); await page.getByRole("button",{name:/House lunch/}).first().click();
  const main=page.getByRole("group",{name:/Main/}), side=page.getByRole("group",{name:/Side/});
  await main.getByRole("radio",{name:/House cheeseburger/}).check();
  await side.getByRole("radio",{name:/House cheeseburger/}).check();
  const cheeses=page.getByRole("group",{name:/Cheese/});
  await cheeses.nth(0).getByRole("radio",{name:/Swiss/}).check();
  assert(await cheeses.nth(1).getByRole("radio",{name:/Cheddar/}).isChecked());
  await page.getByRole("button",{name:/^Add 1/}).click();
  const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem("zenoeats.cart.v2.qa-kitchen")));
  assert.equal(saved.combos.length,1);
});
await check("B6-stock-confirmed-toggle-search-error",{},async({page,state})=>{
  await page.goto(origin+"/manage/stock");
  await page.getByRole("button",{name:"Mark sold out"}).click();
  await page.getByRole("button",{name:"Back in stock"}).waitFor();
  await page.getByLabel("Find an item").fill("No matching food");
  await textIncludes(page,"Nothing matches");
  await page.getByLabel("Find an item").fill("");
  state.errors["/restaurant/items/i1/availability"]="Stock update failed. Try again.";
  await page.getByRole("button",{name:"Back in stock"}).click();
  await textIncludes(page,"Stock update failed");
  assert(await page.getByRole("button",{name:"Back in stock"}).isEnabled());
});
await check("B5-ready-and-invalid-PIN",{},async({page,state})=>{
  await page.goto(origin+"/manage");
  await page.getByRole("button",{name:"Mark ready for pickup",exact:true}).click();
  await page.getByRole("button",{name:"Collect with PIN",exact:true}).click();
  await page.getByLabel(/PIN/).fill("111111");
  await page.getByRole("button",{name:"Hand over",exact:true}).click();
  await textIncludes(page,"That PIN is not correct");
  assert(state.calls.some(c=>c.path.endsWith("/complete")));
});
await check("B5-reasoned-cancel-refund-warning",{},async({page,state})=>{
  await page.goto(origin+"/manage");
  await page.getByRole("button",{name:"cancel order",exact:true}).click();
  const confirm=page.getByRole("button",{name:"Cancel order",exact:true});
  assert(await confirm.isDisabled());
  await page.getByLabel("Reason",{exact:true}).fill("Customer requested cancellation");
  await confirm.click();
  await textIncludes(page,"has not been refunded");
  assert.equal(state.calls.find(c=>c.path.endsWith("/cancel")).body.reason,"Customer requested cancellation");
});
const mapTracking = {
  steps:[{step:"PAID",at:stamp}],driver_name:"Jamie",
  restaurant:{latitude:41.88,longitude:-87.63},destination:{latitude:41.89,longitude:-87.62},
  driver_location:{latitude:41.885,longitude:-87.625,heading:90,recorded_at:new Date().toISOString()},
  eta_seconds:180,eta_computed_at:new Date().toISOString()
};
for(const reduced of [false,true]) await check("A11-map-controls-"+(reduced?"reduced":"normal"),{
  reduced,mapsFixture:true,portal:{maps_browser_key:"test-key-only",maps_map_id:"qa-map"},
  order:{status:"OUT_FOR_DELIVERY",fulfillment_type:"DELIVERY",pickup_pin:null,delivery_address:session.address,tracking:mapTracking}
},async({page})=>{
  await page.goto(origin+"/orders/order-1");
  await page.getByRole("button",{name:"Zoom in",exact:true}).click();
  await page.getByRole("button",{name:"Zoom out",exact:true}).click();
  await page.getByRole("button",{name:"Recenter",exact:true}).click();
  const calls=await page.evaluate(()=>window.__qaMapCalls);
  assert(calls.some(c=>c[0]==="setZoom"&&c[1]===15));
  assert(calls.some(c=>c[0]===(reduced?"setCenter":"panTo")));
  if(reduced) assert(!calls.some(c=>c[0]==="fitBounds"||c[0]==="panTo"));
  await textIncludes(page,"Your driver is nearby");
  await page.evaluate(()=>window.dispatchEvent(new Event("offline")));
  await textIncludes(page,"You're offline");
  assert.equal(await page.getByText("Estimated arrival",{exact:false}).count(),0);
});
await check("A11-map-failure-retry-and-late-auth-failure",{
  mapsFixture:"fail-first",portal:{maps_browser_key:"test-key-only",maps_map_id:"qa-map"},
  order:{status:"OUT_FOR_DELIVERY",fulfillment_type:"DELIVERY",pickup_pin:null,delivery_address:session.address,tracking:mapTracking}
},async({page})=>{
  await page.goto(origin+"/orders/order-1"); await textIncludes(page,"The map could not be loaded");
  await page.getByRole("button",{name:"Retry map"}).click();
  await page.getByRole("button",{name:"Zoom in"}).waitFor();
  await page.evaluate(()=>window.gm_authFailure?.());
  await textIncludes(page,"The map could not be loaded");
  await textIncludes(page,"Payment confirmed");
});
await check("A11-stale-location-no-ETA",{
  order:{status:"OUT_FOR_DELIVERY",fulfillment_type:"DELIVERY",pickup_pin:null,delivery_address:session.address,
    tracking:{...mapTracking,driver_location:{...mapTracking.driver_location,recorded_at:new Date(Date.now()-300000).toISOString()}}}
},async({page})=>{
  await page.goto(origin+"/orders/order-1");
  await textIncludes(page,"Location delayed");
  assert.equal(await page.getByText("Estimated arrival",{exact:false}).count(),0);
});

for(const [id,route,key] of [["G1","/manage","/restaurant/me"],["G4","/checkout","/orders/session"]]) {
  await check(id+"-session-loading-resolves",{cart:true,delay:{[key]:1800}},async({page})=>{
    await page.goto(origin+route);
    await textIncludes(page,"Loading");
    await textIncludes(page,id==="G1"?"Restaurant operations":"Your items");
  });
}
await browser.close();
console.log(JSON.stringify({passed:results.filter(r=>r.status==="passed").length,failed:results.filter(r=>r.status==="failed").length}));
process.exitCode = results.some(r=>r.status==="failed") ? 1 : 0;
