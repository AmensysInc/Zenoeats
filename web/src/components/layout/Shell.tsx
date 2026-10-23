import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { Icon, type IconName } from "@/components/common/icons";

export type NavItem = { href: string; label: string; icon?: IconName };

/**
 * Operator chrome, shared by the platform and restaurant portals.
 *
 * Deliberately plainer than the customer storefront: this is a tool people use
 * all shift, not a shopfront.
 *
 * `action` is required rather than defaulted. The Next version fell back to a
 * third-party user button that rendered nothing useful for either portal --
 * both authenticate against platform-issued credentials it knew nothing about.
 * Making it explicit means a portal cannot accidentally ship without a way to
 * sign out.
 *
 * `titleHref` turns the wordmark into a link home. It is optional because a
 * portal with a single page has nowhere to go, and a wordmark that navigates
 * to the page you are already on is a dead control.
 *
 * ---
 *
 * Navigation has two shapes.
 *
 * `mobileNav="bottom"` (the restaurant portal): from 640px up the tabs sit in
 * the header; below it they move to a bar fixed along the bottom of the
 * screen, where a thumb reaches them. Up to four fit; past that the bar shows
 * the first three and a More button that opens the rest. An admin's seven and
 * a driver's one both read as intended, and every tab stays reachable without
 * the document ever scrolling sideways.
 *
 * `mobileNav="inline"` (the platform portal, one tab): the tab stays in the
 * header at every width. A bottom bar or hamburger holding a single link would
 * be a control that hides the only destination.
 *
 * In the header the tab row can still overflow on a narrow tablet, so it
 * scrolls within itself: `min-w-0` lets the flex item shrink and
 * `overflow-x-auto` makes the overflow the nav's problem instead of the page's
 * -- before this the whole document grew to fit the tabs.
 */
export function Shell({
  title,
  titleHref,
  brandMark = false,
  nav,
  identity,
  action,
  mobileNav = "bottom",
  children,
}: {
  title: string;
  titleHref?: string;
  /** The ZenoEats tile before the wordmark. The platform only: a restaurant's
   *  portal carries the restaurant's name, never a substituted logo. */
  brandMark?: boolean;
  nav: NavItem[];
  /** Who is signed in, beside the account control on wide screens. */
  identity?: ReactNode;
  action: ReactNode;
  mobileNav?: "bottom" | "inline";
  children: ReactNode;
}) {
  const { pathname } = useLocation();
  const activeTab = useRef<HTMLAnchorElement>(null);

  // Bring the current tab into view when the header row is scrolled. Keyed on
  // the tabs as well as the path: before the guard answers, the row holds only
  // the tabs every role has, and the rest appear a beat later without the path
  // changing. Watching the path alone meant the scroll ran against a row that
  // did not yet contain the active tab, and never again.
  const tabKey = nav.map((n) => n.href).join("|");
  useEffect(() => {
    activeTab.current?.scrollIntoView({ inline: "nearest", block: "nearest" });
  }, [pathname, tabKey]);

  const bottom = mobileNav === "bottom";

  const wordmark = (
    <>
      {brandMark && (
        <span
          aria-hidden="true"
          className="mr-2 inline-flex h-[26px] w-[26px] -rotate-3 items-center justify-center rounded-[7px] bg-brick font-display text-[23px] leading-none text-white"
        >
          z
        </span>
      )}
      {title}
    </>
  );
  // A fixed ceiling, whatever the name is. A restaurant called "The Spice
  // House of Whitefield" would otherwise push the nav along and move every
  // tab, so a long name is cut with the full text on hover.
  const wordmarkClass =
    "flex min-w-0 shrink-0 items-center truncate font-display text-[23px] lg:text-[22px] xl:text-2xl " +
    (bottom
      ? "max-w-[calc(100vw-100px)] sm:max-w-[155px] xl:max-w-[200px]"
      : "max-w-[60vw] sm:max-w-[200px]");

  return (
    <div className="min-h-dvh">
      <header className="border-b border-hairline bg-surface px-4 sm:px-[18px] md:px-6 xl:px-8">
        <div className="relative mx-auto flex min-h-[73px] max-w-operator items-center justify-between gap-3.5 sm:min-h-[86px] sm:justify-start lg:gap-[18px]">
          {titleHref ? (
            <Link to={titleHref} title={title} className={`${wordmarkClass} hover:opacity-80`}>
              {wordmark}
            </Link>
          ) : (
            <span title={title} className={wordmarkClass}>
              {wordmark}
            </span>
          )}

          <nav
            aria-label={bottom ? "Restaurant" : "Platform"}
            className={`no-scrollbar min-w-0 items-center gap-px overflow-x-auto lg:gap-1.5 ${
              bottom ? "hidden flex-1 justify-end sm:flex xl:justify-start" : "ml-auto flex sm:ml-0 sm:flex-1"
            }`}
          >
            {nav.map((n) => {
              const active = pathname === n.href;
              return (
                <Link
                  key={n.href}
                  ref={active ? activeTab : undefined}
                  to={n.href}
                  aria-current={active ? "page" : undefined}
                  // shrink-0: tabs scroll out of view rather than squashing
                  // into slivers no one can read or hit.
                  className={`flex min-h-[42px] shrink-0 items-center gap-2 whitespace-nowrap rounded-[9px] px-3 py-2 text-xs font-[550] transition-colors duration-tab ease-standard sm:px-[9px] sm:text-[11px] md:text-xs lg:px-[7px] xl:px-2.5 ${
                    active ? "bg-ink text-white" : "text-ink hover:bg-paper"
                  }`}
                >
                  {n.icon && (
                    <span className={bottom ? "hidden md:inline" : "hidden"}>
                      <Icon name={n.icon} />
                    </span>
                  )}
                  {n.label}
                </Link>
              );
            })}
          </nav>

          {identity && (
            <div className="hidden min-w-[135px] shrink-0 flex-col items-end gap-[3px] whitespace-nowrap text-[11px] xl:flex">
              {identity}
            </div>
          )}
          <div className="shrink-0">{action}</div>
        </div>
      </header>

      {bottom && <BottomNav nav={nav} pathname={pathname} />}

      <main
        className={`mx-auto max-w-[1344px] px-4 pt-[27px] sm:px-6 sm:pb-[65px] sm:pt-9 xl:px-8 ${
          bottom ? "pb-[110px]" : "pb-[65px]"
        }`}
      >
        {children}
      </main>
    </div>
  );
}

