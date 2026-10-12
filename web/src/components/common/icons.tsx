/**
 * Shared inline icons.
 *
 * The design package's set (assets/icons): 24×24, a 1.7 round stroke in
 * currentColor, so an icon takes the colour of the text it sits beside.
 * Inline rather than an icon package or a sprite fetch: thirty short paths do
 * not justify a dependency or a request, and inline SVG needs no CSP change.
 *
 * Every icon is decorative (aria-hidden). A control that shows only an icon
 * must carry its own accessible name.
 */
const PATHS = {
  arrow: "M5 12h14m-5-5 5 5-5 5",
  back: "M19 12H5m5-5-5 5 5 5",
  bag: "M5 7h14l1 14H4L5 7Zm4 0V5a3 3 0 0 1 6 0v2",
  branch: "M6 3v12a3 3 0 0 0 3 3h10m-4-4 4 4-4 4",
  card: "M2 5h20v14H2V5Zm0 5h20M6 15h3",
  check: "m5 12 4 4L19 6",
  chevron: "m9 5 7 7-7 7",
  clock: "M12 8v4l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  close: "M6 6l12 12M18 6L6 18",
  copy: "M8 8h13v13H8V8ZM16 8V3H3v13h5",
  download: "M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5",
  edit: "m16 3 5 5-12 12H4v-5L16 3Zm-3 3 5 5",
  external: "M14 3h7v7m0-7L10 14M10 3H3v18h18v-7",
  heart: "M12 20s-7.5-4.6-9.3-9.2C1.4 7.4 3.6 4 7 4c2.1 0 3.9 1.2 5 3 1.1-1.8 2.9-3 5-3 3.4 0 5.6 3.4 4.3 6.8C19.5 15.4 12 20 12 20Z",
  info: "M12 16v-4m0-4v.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  kitchen: "M4 20h16M5 17V8l3-4h8l3 4v9H5Zm4-8v4m6-4v4",
  lock: "M6 10h12v11H6V10Zm3 0V6a3 3 0 0 1 6 0v4M12 15v2",
  mail: "M3 5h18v14H3V5Zm0 0 9 8 9-8",
  menu: "M5 4h14v16H5V4Zm4 5h6m-6 4h6m-6 4h3",
  minus: "M5 12h14",
  photo: "M3 4h18v16H3V4Zm0 12 5-5 4 4 3-3 6 6M15 8h.01",
  plus: "M12 5v14M5 12h14",
  refresh: "M20 7a9 9 0 0 0-15-2L2 8m0-5v5h5M4 17a9 9 0 0 0 15 2l3-3m0 5v-5h-5",
  report: "M4 20h16M7 16v-4m5 4V5m5 11V8",
  search: "M10 17a7 7 0 1 1 0-14 7 7 0 0 1 0 14m5-2 6 6",
  signout: "M9 3H3v18h6m6-14 5 5-5 5m5-5H8",
  stock: "m3 7 9-4 9 4-9 4-9-4Zm0 0v10l9 4 9-4V7M12 11v10",
  store: "M3 9h18L19 3H5L3 9Zm1 0v12h16V9M9 21v-8h6v8",
  // A luggage-style tag with its hole: a coupon, in one stroke.
  tag: "M3 11 11 3h10v10l-8 8-10-10Zm13-3h.01",
  team: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8m13 10v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8M4 21v-2a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v2",
  warning: "m12 3 10 18H2L12 3Zm0 6v5m0 3v.01",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, className = "h-5 w-5" }: { name: IconName; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      className={`shrink-0 ${className}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

/** The pencil beside a heading that opens everything under it for editing.
 *  Em-sized so it scales with the heading it belongs to. */
export function PencilIcon() {
  return <Icon name="edit" className="h-[0.9em] w-[0.9em]" />;
}

/** A picture frame. Stands in for a photo nobody has added yet. */
export function PhotoIcon() {
  return <Icon name="photo" className="h-[1.1em] w-[1.1em]" />;
}

/** The CSS cloche: decoration beside text that already says what it means.
 *  `ready` turns the dome green; `lifted` raises it for a collected order. */
export function Cloche({
  ready = false,
  lifted = false,
  className = "",
}: {
  ready?: boolean;
  lifted?: boolean;
  className?: string;
}) {
  return (
    <div
      aria-hidden="true"
      className={`cloche ${ready ? "cloche-ready" : ""} ${lifted ? "cloche-lifted" : ""} ${className}`}
    >
      <span className="cloche-plate" />
      <span className="cloche-dome" />
      <span className="cloche-knob" />
    </div>
  );
}
