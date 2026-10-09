import { useLayoutEffect, useMemo } from "react";
import { Loading, StatePage } from "@/components/common/Feedback";
import { Icon } from "@/components/common/icons";
import { MenuImage } from "@/components/common/MenuImage";
import { useLocationsQuery } from "@/features/storefront/storefrontApi";
import { errorMessage } from "@/services/apiClient";
import type { PlatformLocation } from "@/types";

/**
 * The platform root: which of our places do you want to order from?
 *
 * Shown on the bare domain, where there is no restaurant in the Host header
 * to resolve. Before this, that address answered RESTAURANT_NOT_FOUND and the
 * only way in was to already know a subdomain -- so someone typing the domain
 * we print on things reached an error page.
 *
 * Every card reads from `is_orderable`, which the API computes from two gates
 * at once: the location is open, and the restaurant behind it is actually
 * taking orders. A location whose tenant is suspended therefore reads as
 * unavailable here rather than as a link that fails after the click.
 *
 * An unavailable location is a button rather than a dead card. It has
 * something to say -- that we are not there yet -- and saying it on click is
 * the difference between a page that looks broken and a page that answers.
 */
export function LocationsPage() {
  const locations = useLocationsQuery();

  // The customer palette, which this page uses but is not wrapped in.
  //
  // CustomerSurface stamps data-surface="customer" on <html>, and the hero
  // tokens (--ze-hero-title, --ze-hero-subline, --ze-hero-eyebrow) are
  // declared under that attribute. This page sits outside that layout on
  // purpose -- it belongs to no restaurant and must not take one's theme --
  // so without this the tokens resolve to nothing, rgb() is invalid, and
  // every word on the hero falls back to near-black on a dark photograph.
  //
  // Set on <html> rather than a wrapper for the same reason CustomerSurface
  // does it: the variables have to be in scope for the whole document.
  useLayoutEffect(() => {
    const root = document.documentElement;
    const had = root.dataset.surface;
    root.dataset.surface = "customer";
    return () => {
      if (had) root.dataset.surface = had;
      else delete root.dataset.surface;
    };
  }, []);
  // Before the early returns: a hook may not sit behind a conditional.
  const themes = useMemo(
    () => assignThemes(locations.data?.locations ?? []),
    [locations.data],
  );

  if (locations.error) {
    return (
      <StatePage
        title="We can't load our locations"
        action={
          <button
            type="button"
            className="btn-primary"
            disabled={locations.isFetching}
            onClick={() => void locations.refetch()}
          >
            {locations.isFetching ? "Trying again…" : "Try again"}
          </button>
        }
      >
        {errorMessage(locations.error)}
      </StatePage>
    );
  }

  if (!locations.data) {
    return (
      <main className="mx-auto flex min-h-[75vh] max-w-[570px] items-center px-6">
        <div className="w-full">
          <Loading>Finding our locations…</Loading>
        </div>
      </main>
    );
  }

  const all = locations.data.locations;
  const open = all.filter((l) => l.is_orderable);
  const soon = all.filter((l) => !l.is_orderable);

  return (
    <div className="flex min-h-dvh flex-col bg-paper">
      <PlatformHeader />

      {/* A photograph of a counter, not of food: this page is about where to
          eat, and a burger here would compete with the menu one click away.
          Served from web/public rather than the image service, because the
          platform root has no tenant and the image service keys everything
          by one. */}
      <section className="relative isolate overflow-hidden" aria-labelledby="locations-heading">
        <img
          src="/hero-counter.webp"
          alt=""
          aria-hidden="true"
          fetchPriority="high"
          className="absolute inset-0 h-full w-full object-cover"
        />
        {/* Two layers: a wash that keeps the type legible wherever the
            photograph is bright, and a left-weighted gradient so the words
            sit on the darkest part of it. */}
        <span aria-hidden="true" className="absolute inset-0 bg-hero/[.72]" />
        <span
          aria-hidden="true"
          className="absolute inset-0 bg-[linear-gradient(100deg,rgb(var(--ze-hero))_18%,rgb(var(--ze-hero)/.55)_58%,rgb(var(--ze-hero)/.28)_100%)]"
        />

        <div className="relative z-section mx-auto w-full max-w-[1336px] px-[19px] py-[44px] sm:px-7 sm:py-[60px] xl:px-10 xl:py-[72px]">
          <div className="animate-hero max-w-[42ch]">
            <p className="eyebrow text-[rgb(var(--ze-hero-eyebrow))]">Our locations</p>
            <h1
              id="locations-heading"
              className="mt-3 font-display text-[38px] font-bold leading-[1.03] tracking-[-1.4px] text-[rgb(var(--ze-hero-title))] sm:text-[52px] xl:text-[62px]"
            >
              Where are you
              <br />
              eating today?
            </h1>
            <p className="mt-4 max-w-[48ch] text-[15px] text-[rgb(var(--ze-hero-subline))] sm:text-[17px]">
              {open.length === 0
                ? "We're not taking orders anywhere just yet. Here's where we're headed."
                : open.length === 1
                  ? `We're open in ${placeOf(open[0]!)} — start an order below.`
                  : "Pick a location to see its menu and start an order."}
            </p>
            <CountStrip open={open.length} soon={soon.length} />
          </div>
        </div>
      </section>

      <main className="mx-auto w-full max-w-[1336px] flex-1 px-[19px] pb-[96px] pt-9 sm:px-7 sm:pt-12 xl:px-10">

        {all.length === 0 ? (
          <div className="empty mt-12">
            <Icon name="store" className="mx-auto mb-4 h-8 w-8 text-muted" />
            <h2 className="font-display text-2xl text-ink">No locations yet.</h2>
            <p className="mt-2 text-caption text-muted">Check back soon.</p>
          </div>
        ) : (
          <>
            {open.length > 0 && (
              <section className="mt-10 sm:mt-12" aria-labelledby="open-heading">
                {/* The heading earns its place only when there is a set to
                    label. Above a single card it repeats the badge already on
                    it. */}
                {open.length > 1 ? (
                  <SectionHeading
                    id="open-heading"
                    label="Open now"
                    note={`${open.length} locations`}
                  />
                ) : (
                  <h2 id="open-heading" className="sr-only">
                    Open now
                  </h2>
                )}
                {/* A single open location gets the full width rather than a
                    third of a row with empty space beside it. Past one they
                    are peers and a grid is right again. */}
                <ul
                  className={
                    open.length === 1
                      ? "grid grid-cols-1"
                      : "grid grid-cols-1 gap-[15px] sm:gap-[19px] md:grid-cols-2 xl:grid-cols-3"
                  }
                >
                  {open.map((location, index) => (
                    <li key={location.slug} className="min-w-0" style={enterAt(index)}>
                      <OpenCard
                        location={location}
                        theme={themeFor(themes, location)}
                        wide={open.length === 1}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {soon.length > 0 && (
              <section className="mt-11 sm:mt-14" aria-labelledby="soon-heading">
                {/* The other cities are not listed.
                    Naming places a customer cannot order from is a list of
                    disappointments: three cards, three badges, three ways of
                    saying no, above the one thing on the page that works. The
                    count is the honest part -- it says we are growing without
                    inviting anyone to tap a city and be refused. */}
                <div className="flex flex-col items-start gap-4 rounded-[16px] border border-dashed border-[rgb(var(--ze-shortcut-border))] bg-[rgb(var(--ze-shortcut-ground))]/40 px-[19px] py-[18px] sm:flex-row sm:items-center sm:justify-between sm:px-[23px] sm:py-[21px]">
                  <div className="min-w-0">
                    <h2
                      id="soon-heading"
                      className="font-display text-[19px] font-bold leading-[1.25] tracking-[-.3px] text-[rgb(var(--ze-item-title))] sm:text-[21px]"
                    >
                      New stores
                    </h2>
                    <p className="mt-1 max-w-[52ch] text-caption text-[rgb(var(--ze-item-description))]">
                      {soon.length} more {soon.length === 1 ? "store" : "stores"}{" "}
                      {soon.length === 1 ? "is" : "are"} on the way.{" "}
                      None of them is taking orders yet
                      {open.length === 1 ? ` — ${placeOf(open[0]!)} is the only one open today.` : "."}
                    </p>
                  </div>
                  <span className="inline-flex shrink-0 items-center gap-2 rounded-full bg-warningSoft px-[11px] py-[6px] text-[11px] font-[650] uppercase tracking-[1px] text-[rgb(var(--ze-warning))]">
                    <span aria-hidden="true" className="h-[6px] w-[6px] rounded-full bg-warning" />
                    Not available yet
                  </span>
                </div>
              </section>
            )}
          </>
        )}
      </main>

      <footer className="border-t border-[rgb(var(--ze-footer-line))]">
        <div className="mx-auto flex w-full max-w-[1336px] flex-col items-start justify-between gap-4 px-[19px] py-7 text-caption text-[rgb(var(--ze-footer-text))] sm:flex-row sm:items-center sm:px-7 xl:px-10">
          <p className="font-display text-xl font-bold tracking-[-.8px] text-brick">
            Zenoeats<span className="text-accent">.</span>
          </p>
          <nav className="flex flex-wrap items-center gap-x-5 gap-y-1" aria-label="Policies">
            <a className="link text-caption" href="/legal/privacy.html">Privacy</a>
            <a className="link text-caption" href="/legal/terms.html">Terms</a>
            <a className="link text-caption" href="/legal/refunds.html">Refunds</a>
          </nav>
        </div>
      </footer>
    </div>
  );
}

/**
 * Each card arrives a beat after the one before it.
 *
 * Capped, because the delay is a flourish on a short list and a wait on a long
 * one: the twentieth card must not sit blank for a second and a quarter. The
 * global prefers-reduced-motion rule collapses the whole thing to nothing, so
 * there is no second code path for people who asked for less movement.
 */
function enterAt(index: number): React.CSSProperties {
  return { animationDelay: `${Math.min(index, 8) * 55}ms` };
}

function CountStrip({ open, soon }: { open: number; soon: number }) {
  return (
    // On the hero now, so it takes the hero's palette. A muted grey that read
    // as secondary on cream is nearly invisible on a dark photograph.
    <dl className="mt-7 flex flex-wrap items-center gap-x-9 gap-y-3 border-t border-[rgb(var(--ze-hero-title)/.22)] pt-5 sm:gap-x-12">
      <div>
        <dt className="text-caption text-[rgb(var(--ze-hero-subline))]">Open now</dt>
        <dd className="tnum font-display text-[28px] font-bold leading-tight text-gold">{open}</dd>
      </div>
      <div>
        <dt className="text-caption text-[rgb(var(--ze-hero-subline))]">Coming soon</dt>
        <dd className="tnum font-display text-[28px] font-bold leading-tight text-[rgb(var(--ze-hero-title))]">
          {soon}
        </dd>
      </div>
    </dl>
  );
}

/**
 * "Dallas, Texas", but just "New York" where the state repeats the city --
 * "New York, New York" reads as a mistake in a sentence even though it is a
 * real address.
 */
function placeOf(location: PlatformLocation): string {
  // A location made on activation carries the restaurant's address, which
  // may not be filled in yet. Its name is the next best thing to say.
  if (!location.city.trim()) return location.name;
  const region = location.region?.trim();
  if (!region || region.toLowerCase() === location.city.trim().toLowerCase()) {
    return location.city;
  }
  return `${location.city}, ${region}`;
}

// ------------------------------------------------------------------ the mark

/**
 * A placeholder logo, until a location uploads its own.
 *
 * Generated rather than drawn per location on purpose: a hand-made emblem for
 * each of the four we happen to have seeded would be dead weight the moment a
 * fifth is added, and a location added through the admin portal would be the
 * only one with no mark. A monogram on a colour the slug picks gives every
 * location -- including ones that do not exist yet -- something consistent and
 * telling apart, and it is replaced by the real thing as soon as `image_url`
 * is set.
 *
 * Colours come from the existing palette rather than a generated hue, so a new
 * location cannot introduce one that fights the rest of the page.
 */
const MARK_THEMES = [
  { ground: "--ze-hero", ink: "--ze-hero-title" },
  { ground: "--ze-brand", ink: "--ze-hero-title" },
  { ground: "--ze-accent", ink: "--ze-hero-title" },
  { ground: "--ze-success", ink: "--ze-hero-title" },
  { ground: "--ze-meal-title", ink: "--ze-hero-title" },
  { ground: "--ze-brand-hover", ink: "--ze-hero-title" },
] as const;

/**
 * A location's preferred mark colour.
 *
 * FNV-1a with a murmur3 avalanche, rather than the obvious `hash * 31 + c`:
 * with six themes, 31 is congruent to 1 modulo 6, so the positional weighting
 * cancels out exactly and the function collapses into a sum of character
 * codes. Three of the four seeded slugs landed on the same colour that way.
 * The avalanche step is what keeps the low bits -- the only ones the modulo
 * reads -- dependent on the whole string.
 */
function preferredTheme(slug: string): number {
  let hash = 2166136261;
  for (let i = 0; i < slug.length; i += 1) {
    hash ^= slug.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  hash ^= hash >>> 16;
  hash = Math.imul(hash, 2246822507);
  hash ^= hash >>> 13;
  hash = Math.imul(hash, 3266489909);
  return ((hash ^ (hash >>> 16)) >>> 0) % MARK_THEMES.length;
}

/**
 * One colour per location, no two alike while there are colours left.
 *
 * A good hash still collides -- four items in six buckets collide about forty
 * per cent of the time -- and two cards side by side in the same colour reads
 * as a bug rather than as coincidence. So each location asks for the colour
 * its slug prefers and takes the next free one if that is gone. Deterministic
 * for a given set, and past six locations it wraps and starts repeating,
 * which is the point at which repeats stop looking like a mistake anyway.
 */
function assignThemes(locations: PlatformLocation[]): Map<string, (typeof MARK_THEMES)[number]> {
  const taken = new Set<number>();
  const chosen = new Map<string, (typeof MARK_THEMES)[number]>();
  for (const location of locations) {
    const wanted = preferredTheme(location.slug);
    let index = wanted;
    for (let step = 0; step < MARK_THEMES.length && taken.has(index); step += 1) {
      index = (wanted + step + 1) % MARK_THEMES.length;
    }
    taken.add(index);
    if (taken.size === MARK_THEMES.length) taken.clear();
    chosen.set(location.slug, MARK_THEMES[index] ?? MARK_THEMES[0]);
  }
  return chosen;
}

/** "Jr's Corner" -> JC, "Rainforest" -> RA. Two characters either way, so
 *  every badge is optically the same weight. */
function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const [first, second] = words;
  if (!first) return "?";
  if (!second) return first.slice(0, 2).toUpperCase();
  return (first.slice(0, 1) + second.slice(0, 1)).toUpperCase();
}

/** The map always has every location in the list; the fallback only keeps
 *  the lookup total. */
function themeFor(
  themes: Map<string, (typeof MARK_THEMES)[number]>,
  location: PlatformLocation,
): (typeof MARK_THEMES)[number] {
  return themes.get(location.slug) ?? MARK_THEMES[0];
}

function LocationMark({
  location,
  theme,
}: {
  location: PlatformLocation;
  theme: (typeof MARK_THEMES)[number];
}) {
  // A real logo once there is one. Decorative either way: the name is right
  // beside it in text, so announcing the mark would just say it twice.
  if (location.image_url) {
    return (
      <span className="grid h-[54px] w-[54px] shrink-0 place-items-center overflow-hidden rounded-[15px] bg-[rgb(var(--ze-photo-ground))] sm:h-[58px] sm:w-[58px]">
        <img src={location.image_url} alt="" className="h-full w-full object-cover" />
      </span>
    );
  }

  return (
    <span
      aria-hidden="true"
      className="grid h-[54px] w-[54px] shrink-0 place-items-center rounded-[15px] font-display text-[21px] font-bold tracking-[-.5px] shadow-[inset_0_-2px_6px_rgb(0_0_0_/_0.14)] transition-transform duration-[220ms] ease-standard sm:h-[58px] sm:w-[58px] sm:text-[23px]"
      style={{
        backgroundImage: `linear-gradient(145deg, rgb(var(${theme.ground})), rgb(var(${theme.ground}) / 0.78))`,
        color: `rgb(var(${theme.ink}))`,
      }}
    >
      {initialsOf(location.name)}
    </span>
  );
}

// ----------------------------------------------------------------- the cards

function PlatformHeader() {
  return (
    <header className="border-b border-hairline bg-surface">
      <div className="mx-auto flex w-full max-w-[1336px] items-center justify-between gap-4 px-[19px] py-[15px] sm:px-7 sm:py-[17px] xl:px-10">
        <p className="font-display text-[22px] font-bold tracking-[-.8px] text-brick sm:text-2xl">
          Zenoeats<span className="text-accent">.</span>
        </p>
        <p className="hidden text-caption text-muted sm:block">
          Order online for pick-up or delivery
        </p>
      </div>
    </header>
  );
}

function SectionHeading({ id, label, note }: { id: string; label: string; note: string }) {
  return (
    <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-5 gap-y-1">
      <h2
        id={id}
        className="font-display text-[26px] font-bold leading-[1.2] tracking-[-.8px] text-[rgb(var(--ze-meal-title))] sm:text-[29px]"
      >
        {label}
        <span className="text-accent">.</span>
      </h2>
      <p className="text-caption text-[rgb(var(--ze-menu-muted))]">{note}</p>
    </div>
  );
}

/** Shared frame, so an open and a closed card are the same object in two
 *  states rather than two cards that happen to look alike. The entry
 *  animation is on this element and the delay on its <li>, because `both`
 *  fill mode needs the delay and the animation on the same box. */
const CARD_FRAME =
  "animate-rise flex h-full min-h-[206px] w-full flex-col overflow-hidden rounded-[16px] border text-left sm:min-h-[218px]";

/**
 * A location taking orders.
 *
 * A plain anchor, not a router Link: the storefront is on the restaurant's own
 * subdomain, which is a different origin and so a document load rather than a
 * client navigation. The API hands over the absolute URL -- the browser never
 * assembles a hostname out of a slug.
 */
function OpenCard({
  location,
  theme,
  wide = false,
}: {
  location: PlatformLocation;
  theme: (typeof MARK_THEMES)[number];
  /** The only location open: photo beside the detail rather than above it. */
  wide?: boolean;
}) {
  return (
    <a
      href={location.storefront_url ?? "#"}
      className={`${CARD_FRAME} ${wide ? "sm:flex-row" : ""} group border-[rgb(var(--ze-card-border))] bg-cream text-[rgb(var(--ze-card-text))] transition-[box-shadow,border-color,transform] duration-[220ms] ease-standard hover:-translate-y-[3px] hover:border-[rgb(var(--ze-card-hover))] hover:shadow-[0_16px_38px_rgb(var(--ze-card-shadow)_/_0.11)]`}
    >
      {/* The place itself, where there is a photograph of it. The mark then
          overlaps the bottom edge, which is what stops the card reading as a
          picture with a form stapled underneath. */}
      {location.image_url && (
        <span
          className={`relative block shrink-0 overflow-hidden bg-[rgb(var(--ze-photo-ground))] ${
            wide ? "h-[172px] sm:h-auto sm:w-[44%]" : "h-[132px] sm:h-[148px]"
          }`}
        >
          <MenuImage
            src={location.image_url}
            className="absolute inset-0 h-full w-full object-cover transition-transform duration-[420ms] ease-standard group-hover:scale-[1.05]"
          />
          <span
            aria-hidden="true"
            className="absolute inset-0 bg-[linear-gradient(to_top,rgb(var(--ze-hero)/.42),transparent_58%)]"
          />
        </span>
      )}

      {/* relative, so it paints above the positioned photo span: the mark
          below overlaps the photo's lower edge and was being clipped by it. */}
      <span
        className={`relative flex flex-1 flex-col p-[19px] sm:p-[22px] ${
          location.image_url && !wide ? "pt-0 sm:pt-0" : ""
        }`}
      >
        <span
          className={`mb-[15px] flex items-start justify-between gap-3 ${
            location.image_url && !wide ? "-mt-[27px] sm:-mt-[29px]" : ""
          }`}
        >
          <span className="transition-transform duration-[220ms] ease-standard group-hover:-rotate-[3deg] group-hover:scale-[1.04]">
            <LocationMark location={location} theme={theme} />
          </span>
          <span
            className={`inline-flex items-center gap-[7px] rounded-full bg-brickSoft px-[9px] py-[5px] text-[10px] font-[650] uppercase tracking-[1px] text-brick ${
              location.image_url && !wide ? "mt-[33px] sm:mt-[35px]" : ""
            }`}
          >
            {/* The dot breathes. It is the one thing on the page asserting
                something is true *now*, and a live indicator that never
                moves reads as a printed label. Two rings: a solid dot, and a
                second that expands and fades out from under it.
                motion-reduce stops it dead -- a pulse is exactly the kind of
                repeating movement that rule exists for. */}
            <span aria-hidden="true" className="relative grid h-[6px] w-[6px] place-items-center">
              <span className="absolute h-full w-full animate-pulse-ring rounded-full bg-brick motion-reduce:animate-none" />
              <span className="relative h-full w-full rounded-full bg-brick" />
            </span>
            Open now
          </span>
        </span>

        <span className="block font-display text-[23px] font-bold leading-[1.15] tracking-[-.5px] text-[rgb(var(--ze-item-title))] [overflow-wrap:anywhere] sm:text-2xl">
          {location.name}
        </span>

        <span className="mt-1 block text-sm text-[rgb(var(--ze-item-title))]">
          {placeOf(location)}
        </span>

        {location.address_line && (
          <span className="mt-2.5 flex items-start gap-1.5 text-caption text-[rgb(var(--ze-item-description))]">
            <Icon name="store" className="mt-[2px] h-3.5 w-3.5 shrink-0" />
            <span className="[overflow-wrap:anywhere]">{location.address_line}</span>
          </span>
        )}

        {location.blurb && (
          <span className="mt-1.5 flex items-center gap-1.5 text-caption text-[rgb(var(--ze-item-description))]">
            <Icon name="clock" className="h-3.5 w-3.5 shrink-0" />
            {location.blurb}
          </span>
        )}

        <span className="mt-auto flex items-center justify-between gap-2 pt-[17px]">
          <span className="text-[13px] font-[650] text-brick transition-transform duration-[220ms] ease-standard group-hover:translate-x-[2px]">
            See the menu
          </span>
          <span
            aria-hidden="true"
            className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brickSoft text-brick transition-[background-color,color,transform] duration-color group-hover:translate-x-[3px] group-hover:bg-brick group-hover:text-white"
          >
            <Icon name="arrow" className="h-4 w-4" />
          </span>
        </span>
      </span>
    </a>
  );
}

