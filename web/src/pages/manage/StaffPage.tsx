import { useEffect, useRef, useState } from "react";
import { Empty, ErrorNote, Loading, Panel, Spinner } from "@/components/common/Feedback";
import { OneTimeSecret } from "@/components/common/Secret";
import { PageTitle } from "@/components/layout/Shell";
import { ManageShell } from "@/features/restaurant/components/ManageShell";
import {
  useChangeStaffRoleMutation,
  useInviteStaffMutation,
  useResendStaffInviteMutation,
  useResetStaffPasswordMutation,
  useRevokeStaffMutation,
  useStaffQuery,
  type StaffInvite,
  type StaffMember,
  type StaffPasswordReset,
} from "@/features/restaurant/restaurantApi";
import { errorMessage } from "@/services/apiClient";

const ROLES = ["ADMIN", "MANAGER", "KITCHEN", "CASHIER", "DRIVER", "IT_SUPPORT"] as const;
type Role = (typeof ROLES)[number];

const ROLE_HELP: Record<Role, string> = {
  ADMIN: "Everything, including the team: invitations, roles and password resets.",
  MANAGER: "Orders, menu, reports, and handing over or cancelling orders. No staff changes.",
  KITCHEN: "The order board and sold-out toggles.",
  CASHIER: "The counter: collect orders with PINs, and sold-out toggles.",
  DRIVER: "Deliveries assigned to them, and nothing else of the portal.",
  IT_SUPPORT:
    "Setup and presentation: the storefront, the restaurant’s details and the delivery area. " +
    "Reads the board, stock and menu to diagnose them, and changes none of the three. " +
    "No reports, no order actions, no staff.",
};

/** A role as it is written for a person rather than as it is stored. The
 *  lowercase reading was already what the table and both dropdowns showed;
 *  this only stops the one role code with an underscore in it arriving as
 *  "it_support" beside "kitchen". */
function roleName(code: string): string {
  return code.replace(/_/g, " ").toLowerCase();
}

/** One row asking "are you sure", for one of the two actions that need it. */
type Confirming = { id: string; kind: "remove" | "reset" };

/** How long after an invitation is queued the page keeps asking the worker's
 *  answer. Longer than the worker ever takes; a row that has not heard by
 *  then is shown without a status rather than as "sending" for ever. */
const SENDING_WINDOW_MS = 2 * 60_000;

/** Still waiting to hear what became of this row's invitation email. */
function stillSending(m: StaffMember): boolean {
  if (m.status === "ACTIVE" || m.invitation_email_status || !m.invited_at) return false;
  return Date.now() - new Date(m.invited_at).getTime() < SENDING_WINDOW_MS;
}

