"""Read-only edge latency check: python scripts/check_storefront.py --slug spicehouse.

Run after Docker startup or recreation. Never submits an order or prints keys.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import ssl
import statistics
import time
from urllib.request import urlopen
from urllib.error import HTTPError

# Made by scripts/local_https_cert.sh; it signs the edge's certificate.
LOCAL_CA = Path.home() / "zenoeats-secrets" / "local-ca" / "ca.crt"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument(
        "--base-url", help="default https://<slug>.zenoeats.local:8443 (needs the hosts entry)"
    )
    parser.add_argument("--ca", default=str(LOCAL_CA), help="certificate authority to trust")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()
    if args.requests < 1 or args.timeout <= 0:
        parser.error("requests and timeout must be positive")
    # By name, not 127.0.0.1: the edge answers only names its certificate
    # covers, and refuses the TLS handshake for anything else.
    base_url = (args.base_url or f"https://{args.slug}.zenoeats.local:8443").rstrip("/")
    tls = ssl.create_default_context(cafile=args.ca)

    def check(index):
        path = ("/portal", "/menu")[index % 2]
        started = time.monotonic()
        try:
            with urlopen(base_url + "/api/v1" + path, timeout=args.timeout, context=tls) as response:
                response.read()
                status = str(response.status)
        except HTTPError as exc:
            status = str(exc.code)
            exc.close()
        except Exception as exc:
            status = type(exc).__name__
        elapsed = time.monotonic() - started
        return status == "200" and elapsed < args.timeout, elapsed, path, status

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, range(args.requests)))
    failures = [r for r in results if not r[0]]
    print(f"{len(results)} requests; {len(failures)} failures; "
          f"median {statistics.median(r[1] for r in results):.3f}s; "
          f"max {max(r[1] for r in results):.3f}s")
    for _, seconds, path, status in failures:
        print(f"{path}: {status} ({seconds:.3f}s)")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