/**
 * The restaurant portal's phone navigation, fixed to the bottom of the screen.
 *
 * Four tabs or fewer all fit. More than four shows the first three and a More
 * button that opens the rest above the bar, so an admin's Menu, Staff, Reports
 * and Settings are one tap further away rather than a sideways swipe no one
 * knows to try.
 */
function BottomNav({ nav, pathname }: { nav: NavItem[]; pathname: string }) {
  const [moreOpen, setMoreOpen] = useState(false);
  const moreId = useId();
  const moreButton = useRef<HTMLButtonElement>(null);
  const moreMenu = useRef<HTMLElement>(null);

  const primary = nav.length > 4 ? nav.slice(0, 3) : nav;
  const more = nav.length > 4 ? nav.slice(3) : [];
  const moreActive = more.some((n) => n.href === pathname);

  // Choosing a page closes the menu; so does the role arriving and changing
  // which tabs there are.
  useEffect(() => setMoreOpen(false), [pathname, nav.length]);

  useEffect(() => {
    if (!moreOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setMoreOpen(false);
        moreButton.current?.focus();
      }
    }
    function onPointer(e: PointerEvent) {
      const t = e.target as Node;
      if (!moreMenu.current?.contains(t) && !moreButton.current?.contains(t)) setMoreOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [moreOpen]);

  const bottomLink = (n: NavItem) => {
    const active = pathname === n.href;
    return (
      <Link
        key={n.href}
        to={n.href}
        aria-current={active ? "page" : undefined}
        className={`flex min-h-[53px] min-w-[54px] flex-1 flex-col items-center justify-center gap-1 rounded-[9px] px-2 py-1.5 text-[10px] font-[550] transition-colors duration-tab ease-standard ${
          active ? "bg-brickSoft text-brick" : "text-muted"
        }`}
      >
        {n.icon && <Icon name={n.icon} />}
        {n.label}
      </Link>
    );
  };

  return (
    <>
      <nav
        aria-label="Restaurant mobile navigation"
        className="fixed inset-x-0 bottom-0 z-nav flex justify-evenly gap-1 border-t border-hairline bg-surface/[.98] px-2.5 pb-[max(9px,env(safe-area-inset-bottom))] pt-2 sm:hidden"
      >
        {primary.map(bottomLink)}
        {more.length > 0 && (
          <button
            ref={moreButton}
            type="button"
            aria-expanded={moreOpen}
            aria-controls={moreId}
            onClick={() => setMoreOpen((o) => !o)}
            className={`flex min-h-[53px] flex-1 flex-col items-center justify-center gap-1 rounded-[9px] px-2 py-1.5 text-[10px] font-[550] transition-colors duration-tab ease-standard ${
              moreOpen || moreActive ? "bg-brickSoft text-brick" : "text-muted"
            }`}
          >
            <Icon name="menu" />
            More
          </button>
        )}
      </nav>
      {more.length > 0 && moreOpen && (
        <nav
          ref={moreMenu}
          id={moreId}
          aria-label="More restaurant pages"
          className="fixed inset-x-4 bottom-[82px] z-[36] grid animate-disclose grid-cols-2 gap-1.5 rounded-ticket border border-hairline bg-surface p-2.5 shadow-raised sm:hidden"
        >
          {more.map((n) => {
            const active = pathname === n.href;
            return (
              <Link
                key={n.href}
                to={n.href}
                aria-current={active ? "page" : undefined}
                className={`flex items-center gap-2 rounded-[9px] p-3.5 text-sm font-[550] ${
                  active ? "bg-ink text-white" : "text-ink hover:bg-paper"
                }`}
              >
                {n.icon && <Icon name={n.icon} />}
                {n.label}
              </Link>
            );
          })}
        </nav>
      )}
    </>
  );
}

/** A portal page's heading, with an optional line under it and an optional
 *  control or label aligned opposite. */
export function PageTitle({
  title,
  subtitle,
  right,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="mb-[25px] flex flex-col items-start justify-between gap-3 sm:mb-[30px] sm:flex-row sm:items-end sm:gap-5">
      <div className="min-w-0">
        <h1 className="font-display text-[34px] leading-[1.12] tracking-[-1px] sm:text-[32px] lg:text-4xl">
          {title}
        </h1>
        {subtitle && <p className="mt-[9px] text-[13px] text-muted">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}
