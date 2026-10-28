import { createContext, useCallback, useContext, useLayoutEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

/**
 * The column a customer page lays its content out in, so the footer can use
 * the same one.
 *
 * The footer is a sibling of the page, not a part of it: CustomerSurface
 * renders both. It had a column of its own -- the wide one, because the menu
 * is the page most people see -- which meant the allergen notice on every
 * narrower page started at the edge of the window while the order it refers
 * to sat in a centred column hundreds of pixels to the right. Reading as a
 * stray paragraph belonging to nothing is the opposite of what that notice
 * is for.
 *
 * What is passed around is the class string rather than a width, because the
 * measures are responsive -- 720 then 1040, 19px of gutter then 28 then 40 --
 * and Tailwind already expresses that. A number would have to be re-derived
 * per breakpoint in CSS, in a second place, which is how the two drift apart.
 */

/** The menu and the location picker: the full-width pages. */
export const WIDE_COLUMN = "mx-auto w-full max-w-[1336px] px-[19px] sm:px-7 xl:px-10";

/** Checkout, the order, the profile: a form or a receipt, read top to bottom. */
export const READING_COLUMN = "mx-auto w-full max-w-[720px] px-5 sm:px-6 lg:max-w-[1040px]";

type Declared = { column: string; declare: (column: string) => void };

const PageColumn = createContext<Declared>({ column: WIDE_COLUMN, declare: () => {} });

/**
 * Holds what the page on screen declared.
 *
 * Keyed by path rather than reset in an effect: a child's effects run before
 * its parent's, so a parent that cleared the column after navigating would
 * wipe whatever the new page had just declared. Deriving it during render
 * instead means a page that declares nothing gets the wide column, and the
 * previous page's measure is never inherited.
 */
export function PageColumnProvider({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const [declared, setDeclared] = useState({ pathname, column: WIDE_COLUMN });
  const declare = useCallback(
    (column: string) => setDeclared({ pathname, column }),
    [pathname],
  );
  const column = declared.pathname === pathname ? declared.column : WIDE_COLUMN;
  return <PageColumn.Provider value={{ column, declare }}>{children}</PageColumn.Provider>;
}

/**
 * Lay this page out in `column`, and tell the footer to do the same.
 *
 * A layout effect, so the correction happens before the browser paints: a
 * plain effect would show one frame of the footer in the wide column on
 * every narrow page.
 */
export function usePageColumn(column: string): string {
  const { declare } = useContext(PageColumn);
  useLayoutEffect(() => declare(column), [declare, column]);
  return column;
}

/** The column the page on screen is using. For the footer, which has no page
 *  of its own to ask. */
export function usePageColumnClass(): string {
  return useContext(PageColumn).column;
}
