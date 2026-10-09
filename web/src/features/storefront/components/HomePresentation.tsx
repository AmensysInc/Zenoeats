import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Icon } from "@/components/common/icons";
import { MenuImage } from "@/components/common/MenuImage";
import type { Combo, Meal, Item, Portal } from "@/types";
import type { Banner, CategoryStyle, Shortcut, Storefront } from "../theme";

/**
 * One labelled group of cards: the items filed on a top-level type, or one of
 * its subcategories. `path` keeps the parent in view -- "Food / Burgers" --
 * so flattening the two levels into cards loses nothing the menu said.
 */
type Category = {
  key: string;
  itemTypeId: string;
  label: string;
  path: string;
  items: Item[];
};

export function categoriesOf(meal: Meal): Category[] {
  return meal.sections.flatMap((section) => [
    ...(section.items.length > 0
      ? [
          {
            key: `${meal.id}-${section.item_type_id}`,
            itemTypeId: section.item_type_id,
            label: section.label,
            path: section.label,
            items: section.items,
          },
        ]
      : []),
    ...section.groups.map((group) => ({
      key: `${meal.id}-${group.item_type_id}`,
      itemTypeId: group.item_type_id,
      label: group.label,
      path: `${section.label} / ${group.label}`,
      items: group.items,
    })),
  ]);
}

/** Scroll a section into view and put focus on its heading, so a keyboard or
 *  screen-reader user lands where the jump went. Instant under reduced
 *  motion. */
export function jumpTo(id: string) {
  const target = document.getElementById(id);
  if (!target) return;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
  target.focus({ preventScroll: true });
}

/** The first photo on the menu, preferring one of something on sale. The
 *  banner uses the restaurant's own photography or none at all. */
export function heroPhoto(meals: Meal[]): string | null {
  const items = meals.flatMap((m) =>
    m.sections.flatMap((s) => [...s.items, ...s.groups.flatMap((g) => g.items)]),
  );
  const pick = items.find((i) => i.image_url && i.is_available) ?? items.find((i) => i.image_url);
  return pick?.image_url ?? null;
}

// ------------------------------------------------------------------ banner

/**
 * The food banner: the restaurant's name and a way into the menu, over one of
 * its own photos. Without a photo, or when it fails to load, it is the plain
 * green version -- never an empty frame, and never a stock image standing in
 * for food this restaurant does not serve.
 *
 * Depth follows a fine pointer only, capped at ±2.5°, one animation frame at a
 * time, and settles back when the pointer leaves. On touch, and under reduced
 * motion, it never moves.
 */
/** The banner is a fixed shape; a photograph is whatever shape it was taken
 * in. These are the restaurant's own answer to what survives the crop, and
 * the editor previews a photo through this very function. */
export function framing(slide?: Pick<Banner, "focal_x" | "focal_y" | "zoom"> | null): CSSProperties | undefined {
  if (!slide) return undefined;
  const point = `${slide.focal_x}% ${slide.focal_y}%`;
  return {
    objectPosition: point,
    // Zooming about the same point keeps what they chose in the middle of
    // the frame instead of drifting it towards the centre of the photo.
    transform: slide.zoom === 100 ? undefined : `scale(${slide.zoom / 100})`,
    transformOrigin: point,
  };
}

