import { useState } from "react";
import { Link } from "react-router-dom";
import { Spinner, StatusPill } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";
import { RestaurantEditForm } from "./RestaurantEditForm";
import { RestaurantLogins } from "./RestaurantLogins";
import type { Restaurant, RestaurantPatch, StripeSync } from "../adminApi";
import { storefrontUrl } from "@/utils/storefront";

/** Stripe prefixes person requirements with the person id, which means nothing
 *  to an operator reading "what is this restaurant waiting on". */
function readableRequirement(req: string): string {
  return req.replace(/^person_[^.]+\./, "");
}

export type RowActions = {
  onEdit: () => void;
  onSave: (changes: RestaurantPatch) => void;
  onCancelEdit: () => void;
  onDelete: () => void;
  onRestore: () => void;
  onPurge: () => void;
  onActivate: () => void;
  onSuspend: () => void;
  onOnboard: () => void;
  onRefreshStripe: () => void;
  onOwner: () => void;
  onToggleLogins: () => void;
  onResetLogin: (email: string) => void;
};

/**
 * One restaurant: who it is, whether it is live, where Stripe stands, and
 * everything that can be done to it. The editor opens attached underneath,
 * and the two confirmations replace the actions in place, so the restaurant
 * they are about never leaves the screen.
 *
 * A grid rather than a table row, so a phone gets the same actions as a desk
 * without a table scrolling sideways past them.
 */
