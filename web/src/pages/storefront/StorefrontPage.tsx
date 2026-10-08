import { HomeBanner, CategoryShortcuts, heroPhoto, categoriesOf, comboPhoto, jumpTo, shortcutTargets } from "@/features/storefront/components/HomePresentation";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "@/app/hooks";
import { Loading, StatePage } from "@/components/common/Feedback";
import { Cloche, Icon } from "@/components/common/icons";
import { MenuImage } from "@/components/common/MenuImage";
import { ModifierSheet } from "@/features/cart/components/ModifierSheet";
import { ComboSheet, savingLabel } from "@/features/cart/components/ComboSheet";
import { CustomerHeader } from "@/features/storefront/components/CustomerHeader";
import { AccountPanel } from "@/features/storefront/components/AccountPanel";
import { FavouriteButton, useFavourites } from "@/features/storefront/components/FavouriteButton";
import {
  comboAdded,
  itemAdded,
  selectCartCount,
  selectCartPreviewSubtotal,
} from "@/features/cart/cartSlice";
import { useOpenCart } from "@/features/cart/useOpenCart";
import { usePortalQuery, usePublicMenuQuery } from "@/features/storefront/storefrontApi";
import { errorMessage } from "@/services/apiClient";
import { mealHours, money } from "@/utils/format";
import { canAddCalories, kcal } from "@/features/cart/calories";
import type { Combo, Item, Meal } from "@/types";

/** Every meal period, rather than one of them. */
const FULL_MENU = "all";

/**
 * The storefront. Public: no session is required to read a menu, and the
 * tenant comes from the Host header, so this page never names a restaurant
 * itself. Signing in happens at checkout.
 */
