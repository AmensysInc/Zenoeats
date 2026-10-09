import { useEffect, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAppSelector } from "@/app/hooks";
import { Icon } from "@/components/common/icons";
import { selectCartCount } from "@/features/cart/cartSlice";
import { brandFontFamily, brandFrom, loadBrandFont, type Brand } from "@/features/storefront/brand";
import type { Portal } from "@/types";

/**
 * The restaurant's own header on every customer page (revision 06).
 *
 * The wordmark is the restaurant's mark and name, as set in Settings: its logo
 * in place of the monogram of its first letter when it has uploaded one, and
 * its own lettering in place of the typeset name, or the name in the font it
 * chose. It always leads back to the menu.
 *
 * On the menu the header also carries the page's jump links and the order
 * button; through checkout, payment and tracking it is quieter and says only
 * who the customer is ordering from, so nothing competes with the task.
 */
export function CustomerHeader({
  restaurant,
  links,
  showOrder = false,
  flush = false,
}: {
  restaurant: Pick<Portal, "name" | "delivery_offered" | "is_orderable" | "accepting_orders" | "storefront" | "brand" | "platform_url">;
  /** Jump links into the page. Hidden on a phone, where the category row
   *  below does the same job. */
  links?: ReactNode;
  showOrder?: boolean;
  /** The next thing on the page is a full-width block of its own -- the menu
   *  banner -- so the header's bottom rule is dropped. The two meet at the
   *  same pixel, and a line between them reads as a gap rather than as the
   *  edge of anything. Every other page keeps it: there the header sits
   *  above page colour, and the rule is what separates them. */
  flush?: boolean;
}) {
  return (
    <header className={`bg-cream ${flush ? "" : "border-b border-[#E6E5DB]"}`}>
      <div className="mx-auto flex min-h-[72px] max-w-[1336px] items-center gap-[15px] px-[19px] sm:min-h-[78px] sm:gap-[25px] sm:px-7 lg:min-h-[88px] lg:gap-[30px] xl:gap-[50px] xl:px-10">
        <Wordmark name={restaurant.name} brand={brandFrom(restaurant.brand)} />
        {links && (
          <nav className="hidden items-center gap-5 sm:flex lg:gap-7" aria-label="On this page">
            {links}
          </nav>
        )}
        <div className="ml-auto flex min-w-0 items-center gap-5">
          {showOrder ? (
            <>
              {/* The way back out. A storefront reached from the platform root
                  had no route back to it, so the only way to look at another
                  location was to edit the address bar. A plain anchor, not a
                  Link: the root is a different origin to this subdomain. */}
              {/* Shown at every width. It was `hidden sm:flex`, which left a
                  phone -- the device a QR code is scanned on -- with no route
                  back to the location picker at all. The label drops below
                  sm so the row still fits beside the cart button; the icon
                  and the accessible name stay. */}
              <a
                href={restaurant.platform_url}
                aria-label="All locations"
                className="flex min-h-[44px] items-center gap-[7px] text-caption text-[#637365] transition-colors duration-color hover:text-brick"
              >
                <Icon name="store" className="h-[17px] w-[17px]" />
                <span className="hidden sm:inline">All locations</span>
              </a>
              <span className="hidden items-center gap-[7px] text-caption text-[#637365] xl:flex">
                {restaurant.delivery_offered ? "Pick-up & delivery" : "Pick-up"}
              </span>
              <OrderButton orderable={restaurant.is_orderable && restaurant.accepting_orders} />
            </>
          ) : (
            <span className="hidden items-center gap-[7px] text-caption text-[#637365] sm:flex">
              <Icon name="lock" className="h-[17px] w-[17px]" />
              <span className="truncate">Order directly with {restaurant.name}</span>
            </span>
          )}
        </div>
      </div>
    </header>
  );
}

/** The mark and the name. Exported for the Settings preview, which shows
 *  the unsaved brand exactly as the header will. */
