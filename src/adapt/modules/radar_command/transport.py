# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Publish a command to the radar API: one POST per command, never repeated.

Where to publish comes from the environment, through the variables the config
names (``endpoint``): the base URL, the API key and the CA certificate file. The
key is held as a SecretStr and sent only as the ``X-API-Key`` header — never
written to a command, the store, a log or an error message. The server's
certificate is verified against that CA alone, and only for these requests.

A command counts as published only when the API answers HTTP 200 with the
acknowledgment ``status: published`` on queue ``scan_commands`` and the
submitted target back. That says the command is queued for the radar, not that
the radar ran it.
"""

import http.client
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from adapt.modules.radar_command.command import AutoTargetCommand, from_json
from adapt.modules.radar_command.config import ApiConfig

ENDPOINT = "/publish/command"
PUBLISHED, QUEUE = "published", "scan_commands"  # the acknowledgment of a queued command
_QUOTED_CHARS = 200  # how much of an answer or an error an outcome may quote
_ANSWER_BYTES = 65536  # an acknowledgment is a few hundred bytes; more is not read
_VISIBLE_ASCII = "[!-~]+"  # what a header value or an address may be made of


class SendError(Exception):
    """The radar API did not acknowledge the command as published."""


class NotSent(SendError):
    """The request did not go out: the API never received the command."""


class EndpointError(ValueError):
    """The environment does not describe a radar API endpoint that can be used."""


@dataclass(frozen=True)
class Endpoint:
    """Where one command is published and how the connection is secured."""

    url: str
    key: SecretStr
    tls: ssl.SSLContext
    timeout_seconds: float


def endpoint(api: ApiConfig) -> Endpoint:
    """The endpoint the environment describes, through the variables ``api`` names.

    Raises EndpointError naming the variable — never what it holds, except the
    CA file's path — when one is unset or empty, the base URL is not a plain
    ``https://`` address, the key is not visible ASCII, or the CA file cannot be
    read as PEM certificates.
    """
    base_url = _exported(api.base_url_env, "base_url_env", "the radar API base URL")
    if not _plain_https(base_url):
        raise EndpointError(
            f"Environment variable {api.base_url_env!r} (radar_command.api.base_url_env) must "
            "hold an https:// URL of a host with an optional port and path, and nothing else: "
            "no user or password, query, fragment or whitespace"
        )
    key = _exported(api.api_key_env, "api_key_env", "the radar API key")
    if not re.fullmatch(_VISIBLE_ASCII, key):
        raise EndpointError(
            f"Environment variable {api.api_key_env!r} (radar_command.api.api_key_env) holds a "
            "key with a space, a line end or a character that is not plain ASCII; export the "
            "key alone"
        )
    ca_file = _exported(api.ca_file_env, "ca_file_env", "the path of the CA certificate file")
    try:
        # Only this CA is trusted, for this context alone; the server's
        # certificate and host name are both checked.
        tls = ssl.create_default_context(cafile=ca_file)
    except OSError as exc:  # a missing or unreadable file, or one holding no certificate
        raise EndpointError(
            f"Environment variable {api.ca_file_env!r} (radar_command.api.ca_file_env) names "
            f"CA certificate file {ca_file!r}, which cannot be read as PEM certificates: {exc}"
        ) from exc
    return Endpoint(
        url=base_url.rstrip("/") + ENDPOINT,
        key=SecretStr(key),
        tls=tls,
        timeout_seconds=api.timeout_seconds,
    )


def _exported(variable: str, option: str, holds: str) -> str:
    value = os.environ.get(variable, "")
    if not value:
        raise EndpointError(
            f"Environment variable {variable!r} (radar_command.api.{option}) is not set "
            f"or empty; it must hold {holds}"
        )
    return value


def _plain_https(base_url: str) -> bool:
    """Whether ``base_url`` is ``https://host[:port][/path]`` and nothing more."""
    if not re.fullmatch("https://" + _VISIBLE_ASCII, base_url) or set(base_url) & set("?#@"):
        return False
    try:
        address = urllib.parse.urlsplit(base_url)
        host, _port = address.hostname, address.port  # a port that is not a number raises
    except ValueError:
        return False
    return bool(host)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an error, so the key is never sent on to another address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(command_json: str, target: Endpoint) -> str:
    """POST ``command_json`` once; return what the API acknowledged.

    Raises CommandValidationError, before any request, unless ``command_json``
    is a valid wire command; NotSent when the request did not go out; any other
    SendError when it went out and the API did not answer HTTP 200 with the
    acknowledgment of this command. Nothing is retried. Every message
    starts with what happened and ends with the address; none holds the key.
    """
    command = from_json(command_json)
    request = urllib.request.Request(
        target.url,
        data=command_json.encode("utf-8"),
        headers={"X-API-Key": target.key.get_secret_value(), "Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=target.tls), _NoRedirect
    )
    try:
        try:
            with opener.open(request, timeout=target.timeout_seconds) as response:
                status, body = response.status, response.read(_ANSWER_BYTES)
        except urllib.error.HTTPError as refusal:  # an error status, with its body
            status, body = refusal.code, refusal.read(_ANSWER_BYTES)
    except urllib.error.URLError as exc:  # the request did not go out
        raise NotSent(f"unreachable: {_shown(exc.reason, target)} ({target.url})") from None
    except (OSError, http.client.HTTPException) as exc:  # it went out; the answer was lost
        raise SendError(
            f"no readable answer ({_shown(exc, target)}) after the command was sent; "
            f"it may have been published ({target.url})"
        ) from None
    answer = _shown(body.decode("utf-8", errors="replace"), target)
    if status != 200:
        raise SendError(f"HTTP {status}: {answer} ({target.url})")
    unconfirmed = _unconfirmed(body, command)
    if unconfirmed is not None:
        raise SendError(
            f"HTTP 200, but the answer did not confirm the command as published "
            f"({unconfirmed}): {answer} ({target.url})"
        )
    return (
        f"HTTP 200: {PUBLISHED} to queue {QUEUE}; execution by the radar is not confirmed "
        f"({target.url})"
    )


def _unconfirmed(body: bytes, command: AutoTargetCommand) -> str | None:
    """Why this HTTP 200 answer is not the acknowledgment of ``command``; None when it is."""
    try:
        answer: Any = json.loads(body)
    except (ValueError, RecursionError):
        return "the answer is not JSON"
    if not isinstance(answer, dict):
        return "the answer is not a JSON object"
    if answer.get("status") != PUBLISHED:
        return f"status is not {PUBLISHED!r}"
    if answer.get("queue") != QUEUE:
        return f"queue is not {QUEUE!r}"
    message = answer.get("message")
    echoed = message.get("AutoTarget") if isinstance(message, dict) else None
    if not isinstance(echoed, dict):
        return "message holds no AutoTarget"
    differing = sorted(
        name
        for name, value in command.auto_target.model_dump().items()
        # a JSON true equals 1.0 in Python; it does not confirm a number
        if isinstance(echoed.get(name), bool) or echoed.get(name) != value
    )
    if differing:
        return f"message.AutoTarget differs from the submitted one in {', '.join(differing)}"
    return None


def _shown(text: object, target: Endpoint) -> str:
    """The start of ``text`` on one line, with the key removed should it be repeated there."""
    clean = str(text).replace(target.key.get_secret_value(), "[key]")
    return " ".join(clean.split())[:_QUOTED_CHARS]