export function RestaurantRow({
  restaurant: r,
  busy,
  editing,
  loginsOpen,
  stripeStatus,
  actions,
}: {
  restaurant: Restaurant;
  busy: boolean;
  editing: boolean;
  loginsOpen: boolean;
  stripeStatus: StripeSync | undefined;
  actions: RowActions;
}) {
  const deleted = r.deleted_at !== null;
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const outstanding = stripeStatus
    ? [...new Set([...stripeStatus.past_due, ...stripeStatus.currently_due])]
    : [];

  const action = "link min-h-[32px] text-caption";
  // Deleted rows read as faded, but their Restore and Erase controls do not:
  // a confirmation at half opacity looks disabled.
  const faded = deleted ? "opacity-50" : "";

  return (
    <li className="border-b border-hairline py-6 last:border-0">
      <div
        className={`grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3 md:grid-cols-[minmax(0,1.2fr)_95px_minmax(0,1fr)] xl:grid-cols-[minmax(0,1.2fr)_100px_minmax(0,1fr)_minmax(0,1.6fr)] xl:gap-[22px]`}
      >
        <div className={`min-w-0 ${faded}`}>
          <h3 className="flex flex-wrap items-center gap-2 font-semibold">
            {r.name}
            {deleted && <span className="pill-red">deleted</span>}
          </h3>
          <a
            href={storefrontUrl(r.slug)}
            className="mt-1 inline-flex items-center gap-1 text-caption text-muted underline underline-offset-[3px] hover:text-ink"
            target="_blank"
            rel="noreferrer"
          >
            {r.slug}
            <Icon name="external" className="h-3.5 w-3.5" />
            <span className="sr-only">(opens the storefront in a new tab)</span>
          </a>
        </div>

        <div className={faded}>
          <StatusPill status={r.status} />
        </div>

        <div className={`col-span-full text-caption md:col-span-1 ${faded}`}>
          {!r.stripe_account_id ? (
            <span className="text-muted">Not connected</span>
          ) : r.charges_enabled ? (
            <span className="font-semibold text-success">Charges enabled</span>
          ) : (
            <span className="font-semibold text-danger">Onboarding incomplete</span>
          )}

          {/* Why, not just that. Without the outstanding requirement there is
              nothing for an operator to act on. */}
          {stripeStatus && !stripeStatus.charges_enabled && (
            <div className="mt-1 text-muted">
              {stripeStatus.disabled_reason && <div>Stripe: {stripeStatus.disabled_reason}</div>}
              {outstanding.length > 0 && (
                <div className="[overflow-wrap:anywhere]">
                  Needs: {outstanding.map(readableRequirement).join(", ")}
                </div>
              )}
            </div>
          )}
          {stripeStatus?.charges_enabled && <div className="mt-1 text-muted">Synced from Stripe.</div>}
          {/* Cards work either way; without this the wallets are silently
              never offered. "active" is the only state that shows them. */}
          {stripeStatus?.wallet_domain && (
            <div className="mt-1 text-muted [overflow-wrap:anywhere]">
              Apple Pay {stripeStatus.wallet_domain.apple_pay} · Google Pay{" "}
              {stripeStatus.wallet_domain.google_pay}
              {stripeStatus.wallet_domain.problem && (
                <div className="text-danger">{stripeStatus.wallet_domain.problem}</div>
              )}
            </div>
          )}
        </div>

        <div className="col-span-full flex flex-wrap items-center gap-x-[15px] gap-y-1 xl:col-span-1 xl:justify-end">
          {deleted ? (
            <>
              <button type="button" className="btn-primary btn-compact" disabled={busy} onClick={actions.onRestore}>
                {busy && <Spinner />}
                Restore
              </button>
              {/* Two clicks, because this is the one action on this page
                  with nothing behind it. The server refuses any restaurant
                  holding an order or a payment, so the worst a misclick can
                  reach is a menu nobody sold from. */}
              <PurgeButton busy={busy} name={r.name} onPurge={actions.onPurge} />
            </>
          ) : (
            <>
              {r.status !== "ACTIVE" ? (
                <button type="button" className="btn-primary btn-compact" disabled={busy} onClick={actions.onActivate}>
                  {busy && <Spinner />}
                  Activate
                </button>
              ) : (
                <button type="button" className={action} disabled={busy} onClick={actions.onSuspend}>
                  Suspend
                </button>
              )}
              {!r.charges_enabled && (
                // Leaves the app for Stripe-hosted onboarding.
                <button type="button" className={`${action} gap-1`} disabled={busy} onClick={actions.onOnboard}>
                  {r.stripe_account_id ? "Resume Stripe" : "Connect Stripe"}
                  <Icon name="external" className="h-3.5 w-3.5" />
                </button>
              )}
              {r.stripe_account_id && (
                <button type="button" className={action} disabled={busy} onClick={actions.onRefreshStripe}>
                  Refresh Stripe
                </button>
              )}
              <button
                type="button"
                className={action}
                aria-expanded={editing}
                onClick={editing ? actions.onCancelEdit : actions.onEdit}
              >
                {editing ? "Close" : "Edit"}
              </button>
              <button type="button" className={action} onClick={actions.onOwner}>
                Owner login
              </button>
              <button
                type="button"
                className={action}
                aria-expanded={loginsOpen}
                onClick={actions.onToggleLogins}
              >
                {loginsOpen ? "Hide logins" : "Logins"}
              </button>
              <Link className={action} to={`/admin/restaurants/${r.id}/orders`}>
                Orders
              </Link>
              {/* The API refuses to delete an ACTIVE restaurant. Hiding the
                  button avoids offering a certain 409. */}
              {r.status !== "ACTIVE" && !confirmingDelete && (
                <button
                  type="button"
                  className="link-danger min-h-[32px] text-caption"
                  disabled={busy}
                  onClick={() => setConfirmingDelete(true)}
                >
                  Delete
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {/* Reversible, but it still pulls a storefront offline, so it asks --
          in the row rather than in a browser dialog that blocks the tab. */}
      {confirmingDelete && !deleted && (
        <div className="inline-confirm mt-4">
          <p>Delete {r.name}? It can be restored, and its subdomain stays reserved.</p>
          <div className="mt-3.5 flex flex-wrap items-center gap-4">
            <button
              type="button"
              className="btn-danger"
              disabled={busy}
              onClick={() => {
                setConfirmingDelete(false);
                actions.onDelete();
              }}
            >
              Delete
            </button>
            <button type="button" className="link" disabled={busy} onClick={() => setConfirmingDelete(false)}>
              keep it
            </button>
          </div>
        </div>
      )}

      {loginsOpen && !deleted && (
        <RestaurantLogins restaurant={r} busy={busy} onReset={actions.onResetLogin} />
      )}

      {editing && (
        <div className="editor mt-[22px] animate-disclose">
          <RestaurantEditForm
            restaurant={r}
            busy={busy}
            onCancel={actions.onCancelEdit}
            onSave={actions.onSave}
          />
        </div>
      )}
    </li>
  );
}


/**
 * Permanent deletion, behind a second click.
 *
 * Not a browser confirm(): it is unstyled, it blocks the tab, and it reads
 * the same for "delete a draft" as for anything else. The armed state says
 * what goes instead, and moving the mouse away is enough to change your mind.
 */
function PurgeButton({
  busy,
  name,
  onPurge,
}: {
  busy: boolean;
  name: string;
  onPurge: () => void;
}) {
  const [armed, setArmed] = useState(false);

  if (!armed) {
    return (
      <button
        type="button"
        className="link-danger min-h-[32px] text-caption"
        disabled={busy}
        onClick={() => setArmed(true)}
      >
        Delete for good
      </button>
    );
  }

  return (
    <span className="inline-confirm mt-2 flex w-full flex-wrap items-center gap-3 p-4 opacity-100">
      <span className="w-full">Erase {name} and its menu? This cannot be undone.</span>
      <button
        type="button"
        className="btn-danger btn-compact"
        disabled={busy}
        onClick={() => {
          setArmed(false);
          onPurge();
        }}
      >
        Erase
      </button>
      <button type="button" className="link" disabled={busy} onClick={() => setArmed(false)}>
        keep it
      </button>
    </span>
  );
}