export function Wordmark({ name, brand, linked = true }: { name: string; brand: Brand; linked?: boolean }) {
  const initial = name.trim().charAt(0).toUpperCase();
  useEffect(() => {
    if (!brand.name_image_url) loadBrandFont(brand.name_font);
  }, [brand.name_font, brand.name_image_url]);
  const content = (
    <>
      {brand.logo_url ? (
        // The logo's own shape, at the monogram's height: a square logo sits
        // exactly where the initial did, a wider one takes the room it needs.
        <img
          src={brand.logo_url}
          alt=""
          aria-hidden="true"
          className="h-[35px] w-auto max-w-[96px] shrink-0 object-contain sm:h-[43px] sm:max-w-[120px]"
        />
      ) : (
        <span
          aria-hidden="true"
          className="inline-flex h-[35px] w-[33px] shrink-0 items-center justify-center rounded-[9px_9px_9px_2px] bg-brick pr-[3px] font-display text-[26px] font-bold italic leading-none text-gold sm:h-[43px] sm:w-[41px] sm:rounded-[11px_11px_11px_2px] sm:text-[31px]"
        >
          {initial}
        </span>
      )}
      {brand.name_image_url ? (
        <img
          src={brand.name_image_url}
          alt=""
          aria-hidden="true"
          className="h-[28px] w-auto min-w-0 max-w-[170px] object-contain object-left sm:h-[32px] sm:max-w-[220px] lg:h-[38px] lg:max-w-[260px]"
        />
      ) : (
        <span
          aria-hidden="true"
          style={{ fontFamily: brandFontFamily(brand.name_font) }}
          className="truncate font-display text-[23px] font-bold leading-[1.1] tracking-[-1px] sm:text-[25px] lg:text-[29px] lg:tracking-[-1.4px]"
        >
          {name}
          <span className="text-accent">.</span>
        </span>
      )}
    </>
  );
  const className = "inline-flex min-w-0 items-center gap-2 text-brick no-underline sm:gap-[11px]";
  if (!linked) {
    return (
      <span className={className} role="img" aria-label={name}>
        {content}
      </span>
    );
  }
  return (
    <Link to="/" className={className} aria-label={`${name}, back to the menu`}>
      {content}
    </Link>
  );
}

/**
 * The cart, from the header. The same cart and the same rules as the sticky
 * bar at the foot of the menu: nothing to review when it is empty, and not
 * reviewable while the restaurant is not taking orders.
 */
function OrderButton({ orderable }: { orderable: boolean }) {
  const count = useAppSelector(selectCartCount);
  const label = `Review order, ${count} ${count === 1 ? "item" : "items"}`;
  const inner = (
    <>
      <Icon name="bag" className="h-[17px] w-[17px] sm:h-5 sm:w-5" />
      <span className="hidden sm:inline">Your order</span>
      <b className="tnum inline-grid h-5 min-w-[20px] place-items-center rounded-full bg-white/15 px-1 text-[11px] sm:h-[22px] sm:min-w-[22px]">
        {count}
      </b>
    </>
  );
  const shape =
    "inline-flex min-h-[44px] shrink-0 items-center gap-[7px] rounded-full border px-2.5 text-caption font-semibold sm:gap-2.5 sm:px-[17px]";

  if (count === 0 || !orderable) {
    return (
      <span
        className={`${shape} border-[#DCE2D5] bg-[#EBEFE6] text-[#647061] [&_b]:bg-surface`}
        aria-label={count === 0 ? "Your order is empty" : `${label}. Not taking orders right now.`}
        role="img"
      >
        {inner}
      </span>
    );
  }
  return (
    <Link
      to="/cart"
      aria-label={label}
      className={`${shape} border-brick bg-brick text-white transition-colors duration-color ease-standard hover:bg-brickDark`}
    >
      {inner}
    </Link>
  );
}
