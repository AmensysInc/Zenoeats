import { useState } from "react";
import { Spinner } from "@/components/common/Feedback";
import type { Restaurant, RestaurantAddress, RestaurantPatch, TaxMode } from "../adminApi";

/**
 * Inline editor for one restaurant.
 *
 * Sends only what actually changed. A PATCH that resent every field would
 * clobber a concurrent edit by another admin, and would make "clear the
 * tagline" indistinguishable from "leave it alone" -- the endpoint uses
 * exclude_unset for the same reason.
 *
 * slug and status are shown read-only: slug is the tenant's public address,
 * printed on tables and saved in bookmarks, and status belongs to
 * activate/suspend, which enforce a readiness gate a field write would bypass.
 *
 * Tax: a flat rate, or Stripe Tax. Stripe Tax needs the full pickup address
 * and the restaurant's Stripe tax settings finished; the API checks both and
 * says what is missing if saving is refused.
 */

const ADDRESS_FIELDS: { key: keyof RestaurantAddress; label: string; wide?: boolean }[] = [
  { key: "address_line1", label: "Street address", wide: true },
  { key: "address_line2", label: "Suite, unit (optional)", wide: true },
  { key: "address_city", label: "City" },
  { key: "address_state", label: "State" },
  { key: "address_postal_code", label: "ZIP / postal code" },
  { key: "address_country", label: "Country (2 letters)" },
];

// Stripe's "Food for Immediate Consumption": prepared food, meals, heated and
// dispensed drinks.
const DEFAULT_TAX_CODE = "txcd_40060003";

