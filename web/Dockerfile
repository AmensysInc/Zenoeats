# Build the SPA, then serve the result as static files. Node does not ship in
# the final image: nothing is rendered at request time, so there is no runtime
# for an attacker to reach.

# Base images pinned by digest; see backend/Dockerfile.
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS build
WORKDIR /app

# Dependencies first, so a source-only change reuses this layer.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# No build arguments, on purpose. Nothing environment-specific is baked into
# the bundle: the Clerk publishable key is read at container start (see the
# runtime stage), so the image CI builds is the image every environment runs.

# `npm run build` type-checks first. A build that compiles but fails tsc is a
# build that ships the wrong thing, so the failure belongs here.
RUN npm run build


FROM nginx:1.30-alpine@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94 AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# Runs before nginx starts and writes /config.js from CLERK_PUBLISHABLE_KEY.
COPY docker-entrypoint.d/40-zenoeats-config.sh /docker-entrypoint.d/40-zenoeats-config.sh
RUN chmod +x /docker-entrypoint.d/40-zenoeats-config.sh

# Run as nginx, not root. A foothold in this container -- a bug in nginx, a
# crafted request -- then owns nothing but the two files the entrypoint
# rewrites at start (config.js and the CSP), rather than the whole image.
# The port is 3000, so no privilege is needed to bind it; the pid file moves
# to /tmp because /var/run is root's.
RUN mkdir -p /etc/nginx/zenoeats \
 && touch /usr/share/nginx/html/config.js \
 && chown nginx:nginx /usr/share/nginx/html/config.js /etc/nginx/zenoeats \
 && chown -R nginx:nginx /var/cache/nginx \
 && sed -i -e 's#^pid .*#pid /tmp/nginx.pid;#' -e '/^user /d' /etc/nginx/nginx.conf
USER nginx

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:3000/ >/dev/null || exit 1
