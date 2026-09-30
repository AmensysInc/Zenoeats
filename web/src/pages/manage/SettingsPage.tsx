import { SettingsCard } from "@/features/restaurant/components/SettingsCard";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ErrorNote, Loading, Spinner } from "@/components/common/Feedback";
import { PageTitle } from "@/components/layout/Shell";
import { ManageShell } from "@/features/restaurant/components/ManageShell";
import { OwnAccount } from "@/features/restaurant/components/OwnAccount";
import { DeliveryArea } from "@/features/restaurant/components/DeliveryArea";
import { BrandSettings } from "@/features/restaurant/components/BrandSettings";
import { useUploadsInFlight } from "@/features/restaurant/components/ImagePicker";
import {
  useRestaurantProfileQuery,
  useUpdateRestaurantProfileMutation,
  type RestaurantProfile,
  type RestaurantProfilePatch,
} from "@/features/restaurant/restaurantApi";
import { errorMessage } from "@/services/apiClient";
import { restaurantHost } from "@/utils/storefront";

const SECTIONS = [
  ["own-account", "Your account"],
  ["settings-restaurant", "The restaurant"],
  ["settings-brand", "Logo & name"],
  ["settings-address", "Where you are"],
  ["settings-delivery", "Delivery"],
  ["settings-tax", "Tax"],
  ["settings-platform", "Set by Zenoeats"],
] as const;

const ADDRESS_FIELDS = [
  "address_line1",
  "address_line2",
  "address_city",
  "address_state",
  "address_postal_code",
  "address_country",
] as const;

/**
 * The restaurant's own record, for its admin.
 *
 * Everything here used to be the platform's to change, so fixing a typo in a
 * trading name was a support ticket. The fields the platform still owns are
 * shown but not editable, because "you cannot change this here" is a more
 * useful answer than leaving them off the page and letting someone hunt.
 *
 * Seven panels, three ways of saving, on purpose. Your account saves its name
 * and its address separately; Delivery saves each control on the spot; the
 * restaurant, its brand, its address and its tax share the one save bar at
 * the bottom.
 * Folding those into one Save would ask for a password to fix a typo, or
 * discard half-typed delivery rings to save a tagline.
 *
 * Saving sends only what was actually edited. Two admins with the page open
 * can change different things without either one's save reverting the other's,
 * and it is what lets the tagline be cleared -- null means "remove it", where
 * an unsent field means "leave it alone".
 */
