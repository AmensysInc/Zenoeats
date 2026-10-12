"""Fetch a picture from a URL the restaurant pasted in.

The portal has always been able to upload a file. This is the other half of
the same job: most of the photography a small restaurant has is already on
the web -- its own website, a photographer's gallery, a stock library -- and
"download it, then upload it" is two steps and a Downloads folder in between.

WHAT COMES BACK IS NOT THE FILE THEY LINKED. It goes through
services/images.process the way an upload does: decoded, re-encoded as WebP,
stripped of metadata, resized for the kind it is for. So the stored file is
produced by our code rather than theirs, the key is ours, and nothing about
the remote file's name or type survives. A link is a source, never a store:
the storefront must not depend on somebody else's server staying up, or on a
URL that could be re-pointed at something else tomorrow.

THIS IS A REQUEST THE SERVER MAKES ON SOMEBODY ELSE'S BEHALF, which is the
whole security story here. An endpoint that fetches a caller's URL can be
asked to fetch things the caller could never reach: a metadata service on a
cloud instance, an admin page bound to localhost, a database's HTTP console
on the private network. So:

  * only http and https -- no file://, no gopher://, nothing that reads a
    disk or speaks a protocol we did not mean to speak
  * the hostname is resolved first and every address it resolves to has to
    be a public one. Not the first address: a name that resolves to one
    public and one private address is a name designed to get through a check
    that looks at one of them
  * redirects are followed by hand, three at most, re-checking the new host
    every time. Following them automatically would check the URL that was
    typed and fetch the one it pointed at
  * a size cap enforced while reading, not after, so a URL that streams
    forever cannot fill memory or disk
  * a short timeout, because this runs inside a request a person is waiting
    on

The residual gap is a name that passes the check and then resolves to
something private on the connection a moment later. Closing it properly
means pinning the socket to the address that was checked, which httpx does
not expose; the exposure left is a manager of an onboarded restaurant being
able to learn whether an internal host serves an image, which is a smaller
thing than this endpoint is worth.
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse, urlunparse

import httpx

from app.core import errors
from app.services import images
from app.services.images import ImageKind

log = logging.getLogger(__name__)

# The same ceiling an upload has. The limit is what we are willing to decode,
# and that does not change with where the bytes came from.
MAX_BYTES = images.MAX_UPLOAD_BYTES

# Short, because somebody is watching a spinner. A slow image host is a
# failure a manager can act on by saving the file and uploading it.
TIMEOUT_SECONDS = 10.0

# Enough for the http -> https -> CDN hop that a shared link normally makes,
# and not enough to be walked around a check.
MAX_REDIRECTS = 3

SCHEMES = ("http", "https")

# What a manager is told. Deliberately the same sentence for every reason the
# address was refused: which internal hosts exist is not something this
# endpoint should be willing to say.
UNREACHABLE = "We couldn't fetch an image from that link. Check it, or upload the file instead."


def _public(host: str) -> None:
    """Refuse a hostname unless every address it resolves to is public."""
    try:
        resolved = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise errors.validation_error(UNREACHABLE) from None

    if not resolved:
        raise errors.validation_error(UNREACHABLE)

    for family, _type, _proto, _canon, sockaddr in resolved:
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            raise errors.validation_error(UNREACHABLE) from None
        # is_global is the inverse of every range worth refusing at once --
        # loopback, link-local (169.254.169.254 among them), the private
        # blocks, multicast, reserved. Checked per address, not per name.
        if not address.is_global:
            log.warning("refused an image link resolving to %s", address)
            raise errors.validation_error(UNREACHABLE)


def _vetted(url: str) -> str:
    """The URL, if it is one we are willing to request."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        raise errors.validation_error(UNREACHABLE) from None

    if parsed.scheme not in SCHEMES or not parsed.hostname:
        raise errors.validation_error(
            "Paste a link starting with http:// or https://."
        )
    # Credentials in the URL are a sign of a link that was not meant to be
    # shared, and they would travel in our request.
    if parsed.username or parsed.password:
        raise errors.validation_error(UNREACHABLE)

    _public(parsed.hostname)
    # Rebuilt from the parsed parts, so nothing odd in the original string
    # reaches the client.
    return urlunparse(parsed)


def fetch(url: str, kind: ImageKind, restaurant_id) -> dict:
    """Store the picture at `url` as this restaurant's, and say where it is.

    The return is the same shape POST /restaurant/images answers with, on
    purpose: every picker in the portal already knows how to read it, so a
    linked photo and an uploaded one are the same thing from there on.
    """
    target = _vetted(url)

    data = b""
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            try:
                # Streamed, so the cap below is a cap on what is read rather
                # than a complaint about what has already been read. A URL
                # that serves an endless body is the case that matters, and
                # it never advertises a length to check instead.
                with client.stream("GET", target, headers={"Accept": "image/*"}) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise errors.validation_error(UNREACHABLE)
                        # Resolved against the URL it came from, then vetted
                        # again: the check automatic redirects would skip.
                        target = _vetted(str(response.url.join(location)))
                        continue

                    if response.status_code != 200:
                        raise errors.validation_error(UNREACHABLE)

                    for chunk in response.iter_bytes():
                        data += chunk
                        if len(data) > MAX_BYTES:
                            # Hung up on mid-body by leaving the block.
                            raise errors.ApiError(
                                413, "IMAGE_TOO_LARGE",
                                "That image is larger than 8 MB. Choose a smaller one.",
                            )
            except httpx.HTTPError as exc:
                log.info("could not fetch an image link: %s", exc)
                raise errors.validation_error(UNREACHABLE) from exc
            break
        else:
            # Still being redirected after the last allowed hop.
            raise errors.validation_error(UNREACHABLE)

    # From here it is indistinguishable from an upload, including the refusal
    # a file that is not a picture gets.
    picture = images.process(data, kind)
    key = images.new_key(restaurant_id, kind)
    images.storage().save(key, picture)
    log.info("stored %s image %s from a link (%d bytes)", kind.value, key, len(picture))
    return {"image_path": key, "image_url": images.image_url(key)}
