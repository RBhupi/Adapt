# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Send a published command to the radar API: one POST per command.

The API key is read from the environment variable the config names, held as a
SecretStr, and sent only as the ``X-API-Key`` header — never written to a
file, the ledger, or an error message.
"""

import os
import urllib.error
import urllib.request

from pydantic import SecretStr

from adapt.consumers.radar_command.config import ApiConfig

ENDPOINT = "/publish/command"
_BODY_CHARS = 200  # how much of a response body an outcome may quote


class SendError(Exception):
    """The radar API did not accept the command."""


def api_key(api: ApiConfig) -> SecretStr:
    """The key from ``api.api_key_env``; ValueError naming the variable when unset or empty."""
    value = os.environ.get(api.api_key_env, "")
    if not value:
        raise ValueError(
            f"Environment variable {api.api_key_env!r} (radar_command.api.api_key_env) "
            "is not set or empty; it must hold the radar API key"
        )
    return SecretStr(value)


def send(command_json: str, api: ApiConfig, key: SecretStr) -> str:
    """POST ``command_json``; return ``HTTP <status>: <body start>`` on success.

    Raises SendError for an HTTP error status or an unreachable endpoint.
    """
    url = api.base_url.rstrip("/") + ENDPOINT
    request = urllib.request.Request(
        url,
        data=command_json.encode("utf-8"),
        headers={"X-API-Key": key.get_secret_value(), "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=api.timeout_seconds) as response:
            return f"HTTP {response.status}: {_start(response.read())}"
    except urllib.error.HTTPError as exc:
        raise SendError(f"{url} answered HTTP {exc.code}: {_start(exc.read())}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SendError(f"{url} unreachable: {reason}") from None


def _start(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")[:_BODY_CHARS]
