import { Link } from "react-router-dom";
import { Icon } from "@/components/common/icons";

/**
 * The way back, in one place.
 *
 * Checkout and the profile page each carried their own copy of this markup,
 * and the cart and order pages offered a way back only once they were empty
 * -- so a customer reading a full cart, or an order they had just placed,
 * had the browser's own back button and nothing else.
 *
 * A link rather than history.back(): where "back" goes should not depend on
 * how somebody arrived. Someone who opened their order from a confirmation
 * email has no history to go back through, and pressing a control that does
 * nothing is worse than not offering it.
 */
export function BackLink({
  to = "/",
  children = "Back to the menu",
}: {
  to?: string;
  children?: string;
}) {
  return (
    <Link
      to={to}
      className="inline-flex min-h-[40px] items-center gap-2 text-caption text-muted transition-colors duration-color hover:text-ink"
    >
      <Icon name="back" className="h-4 w-4" />
      {children}
    </Link>
  );
}
