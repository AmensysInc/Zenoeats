import { ErrorNote, Loading } from "@/components/common/Feedback";
import { errorMessage } from "@/services/apiClient";
import { storefrontUrl } from "@/utils/storefront";
import { useRestaurantLoginsQuery, type Restaurant, type RestaurantLogin } from "../adminApi";

const ROLE: Record<string, string> = {
  ADMIN: "Owner",
  MANAGER: "Manager",
  KITCHEN: "Kitchen",
  CASHIER: "Cashier",
  DRIVER: "Driver",
};

const STATUS_PILL: Record<RestaurantLogin["status"], string> = {
  ACTIVE: "pill-green",
  INVITED: "pill-amber",
  REVOKED: "pill-red",
};

/**
 * Who can sign in to a restaurant's portal, and whether they have yet.
 *
 * Passwords are never shown -- the server keeps only their hash -- so a lost
 * temporary password is replaced with Reset password, which issues a new one
 * through the same one-time panel as creating the login did.
 */
export function RestaurantLogins({
  restaurant,
  busy,
  onReset,
}: {
  restaurant: Restaurant;
  busy: boolean;
  onReset: (email: string) => void;
}) {
  const logins = useRestaurantLoginsQuery(restaurant.id);
  const signInUrl = `${storefrontUrl(restaurant.slug)}/manage/login`;

  return (
    <div className="editor mt-[22px] animate-disclose">
      <h4 className="font-semibold">Logins for {restaurant.name}</h4>
      <p className="mt-1 text-caption text-muted">
        They sign in at{" "}
        <a className="link-inline [overflow-wrap:anywhere]" href={signInUrl} target="_blank" rel="noreferrer">
          {signInUrl}
        </a>
        . Passwords are never stored readably; Reset password issues a new temporary one.
      </p>

      {logins.isLoading ? (
        <Loading />
      ) : logins.error ? (
        <ErrorNote className="mt-4" message={errorMessage(logins.error)} />
      ) : !logins.data?.length ? (
        <p className="mt-4 text-caption text-muted">
          No logins yet. Use Owner login to create the owner&apos;s.
        </p>
      ) : (
        <ul className="mt-4">
          {logins.data.map((login) => (
            <li
              key={login.user_id}
              className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-hairline py-3 last:border-0"
            >
              <div className="min-w-0 flex-1">
                <div className="font-semibold [overflow-wrap:anywhere]">{login.email}</div>
                <div className="text-caption text-muted">
                  {[login.full_name, ROLE[login.role] ?? login.role].filter(Boolean).join(" · ")}
                  {" · "}
                  {login.signed_in ? "Has signed in" : "Not signed in yet (temporary password)"}
                </div>
              </div>
              <span className={STATUS_PILL[login.status] ?? "pill-amber"}>{login.status}</span>
              {login.status !== "REVOKED" && (
                <button
                  type="button"
                  className="link min-h-[32px] text-caption"
                  disabled={busy}
                  onClick={() => onReset(login.email)}
                >
                  Reset password
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
