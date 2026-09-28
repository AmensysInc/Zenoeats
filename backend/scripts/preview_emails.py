"""Render every email with made-up data, to look at after editing a template.

    python scripts/preview_emails.py            writes to backend/email-previews/
    python scripts/preview_emails.py some/dir   writes there instead

Open index.html in the folder it prints. Each email is there as the HTML
most mail apps show and the plain-text copy the rest do. Nothing is sent.

The templates are in app/templates/email/; the made-up data is in
app/services/email_samples.py.
"""

import html
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.email_samples import samples  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else BACKEND / "email-previews"
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for key, make in samples().items():
        subject, body_html, body_text = make()
        stem = key.replace("/", "--")
        (out / f"{stem}.html").write_text(body_html, encoding="utf-8")
        (out / f"{stem}.txt").write_text(body_text, encoding="utf-8")
        rows.append(
            f"<tr><td>{html.escape(key)}</td><td>{html.escape(subject)}</td>"
            f"<td><a href=\"{stem}.html\">HTML</a> &middot; <a href=\"{stem}.txt\">text</a></td></tr>"
        )

    (out / "index.html").write_text(
        "<!doctype html><meta charset=\"utf-8\"><title>Email previews</title>"
        "<style>body{font:15px system-ui,sans-serif;margin:32px}td,th{padding:6px 14px;"
        "text-align:left;border-bottom:1px solid #ddd}</style>"
        "<h1>Email previews</h1><table><tr><th>Email</th><th>Subject</th><th></th></tr>"
        + "".join(rows) + "</table>",
        encoding="utf-8",
    )
    print(f"{len(rows)} emails written. Open {out / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
