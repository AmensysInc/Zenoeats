import { useState } from "react";
import { Spinner } from "@/components/common/Feedback";
import { OneTimeSecret } from "@/components/common/Secret";
import type { Restaurant } from "../adminApi";

const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

/**
 * Create a restaurant.
 *
 * The slug rule is duplicated from the API on purpose. It is the tenant's
 * permanent public address and cannot be changed afterwards, so catching a bad
 * one before submission is worth the duplication.
 */
export function CreateRestaurantForm({
  busy,
  onCancel,
  onCreate,
}: {
  busy: boolean;
  onCancel: () => void;
  onCreate: (input: {
    slug: string;
    name: string;
    tax_rate_bps: number;
    tagline: string | null;
  }) => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [taxPct, setTaxPct] = useState("8.25");

  const cleanSlug = slug.toLowerCase().trim();
  const slugValid = cleanSlug === "" || SLUG_PATTERN.test(cleanSlug);

  return (
    <form
      className="editor mb-6 animate-disclose"
      onSubmit={(e) => {
        e.preventDefault();
        onCreate({
          slug: cleanSlug,
          name: name.trim(),
          tax_rate_bps: Math.round(parseFloat(taxPct || "0") * 100),
          tagline: null,
        });
      }}
    >
      <h3 className="mb-5 text-lg font-semibold">Add a restaurant</h3>
      <div className="grid grid-cols-1 gap-[18px] sm:grid-cols-3">
        <label className="block">
          <span className="label">Restaurant name</span>
          <input
            className="field mt-[7px]"
            required
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="label">Subdomain</span>
          <input
            className="field mt-[7px]"
            required
            placeholder="spicehouse"
            value={slug}
            aria-invalid={!slugValid || undefined}
            aria-describedby={!slugValid ? "slug-rule" : undefined}
            onChange={(e) => setSlug(e.target.value)}
          />
          {!slugValid && (
            <span id="slug-rule" className="mt-2 block text-caption text-danger">
              Lowercase letters, numbers and hyphens; must start with a letter or number.
            </span>
          )}
        </label>
        <label className="block">
          <span className="label">Tax rate %</span>
          <input
            className="field tnum mt-[7px]"
            inputMode="decimal"
            value={taxPct}
            onChange={(e) => setTaxPct(e.target.value)}
          />
        </label>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary" disabled={busy || !name || !cleanSlug || !slugValid}>
          {busy && <Spinner />}
          {busy ? "Creating…" : "Create as draft"}
        </button>
        <button type="button" className="link" onClick={onCancel}>
          Cancel
        </button>
        <span className="w-full text-caption text-muted">
          Created restaurants stay in draft until Stripe is connected and you activate them.
        </span>
      </div>
    </form>
  );
}

/**
 * Issue or reissue the owner login for a restaurant.
 *
 * One account per restaurant, with the ADMIN role. The owner adds their own
 * staff from the restaurant portal, so this deliberately offers no role choice.
 */
export function OwnerForm({
  restaurant,
  busy,
  onCancel,
  onCreate,
  onReset,
}: {
  restaurant: Restaurant;
  busy: boolean;
  onCancel: () => void;
  onCreate: (email: string, fullName: string) => void;
  onReset: (email: string) => void;
}) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const clean = email.trim().toLowerCase();

  return (
    <form
      className="editor mb-6 animate-disclose"
      onSubmit={(e) => {
        e.preventDefault();
        onCreate(clean, fullName.trim());
      }}
    >
      <h3 className="text-lg font-semibold">Owner login for {restaurant.name}</h3>
      <p className="mt-2 max-w-prose text-caption text-muted">
        Makes this person the restaurant&apos;s owner (ADMIN). A new address gets a
        temporary password they replace at first sign-in. Someone who already runs
        another Zenoeats restaurant keeps their own password: they&apos;re invited
        (and emailed, when email is set up) and accept it after signing in here. Use Reset password for an
        owner who has forgotten theirs.
      </p>

      <div className="mt-5 grid grid-cols-1 gap-[18px] sm:grid-cols-2">
        <label className="block">
          <span className="label">Owner email</span>
          <input
            className="field mt-[7px]"
            type="email"
            required
            autoFocus
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="label">Full name (optional)</span>
          <input
            className="field mt-[7px]"
            value={fullName}
            maxLength={160}
            onChange={(e) => setFullName(e.target.value)}
          />
        </label>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary" disabled={busy || !clean}>
          {busy && <Spinner />}
          {busy ? "Working…" : "Create login"}
        </button>
        {/* type="button" so it does not submit the create form. */}
        <button
          type="button"
          className="btn-quiet"
          disabled={busy || !clean}
          onClick={() => onReset(clean)}
        >
          Reset password
        </button>
        <button type="button" className="link" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/** Shown once after a credential is issued. The API returns the password
 *  exactly once and stores only its hash, so there is no second chance. */
export function IssuedCredentialPanel({
  email,
  password,
  status,
  emailConfigured,
  emailed = false,
  restaurant,
  onDismiss,
}: {
  email: string;
  password: string | null;
  status: "ACTIVE" | "INVITED";
  emailConfigured: boolean;
  /** A reset password, which is also emailed to them when email is set up. */
  emailed?: boolean;
  restaurant: Restaurant;
  onDismiss: () => void;
}) {
  if (password === null) {
    // An existing login, invited rather than issued anything.
    return (
      <OneTimeSecret
        title="Owner invited"
        footer={
          <button type="button" className="btn-quiet" onClick={onDismiss}>
            Done
          </button>
        }
      >
        <p>
          {email} already has a Zenoeats staff login, so no password was issued and theirs
          is unchanged.{" "}
          {emailConfigured
            ? `We're emailing them an invitation to own ${restaurant.name}.`
            : "Email isn't set up yet, so no invitation email was sent: tell them yourself."}{" "}
          They sign in to its portal with their existing password and accept it.
        </p>
      </OneTimeSecret>
    );
  }

  return (
    <OneTimeSecret
      title={`Temporary password issued${status === "INVITED" ? " (invitation still to accept)" : ""}`}
      secret={password}
      footer={
        <button type="button" className="btn-quiet" onClick={onDismiss}>
          I have saved it
        </button>
      }
    >
      <p>
        {emailed && emailConfigured
          ? "We're emailing the new password to them too. It's also shown here in case the email doesn't arrive: it"
          : "Give these to the owner now. The password"}{" "}
        is not stored and cannot be shown again — only reissued. They must replace it at
        first sign-in before the portal will do anything else.
      </p>
      <dl className="mt-3 grid gap-1">
        <div className="flex gap-2">
          <dt className="w-20 text-muted">Email</dt>
          <dd className="[overflow-wrap:anywhere]">{email}</dd>
        </div>
      </dl>
    </OneTimeSecret>
  );
}
