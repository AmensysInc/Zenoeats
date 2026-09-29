"""Clerk's customer emails, sent through SendGrid (services/clerk_emails.py).

Clerk reports an email it did not send itself as an `email.created` webhook.
The webhook is driven here signed exactly as Svix signs it, and the queued
task is then run with SendGrid replaced by a recorder.
"""

import base64
import json
import secrets
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.config import settings
from app.core import crypto
from app.services import clerk_emails
from tests.test_notifications import sendgrid  # noqa: F401  (fixture)

integration = pytest.mark.integration

CODE = "482915"


def _event(delivered_by_clerk=False, slug="verification_code", **data):
    values = {
        "id": f"ema_{uuid.uuid4().hex[:12]}",
        "object": "email",
        "slug": slug,
        "to_email_address": "sam@example.com",
        "subject": f"{CODE} is your verification code",
        "body": f"<p>Clerk's own body with {CODE}</p>",
        "body_plain": f"Clerk's own body with {CODE}",
        "delivered_by_clerk": delivered_by_clerk,
        "data": {"otp_code": CODE, "app": {"name": "Zenoeats"}},
    }
    values.update(data)
    return {"type": "email.created", "object": "event", "data": values}


@pytest.fixture
def clerk_webhook(monkeypatch):
    """Post an event to /webhooks/clerk, signed with a fresh secret."""
    from fastapi.testclient import TestClient
    from svix.webhooks import Webhook

    from app.main import app

    secret = "whsec_" + base64.b64encode(secrets.token_bytes(24)).decode()
    monkeypatch.setattr(settings, "CLERK_WEBHOOK_SECRET", secret)
    client = TestClient(app, base_url="http://spicehouse.zenoeats.local")

    def post(event, msg_id=None):
        msg_id = msg_id or f"msg_{uuid.uuid4().hex}"
        body = json.dumps(event)
        now = datetime.now(timezone.utc)
        headers = {
            "svix-id": msg_id,
            "svix-timestamp": str(int(now.timestamp())),
            "svix-signature": Webhook(secret).sign(msg_id, now, body),
            "content-type": "application/json",
        }
        return client.post("/api/v1/webhooks/clerk", content=body, headers=headers), msg_id

    return post


def _stored(msg_id):
    from app.db.session import system_session

    with system_session() as session:
        return session.execute(
            text("SELECT status, payload FROM clerk_events WHERE clerk_event_id = :i"),
            {"i": msg_id},
        ).one()


def _queued(queued_emails):
    return [args for name, args in queued_emails if name == "send_clerk_email"]


# ------------------------------------------------------------ the webhook ---

@integration
def test_a_code_clerk_did_not_send_is_queued_sealed(clerk_webhook, queued_emails):
    res, msg_id = clerk_webhook(_event())
    assert res.status_code == 200, res.text

    [(sealed, event_id)] = _queued(queued_emails)
    assert event_id == msg_id
    # The broker keeps what it holds on disk: neither code nor address in it.
    assert CODE not in sealed and "sam@example.com" not in sealed
    assert json.loads(crypto.decrypt_field(sealed))["code"] == CODE

    # And the inbox keeps which email it was, never its contents.
    status, payload = _stored(msg_id)
    assert status == "PROCESSED"
    assert CODE not in json.dumps(payload) and "sam@example.com" not in json.dumps(payload)
    assert payload["data"]["slug"] == "verification_code"


@integration
def test_an_email_clerk_sent_itself_is_not_sent_again(clerk_webhook, queued_emails):
    res, msg_id = clerk_webhook(_event(delivered_by_clerk=True))
    assert res.status_code == 200
    assert _queued(queued_emails) == []
    assert _stored(msg_id)[0] == "IGNORED"


@integration
def test_a_redelivered_event_is_queued_once(clerk_webhook, queued_emails):
    event = _event()
    _, msg_id = clerk_webhook(event)
    res, _ = clerk_webhook(event, msg_id=msg_id)
    assert res.json().get("duplicate") is True
    assert len(_queued(queued_emails)) == 1


@integration
def test_a_queue_that_is_down_makes_clerk_deliver_it_again(clerk_webhook, monkeypatch):
    """A 200 here would lose a code the customer is waiting for."""
    from app.api.v1 import webhooks

    broker = {"up": False, "sent": []}

    def delay(*args):
        if not broker["up"]:
            raise ConnectionError("broker unreachable")
        broker["sent"].append(args)

    monkeypatch.setattr(webhooks.send_clerk_email, "delay", delay)
    event = _event()
    res, msg_id = clerk_webhook(event)
    assert res.status_code == 503
    assert broker["sent"] == []

    # Clerk's retry is accepted as new, not refused as a duplicate.
    broker["up"] = True
    res, _ = clerk_webhook(event, msg_id=msg_id)
    assert res.status_code == 200 and "duplicate" not in res.json()
    assert len(broker["sent"]) == 1

    # The FAILED row is what /health/operations reports; take it back out so
    # a development database is not left reporting a fault.
    from app.services import retention

    with retention._platform_transaction() as session:
        session.execute(
            text("DELETE FROM clerk_events WHERE clerk_event_id LIKE :failed"),
            {"failed": f"{msg_id}:unqueued:%"},
        )


# ------------------------------------------------------------- the email ---

def test_a_verification_code_is_sent_in_zenoeats_words(sendgrid):
    sealed = clerk_emails.seal(_event()["data"])
    assert clerk_emails.send(sealed, "msg_1") is True

    [call] = sendgrid.calls
    body = call["json"]
    assert body["personalizations"] == [{"to": [{"email": "sam@example.com"}]}]
    assert body["subject"] == "Your Zenoeats verification code"
    # The code is in the email, never in its subject.
    assert CODE in body["content"][0]["value"] and CODE not in body["subject"]
    assert "Clerk's own body" not in body["content"][1]["value"]


def test_a_reset_code_says_what_it_is_for(sendgrid):
    clerk_emails.send(clerk_emails.seal(_event(slug="reset_password_code")["data"]), "msg_2")
    [call] = sendgrid.calls
    assert call["json"]["subject"] == "Reset your Zenoeats password"
    assert "choose a new password" in call["json"]["content"][0]["value"]


def test_a_kind_without_a_template_goes_in_clerks_own_words(sendgrid):
    """Switching off Clerk's delivery of an email this has no template for
    must still deliver it."""
    clerk_emails.send(clerk_emails.seal(_event(
        slug="password_changed", subject="Your password was changed",
        body="<p>Clerk's own body</p>", body_plain="Clerk's own body",
        data={},
    )["data"]), "msg_3")
    [call] = sendgrid.calls
    assert call["json"]["subject"] == "Your password was changed"
    assert call["json"]["content"] == [
        {"type": "text/plain", "value": "Clerk's own body"},
        {"type": "text/html", "value": "<p>Clerk's own body</p>"},
    ]


def test_the_subject_is_never_logged(sendgrid, caplog):
    """Clerk's own subject for a code carries the code itself."""
    import logging

    caplog.set_level(logging.INFO)
    clerk_emails.send(clerk_emails.seal(_event(slug="some_new_kind", data={})["data"]), "msg_4")
    assert CODE not in caplog.text
    assert "(subject withheld)" in caplog.text
