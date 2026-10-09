import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "@/app/hooks";
import { ErrorNote, Loading, Spinner, StatePage } from "@/components/common/Feedback";
import { BackLink } from "@/components/common/BackLink";
import { Icon } from "@/components/common/icons";
import { MenuImage } from "@/components/common/MenuImage";
import { itemAdded, selectCartCount } from "@/features/cart/cartSlice";
import { ModifierSheet } from "@/features/cart/components/ModifierSheet";
import { useOpenCart } from "@/features/cart/useOpenCart";
import { ChangeEmail } from "@/features/storefront/components/ChangeEmail";
import { CloseAccount } from "@/features/storefront/components/CloseAccount";
import { refreshDraftContact } from "@/features/storefront/checkoutDraft";
import { ContactFields } from "@/features/storefront/components/ContactFields";
import { CustomerAccountBar } from "@/features/storefront/components/CustomerAccountBar";
import { CustomerHeader } from "@/features/storefront/components/CustomerHeader";
import { FavouriteButton } from "@/features/storefront/components/FavouriteButton";
import {
  CONTACT_FIELDS,
  collapse,
  contactErrors,
  contactInputId,
} from "@/features/storefront/contact";
import {
  useCustomerOrdersQuery,
  useCustomerSessionQuery,
  useFavouritesQuery,
  useLazyCustomerOrdersQuery,
  usePortalQuery,
  usePublicMenuQuery,
  useUpdateProfileMutation,
} from "@/features/storefront/storefrontApi";
import { errorMessage } from "@/services/apiClient";
import { money } from "@/utils/format";
import type { Contact, CustomerSession, Item, Meal, OrderSummary } from "@/types";

type Tab = "details" | "orders" | "favourites";

const TABS: { id: Tab; label: string; icon: "user" | "bag" | "heart" }[] = [
  { id: "details", label: "Personal details", icon: "user" },
  { id: "orders", label: "Orders", icon: "bag" },
  { id: "favourites", label: "Favourites", icon: "heart" },
];

const STATUS_LABEL: Record<string, string> = {
  AUTO_ACCEPTED: "Confirmed",
  PREPARING: "Being made",
  READY_FOR_PICKUP: "Ready to collect",
  READY_FOR_DELIVERY: "Waiting for driver",
  OUT_FOR_DELIVERY: "On its way",
  COMPLETED: "Completed",
  CANCELLED: "Cancelled",
};

const IN_PROGRESS = ["AUTO_ACCEPTED", "PREPARING", "READY_FOR_PICKUP", "READY_FOR_DELIVERY", "OUT_FOR_DELIVERY"];

