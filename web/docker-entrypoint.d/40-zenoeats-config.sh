#!/bin/sh
# Runtime configuration for the web container, written before nginx starts:
#
#   /etc/nginx/zenoeats/security-headers.conf   the Content-Security-Policy
#
# The official nginx image runs every executable script in
# /docker-entrypoint.d at startup. Doing this here rather than at build time
# is the point: the image carries no environment-specific value, so the image
# CI built and staging tested is byte for byte the one production runs.
#
# Environment:
#   CSP_EXTRA_IMG_SRC      optional, space-separated https:// origins images may
#                          load from, e.g. the bucket in IMAGES_PUBLIC_BASE.
#   CSP_REPORT_ONLY        "true" sends the policy as Report-Only: violations
#                          are reported in the browser console, nothing is
#                          blocked. For a first rollout on staging.
set -eu

headers_dir=/etc/nginx/zenoeats

extra_img=""
for origin in ${CSP_EXTRA_IMG_SRC:-}; do
  if ! printf '%s' "$origin" | grep -Eq '^https://[A-Za-z0-9.*-]+(:[0-9]+)?$'; then
    echo "40-zenoeats-config: CSP_EXTRA_IMG_SRC entry '$origin' is not an https origin; refusing to start" >&2
    exit 1
  fi
  extra_img="$extra_img $origin"
done

# Sources, per Stripe's and Google's published CSP requirements. Google, Apple
# and Facebook sign-in need nothing here: they are full-page redirects.
#   Stripe  js.stripe.com and *.js.stripe.com (Stripe.js and its frames);
#           hooks.stripe.com (3-D Secure); api.stripe.com; *.stripe.com images;
#           link.com and *.link.com (Link, offered by the Payment Element).
#   Google  The live delivery map on the order page (Maps JavaScript API):
#           script, tiles, fonts and API calls from *.googleapis.com,
#           *.gstatic.com and *.google.com, per Google's CSP guide.
# No 'unsafe-inline' for scripts: the built pages carry none.
google_maps="https://*.googleapis.com https://*.gstatic.com https://*.google.com"
policy="default-src 'self'"
policy="$policy; script-src 'self' https://js.stripe.com https://*.js.stripe.com $google_maps"
policy="$policy; connect-src 'self' https://api.stripe.com https://link.com https://*.link.com $google_maps data: blob:"
policy="$policy; frame-src https://js.stripe.com https://*.js.stripe.com https://hooks.stripe.com https://link.com https://*.link.com https://*.google.com"
policy="$policy; img-src 'self' data: https://*.stripe.com https://*.link.com $google_maps https://*.ggpht.com https://*.googleusercontent.com$extra_img"
policy="$policy; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com"
policy="$policy; worker-src 'self' blob:"
policy="$policy; font-src 'self' https://fonts.gstatic.com"
policy="$policy; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"

header="Content-Security-Policy"
if [ "${CSP_REPORT_ONLY:-false}" = "true" ]; then
  header="Content-Security-Policy-Report-Only"
  echo "40-zenoeats-config: CSP is report-only; nothing will be blocked" >&2
fi

mkdir -p "$headers_dir"
cat > "$headers_dir/security-headers.conf" <<EOF
# Written at container start by 40-zenoeats-config.sh. Do not edit.
add_header $header "$policy" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
EOF
