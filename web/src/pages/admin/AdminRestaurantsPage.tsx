import { useState } from "react";
import { Empty, ErrorNote, Loading, Panel, Stat, StatGrid } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";
import { PageTitle } from "@/components/layout/Shell";
import { AdminShell } from "@/features/admin/components/AdminShell";
import { RestaurantRow } from "@/features/admin/components/RestaurantRow";
import {
  CreateRestaurantForm,
  IssuedCredentialPanel,
  OwnerForm,
} from "@/features/admin/components/RestaurantForms";
import {
  useCreateOwnerMutation,
  useCreateRestaurantMutation,
  useDeleteRestaurantMutation,
  useListRestaurantsQuery,
  usePlatformReportsQuery,
  useRefreshStripeStatusMutation,
  useResetOwnerPasswordMutation,
  usePurgeRestaurantMutation,
  useRestoreRestaurantMutation,
  useSetRestaurantStatusMutation,
  useStartStripeOnboardingMutation,
  useUpdateRestaurantMutation,
  type Restaurant,
  type StripeSync,
} from "@/features/admin/adminApi";
import { errorMessage } from "@/services/apiClient";
import { money } from "@/utils/format";

/**
 * Platform overview and restaurant administration.
 *
 * The page holds only what it alone needs -- which dialog is open, which row is
 * being edited, and the last answer Stripe gave. Everything server-side comes
 * from RTK Query, so a mutation refreshes the list through its tags rather than
 * this component remembering to refetch.
 */
