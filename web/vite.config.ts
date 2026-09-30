import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

/**
 * Multi-page build, not a single SPA bundle.
 *
 * The login pages are deliberately outside React (see login/*.ts), so they get
 * their own HTML entries rather than being routes inside the app. Signing in
 * ends with a full navigation into index.html, by which point the httpOnly
 * session cookie is already set and the SPA is authenticated on first render.
 * That keeps React entirely out of the credential path instead of wrapping a
 * non-React form in a React shell.
 */
/**
 * The credential pages are published at /admin/login, /manage/login,
 * /manage/change-password and /account/* -- the URLs Guards.tsx, the sign-out
 * buttons and Google's return trip navigate to -- but their files are
 * login/*.html. nginx maps one to the other
 * in production (web/nginx.conf); the dev server has no such rule and would
 * hand these paths to the SPA fallback, which renders "Page not found".
 */
const LOGIN_URLS: Record<string, string> = {
  "/admin/login": "/login/admin-login.html",
  "/manage/login": "/login/staff-login.html",
  "/manage/change-password": "/login/change-password.html",
  "/account/sign-in": "/login/customer-sign-in.html",
  "/account/sign-up": "/login/customer-sign-up.html",
  "/account/forgot-password": "/login/customer-forgot-password.html",
  "/account/sso-callback": "/login/customer-sso-callback.html",
  // The policy pages, for the same reason and by the same route. They also
  // have to answer on the root domain, where there is no restaurant at all:
  // an OAuth reviewer at Google or Apple is given one canonical URL, and it
  // cannot be a tenant's subdomain.
  "/legal/privacy": "/legal/privacy.html",
  "/legal/terms": "/legal/terms.html",
  "/legal/refunds": "/legal/refunds.html",
  "/legal/data-deletion": "/legal/data-deletion.html",
};

function loginPageUrls(): Plugin {
  return {
    name: "zenoeats-login-urls",
    configureServer(server) {
      // Registered from configureServer itself, so it runs before Vite's html
      // and history-fallback middlewares rather than after they have already
      // answered with index.html.
      server.middlewares.use((req, _res, next) => {
        // Defaulted: noUncheckedIndexedAccess types a split element as
        // possibly undefined, and an undefined key cannot index the table.
        const [path = "", query] = (req.url ?? "").split("?");
        const file = LOGIN_URLS[path];
        if (file) req.url = query === undefined ? file : `${file}?${query}`;
        next();
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), loginPageUrls()],

  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },

  // The dep scanner only walks index.html, so the login pages' imports are
  // discovered on first visit -- a mid-session re-bundle that reloads the page
  // under you. Naming every entry here pre-bundles them once at startup.
  optimizeDeps: {
    entries: ["index.html", "login/*.html"],
  },

  build: {
    outDir: "dist",
    rollupOptions: {
      input: {
        app: fileURLToPath(new URL("./index.html", import.meta.url)),
        adminLogin: fileURLToPath(new URL("./login/admin-login.html", import.meta.url)),
        staffLogin: fileURLToPath(new URL("./login/staff-login.html", import.meta.url)),
        changePassword: fileURLToPath(
          new URL("./login/change-password.html", import.meta.url),
        ),
        customerSignIn: fileURLToPath(new URL("./login/customer-sign-in.html", import.meta.url)),
        customerSignUp: fileURLToPath(new URL("./login/customer-sign-up.html", import.meta.url)),
        customerForgotPassword: fileURLToPath(
          new URL("./login/customer-forgot-password.html", import.meta.url),
        ),
        customerSsoCallback: fileURLToPath(
          new URL("./login/customer-sso-callback.html", import.meta.url),
        ),
        legalPrivacy: fileURLToPath(new URL("./legal/privacy.html", import.meta.url)),
        legalTerms: fileURLToPath(new URL("./legal/terms.html", import.meta.url)),
        legalRefunds: fileURLToPath(new URL("./legal/refunds.html", import.meta.url)),
        legalDataDeletion: fileURLToPath(
          new URL("./legal/data-deletion.html", import.meta.url),
        ),
      },
      output: {
        // React, the router and Redux change when we upgrade them, a few
        // times a year. Our own code changes every deploy. Kept in one chunk
        // they are invalidated together, so every deploy makes returning
        // customers re-download 300 kB of libraries that did not move.
        //
        // Only the app entry pulls these in; the sign-in and policy pages
        // import nothing from node_modules, so they are unaffected.
        manualChunks(id) {
          if (id.includes("node_modules")) return "vendor";
          return undefined;
        },
      },
    },
  },

  server: {
    port: 3000,
    // Bind every interface. Vite otherwise listens on [::1] only, so IPv4
    // clients are refused -- and every restaurant subdomain resolves to
    // 127.0.0.1 through the hosts file, which is IPv4. The Next dev server
    // bound 0.0.0.0 by default, so this gap did not exist before.
    host: true,
    // Tenancy is resolved from the Host header, so every restaurant is a
    // different origin. Vite refuses unknown hosts by default.
    allowedHosts: [".zenoeats.local", ".zenoeats.com", ".stg9.zenoeats.com"],
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        // changeOrigin must stay false. The API identifies the restaurant from
        // the Host header, and rewriting it to 127.0.0.1 would make every
        // request resolve to no tenant at all. This is the same mistake the
        // Next rewrite made, which is why that setup needed a slug header.
        changeOrigin: false,
      },
      // Uploaded menu images, served by the API off its images directory.
      // Without this the dev server answers /images/* itself: there is no
      // such file in the bundle, so the SPA fallback returns index.html and
      // every <img> on the menu renders as a broken image with a 200 next to
      // it in the network tab. Proxied rather than given an absolute URL so
      // an image path stays same-origin and relative, exactly as /api does.
      "/images": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
});