export function StorefrontPage() {
  const dispatch = useAppDispatch();
  const portal = usePortalQuery();
  const menu = usePublicMenuQuery();
  const [customizing, setCustomizing] = useState<Item | null>(null);
  const [building, setBuilding] = useState<Combo | null>(null);
  // Which meal period is on screen. A way to read the menu, not a rule about
  // it: nothing becomes orderable or unorderable by choosing one, and the
  // cart is untouched.
  const [period, setPeriod] = useState<string>(FULL_MENU);
  const favourites = useFavourites();

  useOpenCart();

  const meals = useMemo(() => menu.data?.meals ?? [], [menu.data]);
  // A period that has gone from the menu since it was chosen falls back to
  // the whole menu rather than to an empty page.
  const chosen = period !== FULL_MENU && meals.some((m) => m.id === period) ? period : FULL_MENU;
  const shown = chosen === FULL_MENU ? meals : meals.filter((m) => m.id === chosen);
  // Every orderable item by id, for the collections and shortcut sections,
  // which hold pointers into the menu rather than copies of it.
  const lookup = useMemo(
    () => new Map(meals.flatMap(categoriesOf).flatMap((c) => c.items.map((i) => [i.id, { item: i, path: c.path }] as const))),
    [meals],
  );

  const loadError =
    (portal.error ? errorMessage(portal.error) : null) ??
    (menu.error ? errorMessage(menu.error) : null);

  if (loadError) {
    const retrying = portal.isFetching || menu.isFetching;
    return (
      <StatePage
        title="This menu isn't available"
        action={
          <button
            type="button"
            className="btn-primary"
            disabled={retrying}
            onClick={() => { void portal.refetch(); void menu.refetch(); }}
          >
            {retrying ? "Trying again…" : "Try again"}
          </button>
        }
      >{loadError}</StatePage>
    );
  }

  if (!portal.data || !menu.data) {
    return (
      <main className="mx-auto flex min-h-[75vh] max-w-[570px] items-center px-6">
        <div className="w-full">
          <Loading />
        </div>
      </main>
    );
  }

  const restaurant = portal.data;
  const open = restaurant.is_orderable && restaurant.accepting_orders;
  // Bound to a const, as checkout does: narrowing on portal.data does not
  // survive into the callbacks below, because it is a property of a mutable
  // object rather than a local.
  const currency = restaurant.currency;
  const severalPeriods = meals.length > 1;

  return (
    <div className="flex min-h-dvh flex-col">
      <CustomerHeader
        restaurant={restaurant}
        showOrder
        links={
          <>
            <HeaderLink onClick={() => jumpTo("menu-heading")}>Our menu</HeaderLink>
            {severalPeriods && (
              <HeaderLink onClick={() => jumpTo("meal-times")}>Meal times</HeaderLink>
            )}
          </>
        }
      />

      {/* No bottom padding: the cart bar is cleared by CustomerFooter,
          which follows this and reserves the room only when there is a
          cart to review. Keeping it here as well left 132px of nothing
          between the restaurant footer and the one below it. */}
      {/* pt: the banner is a card, so it sits below the header rather
          than welded to it. */}
      <div className="mx-auto w-full max-w-[1336px] px-[19px] pt-5 sm:px-7 sm:pt-6 xl:px-10">
        <HomeBanner restaurant={restaurant} photo={heroPhoto(meals)} storefront={restaurant.storefront} onTarget={(slide) => {
          if (slide.cta_target_kind === "item") {
            const item = meals.flatMap(categoriesOf).flatMap((c) => c.items).find((i) => i.id === slide.cta_target_id);
            if (item && item.is_available && open) setCustomizing(item);
          } else {
            setPeriod(FULL_MENU);
            requestAnimationFrame(() => {
              const category = meals.flatMap(categoriesOf).find((c) => c.itemTypeId === slide.cta_target_id);
              jumpTo(slide.cta_target_kind === "collection" ? `collection-${slide.cta_target_id}` : slide.cta_target_kind === "item_type" && category ? `heading-${category.key}` : "menu-heading");
            });
          }
        }} />

        {/* Signing in is offered here and required nowhere on this page: the
            menu is public, and the account only matters once there is an
            order to attach to someone. */}
        <AccountPanel
          leading={
            <span className="flex items-center gap-2 text-caption text-muted">
              <span
                aria-hidden="true"
                className={`h-[7px] w-[7px] rounded-full ${open ? "bg-brick" : "bg-warning"}`}
              />
              {open ? "Taking orders now" : "Not taking orders"}
            </span>
          }
        />

        {!open && (
          <p className="note-warning mt-5" role="status">
            Not taking orders right now. You can still look at the menu.
          </p>
        )}

        {meals.length === 0 ? (
          <div className="empty mt-10">
            <Cloche className="mx-auto mb-4" />
            <h2 className="font-display text-2xl text-ink">Nothing on the menu yet.</h2>
          </div>
        ) : (
          <>
            <CategoryShortcuts meals={shown} severalPeriods={severalPeriods} categories={restaurant.storefront?.categories} shortcuts={restaurant.storefront?.shortcuts} />
            {restaurant.storefront?.collections.map((collection) => {
              const items = collection.item_ids.flatMap((id) => lookup.has(id) ? [lookup.get(id)!] : []);
              if (!items.length) return null;
              return <section key={collection.id} className="mt-8" aria-labelledby={`collection-${collection.id}`}>
                <h2 id={`collection-${collection.id}`} tabIndex={-1} className="mb-4 scroll-mt-6 font-display text-2xl font-bold text-brick">{collection.title}</h2>
                <ul className="no-scrollbar flex gap-4 overflow-x-auto pb-3">{items.map(({item, path}) => <li key={item.id} className="w-[min(85vw,440px)] shrink-0"><MenuCard item={item} path={path} open={open} onPick={() => setCustomizing(item)} /></li>)}</ul>
              </section>;
            })}
            {/* A hand-picked shortcut's own section: the items the restaurant
                chose from that category, under the label it gave them. One
                holding its whole category scrolls to it on the menu instead. */}
            {restaurant.storefront?.shortcuts?.map((shortcut) => {
              if (!shortcutTargets(shown, [shortcut]).get(shortcut.id)!.own) return null;
              const items = shortcut.item_ids.flatMap((id) => lookup.has(id) ? [lookup.get(id)!] : []);
              if (!items.length) return null;
              return <section key={shortcut.id} className="mt-8" aria-labelledby={`heading-shortcut-${shortcut.id}`}>
                <h2 id={`heading-shortcut-${shortcut.id}`} tabIndex={-1} className="mb-4 scroll-mt-6 font-display text-2xl font-bold text-brick">{shortcut.label}</h2>
                <ul className="no-scrollbar flex gap-4 overflow-x-auto pb-3">{items.map(({item, path}) => <li key={item.id} className="w-[min(85vw,440px)] shrink-0"><MenuCard item={item} path={path} open={open} onPick={() => setCustomizing(item)} /></li>)}</ul>
              </section>;
            })}

            <div className="mt-8 flex flex-wrap items-end justify-between gap-x-6 gap-y-2 sm:mt-10">
              <div>
                <p className="eyebrow text-[rgb(var(--ze-menu-muted))]">Our menu</p>
                <h2
                  id="menu-heading"
                  tabIndex={-1}
                  className="mt-2 scroll-mt-6 font-display text-[34px] font-bold leading-[1.1] tracking-[-1px] text-brick sm:text-[40px]"
                >
                  What sounds good?
                </h2>
              </div>
              <p className="text-caption text-muted sm:text-right">
                Choose anything to make it your own.
              </p>
            </div>

            {/* The period selector only when there is more than one period
                to choose between. Hours stay beside each period either way. */}
            {severalPeriods && (
              <div
                id="meal-times"
                tabIndex={-1}
                className="mt-6 flex scroll-mt-6 flex-wrap items-center justify-start gap-2.5 rounded-[12px] bg-sage p-[11px] sm:justify-between sm:gap-4 sm:px-4 sm:py-[13px]"
              >
                <div
                  className="no-scrollbar flex w-full items-center gap-1 overflow-x-auto sm:w-auto"
                  role="group"
                  aria-label="Meal times"
                >
                  {[{ id: FULL_MENU, name: "Full menu" }, ...meals].map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      aria-pressed={chosen === m.id}
                      onClick={() => setPeriod(m.id)}
                      className="min-h-[44px] flex-1 whitespace-nowrap rounded-chip px-[17px] text-caption font-[650] text-[rgb(var(--ze-period-text))] transition-colors duration-color ease-standard hover:text-brick aria-pressed:bg-cream aria-pressed:text-brick aria-pressed:shadow-[0_2px_7px_rgb(var(--ze-period-shadow)_/_0.07058823529411765)] sm:flex-none"
                    >
                      {m.name}
                    </button>
                  ))}
                </div>
                <p className="flex items-start gap-[7px] px-1 text-caption text-[rgb(var(--ze-hours-text))] sm:items-center">
                  <Icon name="clock" className="mt-0.5 h-3.5 w-3.5 shrink-0 sm:mt-0" />
                  Meal times are a guide. Order while the restaurant is taking orders.
                </p>
              </div>
            )}

            {/* Keyed by the choice, so swapping periods fades the new content
                in once rather than animating what stayed. */}
            <div key={chosen} className="animate-fade">
              {shown.map((meal, index) => (
                <MealBlock
                  key={meal.id}
                  meal={meal}
                  first={index === 0}
                  showName={severalPeriods}
                  open={open}
                  currency={currency}
                  onPickItem={setCustomizing}
                  onPickCombo={setBuilding}
                  favourites={favourites}
                />
              ))}
            </div>
          </>
        )}

        {/* The restaurant's own footer.
            It was three lines spread across the full width by
            justify-between, which on a desktop left a band of empty space
            between them and read as a layout accident. It also said nothing
            useful: the address and phone number were in the portal payload
            and shown nowhere on this page, so a customer wanting to call or
            walk in had to find them elsewhere.

            Columns now, sized to their content, with the contact details
            actually in them. The platform's own footer -- allergens and
            policies -- follows this one from CustomerSurface. */}
        <footer className="mt-[39px] border-t border-[rgb(var(--ze-footer-line))] pt-[30px] text-caption text-[rgb(var(--ze-footer-text))] sm:mt-[62px] sm:pt-[38px]">
          {/* Columns sit together and stop. ml-auto on the last one threw
              it at the far edge, so on a wide window the footer read as
              three items stranded across a band of empty space -- the
              wider the window, the worse it looked. */}
          <div className="flex max-w-[980px] flex-col gap-8 sm:flex-row sm:flex-wrap sm:gap-x-[56px] sm:gap-y-9">
            <div className="min-w-0 max-w-[34ch]">
              <p className="font-display text-2xl font-bold tracking-[-.8px] text-brick">
                {restaurant.name}
                <span className="text-accent">.</span>
              </p>
              {restaurant.tagline && <p className="mt-2">{restaurant.tagline}</p>}
              <p className="mt-2">
                {restaurant.delivery_offered
                  ? "Order online for pick-up or delivery."
                  : "Order online for pick-up."}
              </p>
            </div>

            {(restaurant.pickup_address || restaurant.phone) && (
              <div className="min-w-0">
                <p className="mb-2 text-[10px] uppercase tracking-[1.2px] text-[rgb(var(--ze-menu-muted))]">
                  Find us
                </p>
                {restaurant.pickup_address && (
                  <p className="flex items-start gap-2 [overflow-wrap:anywhere]">
                    <Icon name="store" className="mt-[3px] h-3.5 w-3.5 shrink-0" />
                    {restaurant.pickup_address}
                  </p>
                )}
                {restaurant.phone && (
                  <p className="mt-1.5 flex items-center gap-2">
                    <Icon name="info" className="h-3.5 w-3.5 shrink-0" />
                    {/* A tel: link, because most of the people reading this
                        are holding the thing that can dial it. */}
                    <a className="link" href={`tel:${restaurant.phone.replace(/[^+\d]/g, "")}`}>
                      {restaurant.phone}
                    </a>
                  </p>
                )}
              </div>
            )}

            <div className="min-w-0">
              <p className="mb-2 text-[10px] uppercase tracking-[1.2px] text-[rgb(var(--ze-menu-muted))]">
                Other locations
              </p>
              <a className="link" href={restaurant.platform_url}>
                See every Zenoeats location
              </a>
              <p className="mt-2 text-[11px]">Ordering powered by Zenoeats</p>
            </div>
          </div>
        </footer>
      </div>

      {/* Last in the tab order: the menu is read before the order is reviewed. */}
      <CartBar currency={currency} orderable={open} />

      {customizing && (
        <ModifierSheet
          item={customizing}
          currency={currency}
          onClose={() => setCustomizing(null)}
          onAdd={(selected, quantity, note) =>
            dispatch(itemAdded(customizing, selected, quantity, note))
          }
        />
      )}

      {building && (
        <ComboSheet
          combo={building}
          currency={currency}
          onClose={() => setBuilding(null)}
          onAdd={(chosenItems, quantity, note) =>
            dispatch(comboAdded(building, chosenItems, quantity, note))
          }
        />
      )}
    </div>
  );
}