export function AdminRestaurantsPage() {
  const [showDeleted, setShowDeleted] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [ownerFor, setOwnerFor] = useState<Restaurant | null>(null);
  const [issued, setIssued] = useState<{
    email: string;
    password: string | null;
    status: "ACTIVE" | "INVITED";
    emailConfigured: boolean;
    /** A reset, emailed to them as well; a new owner's first password is not. */
    emailed?: boolean;
    restaurant: Restaurant;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // A point-in-time answer, not stored state, so it lives here rather than in
  // the restaurant row the list returns.
  const [stripeStatus, setStripeStatus] = useState<Record<string, StripeSync>>({});

  const restaurants = useListRestaurantsQuery({ includeDeleted: showDeleted });
  const reports = usePlatformReportsQuery(undefined, { pollingInterval: 30_000 });

  const [createRestaurant] = useCreateRestaurantMutation();
  const [updateRestaurant] = useUpdateRestaurantMutation();
  const [deleteRestaurant] = useDeleteRestaurantMutation();
  const [restoreRestaurant] = useRestoreRestaurantMutation();
  const [purgeRestaurant] = usePurgeRestaurantMutation();
  const [setStatus] = useSetRestaurantStatusMutation();
  const [startOnboarding] = useStartStripeOnboardingMutation();
  const [refreshStripe] = useRefreshStripeStatusMutation();
  const [createOwner] = useCreateOwnerMutation();
  const [resetOwner] = useResetOwnerPasswordMutation();

  /** One place that runs a mutation, tracks which row is busy and surfaces the
   *  failure. Without it every handler repeats the same six lines. */
  async function run<T>(id: string, work: () => Promise<T>): Promise<T | undefined> {
    setBusyId(id);
    setError(null);
    try {
      return await work();
    } catch (e) {
      setError(errorMessage(e));
      return undefined;
    } finally {
      setBusyId(null);
    }
  }

  const rows = restaurants.data ?? [];
  const reportRows = reports.data ?? [];
  const totals = reportRows.reduce(
    (acc, r) => ({
      orders: acc.orders + r.orders_paid,
      live: acc.live + (r.status === "ACTIVE" ? 1 : 0),
    }),
    { orders: 0, live: 0 },
  );

  // Gross volume is summed per currency. Each restaurant settles in its own,
  // and this tile used to add the minor units together and print the result
  // as dollars -- so a single restaurant pricing in rupees or euros made the
  // platform's headline revenue figure a number that meant nothing.
  const grossByCurrency = new Map<string, number>();
  for (const r of reportRows) {
    grossByCurrency.set(r.currency, (grossByCurrency.get(r.currency) ?? 0) + r.gross_revenue_minor);
  }
  const grossVolume =
    grossByCurrency.size === 0
      ? money(0)
      : [...grossByCurrency]
          .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
          .map(([currency, minor]) => money(minor, currency))
          .join(" · ");

  return (
    <AdminShell>
      <PageTitle title="Restaurants" subtitle="A clear view of every restaurant." />

      <ErrorNote message={error ?? (restaurants.error ? errorMessage(restaurants.error) : null)} />

      {/* Currencies are never added together: gross volume lists each one. */}
      <StatGrid className="mb-7 grid-cols-1 sm:mb-9 md:grid-cols-3">
        <Stat label="Live restaurants" value={String(totals.live)} />
        <Stat label="Paid orders" value={String(totals.orders)} />
        <div className="min-w-0 bg-surface p-[18px] sm:p-[23px]">
          <div className="text-caption text-muted">Gross volume</div>
          <div className="tnum mt-2 text-[22px] leading-normal tracking-[-.4px]" style={{ overflowWrap: "anywhere" }}>
            {grossVolume}
          </div>
        </div>
      </StatGrid>

      <Panel
        title="Restaurants"
        action={
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex min-h-[40px] items-center gap-2.5 text-sm">
              <input
                type="checkbox"
                className="h-5 w-5 shrink-0"
                checked={showDeleted}
                onChange={(e) => setShowDeleted(e.target.checked)}
              />
              Show deleted
            </label>
            <button
              type="button"
              className="btn-quiet"
              aria-expanded={showCreate}
              onClick={() => setShowCreate((v) => !v)}
            >
              {!showCreate && <Icon name="plus" className="h-4 w-4" />}
              {showCreate ? "Cancel" : "Add restaurant"}
            </button>
          </div>
        }
      >
        {issued && (
          <IssuedCredentialPanel
            email={issued.email}
            password={issued.password}
            status={issued.status}
            emailConfigured={issued.emailConfigured}
            emailed={issued.emailed}
            restaurant={issued.restaurant}
            onDismiss={() => setIssued(null)}
          />
        )}

        {showCreate && (
          <CreateRestaurantForm
            busy={busyId === "new"}
            onCancel={() => setShowCreate(false)}
            onCreate={async (input) => {
              const created = await run("new", () => createRestaurant(input).unwrap());
              if (created) setShowCreate(false);
            }}
          />
        )}

        {ownerFor && (
          <OwnerForm
            restaurant={ownerFor}
            busy={busyId === ownerFor.id}
            onCancel={() => setOwnerFor(null)}
            onCreate={async (email, fullName) => {
              const res = await run(ownerFor.id, () =>
                createOwner({ id: ownerFor.id, email, full_name: fullName || null }).unwrap(),
              );
              if (res) {
                setIssued({
                  email: res.email,
                  password: res.temporary_password,
                  status: res.status,
                  emailConfigured: res.email_configured,
                  restaurant: ownerFor,
                });
                setOwnerFor(null);
              }
            }}
            onReset={async (email) => {
              const res = await run(ownerFor.id, () =>
                resetOwner({ id: ownerFor.id, email }).unwrap(),
              );
              if (res) {
                setIssued({
                  email: res.email,
                  password: res.temporary_password,
                  status: res.status,
                  emailConfigured: res.email_configured,
                  emailed: true,
                  restaurant: ownerFor,
                });
                setOwnerFor(null);
              }
            }}
          />
        )}

        {restaurants.isLoading ? (
          <Loading />
        ) : !rows.length ? (
          <Empty>No restaurants yet. Add one to get started.</Empty>
        ) : (
          <ul className="card py-0">
              {rows.map((r) => (
                <RestaurantRow
                  key={r.id}
                  restaurant={r}
                  busy={busyId === r.id}
                  editing={editing === r.id}
                  stripeStatus={stripeStatus[r.id]}
                  actions={{
                    onEdit: () => setEditing(r.id),
                    onCancelEdit: () => setEditing(null),
                    onSave: async (changes) => {
                      const ok = await run(r.id, () =>
                        updateRestaurant({ id: r.id, changes }).unwrap(),
                      );
                      if (ok) setEditing(null);
                    },
                    // Reversible, but it still pulls a storefront offline, so
                    // the row asks first before calling this.
                    onDelete: () => void run(r.id, () => deleteRestaurant(r.id).unwrap()),
                    onRestore: () => void run(r.id, () => restoreRestaurant(r.id).unwrap()),
                    onPurge: () => void run(r.id, () => purgeRestaurant(r.id).unwrap()),
                    onActivate: () =>
                      void run(r.id, () => setStatus({ id: r.id, action: "activate" }).unwrap()),
                    onSuspend: () =>
                      void run(r.id, () => setStatus({ id: r.id, action: "suspend" }).unwrap()),
                    onOnboard: async () => {
                      const res = await run(r.id, () => startOnboarding(r.id).unwrap());
                      // Stripe hosts the onboarding form, so this leaves the app.
                      if (res) window.location.href = res.onboarding_url;
                    },
                    onRefreshStripe: async () => {
                      const res = await run(r.id, () => refreshStripe(r.id).unwrap());
                      if (res) setStripeStatus((prev) => ({ ...prev, [r.id]: res }));
                    },
                    onOwner: () => setOwnerFor(r),
                  }}
                />
              ))}
          </ul>
        )}
      </Panel>

      <Panel
        title="Reports"
        action={
          <a className="btn-quiet" href="/api/v1/admin/reports.csv" download>
            <Icon name="download" className="h-4 w-4" />
            Download CSV
          </a>
        }
      >
        {!reports.data?.length ? (
          <Empty>No order data yet.</Empty>
        ) : (
          <div className="card overflow-x-auto py-2">
            <table className="data-table min-w-[750px]">
              <thead>
                <tr>
                  <th>Restaurant</th>
                  <th className="text-right">Paid</th>
                  <th className="text-right">Gross</th>
                  <th className="text-right">Tax</th>
                  <th className="text-right">AOV</th>
                  <th className="text-right">Unpaid</th>
                  <th className="text-right">Expired</th>
                </tr>
              </thead>
              <tbody>
                {reports.data.map((r) => (
                  <tr key={r.restaurant_id}>
                    <td>{r.name}</td>
                    <td className="tnum text-right">{r.orders_paid}</td>
                    <td className="tnum text-right">
                      {money(r.gross_revenue_minor, r.currency)}
                    </td>
                    <td className="tnum text-right">
                      {money(r.tax_collected_minor, r.currency)}
                    </td>
                    <td className="tnum text-right">
                      {money(r.average_order_value_minor, r.currency)}
                    </td>
                    <td className="tnum text-right">{r.orders_pending_payment}</td>
                    <td className="tnum text-right">{r.orders_expired}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <p className="mt-[30px] text-[11px] leading-relaxed text-muted">
        Every read on this page is written to the platform audit log with your user,
        the scope requested, and a correlation id.
      </p>
    </AdminShell>
  );
}
