"""The email templates in app/templates/email/.

Every email is rendered here with its made-up examples, so a template edit
that breaks one -- a misspelled placeholder, a stray tag -- fails a test
rather than reaching an inbox.
"""

import jinja2
import pytest

from app.services import email_samples, email_templates

SAMPLES = email_samples.samples()


@pytest.mark.parametrize("name", email_templates.names())
def test_every_email_has_its_three_files_and_an_example(name):
    folder = email_templates.TEMPLATES_DIR / name
    for part in ("subject.txt", "email.html", "email.txt"):
        assert (folder / part).is_file(), f"{name}/{part} is missing"
    assert any(key.startswith(f"{name}/") for key in SAMPLES), (
        f"add an example of {name} to app/services/email_samples.py"
    )


@pytest.mark.parametrize("key", sorted(SAMPLES))
def test_every_example_renders(key):
    subject, body_html, body_text = SAMPLES[key]()
    assert subject and "\n" not in subject
    assert body_html.startswith("<!doctype html>")
    assert body_html.rstrip().endswith("</html>")
    assert body_text.strip()
    # Placeholders left unfilled would show as braces.
    for body in (subject, body_html, body_text):
        assert "{{" not in body and "{%" not in body


def test_a_placeholder_the_code_did_not_pass_is_an_error():
    """A misspelled name must fail loudly, not send an email with a gap."""
    with pytest.raises(jinja2.UndefinedError):
        email_templates.render("staff_invitation", restaurant_name="Spice House")


def test_only_the_html_is_escaped():
    """The subject and the plain text are not HTML; escaping them would show
    "&amp;" to the reader."""
    out = email_templates.render(
        "staff_invitation",
        restaurant_name="Tom & Jerry's", role="a <manager>",
        temporary_password=None, has_temporary_password=False,
        sign_in_url="https://tomjerry.zenoeats.com/manage/login?a=1&b=2",
    )
    assert out.subject == "You're invited to join Tom & Jerry's on Zenoeats"
    assert "Tom & Jerry's has invited you" in out.text
    assert "a <manager>" in out.text
    assert "Tom &amp; Jerry&#39;s" in out.html
    assert "a &lt;manager&gt;" in out.html
    assert "<manager>" not in out.html
    # And in an attribute, where an unescaped "&" or quote would end it.
    assert 'href="https://tomjerry.zenoeats.com/manage/login?a=1&amp;b=2"' in out.html
