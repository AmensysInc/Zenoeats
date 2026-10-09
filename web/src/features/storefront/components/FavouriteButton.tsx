import { useState } from "react";
import { Icon } from "@/components/common/icons";
import { signInHref } from "../session";
import {
  useCustomerSessionQuery,
  useFavouritesQuery,
  useSetFavouriteMutation,
} from "@/features/storefront/storefrontApi";

/**
 * Whether this visitor can keep favourites, and which items they have.
 *
 * Saving one needs an account: a guest session ends with the browser, and a
 * list that vanished with it would be worse than none. Everyone else still
 * sees the heart, and tapping it is the invitation to sign in.
 */
export function useFavourites() {
  const { data: session } = useCustomerSessionQuery({ soft: true });
  const canSave = !!session && !session.is_guest;
  const { data } = useFavouritesQuery(undefined, { skip: !canSave });
  return {
    canSave,
    savedIds: new Set((data ?? []).map((f) => f.item_id)),
  };
}

/** Back to this page after signing in, so the heart is one more tap away. */
export function FavouriteButton({
  itemId,
  name,
  saved,
  canSave,
  className = "",
}: {
  itemId: string;
  name: string;
  saved: boolean;
  canSave: boolean;
  className?: string;
}) {
  const [setFavourite] = useSetFavouriteMutation();
  const [failed, setFailed] = useState(false);

  const label = canSave
    ? saved
      ? `Remove ${name} from favourites`
      : `Save ${name} to favourites`
    : `Sign in to save ${name} to favourites`;

  return (
    <button
      type="button"
      className={`inline-flex h-10 w-10 items-center justify-center rounded-full transition-colors duration-color ease-standard hover:bg-ink/5 ${
        saved ? "text-brick" : "text-muted hover:text-ink"
      } ${className}`}
      aria-label={label}
      aria-pressed={canSave ? saved : undefined}
      title={failed ? "That didn't save. Try again." : label}
      onClick={() => {
        if (!canSave) {
          window.location.assign(signInHref());
          return;
        }
        setFailed(false);
        setFavourite({ itemId, name, saved: !saved })
          .unwrap()
          .catch(() => setFailed(true));
      }}
    >
      <Icon name="heart" className={`h-5 w-5 ${saved ? "fill-current" : ""}`} />
    </button>
  );
}