export function SettingsPage() {
  const profile = useRestaurantProfileQuery();
  const [save, { isLoading: saving }] = useUpdateRestaurantProfileMutation();

  const [draft, setDraft] = useState<RestaurantProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // A logo still uploading holds Save, or the save would go without it.
  const [uploading, trackUpload] = useUploadsInFlight();

  // The server's answer is the starting point, and becomes it again after a
  // save: the response is the row as it was actually stored, trimmed and
  // normalised, which is not always exactly what was typed.
  useEffect(() => {
    if (profile.data) setDraft(profile.data);
  }, [profile.data]);

  const patch = useMemo(
    () => (profile.data && draft ? changedFields(profile.data, draft) : {}),
    [profile.data, draft],
  );
  const changed = Object.keys(patch).length;
  const dirty = changed > 0;
  // Editing the address invalidates where delivery distances are measured
  // from. Said before the save, while it can still be reconsidered.
  const addressDirty = ADDRESS_FIELDS.some((key) => key in patch);

  function edit(changes: Partial<RestaurantProfile>) {
    setSaved(false);
    setDraft((current) => (current ? { ...current, ...changes } : current));
  }

  async function submit() {
    setError(null);
    try {
      await save(patch).unwrap();
      setSaved(true);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  const heading = (
    <PageTitle
      title="Restaurant settings"
      subtitle="The details behind every order."
      right={<span className="pill">Admin only</span>}
    />
  );

  if (profile.isLoading) {
    return (
      <ManageShell>
        {heading}
        <Loading />
      </ManageShell>
    );
  }
  if (!draft) {
    return (
      <ManageShell>
        {heading}
        <ErrorNote message={errorMessage(profile.error) || "Could not load your settings."} />
        <button type="button" className="btn-quiet" onClick={() => void profile.refetch()}>
          Try again
        </button>
      </ManageShell>
    );
  }

  const stripeTaxAvailable = draft.charges_enabled;

  return (
    <ManageShell>
      {heading}

      <div className="grid grid-cols-1 gap-6 md:grid-cols-[160px_minmax(0,1fr)] xl:grid-cols-[190px_minmax(0,1fr)] xl:gap-9">
        <nav aria-label="Settings sections" className="sticky top-[22px] hidden flex-col gap-[5px] self-start md:flex">
          {SECTIONS.map(([id, label]) => (
            <a
              key={id}
              href={`#${id}`}
              className="flex items-center rounded-chip p-3 text-[13px] transition-colors duration-color hover:bg-brickSoft"
            >
              {label}
            </a>
          ))}
        </nav>

        <div className="min-w-0">
          <div className="mb-6">
            <OwnAccount />
          </div>

          <SettingsCard id="settings-restaurant" title="The restaurant" subtitle="What your customers see.">
            <div className="flex flex-col gap-[17px]">
              <Field label="Restaurant name" hint="What customers see on your menu and receipts.">
                <input
                  className="field"
                  value={draft.name}
                  maxLength={160}
                  onChange={(e) => edit({ name: e.target.value })}
                />
              </Field>
              <Field label="Tagline (optional)" hint="A line under your name on the menu.">
                <input
                  className="field"
                  value={draft.tagline ?? ""}
                  maxLength={200}
                  placeholder="Since 1998"
                  onChange={(e) => edit({ tagline: e.target.value })}
                />
              </Field>
              <Field
                label="Phone number"
                hint="Shown to customers on their order page and in every order email, so they can reach you about an order. Needed before your restaurant can go live."
              >
                <input
                  className="field"
                  type="tel"
                  autoComplete="tel"
                  value={draft.phone ?? ""}
                  maxLength={32}
                  placeholder="+1 214 555 0100"
                  onChange={(e) => edit({ phone: e.target.value })}
                />
              </Field>
              <label className="flex items-start gap-2.5 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 h-5 w-5 shrink-0"
                  checked={draft.accepting_orders}
                  onChange={(e) => edit({ accepting_orders: e.target.checked })}
                />
                <span>
                  <span className="font-semibold">Taking orders</span>
                  <span className="field-hint block">
                    Turn this off to stop new orders while keeping your menu readable. Orders already
                    paid for are unaffected.
                  </span>
                </span>
              </label>
            </div>
          </SettingsCard>

          <BrandSettings draft={draft} edit={edit} onBusyChange={trackUpload} />

          <SettingsCard
            id="settings-address"
            title="Where you are"
            subtitle="Your trading address, not a head office."
          >
            <p className="-mt-2 mb-5 max-w-prose text-caption text-muted">
              Where customers collect their orders. It is also the address your sales tax is worked
              out for, so it needs to be the real trading address rather than a head office.
            </p>
            <div className="flex flex-col gap-[17px]">
              <Field label="Street">
                <input
                  className="field"
                  value={draft.address_line1 ?? ""}
                  maxLength={200}
                  autoComplete="address-line1"
                  onChange={(e) => edit({ address_line1: e.target.value })}
                />
              </Field>
              <Field label="Street, line 2" hint="Optional. Unit or suite.">
                <input
                  className="field"
                  value={draft.address_line2 ?? ""}
                  maxLength={200}
                  autoComplete="address-line2"
                  onChange={(e) => edit({ address_line2: e.target.value })}
                />
              </Field>
              <div className="grid grid-cols-1 gap-[18px] sm:grid-cols-2">
                <Field label="City">
                  <input
                    className="field"
                    value={draft.address_city ?? ""}
                    maxLength={100}
                    autoComplete="address-level2"
                    onChange={(e) => edit({ address_city: e.target.value })}
                  />
                </Field>
                <Field label="State">
                  <input
                    className="field"
                    value={draft.address_state ?? ""}
                    maxLength={100}
                    autoComplete="address-level1"
                    onChange={(e) => edit({ address_state: e.target.value })}
                  />
                </Field>
                <Field label="Postal code">
                  <input
                    className="field"
                    value={draft.address_postal_code ?? ""}
                    maxLength={20}
                    autoComplete="postal-code"
                    onChange={(e) => edit({ address_postal_code: e.target.value })}
                  />
                </Field>
                <Field label="Country" hint="Two letters, like US.">
                  <input
                    className="field uppercase"
                    value={draft.address_country ?? ""}
                    maxLength={2}
                    onChange={(e) => edit({ address_country: e.target.value.toUpperCase() })}
                  />
                </Field>
              </div>
              <Field
                label="Timezone"
                hint="Which day an order counts on in your reports. A name like America/Chicago."
              >
                <input
                  className="field"
                  value={draft.timezone}
                  maxLength={64}
                  onChange={(e) => edit({ timezone: e.target.value })}
                />
              </Field>
              <div aria-live="polite">
                {addressDirty ? (
                  <p className="note-warning">
                    Saving this address pauses delivery and clears its old location. Place it again
                    before distances can be quoted.
                  </p>
                ) : (
                  <p className="note">
                    Changing any address field pauses delivery until the new address is placed again.
                  </p>
                )}
              </div>
            </div>
          </SettingsCard>

          <DeliveryArea addressDirty={addressDirty} />

          <SettingsCard id="settings-tax" title="Tax" subtitle="How each order is priced.">
            <fieldset className="flex flex-col gap-4">
              <legend className="mb-3 text-sm font-semibold">Sales tax calculation</legend>
              <label className="flex items-start gap-2.5 text-sm">
                <input
                  type="radio"
                  name="tax-mode"
                  className="mt-0.5 h-5 w-5 shrink-0"
                  checked={draft.tax_mode === "FLAT"}
                  onChange={() => edit({ tax_mode: "FLAT" })}
                />
                <span>
                  <span className="font-semibold">One flat rate</span>
                  <span className="field-hint block">
                    The same percentage on every order. Simple, and only correct where your local
                    rate really is a single number.
                  </span>
                </span>
              </label>

              {draft.tax_mode === "FLAT" && (
                <div className="animate-disclose pl-[30px]">
                  <Field label="Rate" hint="A percentage, up to 30. For example 8.75.">
                    <input
                      className="field tnum max-w-[8rem]"
                      inputMode="decimal"
                      value={percentText(draft.tax_rate_bps)}
                      onChange={(e) => edit({ tax_rate_bps: bpsFromPercent(e.target.value) })}
                    />
                  </Field>
                </div>
              )}

              <label className="flex items-start gap-2.5 text-sm">
                <input
                  type="radio"
                  name="tax-mode"
                  className="mt-0.5 h-5 w-5 shrink-0"
                  disabled={!stripeTaxAvailable}
                  checked={draft.tax_mode === "STRIPE_TAX"}
                  aria-describedby={stripeTaxAvailable ? undefined : "stripe-tax-unavailable"}
                  onChange={() => edit({ tax_mode: "STRIPE_TAX" })}
                />
                <span className={stripeTaxAvailable ? "" : "text-muted"}>
                  <span className="font-semibold">Work it out per order, through Stripe</span>
                  <span className="field-hint block">
                    State, county and city rates for the address above, calculated on your own
                    Stripe account, and reported there.
                  </span>
                  {!stripeTaxAvailable && (
                    <span id="stripe-tax-unavailable" className="mt-1 block text-caption text-danger">
                      Available once your Stripe account is connected and taking payments.
                    </span>
                  )}
                </span>
              </label>

              {draft.tax_mode === "STRIPE_TAX" && (
                <div className="animate-disclose pl-[30px]">
                  <Field
                    label="Product tax code"
                    hint="txcd_40060003 covers prepared food and drink. Change it only if Stripe told you to."
                  >
                    <input
                      className="field max-w-[16rem] font-mono"
                      value={draft.tax_code}
                      onChange={(e) => edit({ tax_code: e.target.value })}
                    />
                  </Field>
                </div>
              )}
            </fieldset>
          </SettingsCard>

          <SettingsCard
            id="settings-platform"
            title="Set by Zenoeats"
            subtitle="Managed by the platform team."
          >
            <dl>
              <ReadOnly
                label="Your web address"
                value={restaurantHost(draft.slug)}
                why="It is printed on your tables and saved in customers' bookmarks, so moving it is a job we do with you rather than a text box."
              />
              <ReadOnly
                label="Status"
                value={draft.status.toLowerCase()}
                why="Going live and pausing a restaurant have checks of their own."
              />
              <ReadOnly
                label="Currency"
                value={draft.currency}
                why="What every order and payment you already have is counted in."
              />
            </dl>
          </SettingsCard>

          {/* Only the restaurant, its address and its tax. Sticky, but above
              the phone's bottom navigation rather than under it. */}
          <footer className="sticky bottom-[85px] z-cart mt-6 flex flex-wrap items-center gap-3 rounded-ticket border border-hairline bg-surface/[.95] p-4 shadow-[0_-8px_24px_#2526200A] backdrop-blur-md sm:bottom-3 sm:flex-nowrap sm:gap-5 sm:px-[22px] sm:py-[18px]">
            <div className="min-w-0 basis-full sm:basis-auto sm:flex-1" aria-live="polite">
              <strong className="text-sm font-semibold">
                {dirty
                  ? `${changed} unsaved ${changed === 1 ? "change" : "changes"}`
                  : saved
                    ? "Saved."
                    : "Nothing to save."}
              </strong>
              <p className="text-caption text-muted">
                {uploading ? "Waiting for the picture to finish uploading…" : "Restaurant, brand, address and tax only."}
              </p>
              <ErrorNote message={error} className="mt-3" />
            </div>
            <button
              type="button"
              className="btn-primary flex-1 sm:flex-none"
              disabled={!dirty || saving || uploading > 0}
              onClick={submit}
            >
              {saving && <Spinner />}
              {saving ? "Saving…" : "Save changes"}
            </button>
            <button
              type="button"
              className="link"
              disabled={!dirty || saving}
              onClick={() => {
                setError(null);
                setDraft(profile.data ?? null);
              }}
            >
              Discard
            </button>
          </footer>
        </div>
      </div>
    </ManageShell>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="label mb-[7px]">{label}</span>
      {children}
      {hint && <span className="field-hint block">{hint}</span>}
    </label>
  );
}

function ReadOnly({ label, value, why }: { label: string; value: string; why: string }) {
  return (
    <div className="grid grid-cols-1 gap-[5px] border-b border-hairline py-[15px] text-sm last:border-0 sm:grid-cols-[130px_minmax(0,1fr)] sm:gap-5">
      <dt className="font-semibold">{label}</dt>
      <dd className="[overflow-wrap:anywhere]">
        {value}
        <small className="mt-1.5 block text-caption text-muted">{why}</small>
      </dd>
    </div>
  );
}

/** 875 -> "8.75". Kept as a string so a half-typed "8." survives the render. */
function percentText(bps: number): string {
  return String(bps / 100);
}

function bpsFromPercent(text: string): number {
  const value = Number.parseFloat(text);
  if (!Number.isFinite(value)) return 0;
  // Round rather than truncate: 8.75 in binary floating point is 874.9999…,
  // and a tax rate quietly one hundredth of a percent low is the kind of bug
  // nobody notices until an accountant does.
  return Math.max(0, Math.round(value * 100));
}

/**
 * What actually changed, in the shape the API takes.
 *
 * An empty text field means "there isn't one" and is sent as null, which is
 * how the tagline and the optional address lines are cleared. The server trims
 * and normalises too -- this is about not sending fields nobody edited.
 */
function changedFields(original: RestaurantProfile, draft: RestaurantProfile) {
  const patch: RestaurantProfilePatch = {};
  const editable = [
    "name",
    "tagline",
    "phone",
    "timezone",
    "accepting_orders",
    "tax_mode",
    "tax_rate_bps",
    "tax_code",
    "address_line1",
    "address_line2",
    "address_city",
    "address_state",
    "address_postal_code",
    "address_country",
    "logo_path",
    "brand_name_image_path",
    "brand_name_font",
  ] as const;

  for (const key of editable) {
    const before = original[key];
    const after = draft[key];
    const cleaned = typeof after === "string" ? after.trim() : after;
    // The name has no "none": an empty one is not a change worth sending, and
    // the server would refuse it anyway.
    const value = cleaned === "" && key !== "name" ? null : cleaned;
    if (value === before || (value === null && before === null)) continue;
    if (key === "name" && value === "") continue;
    (patch as Record<string, unknown>)[key] = value;
  }
  return patch;
}