export function RestaurantEditForm({
  restaurant,
  busy,
  onCancel,
  onSave,
}: {
  restaurant: Restaurant;
  busy: boolean;
  onCancel: () => void;
  onSave: (changes: RestaurantPatch) => void;
}) {
  const [storefrontEnabled, setStorefrontEnabled] = useState(restaurant.storefront_customization_enabled);
  const [name, setName] = useState(restaurant.name);
  const [tagline, setTagline] = useState(restaurant.tagline ?? "");
  const [phone, setPhone] = useState(restaurant.phone ?? "");
  const [taxPct, setTaxPct] = useState((restaurant.tax_rate_bps / 100).toString());
  const [accepting, setAccepting] = useState(restaurant.accepting_orders);
  const [taxMode, setTaxMode] = useState<TaxMode>(restaurant.tax_mode ?? "FLAT");
  const [taxCode, setTaxCode] = useState(restaurant.tax_code ?? DEFAULT_TAX_CODE);
  const [address, setAddress] = useState<Record<keyof RestaurantAddress, string>>(() => ({
    address_line1: restaurant.address_line1 ?? "",
    address_line2: restaurant.address_line2 ?? "",
    address_city: restaurant.address_city ?? "",
    address_state: restaurant.address_state ?? "",
    address_postal_code: restaurant.address_postal_code ?? "",
    address_country: restaurant.address_country ?? "US",
  }));

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const changes: RestaurantPatch = {};
    if (storefrontEnabled !== restaurant.storefront_customization_enabled) changes.storefront_customization_enabled = storefrontEnabled;

    if (name !== restaurant.name) changes.name = name;

    // An empty box means "no tagline", which is null rather than "".
    const nextTagline = tagline.trim() === "" ? null : tagline.trim();
    if (nextTagline !== restaurant.tagline) changes.tagline = nextTagline;
    const nextPhone = phone.trim() === "" ? null : phone.trim();
    if (nextPhone !== (restaurant.phone ?? null)) changes.phone = nextPhone;

    const bps = Math.round(parseFloat(taxPct || "0") * 100);
    if (Number.isFinite(bps) && bps !== restaurant.tax_rate_bps) changes.tax_rate_bps = bps;

    if (accepting !== restaurant.accepting_orders) changes.accepting_orders = accepting;

    if (taxMode !== (restaurant.tax_mode ?? "FLAT")) changes.tax_mode = taxMode;
    const nextCode = taxCode.trim();
    if (nextCode && nextCode !== (restaurant.tax_code ?? DEFAULT_TAX_CODE)) {
      changes.tax_code = nextCode;
    }

    for (const { key } of ADDRESS_FIELDS) {
      const raw = address[key].trim();
      const next = raw === "" ? null : key === "address_country" ? raw.toUpperCase() : raw;
      if (next !== (restaurant[key] ?? null)) changes[key] = next;
    }

    if (Object.keys(changes).length === 0) {
      onCancel();
      return;
    }
    onSave(changes);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-[18px] sm:grid-cols-2">
        <label className="block">
          <span className="label">Name</span>
          <input className="field mt-[7px]" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="block">
          <span className="label">Tagline</span>
          <input
            className="field mt-[7px]"
            value={tagline}
            maxLength={200}
            placeholder="none"
            onChange={(e) => setTagline(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="label">Phone for customers</span>
          <input
            className="field mt-[7px]"
            type="tel"
            value={phone}
            maxLength={32}
            placeholder="needed to activate"
            onChange={(e) => setPhone(e.target.value)}
          />
        </label>
      </div>

      <fieldset className="border-t border-hairline pt-5">
        <legend className="float-left mb-4 w-full text-sm font-semibold">Sales tax</legend>
        <div className="clear-both grid grid-cols-1 gap-[18px] sm:grid-cols-3">
          <label className="block">
            <span className="label">How tax is calculated</span>
            <select
              className="field mt-[7px]"
              value={taxMode}
              onChange={(e) => setTaxMode(e.target.value as TaxMode)}
            >
              <option value="FLAT">Flat rate</option>
              <option value="STRIPE_TAX">Stripe Tax</option>
            </select>
          </label>
          {taxMode === "FLAT" ? (
            <label className="block">
              <span className="label">Tax rate %</span>
              <input
                className="field tnum mt-[7px]"
                value={taxPct}
                inputMode="decimal"
                onChange={(e) => setTaxPct(e.target.value)}
              />
            </label>
          ) : (
            <label className="block">
              <span className="label">Stripe product tax code</span>
              <input
                className="field mt-[7px] font-mono"
                value={taxCode}
                pattern="txcd_[0-9]{8}"
                onChange={(e) => setTaxCode(e.target.value)}
              />
            </label>
          )}
        </div>
        {taxMode === "STRIPE_TAX" && (
          <p className="field-hint max-w-prose">
            Tax is calculated on this restaurant&apos;s Stripe account for the pickup address
            below. The restaurant must finish Stripe&apos;s tax settings and add a registration for
            its state, or orders are charged no tax. <code>{DEFAULT_TAX_CODE}</code> is food for
            immediate consumption.
          </p>
        )}
      </fieldset>

      <fieldset className="border-t border-hairline pt-5">
        <legend className="float-left mb-4 w-full text-sm font-semibold">
          Pickup address{taxMode === "STRIPE_TAX" ? " (required for Stripe Tax)" : ""}
        </legend>
        <div className="clear-both grid grid-cols-1 gap-[18px] sm:grid-cols-4">
          {ADDRESS_FIELDS.map(({ key, label, wide }) => (
            <label key={key} className={`block ${wide ? "sm:col-span-2" : "sm:col-span-2 lg:col-span-1"}`}>
              <span className="label">{label}</span>
              <input
                className="field mt-[7px]"
                value={address[key]}
                maxLength={key === "address_country" ? 2 : 200}
                onChange={(e) => setAddress((prev) => ({ ...prev, [key]: e.target.value }))}
              />
            </label>
          ))}
        </div>
      </fieldset>

      <div className="flex flex-wrap items-center gap-4 border-t border-hairline pt-5">
        <label className="flex min-h-[40px] items-center gap-2.5 text-sm">
          <input
            type="checkbox"
            className="h-5 w-5 shrink-0"
            checked={accepting}
            onChange={(e) => setAccepting(e.target.checked)}
          />
          Accepting orders
        </label>
        <span className="text-caption text-muted">
          Subdomain <code>{restaurant.slug}</code> and status{" "}
          <code>{restaurant.status}</code> are not editable here.
        </span>
      </div>

      <label className="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" className="h-5 w-5" checked={storefrontEnabled} onChange={(e) => setStorefrontEnabled(e.target.checked)} />Storefront customisation</label>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy && <Spinner />}
          {busy ? "Saving…" : "Save changes"}
        </button>
        <button type="button" className="link" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
