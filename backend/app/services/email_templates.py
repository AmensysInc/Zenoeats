"""The emails' words and layout, read from app/templates/email/.

Each email is a folder holding three files:

    subject.txt   the subject line
    email.html    what most mail apps show
    email.txt     the plain-text copy, for the ones that show nothing else

The code decides what goes into an email -- the order, the link, the
amounts, already formatted -- and the templates decide how it reads. Editing
a sentence or a colour therefore means editing a template, not Python.

Everything put into an .html template is HTML-escaped unless a template says
otherwise, so an item name or a restaurant name typed with a "<" in it shows
as text rather than markup. Plain text and subjects are not escaped: they are
not HTML, and escaping them would show "&amp;" to the reader.

A variable a template uses but the code did not pass is an error rather than
an empty gap, so a misspelled name fails the tests instead of reaching an
inbox as a blank.
"""

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
    undefined=StrictUndefined,
    # A tag on a line of its own leaves no blank line behind, which is what
    # keeps the plain-text copies readable as they are written.
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class Rendered:
    subject: str
    html: str
    text: str


def render(name: str, **context) -> Rendered:
    """One email, from its folder under app/templates/email/."""
    subject = _env.get_template(f"{name}/subject.txt").render(context)
    return Rendered(
        # One line whatever the template's line breaks: a newline in a
        # subject header is not allowed.
        subject=" ".join(subject.split()),
        html=_env.get_template(f"{name}/email.html").render(context).strip() + "\n",
        text=_env.get_template(f"{name}/email.txt").render(context).strip() + "\n",
    )


def names() -> list[str]:
    """Every email there is a template for."""
    return sorted(
        p.name for p in TEMPLATES_DIR.iterdir()
        if p.is_dir() and (p / "subject.txt").exists()
    )
