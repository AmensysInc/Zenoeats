import { useCallback, useRef, useState } from "react";
import { Icon, PhotoIcon } from "@/components/common/icons";
import { errorMessage } from "@/services/apiClient";
import { shrinkForUpload } from "@/utils/image";
import { imageFromUrl, uploadImage, type ImageKind, type UploadedImage } from "../restaurantApi";

/** The photo a form is holding: a key to save and a URL to preview, or both
 *  null when there is none. */
export type ImageDraft = { path: string | null; url: string | null };

export const NO_IMAGE: ImageDraft = { path: null, url: null };

export function imageDraft(uploaded: UploadedImage | null): ImageDraft {
  return uploaded ? { path: uploaded.image_path, url: uploaded.image_url } : NO_IMAGE;
}

/**
 * What to upload for each kind of photo, from how the storefront shows it.
 *
 * Every one is cropped to a frame -- `object-cover` -- except the logo, so
 * the useful advice is the shape of the frame and where the crop falls, not
 * a pixel count on its own. The sizes are twice the largest place each photo
 * is drawn, for sharp high-density screens:
 *
 *   items       the menu card (a third of the card, portrait on a phone,
 *               near square on a laptop) and the item page (200px tall,
 *               full width -- wide). Square survives both crops.
 *   options     a 40px tile beside the choice.
 *   categories  a 58px circle among the shortcuts. The corners are lost.
 *   banners     1.25:1 under the text on a phone, about 1.6:1 beside it on
 *               a laptop, 3:2 in the framing editor. The server keeps 2400px.
 *   branding    at most 180 x 56 in the header, never cropped. Small, so a
 *               transparent PNG goes up untouched -- utils/image flattens
 *               anything over 2000px or 1.5 MB onto white, which would be a
 *               white box on the cream header.
 *
 * Change these if those layouts change.
 *
 * The spaces inside each dimension ("1200 × 1200 px") are non-breaking, so
 * a narrow caption never splits a size across two lines. They look like
 * ordinary spaces here; keep them when editing.
 */
export const IMAGE_GUIDE: Record<ImageKind, { size: string; tip: string }> = {
  items: {
    size: "Square, 1200 × 1200 px or larger",
    tip: "Keep the dish in the centre — the menu and the item page crop the edges differently.",
  },
  options: {
    size: "Square, 400 × 400 px or larger",
    tip: "Shown as a small tile, so a close-up reads best.",
  },
  categories: {
    size: "Square, 400 × 400 px or larger",
    tip: "Shown in a circle, so keep the subject in the middle.",
  },
  banners: {
    size: "Landscape 3:2, 2400 × 1600 px or larger",
    tip: "Then drag the preview to choose what stays in view.",
  },
  branding: {
    size: "PNG with a transparent background, about 540 × 170 px",
    tip: "Shown up to 180 × 56 px in the header and never cropped.",
  },
};

/** The same advice as a line of its own, for a list of compact pickers that
 *  has no room for it beside each thumbnail. Placed once, above the rows. */
export function ImageSizeHint({ kind, className = "" }: { kind: ImageKind; className?: string }) {
  const guide = IMAGE_GUIDE[kind];
  return (
    <p className={`field-hint ${className}`}>
      <span className="font-semibold text-ink">Photos:</span> {guide.size}. {guide.tip} JPEG, PNG
      or WebP.
    </p>
  );
}

/**
 * Choose, replace or remove the photo of an item or an option.
 *
 * Choosing uploads straight away and hands the new key to the form; it does
 * not save anything. The photo reaches the menu when the form it sits in is
 * saved, so cancelling that form really does leave the menu as it was --
 * the same contract every other field in this builder keeps.
 *
 * Two sizes. `md` is for a form with room, and says what it does in words.
 * `sm` sits in a dense row of inputs, where it is a thumbnail that opens the
 * file chooser and a small cross to take the photo off.
 *
 * `onBusyChange` lets the form hold its Save button while a photo is still
 * uploading. Without it, saving mid-upload would quietly save the row without
 * the photo the manager had just chosen.
 */