function HeaderLink({ children, onClick }: { children: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="min-h-[44px] whitespace-nowrap text-[13px] font-semibold text-[rgb(var(--ze-header-link))] transition-colors duration-color hover:text-accent"
    >
      {children}
    </button>
  );
}

// ------------------------------------------------------------------- menu

function MealBlock({
  meal,
  first,
  showName,
  open,
  currency,
  onPickItem,
  onPickCombo,
  favourites,
}: {
  meal: Meal;
  first: boolean;
  /** Only when there is more than one period to tell apart. */
  showName: boolean;
  open: boolean;
  currency: string;
  onPickItem: (item: Item) => void;
  onPickCombo: (combo: Combo) => void;
  favourites: ReturnType<typeof useFavourites>;
}) {
  const hours = mealHours(meal.starts_at, meal.ends_at);
  const categories = categoriesOf(meal);

  return (
    <section
      aria-label={showName ? meal.name : undefined}
      className={first ? "mt-8 sm:mt-10" : "mt-12 border-t border-[rgb(var(--ze-footer-line))] pt-8 sm:mt-14 sm:pt-10"}
    >
      {/* The hours stay even when the name is hidden, because they would
          otherwise disappear with it. */}
      {(showName || hours) && (
        <div className="mb-[17px] flex flex-wrap items-center justify-between gap-x-5 gap-y-2 sm:mb-[21px]">
          {showName ? (
            <h2 className="font-display text-[27px] font-bold leading-[1.2] tracking-[-.8px] text-[rgb(var(--ze-meal-title))] sm:text-[30px]">
              {meal.name}
              <span className="text-accent">.</span>
            </h2>
          ) : (
            <span />
          )}
          {hours && (
            <p className="flex items-center gap-2 text-caption text-muted">
              <Icon name="clock" className="h-4 w-4" />
              {hours}
            </p>
          )}
        </div>
      )}

      {/* Combos lead the period. A meal deal is the thing a menu wants read
          first, and it is assembled from the lists below it. */}
      {meal.combos.length > 0 && (
        <section className="mb-8">
          <CategoryHeading id={`heading-combos-${meal.id}`} label="Combos" />
          <ul className="grid grid-cols-1 gap-[13px] sm:gap-[19px] lg:grid-cols-2">
            {meal.combos.map((combo) => (
              <li key={combo.id} className="min-w-0">
                <ComboCard combo={combo} currency={currency} open={open} onPick={() => onPickCombo(combo)} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Headings come from the server, derived from the kinds of the items
          served in this period. There is no empty one to guard against and
          no name to fall back on. A kind with one dish shares a row with its
          neighbour on a wide screen; a kind with several takes the row. */}
      <div className="grid grid-cols-1 gap-x-[19px] gap-y-8 lg:grid-cols-2">
        {categories.map((category) => {
          const wide = category.items.length > 1;
          return (
            <section key={category.key} className={wide ? "lg:col-span-2" : ""}>
              <CategoryHeading
                id={`heading-${category.key}`}
                label={category.label}
                count={category.items.length}
              />
              <ul className={`grid grid-cols-1 gap-[13px] sm:gap-[19px] ${wide ? "lg:grid-cols-2" : ""}`}>
                {category.items.map((item) => (
                  <li key={item.id} className="relative min-w-0">
                    <MenuCard
                      item={item}
                      path={category.path}
                      open={open}
                      onPick={() => onPickItem(item)}
                    />
                    {/* Beside the card rather than inside it: a button cannot
                        hold another button, and saving an item must not open
                        its sheet. Still works on a sold-out item -- that is
                        exactly one worth remembering for next time. */}
                    <FavouriteButton
                      itemId={item.id}
                      name={item.name}
                      saved={favourites.savedIds.has(item.id)}
                      canSave={favourites.canSave}
                      className="absolute right-1 top-1 sm:right-2 sm:top-2"
                    />
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>
    </section>
  );
}

function CategoryHeading({ id, label, count }: { id: string; label: string; count?: number }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-[15px]">
      <h3
        id={id}
        tabIndex={-1}
        className="scroll-mt-6 text-lg font-bold leading-[1.3] tracking-[-.5px] text-[rgb(var(--ze-category-title))] sm:text-[19px]"
      >
        {label}
      </h3>
      {count !== undefined && (
        <span className="text-caption text-[rgb(var(--ze-menu-muted))]">
          {count} {count === 1 ? "item" : "items"}
        </span>
      )}
    </div>
  );
}

/**
 * One dish. The whole card opens its sheet; a sold-out dish, or any dish while
 * the restaurant is closed, still reads but cannot be chosen. The photo is
 * optional, and one that fails to load gives its column back to the text.
 */
export function MenuCard({
  item,
  path,
  open,
  onPick,
}: {
  item: Item;
  path: string;
  open: boolean;
  onPick: () => void;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  const photo = item.image_url && failed !== item.image_url ? item.image_url : null;
  const orderable = item.is_available && open;

  return (
    <button
      type="button"
      disabled={!orderable}
      onClick={onPick}
      className={`group flex w-full overflow-hidden rounded-[12px] border border-[rgb(var(--ze-card-border))] text-left text-[rgb(var(--ze-card-text))] transition-[box-shadow,border-color] duration-[180ms] ease-standard enabled:hover:border-[rgb(var(--ze-card-hover))] enabled:hover:shadow-[0_8px_25px_rgb(var(--ze-card-shadow)_/_0.050980392156862744)] disabled:cursor-not-allowed sm:rounded-product min-h-[177px] lg:min-h-[224px] ${
        // One height for every card on the menu, so a row of cards with
        // photographs and a row without line up. Only the background differs:
        // a card with no photo fills the space a photo would have taken.
        photo
          ? "bg-cream"
          : "bg-[linear-gradient(115deg,rgb(var(--ze-card-gradient)),rgb(var(--ze-card-paper)))]"
      } ${orderable ? "" : "[&>*]:opacity-[.72]"}`}
    >
      {photo && (
        // Absolutely placed, so a tall photograph fills this column rather
        // than stretching the card: a portrait picture used to make its card
        // three times the height of the one beside it, and a grid row is as
        // tall as its tallest card. The card's own min-height is the size.
        <span className="relative w-[36%] shrink-0 overflow-hidden bg-[rgb(var(--ze-photo-ground))] lg:w-[41%]">
          <MenuImage
            src={photo}
            onFail={setFailed}
            className="photo-zoom absolute inset-0 h-full w-full object-cover"
          />
        </span>
      )}
      <span
        className={`flex min-w-0 flex-1 flex-col px-[15px] py-[17px] sm:px-5 lg:px-[21px] lg:py-[22px] ${
          photo ? "" : "sm:px-6 lg:px-[31px] lg:py-[27px]"
        }`}
      >
        <span className="mb-2 block pr-10 text-[9px] uppercase tracking-[1.2px] text-[rgb(var(--ze-menu-muted))] sm:text-[10px] lg:mb-2.5">
          {path}
        </span>
        <span className="mb-[7px] block pr-8 text-base font-bold leading-[1.25] tracking-[-.3px] text-[rgb(var(--ze-item-title))] [overflow-wrap:anywhere] sm:text-[17px] lg:mb-2.5 lg:text-lg">
          {item.name}
        </span>
        {item.description && (
          <span className="block max-w-[45ch] text-caption text-[rgb(var(--ze-item-description))]">{item.description}</span>
        )}
        <span className="mt-auto flex items-center justify-between gap-2 pt-[13px] lg:pt-5">
          <span className="min-w-0 truncate text-sm text-[rgb(var(--ze-item-title))] sm:text-[15px]">
            <span className="tnum whitespace-nowrap">{money(item.base_price_minor, item.currency)}</span>
            {/* Only where the restaurant has stated one. No figure is not a
                figure of zero, and a menu must not read as if it were. */}
            {kcal(item.calories) && (
              <span className="whitespace-nowrap text-caption text-[rgb(var(--ze-menu-muted))]">
                {" · "}
                {/* "from" once a size or an extra can raise it: stating 310
                    flat would be wrong for everyone who picks the large. */}
                {canAddCalories(item) ? "from " : ""}
                <span className="tnum">{kcal(item.calories)}</span>
              </span>
            )}
          </span>
          {!item.is_available ? (
            <span className="rounded-[5px] border border-[rgb(var(--ze-soldout-border))] bg-[rgb(var(--ze-soldout-ground))] px-[7px] py-1 text-[11px] font-[650] text-danger">
              Sold out
            </span>
          ) : (
            <span
              aria-hidden="true"
              className="inline-flex min-h-[32px] items-center gap-1.5 rounded-full px-1 text-[11px] font-[650] text-brick"
            >
              <span className="grid h-7 w-7 place-items-center rounded-full bg-brickSoft transition-colors duration-color group-enabled:group-hover:bg-brick group-enabled:group-hover:text-white">
                <Icon name="plus" className="h-4 w-4" />
              </span>
              <span className="hidden sm:inline">Customize</span>
            </span>
          )}
        </span>
      </span>
    </button>
  );
}

/**
 * A meal deal, as a feature: what it is, what it is made of, what it saves.
 * The picture is one of its own items' photos when any has one.
 */
function ComboCard({
  combo,
  currency,
  open,
  onPick,
}: {
  combo: Combo;
  currency: string;
  open: boolean;
  onPick: () => void;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  const found = comboPhoto([combo]);
  const photo = found && failed !== found ? found : null;

  return (
    <button
      type="button"
      disabled={!open}
      onClick={onPick}
      className={`group flex w-full overflow-hidden rounded-[12px] border border-[rgb(var(--ze-combo-border))] text-left text-[rgb(var(--ze-combo-text))] transition-[box-shadow,border-color] duration-[180ms] ease-standard enabled:hover:border-[rgb(var(--ze-combo-hover-border))] enabled:hover:shadow-[0_8px_25px_rgb(var(--ze-card-shadow)_/_0.050980392156862744)] disabled:cursor-not-allowed disabled:opacity-[.68] sm:rounded-product min-h-[177px] bg-[rgb(var(--ze-combo-ground))] lg:min-h-[224px]`}
    >
      {photo && (
        <span className="relative w-[36%] shrink-0 overflow-hidden bg-[rgb(var(--ze-photo-ground))] lg:w-[41%]">
          <MenuImage src={photo} onFail={setFailed} className="photo-zoom absolute inset-0 h-full w-full object-cover" />
        </span>
      )}
      <span
        className={`flex min-w-0 flex-1 flex-col px-[15px] py-[17px] sm:px-5 lg:px-[21px] lg:py-[22px] ${
          photo ? "" : "sm:px-6 lg:px-[31px] lg:py-[27px]"
        }`}
      >
        <span className="mb-2 block text-[9px] uppercase tracking-[1.2px] text-[rgb(var(--ze-menu-muted))] sm:text-[10px] lg:mb-2.5">
          Meal deal
        </span>
        <span className="mb-[7px] block text-base font-bold leading-[1.25] tracking-[-.3px] [overflow-wrap:anywhere] sm:text-[17px] lg:mb-2.5 lg:text-lg">
          {combo.name}
        </span>
        {combo.description && (
          <span className="block max-w-[45ch] text-caption text-[rgb(var(--ze-combo-muted))]">{combo.description}</span>
        )}
        {/* What has to be chosen, in the restaurant's own words for each
            slot: the one thing a meal deal card has to say that an item card
            does not. */}
        <span className="mt-1.5 block truncate text-caption text-[rgb(var(--ze-combo-muted))]">
          {combo.slots.map((slot) => slot.label).join(" · ")}
        </span>
        <span className="mt-auto flex items-center justify-between gap-2 pt-[13px] lg:pt-5">
          <span className="rounded-[5px] border border-[rgb(var(--ze-saving-border))] bg-[rgb(var(--ze-saving-ground))] px-[7px] py-1 text-[11px] font-bold text-[rgb(var(--ze-saving-text))]">
            {savingLabel(combo, currency)}
          </span>
          <span
            aria-hidden="true"
            className="inline-flex min-h-[32px] shrink-0 items-center gap-1.5 rounded-full px-1 text-[11px] font-[650] text-brick"
          >
            <span className="grid h-7 w-7 place-items-center rounded-full bg-brickSoft transition-colors duration-color group-enabled:group-hover:bg-brick group-enabled:group-hover:text-white">
              <Icon name="arrow" className="h-4 w-4" />
            </span>
            <span className="hidden sm:inline">Build</span>
          </span>
        </span>
      </span>
    </button>
  );
}

// ---------------------------------------------------------------- cart bar

function CartBar({ currency, orderable }: { currency: string; orderable: boolean }) {
  const count = useAppSelector(selectCartCount);
  const subtotal = useAppSelector(selectCartPreviewSubtotal);
  // Only the bar's first appearance moves: later adds change the count in
  // place, and money never animates.
  const [animate] = useState(() => count === 0);

  const items = `${count} ${count === 1 ? "item" : "items"}`;

  return (
    <>
      {/* Announced politely, in words, whenever the count changes. */}
      <p className="sr-only" aria-live="polite">
        {count > 0 ? `${items} in your order` : ""}
      </p>
      {count > 0 && (
        <div
          className={`fixed inset-x-0 bottom-0 z-cart border-t border-hairline bg-paper/[.96] px-5 pb-3.5 pt-[15px] backdrop-blur-md ${
            animate ? "animate-cart" : ""
          }`}
        >
          <div className="mx-auto max-w-[720px]">
            <Link
              to="/cart"
              aria-disabled={!orderable}
              tabIndex={orderable ? undefined : -1}
              className={`btn-primary min-h-[56px] w-full justify-between rounded-full px-6 ${
                orderable ? "" : "pointer-events-none opacity-[.44]"
              }`}
            >
              <span className="flex items-center gap-3">
                <Icon name="bag" />
                Review order
                <span className="tnum min-w-[25px] rounded-full bg-white/15 px-[7px] py-0.5 text-center">
                  {count}
                  <span className="sr-only"> {count === 1 ? "item" : "items"}</span>
                </span>
              </span>
              <span className="tnum">{money(subtotal, currency)}</span>
            </Link>
            <p className="mt-[7px] text-center text-[11px] text-muted">Tax is calculated at checkout.</p>
          </div>
        </div>
      )}
    </>
  );
}
