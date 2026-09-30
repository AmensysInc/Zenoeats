# Pinned by digest: a tag can be re-pointed at different contents by whoever
# publishes it, a digest cannot. Dependabot proposes updates.
FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Use a fixed high UID/GID in image metadata. Docker can start the process by
# numeric identity even if a host/runtime user database is unavailable, while
# the named account remains present for tools that resolve HOME or ownership.
RUN groupadd --gid 10001 zeno \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin zeno \
    && chown -R 10001:10001 /srv
USER 10001:10001

# Process settings, read by uvicorn itself and overridable per deployment
# without a rebuild.
#
#   WEB_CONCURRENCY      worker processes. One process serves one request's
#                        synchronous database work at a time per thread pool;
#                        more workers use more of the machine. Size it with
#                        the database in mind: every worker opens its own
#                        pools (DB_POOL_SIZE + DB_MAX_OVERFLOW for the app
#                        role, and up to 10 for the system role), and the sum
#                        across api and Celery must stay under Postgres'
#                        max_connections.
#
#   FORWARDED_ALLOW_IPS  the proxies whose X-Forwarded-For / -Proto uvicorn
#                        believes. Without it uvicorn trusts only 127.0.0.1,
#                        so behind nginx on a container network every request
#                        looked like plain http from nginx's own address.
#                        Keep it in step with TRUSTED_PROXY_CIDRS.
ENV WEB_CONCURRENCY=2 \
    FORWARDED_ALLOW_IPS="127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

# --timeout-keep-alive 30 stays above nginx's upstream keepalive_timeout, so
# the proxy always closes an idle connection before uvicorn does.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-keep-alive", "30"]
