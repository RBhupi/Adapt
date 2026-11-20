# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Publishing a command: where the settings come from, the one request the radar
API receives, and what counts as its acknowledgment.

No test reaches a network: the radar API is stood in for at the connection, so
everything above it (the request, TLS settings, redirects, errors) is the real code.
"""

import http.client
import io
import json
import ssl
import urllib.request
from pathlib import Path

import certifi
import pytest

from adapt.modules.arm_radar_comm.command import CommandValidationError
from adapt.modules.arm_radar_comm.config import ApiConfig, RadarConfig
from adapt.modules.arm_radar_comm.transport import NotSent, SendError, endpoint, send, user_id

pytestmark = pytest.mark.unit

_API = ApiConfig(
    base_url_env="ADAPT_TEST_URL",
    api_key_env="ADAPT_TEST_KEY",
    ca_file_env="ADAPT_TEST_CA",
    timeout_seconds=30,
    max_scan_age_seconds=300,
)
_URL = "https://radar.example.test/site/publish/command"
_KEY = "k-123"
_TARGET = {
    "lat": 34.642,
    "lon": -87.25,
    "alt": 1000.0,
    "lat_extent": 320.0,
    "lon_extent": 1000.0,
    "alt_extent": 250.0,
    "user_id": "user-123",
    "target_id": "target-456",
    "scan_pattern": "rhi3_ppi7",
}
_COMMAND = json.dumps({"AutoTarget": _TARGET}, indent=2) + "\n"
_ACK = {"status": "published", "queue": "scan_commands", "message": {"AutoTarget": _TARGET}}


@pytest.fixture
def one_ca(tmp_path):
    """A CA file holding exactly one certificate (the first of the certifi bundle)."""
    end = "-----END CERTIFICATE-----"
    bundle = Path(certifi.where()).read_text(encoding="ascii")
    path = tmp_path / "ca.pem"
    path.write_text(bundle[: bundle.index(end) + len(end)] + "\n", encoding="ascii")
    return str(path)


@pytest.fixture
def exported(monkeypatch, one_ca):
    """The three variables ``_API`` names, set as a user exports them."""
    values = {
        "ADAPT_TEST_URL": "https://radar.example.test/site/",
        "ADAPT_TEST_KEY": _KEY,
        "ADAPT_TEST_CA": one_ca,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


class _Answer(io.BytesIO):
    """One HTTP response, as the connection hands it to urllib."""

    def __init__(self, status: int, body: bytes, headers: dict[str, str], fails: Exception | None):
        super().__init__(body)
        self.status = self.code = status
        self.reason = "reason"
        self.headers = headers
        self._fails = fails

    def info(self):
        return self.headers

    def read(self, *size):
        if self._fails is not None:
            raise self._fails
        return super().read(*size)


class _Radar:
    """The radar API, stood in for at the connection: keeps each request it is
    sent and answers as told (by default, the acknowledgment of ``_COMMAND``)."""

    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200
        self.body: bytes = json.dumps(_ACK).encode()
        self.headers: dict[str, str] = {}
        self.fails_on_request: Exception | None = None
        self.fails_on_response: Exception | None = None
        self.fails_on_read: Exception | None = None

    def answers(self, status: int = 200, body: object = None, **headers: str) -> None:
        self.status, self.headers = status, headers
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def connection_class(self) -> type:
        radar = self

        class Connection(http.client.HTTPSConnection):
            """What urllib asks of a connection, without ever opening a socket."""

            sock = None

            def __init__(self, host, timeout=None, context=None, **_):
                self._seen = {"host": host, "timeout": timeout, "tls": context}

            def set_debuglevel(self, level):
                pass

            def close(self):
                pass

            def request(self, method, url, body=None, headers=None, **_):
                radar.requests.append(
                    self._seen | {"method": method, "path": url, "body": body, "headers": headers}
                )
                if radar.fails_on_request is not None:
                    raise radar.fails_on_request

            def getresponse(self):
                if radar.fails_on_response is not None:
                    raise radar.fails_on_response
                return _Answer(radar.status, radar.body, radar.headers, radar.fails_on_read)

        return Connection


@pytest.fixture
def radar(monkeypatch, exported):
    stand_in = _Radar()
    monkeypatch.setattr(http.client, "HTTPSConnection", stand_in.connection_class())
    monkeypatch.setattr(urllib.request, "getproxies", dict)  # never the machine's proxy
    return stand_in


# --- where the settings come from ------------------------------------------------


def test_the_endpoint_is_read_from_the_named_variables(exported):
    target = endpoint(_API)

    assert target.url == _URL
    assert target.key.get_secret_value() == _KEY
    assert target.timeout_seconds == 30


@pytest.mark.parametrize("name", ["ADAPT_TEST_URL", "ADAPT_TEST_KEY", "ADAPT_TEST_CA"])
@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_a_missing_variable_raises_naming_it(monkeypatch, exported, name, value):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        endpoint(_API)


@pytest.mark.parametrize(
    "url",
    [
        "http://radar.example.test/site",
        "ftp://radar.example.test",
        "radar.example.test",
        "https://",
        "https://radar.example.test/site\n",
        "https://radar.example.test/si te",
        " https://radar.example.test/site",
        "https://user:hunter2@radar.example.test/site",
        "https://radar.example.test/site?token=hunter2",
        "https://radar.example.test/site#hunter2",
        "https://radar.example.test:port/site",
    ],
    ids=[
        "plain-http",
        "ftp",
        "no-scheme",
        "no-host",
        "trailing-newline",
        "space-inside",
        "leading-space",
        "user-and-password",
        "query",
        "fragment",
        "port-not-a-number",
    ],
)
def test_the_address_must_be_a_plain_https_url(monkeypatch, exported, url):
    monkeypatch.setenv("ADAPT_TEST_URL", url)

    with pytest.raises(ValueError, match="ADAPT_TEST_URL.*https://") as info:
        endpoint(_API)
    assert "hunter2" not in str(info.value)  # what the variable holds is not repeated


def test_a_port_and_a_path_are_part_of_the_address(monkeypatch, exported):
    monkeypatch.setenv("ADAPT_TEST_URL", "https://radar.example.test:8443/a/b//")

    assert endpoint(_API).url == "https://radar.example.test:8443/a/b/publish/command"


def test_the_radar_is_part_of_the_base_url(monkeypatch, exported):
    monkeypatch.setenv("ADAPT_TEST_URL", "https://apigw.example.test/bnf")

    assert endpoint(_API).url == "https://apigw.example.test/bnf/publish/command"


_RADAR = RadarConfig(scan_pattern="rhi3_ppi7", user_id_env="ADAPT_TEST_USER")


def test_the_user_id_is_what_its_variable_holds(monkeypatch):
    monkeypatch.setenv("ADAPT_TEST_USER", "adapt_ops")

    assert user_id(_RADAR) == "adapt_ops"


@pytest.mark.parametrize("value", [None, "", "two words", "line\nend"])
def test_a_user_id_variable_unset_or_unusable_is_refused_naming_it(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ADAPT_TEST_USER", raising=False)
    else:
        monkeypatch.setenv("ADAPT_TEST_USER", value)

    with pytest.raises(ValueError, match="ADAPT_TEST_USER.*arm_radar_comm.radar.user_id_env"):
        user_id(_RADAR)


@pytest.mark.parametrize(
    "key",
    ["k-123\r", "k-123\n", "   ", " k-123", "k-123 ", "k\t123", "cl\u00e9-123"],
    ids=["cr", "lf", "blank", "leading-space", "trailing-space", "tab", "not-ascii"],
)
def test_a_key_that_cannot_be_a_header_is_refused_without_showing_it(monkeypatch, exported, key):
    monkeypatch.setenv("ADAPT_TEST_KEY", key)

    with pytest.raises(ValueError, match="ADAPT_TEST_KEY") as info:
        endpoint(_API)
    assert key.strip() == "" or key.strip() not in str(info.value)


def test_a_ca_file_that_is_not_there_raises_naming_it(monkeypatch, exported, tmp_path):
    monkeypatch.setenv("ADAPT_TEST_CA", str(tmp_path / "gone.cer"))

    with pytest.raises(ValueError, match="ADAPT_TEST_CA.*gone.cer"):
        endpoint(_API)


def test_a_ca_file_that_holds_no_certificate_raises_naming_it(monkeypatch, exported, tmp_path):
    (tmp_path / "notes.cer").write_text("not a certificate\n")
    monkeypatch.setenv("ADAPT_TEST_CA", str(tmp_path / "notes.cer"))

    with pytest.raises(ValueError, match="ADAPT_TEST_CA.*notes.cer"):
        endpoint(_API)


def test_the_key_is_masked_when_the_endpoint_is_printed(exported):
    assert _KEY not in repr(endpoint(_API)) and _KEY not in str(endpoint(_API))


def test_no_setting_error_shows_the_key(monkeypatch, exported, tmp_path):
    monkeypatch.setenv("ADAPT_TEST_URL", f"http://radar.example.test/{_KEY}")
    with pytest.raises(ValueError) as wrong_url:
        endpoint(_API)

    monkeypatch.setenv("ADAPT_TEST_URL", exported["ADAPT_TEST_URL"])
    monkeypatch.setenv("ADAPT_TEST_CA", str(tmp_path / "gone.cer"))
    with pytest.raises(ValueError) as wrong_ca:
        endpoint(_API)

    assert _KEY not in str(wrong_url.value) and _KEY not in str(wrong_ca.value)


# --- TLS ----------------------------------------------------------------------------


def test_the_server_certificate_and_host_name_are_verified(exported):
    tls = endpoint(_API).tls

    assert tls.verify_mode == ssl.CERT_REQUIRED
    assert tls.check_hostname is True


def test_only_the_given_ca_is_trusted(exported):
    assert endpoint(_API).tls.cert_store_stats()["x509"] == 1


def test_the_request_is_made_with_the_endpoints_tls_settings(radar):
    target = endpoint(_API)

    send(_COMMAND, target)

    assert radar.requests[0]["tls"] is target.tls


def test_another_https_request_of_the_process_does_not_use_the_radar_ca(radar):
    target = endpoint(_API)
    send(_COMMAND, target)

    urllib.request.urlopen("https://elsewhere.example.test/tiles", timeout=1).close()

    elsewhere = radar.requests[-1]
    assert elsewhere["host"] == "elsewhere.example.test"
    assert elsewhere["tls"] is not target.tls
    assert "X-Api-Key" not in elsewhere["headers"]


# --- the request ----------------------------------------------------------------------


def test_command_is_posted_as_json_to_the_publish_endpoint(radar):
    send(_COMMAND, endpoint(_API))

    (request,) = radar.requests
    assert (request["method"], request["host"], request["path"]) == (
        "POST",
        "radar.example.test",
        "/site/publish/command",
    )
    assert request["body"] == _COMMAND.encode()
    assert request["headers"]["Content-Type"] == "application/json"
    assert request["timeout"] == 30


def test_key_travels_only_in_the_api_key_header(radar):
    send(_COMMAND, endpoint(_API))

    (request,) = radar.requests
    carrying = {name.lower(): value for name, value in request["headers"].items() if _KEY in value}
    assert carrying == {"x-api-key": _KEY}
    assert _KEY.encode() not in request["body"]
    assert _KEY not in request["path"]


@pytest.mark.parametrize(
    "command",
    [
        "not json",
        json.dumps(_TARGET),
        json.dumps({"AutoTarget": _TARGET | {"lat": True}}),
        json.dumps({"AutoTarget": _TARGET | {"extra": 1}}),
        json.dumps({"AutoTarget": {k: v for k, v in _TARGET.items() if k != "alt"}}),
        _COMMAND.replace("1000.0", "NaN"),
    ],
    ids=["not-json", "no-wrapper", "boolean-number", "extra-field", "missing-field", "nan"],
)
def test_a_command_that_is_not_valid_is_refused_before_any_request(radar, command):
    with pytest.raises(CommandValidationError):
        send(command, endpoint(_API))

    assert radar.requests == []


# --- the acknowledgment -----------------------------------------------------------


def test_publication_is_confirmed_by_the_acknowledgment(radar):
    detail = send(_COMMAND, endpoint(_API))

    assert "published" in detail and "scan_commands" in detail and "HTTP 200" in detail


def test_publication_is_not_reported_as_execution(radar):
    assert "not confirmed" in send(_COMMAND, endpoint(_API))


def test_an_acknowledgment_may_say_more_than_it_must(radar):
    radar.answers(body=_ACK | {"id": 7, "message": {"AutoTarget": _TARGET | {"seq": 1}, "at": 0}})

    assert "published" in send(_COMMAND, endpoint(_API))


@pytest.mark.parametrize(
    "body",
    [
        b"OK",
        b"",
        [_ACK],
        "published",
        _ACK | {"status": "accepted"},
        _ACK | {"queue": "other_queue"},
        {k: v for k, v in _ACK.items() if k != "status"},
        {k: v for k, v in _ACK.items() if k != "queue"},
        {k: v for k, v in _ACK.items() if k != "message"},
        _ACK | {"message": "AutoTarget"},
        _ACK | {"message": {}},
        _ACK | {"message": {"AutoTarget": _TARGET | {"lat": 34.0}}},
        _ACK | {"message": {"AutoTarget": _TARGET | {"target_id": "another"}}},
        _ACK | {"message": {"AutoTarget": {k: v for k, v in _TARGET.items() if k != "lon"}}},
    ],
    ids=[
        "not-json",
        "empty",
        "a-list",
        "a-string",
        "status-not-published",
        "another-queue",
        "no-status",
        "no-queue",
        "no-message",
        "message-not-an-object",
        "message-without-the-target",
        "another-box",
        "another-target-id",
        "target-missing-a-field",
    ],
)
def test_a_200_that_does_not_confirm_the_command_is_a_failure(radar, body):
    radar.answers(body=body)

    with pytest.raises(SendError, match=f"HTTP 200.*did not confirm.*{_URL}"):
        send(_COMMAND, endpoint(_API))


@pytest.mark.parametrize("status", [201, 202, 204])
def test_only_http_200_is_a_publication(radar, status):
    radar.answers(status=status, body=_ACK)

    with pytest.raises(SendError, match=f"HTTP {status}"):
        send(_COMMAND, endpoint(_API))


# --- failures ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 422, 500, 503])
def test_an_http_error_raises_naming_the_status_and_body(radar, status):
    radar.answers(status=status, body=b"refused: bad request")

    with pytest.raises(SendError, match=f"HTTP {status}: refused: bad request .*{_URL}"):
        send(_COMMAND, endpoint(_API))


@pytest.mark.parametrize("status", [401, 200])
def test_a_reply_that_repeats_the_key_is_not_quoted_with_it(radar, status):
    radar.answers(status=status, body=f"key {_KEY} is not allowed".encode())

    with pytest.raises(SendError) as info:
        send(_COMMAND, endpoint(_API))
    assert _KEY not in str(info.value)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_is_a_failure_and_is_not_followed(radar, status):
    radar.answers(status=status, body=b"", location="https://elsewhere.example.test/publish")

    with pytest.raises(SendError, match=f"HTTP {status}"):
        send(_COMMAND, endpoint(_API))
    assert [request["host"] for request in radar.requests] == ["radar.example.test"]


def test_an_unreachable_endpoint_raises_naming_it(radar):
    radar.fails_on_request = ConnectionRefusedError("connection refused")

    with pytest.raises(NotSent, match=f"unreachable: .*refused.*{_URL}"):
        send(_COMMAND, endpoint(_API))


def test_a_certificate_the_ca_did_not_sign_is_a_failure(radar):
    radar.fails_on_request = ssl.SSLCertVerificationError(1, "certificate verify failed")

    with pytest.raises(SendError, match="certificate verify failed"):
        send(_COMMAND, endpoint(_API))


_NO_REPLY = [
    TimeoutError("timed out"),
    http.client.RemoteDisconnected("closed without response"),
    http.client.BadStatusLine("not http"),
]


@pytest.mark.parametrize("failure", _NO_REPLY, ids=["timeout", "dropped", "not-http"])
def test_a_reply_that_never_arrives_is_a_failure_that_may_have_been_published(radar, failure):
    radar.fails_on_response = failure

    with pytest.raises(
        SendError, match=f"no readable answer.*may have been published.*{_URL}"
    ) as info:
        send(_COMMAND, endpoint(_API))
    assert "unreachable" not in str(info.value)


@pytest.mark.parametrize("status", [200, 500])
@pytest.mark.parametrize(
    "failure",
    [http.client.IncompleteRead(b"short"), TimeoutError("timed out")],
    ids=["cut-short", "timeout"],
)
def test_a_reply_whose_body_cannot_be_read_is_a_failure(radar, status, failure):
    radar.answers(status=status, body=_ACK)
    radar.fails_on_read = failure

    with pytest.raises(SendError, match="radar.example.test"):
        send(_COMMAND, endpoint(_API))


def test_a_reply_too_deep_to_read_is_not_a_confirmation(radar):
    radar.answers(body=b"[" * 200_000)

    with pytest.raises(SendError, match="did not confirm"):
        send(_COMMAND, endpoint(_API))


def test_a_reply_far_larger_than_an_acknowledgment_is_not_a_confirmation(radar):
    radar.answers(body=_ACK | {"padding": "x" * 200_000})

    with pytest.raises(SendError, match="did not confirm"):
        send(_COMMAND, endpoint(_API))


def test_a_boolean_does_not_confirm_a_number(radar):
    command = _COMMAND.replace('"alt": 1000.0', '"alt": 1.0')
    assert command != _COMMAND
    radar.answers(body=_ACK | {"message": {"AutoTarget": _TARGET | {"alt": True}}})

    with pytest.raises(SendError, match="did not confirm.*alt"):
        send(command, endpoint(_API))


@pytest.mark.parametrize("how", ["no-reply", "unreadable", "unconfirmed", "401", "500"])
def test_a_failure_after_the_request_went_out_is_not_reported_as_not_sent(radar, how):
    _failing(radar, how)

    with pytest.raises(SendError) as info:
        send(_COMMAND, endpoint(_API))
    assert not isinstance(info.value, NotSent)


def _failing(radar, how: str) -> None:
    if how == "refused":
        radar.fails_on_request = ConnectionRefusedError("connection refused")
    elif how == "no-reply":
        radar.fails_on_response = TimeoutError("timed out")
    elif how == "unreadable":
        radar.fails_on_read = http.client.IncompleteRead(b"short")
    elif how == "unconfirmed":
        radar.answers(body={"status": "accepted"})
    else:
        radar.answers(status=int(how), body=b"down")


@pytest.mark.parametrize("how", ["refused", "no-reply", "unreadable", "unconfirmed", "500", "503"])
def test_a_failed_command_is_not_sent_again(radar, how):
    _failing(radar, how)

    with pytest.raises(SendError):
        send(_COMMAND, endpoint(_API))
    assert len(radar.requests) == 1


@pytest.mark.parametrize("where", ["fails_on_request", "fails_on_response", "fails_on_read"])
def test_a_failure_that_repeats_the_key_is_not_quoted_with_it(radar, where):
    setattr(radar, where, http.client.BadStatusLine(f"HTTP/1.1 {_KEY} no"))

    with pytest.raises(SendError) as info:
        send(_COMMAND, endpoint(_API))
    assert _KEY not in str(info.value)


def test_no_failure_shows_the_key(radar):
    radar.fails_on_request = ConnectionRefusedError("connection refused")

    with pytest.raises(SendError) as info:
        send(_COMMAND, endpoint(_API))
    assert _KEY not in str(info.value) and _KEY not in repr(info.value)
