"""Outbound delivery for notifications (P8.8-A): the email port and the webhook transport.

Email (``EmailPort``): ``none`` (default; the tick records ``skipped_disabled`` and never calls an
adapter), ``smtp`` (stdlib ``smtplib``, STARTTLS required, any SMTP relay) and ``postmark``
(HTTPS API through ``httpx`` with ``trust_env=False``). Credentials come from the environment
(``Settings``), are never logged and never appear in a result: an adapter returns ``None`` or a
closed error code, never exception text. No retries inside an adapter (the outbox retries).

Webhooks (``deliver_webhook``): https only, no userinfo, allowed ports only (443 by default),
hostnames must be DNS names (no numeric tricks such as ``0x7f.1`` or ``2130706433``). The host is
resolved ONCE; every resolved address must be public (no loopback, RFC 1918, CGNAT, link-local
incl. cloud metadata, unique-local, multicast, reserved, IPv4-mapped/NAT64/6to4 forms), and the
connection goes to that pinned address with SNI, certificate check and ``Host`` of the original
name (a DNS answer changed between check and connect is never used). The lookup runs in a small
bounded thread pool inside the delivery's deadline, for ``host.`` (fully qualified: no resolver
search domains), at most one lookup in flight per workspace (``dns_key``); a lookup that never got
a thread, or a workspace whose earlier lookup is still running, answers ``dns_busy`` (not an
attempt: the row waits). ``http.client`` follows no redirects and reads no proxy settings. 3 s to
resolve and to connect, 5 s in total (a timer closes the socket), a request body of at most
16 KiB, at most 4 KiB of the response read, none kept.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import re
import smtplib
import socket
import ssl
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import formatdate, make_msgid
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.domain.notifications import (
    SIGNATURE_SCHEME,
    WEBHOOK_BODY_MAX_BYTES,
    WEBHOOK_CONNECT_TIMEOUT_S,
    WEBHOOK_RESPONSE_READ_MAX,
    WEBHOOK_TOTAL_TIMEOUT_S,
    WEBHOOK_URL_MAX,
    clean_untrusted_text,
)

EMAIL_TIMEOUT_S = 10.0
POSTMARK_URL = "https://api.postmarkapp.com/email"
_ADDRESS_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


# --- email --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str


class EmailPort(Protocol):
    def send(self, email: OutgoingEmail, *, timeout: float = EMAIL_TIMEOUT_S) -> str | None:
        """Send one message: ``None`` when the provider accepted it, else a closed error code."""


def valid_address(value: str | None) -> bool:
    return bool(value) and _ADDRESS_RE.fullmatch(value or "") is not None


def build_message(sender: str, email: OutgoingEmail) -> EmailMessage:
    """Plain text only. Header values are cleaned (no CR/LF can survive) and the SMTP policy
    refuses any line break in a header as a second line of defence."""

    if not (valid_address(sender) and valid_address(email.to)):
        raise ValueError("invalid address")
    message = EmailMessage(policy=SMTP_POLICY)
    message["From"] = sender
    message["To"] = email.to
    message["Subject"] = clean_untrusted_text(email.subject, 160)
    message["Auto-Submitted"] = "auto-generated"
    message["Date"] = formatdate(usegmt=True)
    message["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2])
    message.set_content(email.text)
    return message


class SmtpEmailAdapter:
    def __init__(self, *, host: str, port: int, username: str, password: str, sender: str,
                 smtp_factory: Callable[..., Any] = smtplib.SMTP) -> None:
        self._host, self._port, self._username, self._password = host, port, username, password
        self._sender, self._factory = sender, smtp_factory

    def send(self, email: OutgoingEmail, *, timeout: float = EMAIL_TIMEOUT_S) -> str | None:
        try:
            message = build_message(self._sender, email)
        except ValueError:
            return "recipient_unavailable"
        smtp = None
        try:
            smtp = self._factory(self._host, self._port, timeout=max(0.5, timeout))
            smtp.ehlo()
            if not smtp.has_extn("starttls"):
                return "tls_failed"  # never send credentials or content in clear text
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            if self._username:
                smtp.login(self._username, self._password)
            smtp.send_message(message)
            return None  # accepted; a failing QUIT below changes nothing
        except smtplib.SMTPAuthenticationError:
            return "auth_failed"
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
            return "provider_rejected"
        except smtplib.SMTPException:
            return "provider_unavailable"
        except ssl.SSLError:
            return "tls_failed"
        except TimeoutError:
            return "timeout"
        except OSError:
            return "connect_failed"
        finally:
            if smtp is not None:  # QUIT after the server accepted the message never makes it a failure
                try:
                    smtp.quit()
                except Exception:  # noqa: BLE001
                    try:
                        smtp.close()
                    except Exception:  # noqa: BLE001
                        pass


class PostmarkEmailAdapter:
    def __init__(self, *, token: str, sender: str, client_factory: Callable[[], Any] | None = None) -> None:
        self._token, self._sender, self._client_factory = token, sender, client_factory

    def _client(self, timeout: float) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        import httpx

        return httpx.Client(trust_env=False, follow_redirects=False,
                            timeout=httpx.Timeout(max(0.5, timeout), connect=min(5.0, max(0.5, timeout))))

    def send(self, email: OutgoingEmail, *, timeout: float = EMAIL_TIMEOUT_S) -> str | None:
        import httpx

        if not (valid_address(self._sender) and valid_address(email.to)):
            return "recipient_unavailable"
        body = {"From": self._sender, "To": email.to, "Subject": clean_untrusted_text(email.subject, 160),
                "TextBody": email.text, "MessageStream": "outbound"}
        try:
            with self._client(timeout) as client:
                response = client.post(POSTMARK_URL, json=body, headers={
                    "Accept": "application/json", "X-Postmark-Server-Token": self._token})
        except httpx.TimeoutException:
            return "timeout"
        except httpx.HTTPError:
            return "connect_failed"
        status = int(response.status_code)
        if 200 <= status < 300:
            return None
        if status in (401, 403):
            return "auth_failed"
        if status == 422:
            return "provider_rejected"
        return "provider_unavailable"


class _UnconfiguredEmail:
    def send(self, email: OutgoingEmail, *, timeout: float = EMAIL_TIMEOUT_S) -> str | None:
        return "not_configured"


def email_adapter(settings: Any) -> EmailPort | None:
    """The configured adapter; ``None`` for ``EMAIL_PROVIDER=none`` (nothing is ever sent)."""

    provider = str(settings.email_provider or "none").strip().lower()
    sender = str(settings.email_from_address or "").strip()
    if provider == "smtp":
        if not settings.smtp_host.strip() or not valid_address(sender):
            return _UnconfiguredEmail()
        return SmtpEmailAdapter(host=settings.smtp_host.strip(), port=int(settings.smtp_port),
                                username=settings.smtp_username.strip(),
                                password=settings.smtp_password.get_secret_value(), sender=sender)
    if provider == "postmark":
        token = settings.postmark_server_token.get_secret_value().strip()
        if not token or not valid_address(sender):
            return _UnconfiguredEmail()
        return PostmarkEmailAdapter(token=token, sender=sender)
    return None


# --- webhooks: destination checks ----------------------------------------------------------------


class WebhookUrlError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_TLD = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
_BLOCKED_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4",
    "::/128", "::1/128", "64:ff9b::/96", "64:ff9b:1::/48", "2001::/32", "2002::/16", "fc00::/7", "fe80::/10",
    "fec0::/10", "ff00::/8",
))


@dataclass(frozen=True)
class WebhookTarget:
    host: str
    port: int
    path: str
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None  # the host when it is an IP literal


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return is_public_ip(ip.ipv4_mapped)
    if (not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified):
        return False
    return not any(ip in net for net in _BLOCKED_NETWORKS if net.version == ip.version)


def allowed_ports(raw: str) -> frozenset[int]:
    ports = set()
    for part in str(raw or "").split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= 65535:
            ports.add(int(part))
    return frozenset(ports or {443})


def validate_webhook_url(url: str, ports: frozenset[int]) -> WebhookTarget:
    """Syntax and destination rules that need no DNS (the resolved address is checked at send)."""

    if not isinstance(url, str) or not 9 <= len(url) <= WEBHOOK_URL_MAX or any(
            ord(ch) <= 0x20 or ord(ch) >= 0x7f or ch == "\\" for ch in url):
        raise WebhookUrlError("invalid_webhook_url", "the URL must be a plain https URL (no spaces or non-ASCII)")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise WebhookUrlError("invalid_webhook_url", "webhooks are sent over https only")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise WebhookUrlError("invalid_webhook_url", "the URL must not contain a user name or password")
    if "%" in parts.netloc:
        raise WebhookUrlError("invalid_webhook_url", "the host must not contain '%' (no scoped or encoded hosts)")
    if parts.fragment:
        raise WebhookUrlError("invalid_webhook_url", "the URL must not contain a #fragment")
    try:
        port = parts.port if parts.port is not None else 443
    except ValueError as exc:
        raise WebhookUrlError("invalid_webhook_url", "the URL's port is not a number") from exc
    if port not in ports:
        raise WebhookUrlError("invalid_webhook_url", f"port {port} is not allowed (allowed: {sorted(ports)})")
    host = (parts.hostname or "").rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not is_public_ip(literal):
            raise WebhookUrlError("destination_refused", "the URL points to a private or local address")
    else:
        labels = host.split(".")
        if len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels) or not _TLD.fullmatch(labels[-1]):
            raise WebhookUrlError("invalid_webhook_url", "the host must be a DNS name such as hooks.example.com")
        if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
            raise WebhookUrlError("destination_refused", "the URL points to a private or local name")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return WebhookTarget(host=host, port=port, path=path, ip=literal)


class DeliveryError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


Resolver = Callable[..., list[Any]]
# getaddrinfo cannot be cancelled: lookups run here, bounded (a stuck lookup holds one thread until
# the system resolver gives up; further lookups queue and time out instead of piling up threads).
_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dclab-webhook-dns")
_DNS_INFLIGHT: dict[str, Any] = {}  # dns_key (workspace) -> its running lookup
_DNS_LOCK = threading.RLock()  # re-entrant: a lookup already done runs its callback in the submitting thread


def _forget(key: str, done: Any) -> None:
    with _DNS_LOCK:
        if _DNS_INFLIGHT.get(key) is done:
            del _DNS_INFLIGHT[key]


def resolve_public(target: WebhookTarget, resolver: Resolver = socket.getaddrinfo,
                   timeout: float = WEBHOOK_CONNECT_TIMEOUT_S, dns_key: str | None = None) -> str:
    """The one address to connect to: every answer must be public (a mixed answer is refused).
    The name is looked up fully qualified (``host.``) within ``timeout`` seconds. ``dns_busy``:
    the lookup never started (pool saturated) or ``dns_key`` already has one running."""

    if target.ip is not None:
        return str(target.ip)
    with _DNS_LOCK:
        running = _DNS_INFLIGHT.get(dns_key) if dns_key is not None else None
        if running is not None and not running.done():
            raise DeliveryError("dns_busy")
        future = _DNS_POOL.submit(resolver, target.host + ".", target.port, 0, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        if dns_key is not None:
            _DNS_INFLIGHT[dns_key] = future
            future.add_done_callback(lambda done, key=dns_key: _forget(key, done))
    try:
        infos = future.result(timeout=max(0.05, timeout))
    except FutureTimeout as exc:
        raise DeliveryError("dns_busy" if future.cancel() else "timeout") from exc
    except (OSError, UnicodeError) as exc:
        raise DeliveryError("dns_failed") from exc
    addresses = []
    for info in infos or []:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except (ValueError, IndexError, TypeError) as exc:
            raise DeliveryError("destination_refused") from exc
        if not is_public_ip(ip):
            raise DeliveryError("destination_refused")
        addresses.append(ip)
    if not addresses:
        raise DeliveryError("dns_failed")
    return str(addresses[0])


# --- webhooks: signing and transport -------------------------------------------------------------


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """``X-DCLab-Signature``: ``v1=`` + hex HMAC-SHA256 over ``"{timestamp}.{body}"``."""

    mac = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_SCHEME}={mac}"


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to the pinned (validated) address, never re-resolving the name; TLS uses the
    original name for SNI and certificate checks, and ``Host`` carries it too."""

    def __init__(self, host: str, port: int, ip: str, *, timeout: float, context: ssl.SSLContext) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