/** Up to two initials for the avatar, or none when there is no name yet. */
function initials(name: string | null): string {
  return (name ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}

/**
 * The customer's own page at this restaurant: who they are, what they have
 * ordered here, and what they have saved to order again.
 *
 * The section is in the address, so "Orders" can be linked to and Back returns
 * to the section someone was reading. The cart is untouched by anything here,
 * and a way back to it is offered whenever there is one to go back to.
 */
export function ProfilePage() {
  const [params, setParams] = useSearchParams();
  const tab: Tab = TABS.some((t) => t.id === params.get("tab"))
    ? (params.get("tab") as Tab)
    : "details";
  const portal = usePortalQuery();
  const session = useCustomerSessionQuery();
  const cartCount = useAppSelector(selectCartCount);

  useOpenCart();

  if (!portal.data || !session.data) {
    if (portal.error) {
      return <StatePage title="This page is unavailable">{errorMessage(portal.error)}</StatePage>;
    }
    return <StatePage busy>Loading…</StatePage>;
  }

  const guest = session.data.is_guest;
  const monogram = initials(session.data.full_name);

  return (
    <div className="flex min-h-dvh flex-col">
      <CustomerHeader restaurant={portal.data} />
      <main className="mx-auto w-full max-w-[720px] px-5 pb-[70px] pt-3 sm:px-6 sm:pt-6 lg:max-w-[1040px]">
        <CustomerAccountBar />
        <BackLink />

        <div className="my-6 flex items-center justify-between gap-6 sm:my-9">
          <div className="min-w-0">
            <p className="eyebrow text-muted">{guest ? "Guest session" : "Your account"}</p>
            <h1 className="my-2 font-display text-[34px] leading-[1.12] tracking-[-1px] sm:text-[40px]">
              {guest ? "Your orders" : "Manage profile"}
            </h1>
            <p className="text-muted">Keep your details ready for your next order.</p>
          </div>
          {monogram && (
            <span
              aria-hidden="true"
              className="hidden h-[84px] w-[84px] shrink-0 place-items-center rounded-full bg-[#E0E9D9] text-[28px] font-bold text-brick sm:grid"
            >
              {monogram}
            </span>
          )}
        </div>

        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[220px_minmax(0,1fr)] lg:gap-9">
          <nav aria-label="Profile sections" className="flex flex-wrap gap-2 lg:flex-col lg:gap-3">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                aria-current={tab === t.id ? "page" : undefined}
                onClick={() => setParams(t.id === "details" ? {} : { tab: t.id })}
                className="flex min-h-[44px] items-center gap-3 rounded-[12px] px-4 text-sm font-[650] text-ink transition-colors duration-color hover:bg-brickSoft/60 aria-[current=page]:bg-[#E7EDDE] aria-[current=page]:text-brick lg:min-h-[52px] lg:px-[18px]"
              >
                <Icon name={t.icon} className="h-5 w-5" />
                {t.label}
              </button>
            ))}
            {cartCount > 0 ? (
              <Link
                to="/cart"
                className="flex min-h-[44px] items-center gap-3 rounded-[12px] px-4 text-sm font-[650] text-ink hover:bg-brickSoft/60 lg:min-h-[52px] lg:px-[18px]"
              >
                <Icon name="arrow" className="h-5 w-5" />
                Return to your order
              </Link>
            ) : (
              <Link
                to="/"
                className="flex min-h-[44px] items-center gap-3 rounded-[12px] px-4 text-sm font-[650] text-ink hover:bg-brickSoft/60 lg:min-h-[52px] lg:px-[18px]"
              >
                <Icon name="store" className="h-5 w-5" />
                Browse the menu
              </Link>
            )}
          </nav>

          <div className="min-w-0">
            {tab === "details" && <DetailsTab session={session.data} slug={portal.data.slug} />}
            {tab === "orders" && <OrdersTab />}
            {tab === "favourites" && (
              <FavouritesTab
                session={session.data}
                currency={portal.data.currency}
                orderable={portal.data.is_orderable}
              />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

// ----------------------------------------------------------------- details

function savedContact(session: CustomerSession): Contact {
  return {
    full_name: session.full_name ?? "",
    phone: session.phone ?? "",
    address: session.address ?? "",
  };
}

function DetailsTab({ session, slug }: { session: CustomerSession; slug: string }) {
  const [save, { isLoading }] = useUpdateProfileMutation();
  const [contact, setContact] = useState<Contact>(() => savedContact(session));
  const [showErrors, setShowErrors] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // Held here rather than inside ChangeEmail: the link that opens it sits
  // beside the email field, and the form it opens sits under the card.
  const [changingEmail, setChangingEmail] = useState(false);
  const errors = contactErrors(contact);
  // What Discard goes back to: the details as last saved, which the session
  // reflects once a save has landed.
  const last = savedContact(session);
  const dirty = CONTACT_FIELDS.some((field) => collapse(contact[field]) !== collapse(last[field]));

  async function submit() {
    const first = CONTACT_FIELDS.find((field) => errors[field]);
    if (first) {
      setShowErrors(true);
      document.getElementById(contactInputId(first))?.focus();
      return;
    }
    setError(null);
    setSaved(false);
    try {
      const next = {
        full_name: collapse(contact.full_name),
        phone: collapse(contact.phone),
        address: collapse(contact.address),
      };
      await save(next).unwrap();
      refreshDraftContact(slug, session, next);
      setContact(next);
      setSaved(true);
    } catch (e) {
      // The draft stays exactly as typed, so a retry needs nothing re-entered.
      setError(errorMessage(e));
    }
  }

  return (
    <section
      className="rounded-banner border border-hairline bg-surface p-5 sm:p-8"
      aria-labelledby="details-heading"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 id="details-heading" className="font-display text-[26px] leading-[1.2] tracking-[-.5px]">
          Personal details
        </h2>
        <span className={session.is_guest ? "pill" : "pill-green"}>
          {session.is_guest ? "Guest session" : "Registered account"}
        </span>
      </div>
      <p className="mb-6 mt-2 text-sm text-muted">
        Filled in for you at checkout. Orders you have already placed keep the details they were
        placed with.
      </p>
      <form
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <ContactFields
          columns
          contact={contact}
          onChange={(next) => {
            setSaved(false);
            setContact(next);
          }}
          errors={showErrors ? errors : {}}
          email={session.email}
          emailHint={
            session.is_guest
              ? "Where this guest session's receipts go. Create an account to keep your details."
              : "The email you sign in with. A new one is verified before it replaces it."
          }
          emailAction={
            session.is_guest || changingEmail ? null : (
              <button type="button" className="link" onClick={() => setChangingEmail(true)}>
                Change
              </button>
            )
          }
          disabled={isLoading}
        />
        <ErrorNote message={error} className="mt-5" />
        {/* Announced without moving focus: the customer stays where they
            were in the form. */}
        <p className="sr-only" aria-live="polite">
          {saved ? "Your details are saved." : ""}
        </p>
        {saved && (
          <p className="note-success mt-5" aria-hidden="true">
            Saved.
          </p>
        )}
        <div className="mt-7 flex flex-wrap items-center gap-3 border-t border-hairline pt-6">
          <button type="submit" className="btn-primary flex-1 rounded-full sm:flex-none" disabled={isLoading}>
            {isLoading ? (
              <>
                <Spinner />
                Saving…
              </>
            ) : (
              "Save changes"
            )}
          </button>
          <button
            type="button"
            className="btn-quiet flex-1 rounded-full sm:flex-none"
            disabled={isLoading || !dirty}
            onClick={() => {
              setSaved(false);
              setContact(last);
              setShowErrors(false);
              setError(null);
            }}
          >
            Discard changes
          </button>
        </div>
      </form>
      <ChangeEmail
        session={session}
        slug={slug}
        open={changingEmail}
        onOpenChange={setChangingEmail}
      />
      <CloseAccount session={session} />
    </section>
  );
}

// ------------------------------------------------------------------ orders

function OrdersTab() {
  // Fresh on every visit: the order someone just paid for should be here.
  const first = useCustomerOrdersQuery(undefined, { refetchOnMountOrArgChange: true });
  const [loadMore, more] = useLazyCustomerOrdersQuery();
  // Later pages, appended below the first. Reset whenever the first page is
  // fetched again, so a refetch never leaves a stale tail beneath it.
  const [older, setOlder] = useState<OrderSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const firstData = first.data;

  useEffect(() => {
    setOlder([]);
    setCursor(firstData?.next_before ?? null);
  }, [firstData]);

  if (first.error) return <ErrorNote message={errorMessage(first.error)} />;
  if (!firstData) return <Loading>Loading your orders…</Loading>;

  const orders = [...firstData.orders, ...older];
  if (orders.length === 0) {
    return (
      <div className="empty">
        <p className="font-display text-2xl text-ink">No orders here yet.</p>
        <Link to="/" className="btn-primary mt-5">
          Browse the menu
        </Link>
      </div>
    );
  }

  return (
    <section aria-label="Your orders">
      <ul className="flex flex-col gap-3">
        {orders.map((order) => (
          <li key={order.order_id}>
            <Link
              to={`/orders/${order.order_id}`}
              className="flex items-start justify-between gap-4 rounded-product border border-hairline bg-surface p-5 transition-[box-shadow,border-color] duration-color hover:border-[#AFC0A3] hover:shadow-[0_8px_25px_#2549240D] sm:p-6"
            >
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-2.5">
                  <span className="font-semibold">#{order.order_number}</span>
                  <span className={IN_PROGRESS.includes(order.status) ? "pill-amber" : order.status === "CANCELLED" ? "pill-red" : "pill"}>
                    {STATUS_LABEL[order.status] ?? order.status}
                  </span>
                </p>
                <p className="mt-1 text-caption text-muted">
                  {new Date(order.created_at).toLocaleString(undefined, {
                    dateStyle: "medium",
                    timeStyle: "short",
                  })}
                  {" · "}
                  {order.fulfillment_type === "DELIVERY" ? "Delivery" : "Pickup"}
                </p>
                <p className="mt-2 text-sm [overflow-wrap:anywhere]">{order.lines.join(", ")}</p>
              </div>
              <span className="tnum whitespace-nowrap text-[15px] font-[550]">
                {money(order.total_minor, order.currency)}
              </span>
            </Link>
          </li>
        ))}
      </ul>

      <ErrorNote message={more.error ? errorMessage(more.error) : null} className="mt-4" />
      {cursor && (
        <button
          type="button"
          className="btn-quiet mt-5 w-full"
          disabled={more.isFetching}
          onClick={async () => {
            const page = await loadMore({ before: cursor }).unwrap().catch(() => null);
            if (!page) return;
            setOlder((prev) => [...prev, ...page.orders]);
            setCursor(page.next_before);
          }}
        >
          {more.isFetching ? "Loading…" : "Show older orders"}
        </button>
      )}
    </section>
  );
}

// -------------------------------------------------------------- favourites

function menuItems(meals: Meal[]): Map<string, Item> {
  const byId = new Map<string, Item>();
  for (const meal of meals) {
    for (const section of meal.sections) {
      for (const item of [...section.items, ...section.groups.flatMap((g) => g.items)]) {
        byId.set(item.id, item);
      }
    }
  }
  return byId;
}

function FavouritesTab({
  session,
  currency,
  orderable,
}: {
  session: CustomerSession;
  currency: string;
  orderable: boolean;
}) {
  if (session.is_guest) {
    return (
      <div className="empty">
        <Icon name="heart" className="mx-auto mb-3 h-7 w-7" />
        <p className="text-ink">Save the things you order most, and find them here next time.</p>
        <p className="mt-1">Favourites need an account, so they are still here after this browser forgets you.</p>
        <a
          href={`/account/sign-up?next=${encodeURIComponent("/profile?tab=favourites")}`}
          className="btn-primary mt-5"
        >
          Create an account
        </a>
      </div>
    );
  }
  return <SavedItems currency={currency} orderable={orderable} />;
}

function SavedItems({ currency, orderable }: { currency: string; orderable: boolean }) {
  const dispatch = useAppDispatch();
  const favourites = useFavouritesQuery();
  const menu = usePublicMenuQuery();
  const cartCount = useAppSelector(selectCartCount);
  const [customizing, setCustomizing] = useState<Item | null>(null);
  const [added, setAdded] = useState<string | null>(null);
  const addedTimer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(addedTimer.current), []);

  const error = favourites.error ?? menu.error;
  if (error) return <ErrorNote message={errorMessage(error)} />;
  if (!favourites.data || !menu.data) return <Loading>Loading your favourites…</Loading>;

  if (favourites.data.length === 0) {
    return (
      <div className="empty">
        <Icon name="heart" className="mx-auto mb-3 h-7 w-7" />
        <p className="text-ink">Nothing saved yet.</p>
        <p className="mt-1">Tap the heart on anything on the menu to keep it here.</p>
        <Link to="/" className="btn-primary mt-5">
          Browse the menu
        </Link>
      </div>
    );
  }

  const served = menuItems(menu.data.meals);

  return (
    <section aria-label="Your favourites">
      <p className="sr-only" aria-live="polite">
        {added ? `${added} added to your order.` : ""}
      </p>
      {cartCount > 0 && (
        <Link to="/cart" className="btn-primary mb-5 w-full justify-between">
          <span className="flex items-center gap-3">
            <Icon name="bag" />
            Review order
          </span>
          <span className="tnum">{cartCount}</span>
        </Link>
      )}

      <ul className="flex flex-col">
        {favourites.data.map((fav) => {
          // Today's version from the menu, or nothing when this item is not
          // being served right now (another meal period, or off the menu).
          const item = served.get(fav.item_id);
          const canOrder = !!item && item.is_available && orderable;
          return (
            <li key={fav.item_id} className="flex items-center gap-4 border-t border-hairline py-4">
              {item?.image_url ? (
                <MenuImage
                  src={item.image_url}
                  className="h-16 w-16 shrink-0 rounded-button bg-paper object-cover"
                />
              ) : (
                <span className="h-16 w-16 shrink-0 rounded-button bg-[#EFEEE8]" aria-hidden />
              )}
              <div className="min-w-0 flex-1">
                <p className="font-semibold [overflow-wrap:anywhere]">{item?.name ?? fav.name}</p>
                <p className="mt-0.5 text-caption text-muted">
                  {!item
                    ? "Not being served right now"
                    : !item.is_available
                      ? "Sold out"
                      : money(item.base_price_minor, item.currency)}
                </p>
                {canOrder && (
                  <button
                    type="button"
                    className="link mt-1 text-caption"
                    onClick={() => setCustomizing(item)}
                  >
                    {added === item.name ? "Added. Add another" : "Add to order"}
                  </button>
                )}
              </div>
              <FavouriteButton itemId={fav.item_id} name={item?.name ?? fav.name} saved canSave />
            </li>
          );
        })}
      </ul>

      {customizing && (
        <ModifierSheet
          item={customizing}
          currency={currency}
          onClose={() => setCustomizing(null)}
          onAdd={(selected, quantity, note) => {
            dispatch(itemAdded(customizing, selected, quantity, note));
            setAdded(customizing.name);
            clearTimeout(addedTimer.current);
            addedTimer.current = setTimeout(() => setAdded(null), 4000);
          }}
        />
      )}
    </section>
  );
}