function BannerFrame({
  restaurant,
  photo,
  slide,
  previous,
  controls,
  onAction,
}: {
  restaurant: Pick<Portal, "name" | "tagline" | "delivery_offered">;
  photo: string | null;
  slide?: Banner;
  previous?: Banner | null;
  controls?: ReactNode;
  onAction?: () => void;
}) {
  const banner = useRef<HTMLElement>(null);
  const media = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const hasPhoto = !!photo && failed !== photo;

  useEffect(() => {
    const surface = banner.current;
    const node = media.current;
    if (!surface || !node) return;
    const fine = window.matchMedia("(hover: hover) and (pointer: fine)");
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;

    function onMove(e: PointerEvent) {
      if (!fine.matches || reduced.matches || e.pointerType !== "mouse" || frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const box = surface!.getBoundingClientRect();
        const x = (e.clientX - box.left) / box.width - 0.5;
        const y = (e.clientY - box.top) / box.height - 0.5;
        node!.style.setProperty("--tilt-y", `${(x * 5).toFixed(2)}deg`);
        node!.style.setProperty("--tilt-x", `${(-y * 5).toFixed(2)}deg`);
      });
    }
    function onLeave() {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      node!.style.removeProperty("--tilt-x");
      node!.style.removeProperty("--tilt-y");
    }

    surface.addEventListener("pointermove", onMove);
    surface.addEventListener("pointerleave", onLeave);
    return () => {
      surface.removeEventListener("pointermove", onMove);
      surface.removeEventListener("pointerleave", onLeave);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [hasPhoto, photo]);

  return (
    <section
      ref={banner}
      aria-labelledby="restaurant-name"
      className={`home-banner ${hasPhoto ? "" : "home-banner-plain"} ${slide ? "home-banner-slide" : ""}`}
    >
      {previous && previous.image_url !== photo && <div className="home-banner-media home-banner-outgoing" aria-hidden="true" key={`previous-${photo}`}><img src={previous.image_url} alt="" style={framing(previous)} /></div>}
      {hasPhoto && (
        <div ref={media} className={`home-banner-media ${slide ? "home-banner-current" : ""}`} key={photo} aria-hidden="true">
          <img src={photo!} alt="" decoding="async" draggable={false} style={framing(slide)} onError={() => setFailed(photo)} />
        </div>
      )}
      <div
        className={`home-banner-content relative z-section w-full px-[26px] py-7 sm:px-9 sm:py-[38px] lg:px-[52px] lg:py-[50px] ${
          hasPhoto ? "sm:w-[62%] lg:w-[55%] xl:w-[52%] xl:max-w-[600px]" : "sm:w-[70%] sm:max-w-[700px]"
        }`}
      >
        <p className="eyebrow mb-[13px] text-[rgb(var(--ze-hero-eyebrow))] sm:mb-[19px]">Order from {restaurant.name}</p>
        <h1
          id="restaurant-name"
          className="mb-2.5 font-display text-[45px] font-bold leading-[1.1] tracking-[-2px] text-[rgb(var(--ze-hero-title))] [overflow-wrap:anywhere] sm:mb-[13px] sm:text-[49px] lg:text-[56px] lg:tracking-[-3px] xl:text-[64px] xl:leading-[1.03]"
        >
          {restaurant.name}
          <span className="text-gold">.</span>
        </h1>
        {slide?.headline && <p className="mb-2 text-xl font-semibold text-[rgb(var(--ze-hero-title))]">{slide.headline}</p>}
        {(slide ? slide.subline : restaurant.tagline) && (
          <p className="mb-[17px] max-w-[34ch] text-[15px] text-[rgb(var(--ze-hero-subline))] sm:mb-7 sm:text-[17px]">
            {slide ? slide.subline : restaurant.tagline}
          </p>
        )}
        <button
          type="button"
          onClick={onAction ?? (() => jumpTo("menu-heading"))}
          className={`btn min-h-[44px] gap-[15px] rounded-full border-gold bg-gold px-4 text-caption font-bold text-[rgb(var(--ze-cta-text))] hover:border-[rgb(var(--ze-cta-hover))] hover:bg-[rgb(var(--ze-cta-hover))] sm:min-h-[49px] sm:gap-5 sm:px-[23px] sm:text-[13px] ${
            restaurant.tagline ? "" : "mt-[7px] sm:mt-3"
          }`}
        >
          {slide?.cta_label || "Explore the menu"}
          <Icon name="arrow" className="h-[18px] w-[18px]" />
        </button>
        <p className="mt-[11px] text-caption text-[rgb(var(--ze-hero-note))] sm:mt-4">
          {restaurant.delivery_offered ? "Order online for pick-up or delivery." : "Order online for pick-up."}
        </p>
      </div>
      {controls}
    </section>
  );
}

// -------------------------------------------------------------- shortcuts

/**
 * A shortcut to every kind of food on screen, and to the meal times. Each
 * jumps to the first place that kind appears and focuses its heading. It
 * scrolls sideways on a phone rather than widening the page.
 */
/**
 * Where each shortcut scrolls to.
 *
 * A shortcut holding exactly what its category already shows on the menu
 * goes to that category's own heading; only a hand-picked one gets a section
 * of its own (`own`). Otherwise a restaurant whose shortcuts cover its menu
 * -- every one migrated from the old automatic row does -- would print the
 * whole menu twice, once in shortcut sections and once below.
 */
export function shortcutTargets(meals: Meal[], shortcuts: Shortcut[]): Map<string, { target: string; own: boolean }> {
  const categories = meals.flatMap(categoriesOf);
  const targets = new Map<string, { target: string; own: boolean }>();
  for (const s of shortcuts) {
    const matching = categories.filter((c) => c.itemTypeId === s.item_type_id);
    const menuIds = new Set(matching.flatMap((c) => c.items.map((i) => i.id)));
    const chosen = new Set(s.item_ids);
    const whole = matching.length > 0 && menuIds.size === chosen.size && [...chosen].every((id) => menuIds.has(id));
    targets.set(s.id, whole ? { target: matching[0]!.key, own: false } : { target: `shortcut-${s.id}`, own: true });
  }
  return targets;
}

/**
 * The row of round shortcuts under the banner.
 *
 * With `shortcuts` -- a customized storefront -- the row is exactly the ones
 * the restaurant built, each scrolling to its own section of chosen items.
 * Without, every category gets one, named after it, as it always did.
 */
export function CategoryShortcuts({ meals, severalPeriods, categories = {}, shortcuts }: { meals: Meal[]; severalPeriods: boolean; categories?: Record<string, CategoryStyle>; shortcuts?: Shortcut[] }) {
  const seen = new Map<string, { order: number; target: string; label: string; photo: string | null }>();
  let combos: { target: string; photo: string | null } | null = null;

  // Hand-picked shortcuts REPLACE the per-category row -- but only when there
  // are some. `shortcuts` arrives as [] from any restaurant that has never
  // opened the storefront editor, and an empty array is truthy, so the old
  // `if (shortcuts)` took the hand-picked path with nothing in it: every
  // category was skipped and the row rendered as a single lonely "Combos"
  // chip. Length is the question, not existence.
  const handPicked = shortcuts && shortcuts.length > 0 ? shortcuts : null;

  if (handPicked) {
    const photos = new Map(meals.flatMap(categoriesOf).flatMap((c) => c.items.map((i) => [i.id, i.image_url] as const)));
    const targets = shortcutTargets(meals, handPicked);
    handPicked.forEach((s, order) => {
      const photo = s.image_url ?? s.item_ids.map((id) => photos.get(id)).find(Boolean) ?? null;
      seen.set(s.id, { order, target: targets.get(s.id)!.target, label: s.label, photo });
    });
  }

  for (const meal of meals) {
    if (meal.combos.length > 0 && !combos) {
      combos = { target: `combos-${meal.id}`, photo: comboPhoto(meal.combos) };
    }
    if (handPicked) continue;
    for (const category of categoriesOf(meal)) {
      if (categories[category.itemTypeId]?.show_in_shortcuts === false) continue;
      const photo = categories[category.itemTypeId]?.image_url ?? category.items.find((i) => i.image_url)?.image_url ?? null;
      const known = seen.get(category.itemTypeId);
      if (!known) seen.set(category.itemTypeId, { order: categories[category.itemTypeId]?.sort_order ?? seen.size, target: category.key, label: category.label, photo });
      else if (!known.photo && photo) known.photo = photo;
    }
  }

  const entries = [
    ...[...seen.values()].sort((a, b) => a.order - b.order),
    ...(combos ? [{ ...combos, label: "Combos" }] : []),
  ];
  if (entries.length === 0) return null;

  return (
    <nav
      aria-label="Menu categories"
      className="no-scrollbar mt-4 flex items-stretch justify-start gap-[3px] overflow-x-auto border-b border-[rgb(var(--ze-shortcut-line))] pb-4 pt-[9px] sm:mt-5 sm:gap-1 lg:mt-7 lg:justify-around lg:gap-[9px] lg:px-2.5 lg:pb-5 lg:pt-[15px]"
    >
      {entries.map((entry, index) => (
        // Each shortcut arrives a beat after the one before it, left to
        // right, so the row reads as one movement rather than six things
        // appearing at once. Capped at the eighth: the delay is a flourish on
        // a short row and a wait on a long one, and a restaurant with twenty
        // categories must not have the last of them blank for a second.
        // The global prefers-reduced-motion rule collapses it to nothing.
        <Shortcut
          key={entry.target}
          label={entry.label}
          style={{ animationDelay: `${Math.min(index, 8) * 45}ms` }}
          onClick={() => jumpTo(`heading-${entry.target}`)}
        >
          <ShortcutThumb photo={entry.photo} label={entry.label} />
        </Shortcut>
      ))}
      {severalPeriods && (
        <Shortcut label="Meal times" onClick={() => jumpTo("meal-times")}>
          <span className="grid h-full w-full place-items-center bg-[rgb(var(--ze-shortcut-clock))] text-brick">
            <Icon name="clock" className="h-7 w-7" />
          </span>
        </Shortcut>
      )}
    </nav>
  );
}

function Shortcut({
  label,
  onClick,
  children,
  style,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
  /** The entry delay; see the call site. */
  style?: CSSProperties;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={style}
      className="animate-rise group flex min-w-[89px] flex-col items-center gap-2 rounded-[13px] px-2 pb-[11px] pt-[7px] text-[11px] font-[650] text-[rgb(var(--ze-shortcut-text))] transition-colors duration-color hover:bg-[rgb(var(--ze-shortcut-hover))] hover:text-brick sm:min-w-[95px] lg:min-w-[104px] lg:flex-1 lg:gap-[11px] lg:px-3 lg:text-caption"
    >
      <span className="grid h-[54px] w-[54px] place-items-center overflow-hidden rounded-full border border-[rgb(var(--ze-shortcut-border))] bg-[rgb(var(--ze-shortcut-ground))] sm:h-[58px] sm:w-[58px] lg:h-[65px] lg:w-[65px]">
        {children}
      </span>
      {/* Two lines before it gives up, rather than one. A restaurant names its
          own categories, so a long one is ordinary -- and "Not-so-secret me…"
          on a single truncated line tells someone less than the two words that
          would fit underneath it. The clamp still stops a sentence from
          dragging the whole row taller. */}
      <span className="line-clamp-2 max-w-[120px] text-balance leading-[1.25] [overflow-wrap:anywhere]">
        {label}
      </span>
    </button>
  );
}

function ShortcutThumb({ photo, label }: { photo: string | null; label: string }) {
  const [failed, setFailed] = useState<string | null>(null);
  if (photo && failed !== photo) {
    return (
      <MenuImage
        src={photo}
        onFail={() => setFailed(photo)}
        className="h-full w-full object-cover transition-transform duration-[160ms] group-hover:scale-[1.06] motion-reduce:group-hover:scale-100"
      />
    );
  }
  // No photo of this kind of food: its initial, rather than an empty circle.
  return (
    <span aria-hidden="true" className="font-display text-2xl font-bold text-brick">
      {label.trim().charAt(0).toUpperCase()}
    </span>
  );
}

/** A photo for a meal deal: its own if the restaurant took one, otherwise
 *  borrowed from one of the items it is built from. */
export function comboPhoto(combos: Combo[]): string | null {
  for (const combo of combos) {
    if (combo.image_url) return combo.image_url;
    for (const slot of combo.slots) {
      const photo = slot.items.find((i) => i.image_url)?.image_url;
      if (photo) return photo;
    }
  }
  return null;
}

/** Automatic movement is optional and never announced. Focus, hover, a
 * hidden tab and reduced motion each stop the timer on their own, and leaving
 * one of those conditions must not override another that still holds. There
 * is no pause button: reaching the slides with a pointer or the keyboard
 * stops them, and the dots move between them. */
export function HomeBanner({ restaurant, photo, storefront, onTarget }: {
  restaurant: Pick<Portal, "name" | "tagline" | "delivery_offered">;
  photo: string | null;
  storefront?: Storefront | null;
  onTarget?: (banner: Banner) => void;
}) {
  const slides = storefront?.banners;
  const count = slides?.length ?? 0;
  const [index, setIndex] = useState(0);
  const [previous, setPrevious] = useState<Banner | null>(null);
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [hidden, setHidden] = useState(document.hidden);
  const [reduced, setReduced] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const touch = useRef<number | null>(null);
  const active = count ? index % count : 0;
  const current = slides?.[active];
  // Read inside the timer rather than depended upon: the portal's live
  // preview rebuilds this array on every keystroke, and depending on the
  // object would restart the timer each time. Its photo still is a
  // dependency, which is what actually decides the transition.
  const latest = useRef(current);
  latest.current = current;
  const interval = storefront?.banner_interval_ms ?? 5000;
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const motion = () => setReduced(media.matches);
    const visibility = () => setHidden(document.hidden);
    media.addEventListener("change", motion);
    document.addEventListener("visibilitychange", visibility);
    return () => { media.removeEventListener("change", motion); document.removeEventListener("visibilitychange", visibility); };
  }, []);
  useEffect(() => {
    if (count < 2 || hover || focus || hidden || reduced) return;
    const timer = window.setTimeout(() => {
      setPrevious(latest.current ?? null);
      setIndex((value) => (value + 1) % count);
    }, interval);
    return () => window.clearTimeout(timer);
  }, [active, count, current?.image_url, hover, focus, hidden, reduced, interval]);
  useEffect(() => {
    if (!previous) return;
    const timer = window.setTimeout(() => setPrevious(null), reduced ? 0 : 450);
    return () => window.clearTimeout(timer);
  }, [previous, active, reduced]);
  useEffect(() => {
    if (!slides || count < 2) return;
    const link = document.createElement("link");
    link.rel = "preload"; link.as = "image"; link.href = slides[(active + 1) % count]!.image_url;
    document.head.appendChild(link);
    return () => link.remove();
  }, [slides, active, count]);
  function choose(next: number) {
    setPrevious(reduced ? null : latest.current ?? null);
    setIndex((next + count) % count);
  }
  if (!current) return <BannerFrame restaurant={restaurant} photo={photo} />;
  return <div aria-roledescription="carousel" aria-label="Restaurant highlights" role="region"
    onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
    onFocusCapture={() => setFocus(true)} onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setFocus(false); }}
    onTouchStart={(event) => { touch.current = event.touches[0]!.clientX; }}
    onTouchEnd={(event) => { if (touch.current !== null) { const delta = event.changedTouches[0]!.clientX - touch.current; if (Math.abs(delta) > 50) choose(active + (delta < 0 ? 1 : -1)); } touch.current = null; }}>
    <BannerFrame restaurant={restaurant} photo={current.image_url} slide={current} previous={previous}
      onAction={() => onTarget ? onTarget(current) : jumpTo("menu-heading")}
      controls={<div className="absolute bottom-3 right-3 z-section flex max-w-full flex-wrap items-center rounded-full bg-hero/95 px-2 text-[rgb(var(--ze-hero-title))]">
        {slides?.map((slide, n) => <button type="button" key={slide.id} className="grid h-11 w-7 place-items-center" aria-label={`Slide ${n + 1} of ${count}`} aria-pressed={active === n} onClick={() => choose(n)}><span className={`h-2 w-2 rounded-full ${active === n ? "bg-gold" : "bg-white/50"}`} /></button>)}
      </div>} />
  </div>;
}