export function StaffPage() {
  // Refreshed every few seconds while an invitation email is on its way, so
  // whether it was delivered appears in the list without a reload.
  const [pollMs, setPollMs] = useState(0);
  const staff = useStaffQuery(undefined, { pollingInterval: pollMs });
  useEffect(() => {
    setPollMs(staff.data?.some(stillSending) ? 3000 : 0);
  }, [staff.data]);
  const [inviteStaff] = useInviteStaffMutation();
  const [revokeStaff] = useRevokeStaffMutation();
  const [changeRole] = useChangeStaffRoleMutation();
  const [resetPassword] = useResetStaffPasswordMutation();
  const [resendInvite] = useResendStaffInviteMutation();
  const [resending, setResending] = useState<string | null>(null);
  // Whether the panel is showing a resend rather than a new invitation.
  const [issuedIsResend, setIssuedIsResend] = useState(false);
  const issuedPanel = useRef<HTMLDivElement>(null);

  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("KITCHEN");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The last invitation sent, and the last password reset. Held only in this
  // component: a temporary password is shown once, and leaving the page is
  // how it goes away.
  const [issued, setIssued] = useState<StaffInvite | null>(null);
  const [reset, setReset] = useState<StaffPasswordReset | null>(null);
  const [confirming, setConfirming] = useState<Confirming | null>(null);
  const [acting, setActing] = useState(false);
  const [savingRole, setSavingRole] = useState<string | null>(null);

  async function invite() {
    setBusy(true);
    setError(null);
    setIssued(null);
    try {
      setIssued(await inviteStaff({ email: email.trim(), role_code: role }).unwrap());
      setIssuedIsResend(false);
      setEmail("");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(member: StaffMember) {
    if (!confirming) return;
    setError(null);
    setActing(true);
    try {
      if (confirming.kind === "remove") {
        await revokeStaff(member.id).unwrap();
      } else {
        setReset(await resetPassword(member.id).unwrap());
      }
      setConfirming(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setActing(false);
    }
  }

  async function resend(member: StaffMember) {
    setError(null);
    setResending(member.id);
    try {
      setIssued(await resendInvite(member.id).unwrap());
      setIssuedIsResend(true);
      // The panel is at the top of the page and the button far below it; a
      // new temporary password nobody scrolled up to see would be lost.
      requestAnimationFrame(() =>
        issuedPanel.current?.scrollIntoView({ behavior: "smooth", block: "center" }),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setResending(null);
    }
  }

  async function saveRole(member: StaffMember, next: Role) {
    if (next === member.role_code) return;
    setError(null);
    setSavingRole(member.id);
    try {
      await changeRole({ membershipId: member.id, role_code: next }).unwrap();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSavingRole(null);
    }
  }

  // The API refuses removing or demoting the last active admin; the row says
  // so up front rather than offering controls that can only fail.
  const activeAdmins = (staff.data ?? []).filter(
    (m) => m.role_code === "ADMIN" && m.status === "ACTIVE",
  ).length;

  const signIn = `${window.location.origin}/manage/login`;

  return (
    <ManageShell>
      <PageTitle title="Your team" subtitle="The right access for every role." />

      <ErrorNote message={error ?? (staff.error ? errorMessage(staff.error) : null)} />

      <Panel title="Invite someone">
        <form
          className="flex flex-col gap-[17px]"
          onSubmit={(e) => {
            e.preventDefault();
            if (!busy && email.includes("@")) void invite();
          }}
        >
          <div className="grid grid-cols-1 gap-[18px] sm:grid-cols-2">
            <label className="block">
              <span className="label">Email</span>
              <input
                className="field mt-[7px]"
                type="email"
                autoComplete="off"
                placeholder="name@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
            <label className="block">
              <span className="label">Role</span>
              <select
                className="field mt-[7px]"
                value={role}
                aria-describedby="role-help"
                onChange={(e) => setRole(e.target.value as Role)}
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {roleName(r)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p id="role-help" className="-mt-2 text-caption text-muted" aria-live="polite">
            {ROLE_HELP[role]}
          </p>
          <div>
            <button type="submit" className="btn-primary" disabled={busy || !email.includes("@")}>
              {busy && <Spinner />}
              {busy ? "Inviting…" : "Invite"}
            </button>
          </div>
        </form>

        <div ref={issuedPanel} />
        {issued && (
          <OneTimeSecret
            title={
              <span>
                {issuedIsResend ? "Invitation resent to " : "Invited "}
                <span className="[overflow-wrap:anywhere]">{issued.email}</span>.
              </span>
            }
            secret={issued.temporary_password}
            link={signIn}
          >
            {/* Only claim an email when one is actually on its way. With no
                email provider configured nothing is sent, and a restaurant
                told otherwise waits on an invitation that never arrives. */}
            {!issued.email_configured && (
              <p className="mb-2 font-semibold text-warning">
                Email isn&apos;t set up yet, so no invitation email was sent. Send them the
                sign-in link{issued.temporary_password ? " and temporary password" : ""} below
                yourself.
              </p>
            )}
            <p>
              {issued.temporary_password
                ? issued.email_configured
                  ? `We're emailing them the sign-in link and this temporary password${issuedIsResend ? " — a new one; any earlier password no longer works" : ""}. It's also shown here once, in case the email doesn't arrive, and can't be looked up again. They choose their own at first sign-in, and this one stops working then.`
                  : "Give them this temporary password yourself. It's shown once and can't be looked up again. They choose their own at first sign-in."
                : `${issued.email_configured ? "We're emailing them the sign-in link. " : ""}They already have a Zenoeats staff login and sign in with the password they have. If they've lost it and work only here, you can reset it from the team list; otherwise Zenoeats support can.`}
            </p>
            {issued.email_configured && (
              // Sending happens a moment later, and can still be refused; the
              // team list shows what actually happened.
              <p className="mt-2 text-caption text-muted">
                Whether it was delivered shows beside them in the team list below.
              </p>
            )}
          </OneTimeSecret>
        )}

        <p className="mt-6 max-w-[78ch] text-caption text-muted">
          An invitation grants nothing on its own. The person signs in at this
          restaurant&apos;s sign-in page — we email them the link when email is set up —
          and accepts it there before the role becomes active. Someone new also needs the
          temporary password shown here. It goes in the same email when email is set up;
          otherwise pass it on yourself. Someone who already works at another Zenoeats
          restaurant signs in with the password they have.
        </p>
      </Panel>

      <Panel title="Team">
        {reset && (
          <OneTimeSecret
            title={
              <span>
                New temporary password for{" "}
                <span className="[overflow-wrap:anywhere]">{reset.email}</span>.
              </span>
            }
            secret={reset.temporary_password}
            link={signIn}
            footer={
              <button type="button" className="link" onClick={() => setReset(null)}>
                done
              </button>
            }
          >
            <p>
              {reset.email_configured
                ? "We're emailing it to them too. It's also shown here once, in case the email doesn't arrive, and can't be looked up again."
                : "Give it to them yourself; it is shown once and cannot be looked up again."}{" "}
              They choose their own the next time they sign in.
            </p>
          </OneTimeSecret>
        )}
        {staff.isLoading ? (
          <Loading />
        ) : !staff.data?.length ? (
          <Empty>Just you so far.</Empty>
        ) : (
          // A table on a tablet and up; labelled cards on a phone, where four
          // columns of names, selects and links would not fit.
          <table className="data-table block md:table">
            <thead className="hidden md:table-header-group">
              <tr>
                <th>Person</th>
                <th>Role</th>
                <th>Status</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="block md:table-row-group">
              {staff.data.map((m) => {
                const invited = m.status !== "ACTIVE";
                const onlyAdmin = !invited && m.role_code === "ADMIN" && activeAdmins === 1;
                const name = m.full_name ?? m.email;

                if (confirming?.id === m.id) {
                  const removing = confirming.kind === "remove";
                  return (
                    <tr key={m.id} className="block md:table-row">
                      <td colSpan={4} className="block md:table-cell">
                        {/* Replaces its own row, never a modal: the person it
                            is about stays exactly where they were. */}
                        <div className="inline-confirm">
                          <p>
                            {!removing
                              ? `Reset ${name}'s password? Their current password stops working and they are signed out on every device. You'll see a new temporary password here, and it's emailed to them too if email is set up.`
                              : invited
                                ? `Cancel ${name}'s invitation? The invitation stops working.`
                                : `Remove ${name} from the team? They lose access to this restaurant straight away.`}
                          </p>
                          <div className="mt-3.5 flex flex-wrap items-center gap-4">
                            <button
                              type="button"
                              className="btn-danger"
                              disabled={acting}
                              onClick={() => void confirm(m)}
                            >
                              {acting && <Spinner />}
                              {acting
                                ? "Working…"
                                : !removing
                                  ? "Reset password"
                                  : invited
                                    ? "Cancel invitation"
                                    : "Remove"}
                            </button>
                            <button
                              type="button"
                              className="link"
                              disabled={acting}
                              onClick={() => setConfirming(null)}
                            >
                              keep
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  );
                }

                return (
                  <tr key={m.id} className="block border-b border-hairline py-[18px] md:table-row md:py-0">
                    <td className="block border-0 py-1 md:table-cell md:border-b md:py-5">
                      <strong className="font-semibold [overflow-wrap:anywhere]">{name}</strong>
                      {m.full_name && (
                        <p className="text-caption text-muted [overflow-wrap:anywhere]">{m.email}</p>
                      )}
                    </td>
                    <td className="block border-0 py-1 md:table-cell md:border-b md:py-5">
                      <span className="text-caption text-muted md:hidden">Role: </span>
                      {/* Your own role, and the only admin's, are not offered:
                          the API refuses both, since either could leave the
                          restaurant with no admin. */}
                      {m.is_you || onlyAdmin ? (
                        roleName(m.role_code)
                      ) : (
                        <select
                          className="field mt-1 max-w-[200px] md:mt-0 md:w-36"
                          aria-label={`${name}'s role`}
                          value={m.role_code}
                          disabled={savingRole === m.id}
                          onChange={(e) => void saveRole(m, e.target.value as Role)}
                        >
                          {ROLES.map((r) => (
                            <option key={r} value={r}>
                              {roleName(r)}
                            </option>
                          ))}
                        </select>
                      )}
                    </td>
                    <td className="block border-0 py-1 md:table-cell md:border-b md:py-5">
                      <span className="text-caption text-muted md:hidden">Status: </span>
                      {invited ? (
                        <>
                          <span className="pill-red">waiting to accept</span>
                          <InvitationEmail member={m} />
                        </>
                      ) : (
                        <span className="pill-green">active</span>
                      )}
                    </td>
                    <td className="block border-0 py-1 md:table-cell md:border-b md:py-5">
                      {m.is_you ? (
                        <span className="text-caption text-muted">you</span>
                      ) : (
                        <span className="flex flex-wrap gap-x-3 md:justify-end">
                          {invited && (
                            <button
                              type="button"
                              className="link min-h-[30px] text-caption"
                              disabled={resending === m.id}
                              onClick={() => void resend(m)}
                            >
                              {resending === m.id ? "resending…" : "resend invite"}
                            </button>
                          )}
                          {/* An admin's password goes through Zenoeats support,
                              so one admin cannot sign in as another. */}
                          {m.role_code !== "ADMIN" && (
                            <button type="button" className="link min-h-[30px] text-caption" onClick={() => openConfirm(m.id, "reset")}>
                              reset password
                            </button>
                          )}
                          {onlyAdmin ? (
                            <span className="self-center text-caption text-muted">only admin</span>
                          ) : (
                            <button
                              type="button"
                              className="link-danger min-h-[30px] text-caption"
                              onClick={() => openConfirm(m.id, "remove")}
                            >
                              {invited ? "cancel invitation" : "remove"}
                            </button>
                          )}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
    </ManageShell>
  );

  function openConfirm(id: string, kind: Confirming["kind"]) {
    setError(null);
    setConfirming({ id, kind });
  }
}

/**
 * What became of a pending invitation's email, beside the person it was for.
 *
 * Sending happens on the worker a moment after inviting, and a provider can
 * still refuse it -- so the invite panel can only say an email is on its way,
 * and this is where the admin finds out whether it arrived.
 */
function InvitationEmail({ member }: { member: StaffMember }) {
  const at = member.invitation_email_at
    ? new Date(member.invitation_email_at).toLocaleTimeString([], {
        hour: "numeric",
        minute: "2-digit",
      })
    : null;
  let text: string | null;
  let tone = "text-muted";
  switch (member.invitation_email_status) {
    case "SENT":
      text = `Invitation emailed${at ? ` at ${at}` : ""}.`;
      break;
    case "FAILED":
      text = `${member.invitation_email_problem ?? "The invitation email was not delivered."} Pass on the sign-in link yourself, or resend once that's fixed.`;
      tone = "text-danger";
      break;
    case "NOT_CONFIGURED":
      text = "No email sent: email isn't set up. Pass on the sign-in link yourself.";
      tone = "text-warning";
      break;
    default:
      text = stillSending(member) ? "Sending the invitation email…" : null;
  }
  if (!text) return null;
  // Asked to pass the link on, the admin needs the link: after a reload the
  // invite panel that showed it is gone, and nothing else on the page has it.
  const handOver =
    member.invitation_email_status === "FAILED" ||
    member.invitation_email_status === "NOT_CONFIGURED";
  const signIn = `${window.location.origin}/manage/login`;
  return (
    <>
      <p className={`mt-1.5 max-w-[34ch] text-caption ${tone}`}>{text}</p>
      {handOver && (
        <p className="mt-1 max-w-[34ch] text-caption">
          <span className="text-muted">Sign-in link: </span>
          <a href={signIn} className="link [overflow-wrap:anywhere]">
            {signIn}
          </a>
        </p>
      )}
    </>
  );
}
