import { useEffect, useState, type ReactNode } from "react";
import { useAppDispatch } from "@/app/hooks";
import { sessionEstablished, sessionEnded } from "@/features/session/sessionSlice";
import { useAdminMeQuery } from "@/features/admin/adminApi";
import {
  useAcceptInvitationMutation,
  useStaffLogoutMutation,
  useStaffMeQuery,
  type StaffMe,
} from "@/features/restaurant/restaurantApi";
import { ApiError, errorMessage } from "@/services/apiClient";
import { ManageShell } from "@/features/restaurant/components/ManageShell";
import { ErrorNote, StatePage } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";
import { AuthLayout } from "./AuthLayout";
import { Booting, isRefusal, loginSucceeded, SendToLogin, Unreachable } from "./guardParts";

/**
 * Route guards for the two credentialed portals.
 *
 * These are a convenience, not a security control. The session is an httpOnly
 * cookie the browser sends by itself, and the API authorises every request
 * independently -- a guard that was bypassed would reach endpoints that refuse
 * it anyway. What this buys is not showing an empty shell to someone who is
 * about to be redirected.
 *
 * The redirect is a full navigation rather than a router push, because the
 * login pages are separate HTML entry points outside React.
 *
 * In its own file, away from the customer guard, because importing it pulls
 * in the admin API, the staff API and the manage shell. Both portal areas are
 * loaded on demand, and this has to be inside that boundary rather than
 * beside it.
 */

export function RequireAdmin({ children }: { children: ReactNode }) {
  const dispatch = useAppDispatch();
  const { data, error, isLoading, isFetching, refetch } = useAdminMeQuery();

  useEffect(() => {
    if (data) {
      loginSucceeded();
      dispatch(sessionEstablished({ portal: "admin", email: data.email }));
    } else if (error) dispatch(sessionEnded());
  }, [data, error, dispatch]);

  // "Not asked yet" is not "not signed in". Redirecting during the first
  // request would bounce an authenticated operator to the login page.
  if (isLoading) return <Booting />;
  if (error instanceof ApiError && error.status === 401)
    return <SendToLogin loginPath="/admin/login" />;
  if (isRefusal(error)) return <Refused message={error.message} />;
  if (error) return <Unreachable retry={refetch} retrying={isFetching} />;
  return <>{children}</>;
}

export function RequireStaff({
  children,
  roles,
}: {
  children: ReactNode;
  /** Roles this page is for. Left out, every active member may open it. */
  roles?: string[];
}) {
  const dispatch = useAppDispatch();
  const { data, error, isLoading, isFetching, refetch } = useStaffMeQuery();

  useEffect(() => {
    if (data) {
      loginSucceeded();
      dispatch(
        sessionEstablished({
          portal: "staff",
          email: data.email,
          fullName: data.full_name,
          roleCode: data.role_code,
          restaurantName: data.restaurant_name,
          storefrontEnabled: data.storefront_customization_enabled,
          mustChangePassword: data.must_change_password,
        }),
      );
    } else if (error) dispatch(sessionEnded());
  }, [data, error, dispatch]);

  if (isLoading) return <Booting />;
  if (error instanceof ApiError && error.status === 401)
    return <SendToLogin loginPath="/manage/login" />;
  if (isRefusal(error)) return <Refused message={error.message} signOut />;
  if (error) return <Unreachable retry={refetch} retrying={isFetching} />;

  // An account still holding a temporary password is refused everywhere but
  // change-password, so sending it anywhere else would only bounce. The API
  // enforces this regardless; this makes the refusal actionable instead of a
  // wall of 403s.
  if (data?.must_change_password) {
    window.location.replace("/manage/change-password");
    return null;
  }
  // Signed in, but not yet on the team. Every other staff endpoint refuses
  // this session, so the invitation is the only thing worth showing.
  if (data?.membership_status === "INVITED") return <AcceptInvitation me={data} />;
  // Reached by a bookmark or a typed address rather than the tabs, which
  // already leave this page out. Every call the page makes would be refused,
  // so say why once instead of showing a screen of errors.
  if (data && roles && !roles.includes(data.role_code)) return <NotForYourRole me={data} />;
  return <>{children}</>;
}

/**
 * Rule 27, in the portal: an invitation grants nothing until the person it
 * names accepts it, signed in as themselves.
 */
function AcceptInvitation({ me }: { me: StaffMe }) {
  const [accept, { isLoading }] = useAcceptInvitationMutation();
  const [logout] = useStaffLogoutMutation();
  const [error, setError] = useState<string | null>(null);

  return (
    <AuthLayout
      operator
      title={`Join ${me.restaurant_name}`}
      intro={
        <>
          You&apos;ve been invited to the team as{" "}
          <strong className="font-semibold text-ink">{me.role_code.toLowerCase()}</strong>. Accept
          to start using the portal, signed in as {me.email}.
        </>
      }
    >
      <ErrorNote message={error} className="mb-5" />
      <div className="auth-stack">
        <button
          type="button"
          className="btn-primary w-full"
          disabled={isLoading}
          onClick={async () => {
            setError(null);
            try {
              await accept().unwrap();
            } catch (e) {
              setError(errorMessage(e));
            }
          }}
        >
          {isLoading ? "Accepting…" : "Accept invitation"}
        </button>
        <button
          type="button"
          className="btn-quiet w-full"
          onClick={async () => {
            await logout().unwrap().catch(() => undefined);
            window.location.assign("/manage/login");
          }}
        >
          Not now — sign out
        </button>
      </div>
    </AuthLayout>
  );
}

function NotForYourRole({ me }: { me: StaffMe }) {
  return (
    <ManageShell>
      <div className="mx-auto max-w-md py-12 text-center sm:py-16">
        <Icon name="lock" className="mx-auto mb-4 h-7 w-7 text-muted" />
        <h1 className="font-display text-[34px] leading-[1.12] tracking-[-1px] sm:text-4xl">
          Not part of your role
        </h1>
        <p className="mt-4 text-muted">
          You&apos;re signed in to {me.restaurant_name} as{" "}
          <strong className="font-semibold text-ink">{me.role_code.toLowerCase()}</strong>, which
          doesn&apos;t include this page. An admin at the restaurant can change your role.
        </p>
      </div>
    </ManageShell>
  );
}

/** Offers a way out as well as a reason, because the usual cause is being
 *  signed in as the wrong person. */
function Refused({ message, signOut = false }: { message: string; signOut?: boolean }) {
  const [logout] = useStaffLogoutMutation();
  return (
    <StatePage
      title="No access here"
      action={
        signOut && (
          <button
            type="button"
            className="btn-quiet"
            onClick={async () => {
              await logout().unwrap().catch(() => undefined);
              window.location.assign("/manage/login");
            }}
          >
            Sign in with another account
          </button>
        )
      }
    >
      {message}
    </StatePage>
  );
}