export function ImagePicker({
  kind,
  image,
  label,
  size = "md",
  disabled = false,
  onChange,
  onError,
  onBusyChange,
  guide = IMAGE_GUIDE[kind],
  fit = "cover",
}: {
  kind: ImageKind;
  image: ImageDraft;
  /** What the photo is of, for screen readers: "Smash Burger". */
  label: string;
  size?: "sm" | "md";
  disabled?: boolean;
  onChange: (next: ImageDraft) => void;
  onError: (message: string | null) => void;
  onBusyChange?: (busy: boolean) => void;
  /** Advice for this particular picture, when one kind covers several: the
   *  square logo and the wide name lettering are both "branding". */
  guide?: { size: string; tip: string };
  /** `contain` for a logo, which is shown whole and never cropped. */
  fit?: "cover" | "contain";
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  // The link field, shown only when asked for. Most photos are files on the
  // phone that took them; a link is the other case, not the common one.
  const [linking, setLinking] = useState(false);
  const [link, setLink] = useState("");
  const off = disabled || busy;

  /** One wrapper around both ways a photo arrives, so the form is held, the
   *  error is shown and the draft is handed over identically either way. */
  async function store(get: () => Promise<UploadedImage>) {
    setBusy(true);
    onBusyChange?.(true);
    try {
      onChange(imageDraft(await get()));
      onError(null);
      setLinking(false);
      setLink("");
    } catch (e) {
      onError(errorMessage(e));
    } finally {
      setBusy(false);
      onBusyChange?.(false);
      // Cleared so choosing the same file again, after a failure, still fires.
      if (input.current) input.current.value = "";
    }
  }

  function upload(file: File) {
    return store(async () => {
      // Shrunk in the browser first: a 12 MP phone photo is refused by the
      // server's 8 MB ceiling, and resizing it here is faster than finding
      // that out over a restaurant's uplink.
      const photo = await shrinkForUpload(file, kind === "banners" ? 2800 : undefined);
      return uploadImage(kind, photo.blob, photo.filename);
    });
  }

  function fromLink() {
    const url = link.trim();
    if (!url) return;
    // No shrinking: the server fetches and re-encodes it, so the browser
    // never holds the file at all.
    return store(() => imageFromUrl(kind, url));
  }

  const fileInput = (
    <input
      ref={input}
      type="file"
      // Named formats rather than image/*. It is the list the server accepts,
      // and it is what makes an iPhone convert a HEIC photo to JPEG as it
      // hands it over instead of sending something that would be refused.
      accept="image/jpeg,image/png,image/webp"
      hidden
      onChange={(e) => {
        const file = e.target.files?.[0];
        if (file) void upload(file);
      }}
    />
  );

  const thumbnail = (box: string) => (
    <button
      type="button"
      disabled={off}
      onClick={() => input.current?.click()}
      aria-label={image.url ? `Change the photo of ${label}` : `Add a photo of ${label}`}
      // The compact picker has no room for the size advice, so it rides on
      // the tooltip here; lists of them also carry an ImageSizeHint above.
      title={`${image.url ? "Change photo" : "Add photo"} — ${guide.size}`}
      className={`${box} relative flex shrink-0 items-center justify-center overflow-hidden border border-hairline bg-paper text-muted transition-colors duration-color hover:border-ink disabled:hover:border-hairline`}
    >
      {image.url ? (
        <img
          src={image.url}
          alt=""
          className={`h-full w-full ${fit === "contain" ? "object-contain p-1" : "object-cover"}`}
        />
      ) : (
        <PhotoIcon />
      )}
      {/* Over the thumbnail only while uploading, and never catching the
          pointer: the form's other controls stay usable around it. */}
      {busy && (
        <span className="pointer-events-none absolute inset-0 grid place-items-center bg-white/[.86] text-[11px] text-ink">
          …
        </span>
      )}
    </button>
  );

  if (size === "sm") {
    return (
      <span className="relative inline-flex shrink-0">
        {thumbnail("h-[38px] w-[38px] rounded-status")}
        {image.url && !busy && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => onChange(NO_IMAGE)}
            aria-label={`Remove the photo of ${label}`}
            title="Remove photo"
            className="absolute -right-2 -top-2 grid h-5 w-5 place-items-center rounded-full bg-danger text-white"
          >
            <Icon name="close" className="h-3 w-3" />
          </button>
        )}
        {fileInput}
      </span>
    );
  }

  return (
    <div className="flex items-center gap-[13px]">
      {thumbnail("h-20 w-20 rounded-field")}
      <div className="flex flex-col items-start">
        <button
          type="button"
          className="link min-h-[32px]"
          disabled={off}
          onClick={() => input.current?.click()}
        >
          {busy ? "Uploading…" : image.url ? "change photo" : "add a photo"}
        </button>
        {image.url && !busy && (
          <button
            type="button"
            className="link-danger min-h-[32px]"
            disabled={disabled}
            onClick={() => onChange(NO_IMAGE)}
          >
            remove photo
          </button>
        )}

        {/* The other way a photo arrives. Most of the photography a small
            restaurant has is already on the web -- its own site, a
            photographer's gallery -- and downloading a file in order to
            upload it is two steps and a Downloads folder in between.

            What gets stored is still our own re-encoded file under our own
            key: the link is a source, not a store, so the storefront never
            depends on somebody else's server staying up. */}
        {linking ? (
          <div className="mt-1 flex w-full max-w-[42ch] flex-col gap-2">
            <div className="flex gap-2">
              <input
                className="field"
                type="url"
                inputMode="url"
                value={link}
                onChange={(e) => setLink(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    // Inside somebody's form: without this, Enter saves the
                    // row and the link is lost unused.
                    e.preventDefault();
                    void fromLink();
                  }
                }}
                placeholder="https://…/photo.jpg"
                aria-label={`Link to a photo of ${label}`}
                disabled={off}
                autoFocus
              />
              <button
                type="button"
                className="btn-quiet shrink-0"
                disabled={off || !link.trim()}
                onClick={() => void fromLink()}
              >
                {busy ? "Fetching…" : "Use"}
              </button>
            </div>
            <button
              type="button"
              className="link self-start"
              onClick={() => {
                setLinking(false);
                setLink("");
              }}
            >
              cancel
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="link min-h-[32px]"
            disabled={off}
            onClick={() => setLinking(true)}
          >
            or paste a link
          </button>
        )}

        {/* Before choosing, not after: the point is to pick a photo that
            fits, and once it is uploaded the crop has already happened. */}
        <span className="max-w-[40ch] text-caption text-ink">{guide.size}</span>
        <span className="max-w-[40ch] text-caption text-muted">
          {guide.tip} JPEG, PNG or WebP.
        </span>
      </div>
      {fileInput}
    </div>
  );
}

/**
 * How many photos a form is still uploading, and the callback that keeps
 * count. A counter rather than a flag, because a list editor can have
 * several rows uploading at once and the first to finish must not release
 * Save while the others are still on their way.
 */
export function useUploadsInFlight(): [number, (busy: boolean) => void] {
  const [count, setCount] = useState(0);
  const track = useCallback((busy: boolean) => {
    setCount((n) => Math.max(0, n + (busy ? 1 : -1)));
  }, []);
  return [count, track];
}
