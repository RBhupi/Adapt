# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Sending a command: the request the radar API receives, and what failures say."""

import io
import json
import urllib.error
import urllib.request

import pytest
from pydantic import SecretStr

from adapt.consumers.radar_command.config import ApiConfig
from adapt.consumers.radar_command.transport import SendError, api_key, send

pytestmark = pytest.mark.unit

_API = ApiConfig(
    base_url="https://radar.example.test/sandbox/",
    api_key_env="ADAPT_TEST_RADAR_KEY",
    timeout_seconds=30,
)
_KEY = SecretStr("k-123")
_PAYLOAD = json.dumps({"lat": 34.6, "scan_pattern": "3rhi_7ppi"})


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def received(monkeypatch):
    """Every request urlopen is asked to make, answered with a 200."""
    requests = []

    def urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response(b'{"status": "accepted"}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return requests


def test_command_is_posted_as_json_to_the_publish_endpoint(received):
    send(_PAYLOAD, _API, _KEY)

    ((request, timeout),) = received
    assert request.get_method() == "POST"
    assert request.full_url == "https://radar.example.test/sandbox/publish/command"
    assert json.loads(request.data) == json.loads(_PAYLOAD)
    assert request.get_header("Content-type") == "application/json"
    assert timeout == 30


def test_key_travels_only_in_the_api_key_header(received):
    send(_PAYLOAD, _API, _KEY)

    ((request, _),) = received
    assert request.get_header("X-api-key") == "k-123"
    assert b"k-123" not in request.data


def test_success_reports_the_status_and_the_start_of_the_body(received):
    assert send(_PAYLOAD, _API, _KEY) == 'HTTP 200: {"status": "accepted"}'


def _failing(monkeypatch, exc):
    def urlopen(request, timeout):
        raise exc

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


def test_an_http_error_raises_naming_the_status_and_body_but_not_the_key(monkeypatch):
    url = "https://radar.example.test/sandbox/publish/command"
    _failing(monkeypatch, urllib.error.HTTPError(url, 401, "no", {}, io.BytesIO(b"bad key")))

    with pytest.raises(SendError, match="HTTP 401: bad key") as info:
        send(_PAYLOAD, _API, _KEY)
    assert "k-123" not in str(info.value)


def test_an_unreachable_endpoint_raises_naming_it(monkeypatch):
    _failing(monkeypatch, urllib.error.URLError("connection refused"))

    with pytest.raises(SendError, match="radar.example.test.*unreachable.*refused"):
        send(_PAYLOAD, _API, _KEY)


def test_api_key_is_read_from_the_named_variable(monkeypatch):
    monkeypatch.setenv("ADAPT_TEST_RADAR_KEY", "k-123")
    assert api_key(_API).get_secret_value() == "k-123"


def test_api_key_is_masked_when_printed(monkeypatch):
    monkeypatch.setenv("ADAPT_TEST_RADAR_KEY", "k-123")
    assert "k-123" not in repr(api_key(_API)) and "k-123" not in str(api_key(_API))


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_missing_api_key_raises_naming_the_variable(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ADAPT_TEST_RADAR_KEY", raising=False)
    else:
        monkeypatch.setenv("ADAPT_TEST_RADAR_KEY", value)

    with pytest.raises(ValueError, match="ADAPT_TEST_RADAR_KEY"):
        api_key(_API)
