import type { ReactNode } from "react";
import { Cloche } from "@/components/common/icons";

/**
 * The sign-in layout, for the one credential screen that renders inside React
 * (accepting a staff invitation). The login pages themselves are separate
 * HTML entry points and write the same markup and classes by hand, so the two
 * cannot drift apart visually.
 */
export function AuthLayout({
  title,
  intro,
  operator = false,
  children,
  foot,
}: {
  title: ReactNode;
  intro?: ReactNode;
  /** Staff and platform screens: a different line on the art panel. */
  operator?: boolean;
  children: ReactNode;
  foot?: ReactNode;
}) {
  return (
    <main className="auth">
      <aside className="auth-art" aria-hidden="true">
        <Brand />
        <div>
          <Cloche className="ml-10 mt-10 scale-150" />
          <p className="auth-art-caption">
            {operator ? (
              <>
                A little order.
                <br />A better service.
              </>
            ) : (
              <>
                Good food.
                <br />A little closer.
              </>
            )}
          </p>
        </div>
        <p className="text-caption text-muted">
          {operator ? "The care behind every order." : "From their kitchen to your hands."}
        </p>
      </aside>
      <section className="auth-form">
        <Brand />
        <h1>{title}</h1>
        {intro && <p className="auth-intro">{intro}</p>}
        {children}
        {foot && <p className="auth-foot">{foot}</p>}
      </section>
    </main>
  );
}

function Brand() {
  return (
    <div className="auth-brand">
      <span className="brand-mark" aria-hidden="true">
        z
      </span>
      ZenoEats
    </div>
  );
}