@dataclass(frozen=True)
class WebhookResult:
    status_code: int | None
    error_code: str | None
    duration_ms: int


def _tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def deliver_webhook(url: str, body: bytes, headers: dict[str, str], *, ports: frozenset[int],
                    deadline: float = WEBHOOK_TOTAL_TIMEOUT_S, resolver: Resolver = socket.getaddrinfo,
                    connection_factory: Callable[..., Any] = PinnedHTTPSConnection,
                    dns_key: str | None = None) -> WebhookResult:
    """One attempt within ``deadline`` seconds in total (DNS, connect, TLS, request, response)."""

    started = time.monotonic()
    deadline = max(0.1, min(float(deadline), WEBHOOK_TOTAL_TIMEOUT_S))

    def left() -> float:
        return deadline - (time.monotonic() - started)

    def result(status: int | None = None, error: str | None = None) -> WebhookResult:
        return WebhookResult(status, error, max(0, int((time.monotonic() - started) * 1000)))

    if len(body) > WEBHOOK_BODY_MAX_BYTES:
        return result(error="body_too_large")
    try:
        target = validate_webhook_url(url, ports)
        ip = resolve_public(target, resolver, timeout=min(WEBHOOK_CONNECT_TIMEOUT_S, left()), dns_key=dns_key)
    except WebhookUrlError:
        return result(error="destination_refused")
    except DeliveryError as exc:
        return result(error=exc.code)
    if left() <= 0.05:
        return result(error="timeout")
    connection = connection_factory(target.host, target.port, ip, timeout=min(WEBHOOK_CONNECT_TIMEOUT_S, left()),
                                    context=_tls_context())
    fired = threading.Event()

    def abort() -> None:  # the total deadline: unblock whatever the connection is waiting on
        fired.set()
        sock = getattr(connection, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    timer = threading.Timer(max(0.05, left()), abort)
    timer.daemon = True
    timer.start()
    status: int | None = None
    try:
        connection.connect()
        sock = getattr(connection, "sock", None)
        if sock is not None:
            sock.settimeout(max(0.05, left()))
        connection.request("POST", target.path, body=body, headers=headers)
        response = connection.getresponse()
        status = int(response.status)
        try:
            response.read(WEBHOOK_RESPONSE_READ_MAX)  # bounded, discarded: never stored or logged
        except (OSError, http.client.HTTPException):
            pass
    except (ssl.SSLError, ssl.CertificateError):
        return result(error="timeout" if fired.is_set() else "tls_failed")
    except TimeoutError:
        return result(error="timeout")
    except http.client.HTTPException:
        return result(error="timeout" if fired.is_set() else "invalid_response")
    except OSError:
        return result(error="timeout" if fired.is_set() else "connect_failed")
    finally:
        timer.cancel()
        try:
            connection.close()
        except Exception:  # noqa: BLE001 - closing never changes the outcome
            pass
    if 200 <= status < 300:
        return result(status)
    if 300 <= status < 400:
        return result(status, "redirect_not_followed")
    return result(status, "http_error")
