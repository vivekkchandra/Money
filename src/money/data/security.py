"""Bounded, read-only provider transport and untrusted document handling.

The TCP connection uses the validated address, while TLS verifies the original
hostname. DNS is never resolved a second time by the connection (rebinding).
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import stat
import threading
import time
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from queue import Empty, Queue
from typing import Any
from urllib.parse import urljoin, urlsplit
from zipfile import BadZipFile, ZipFile


class SourceSecurityError(ValueError):
    """Unsafe source; never retry or disclose the original URL/credentials."""


class ProviderFailure(RuntimeError):
    def __init__(
        self, code: str, *, retryable: bool = False, http_status: int | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        # Numeric transport evidence only: never response bodies or credential URLs.
        self.http_status = (
            http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        )


def validate_url(url: str, allowed_hosts: frozenset[str]) -> tuple[str, str]:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if (
            len(url) > 4096
            or parts.scheme != "https"
            or host not in allowed_hosts
            or parts.port not in (None, 443)
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or any(ord(c) < 33 or ord(c) > 126 for c in url)
            or "\\" in url
        ):
            raise SourceSecurityError("SOURCE_URL_DENIED")
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        return host, target
    except ValueError as error:
        raise SourceSecurityError("SOURCE_URL_DENIED") from error


_DNS_SLOTS = threading.BoundedSemaphore(16)


def public_addresses(host: str, timeout_seconds: float = 5) -> tuple[str, ...]:
    # libc DNS may otherwise outlive a socket timeout. Daemon resolvers are
    # bounded globally: timed-out work never grows an unbounded thread pool.
    if not 0 < timeout_seconds <= 5 or not _DNS_SLOTS.acquire(blocking=False):
        raise ProviderFailure("PROVIDER_DNS_UNAVAILABLE", retryable=True)
    result: Queue[tuple[str, ...] | OSError] = Queue(maxsize=1)

    def resolve() -> None:
        try:
            result.put(tuple(dict.fromkeys(str(row[4][0]) for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))))
        except OSError as error:
            result.put(error)
        finally:
            _DNS_SLOTS.release()

    try:
        threading.Thread(target=resolve, daemon=True, name="money-source-dns").start()
    except RuntimeError as error:
        _DNS_SLOTS.release()
        raise ProviderFailure("PROVIDER_DNS_UNAVAILABLE", retryable=True) from error
    try:
        resolved = result.get(timeout=timeout_seconds)
    except Empty as error:
        raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True) from error
    if isinstance(resolved, OSError):
        raise ProviderFailure("PROVIDER_UNAVAILABLE", retryable=True) from resolved
    addresses = resolved
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if not parsed.is_global or parsed.is_multicast:
            raise SourceSecurityError("SOURCE_ADDRESS_DENIED")
        if isinstance(parsed, ipaddress.IPv6Address):
            embedded = parsed.ipv4_mapped or parsed.sixtofour
            if parsed in ipaddress.ip_network("64:ff9b::/96"):
                embedded = ipaddress.IPv4Address(int(parsed) & 0xFFFFFFFF)
            if (embedded and not embedded.is_global) or parsed.teredo is not None:
                raise SourceSecurityError("SOURCE_ADDRESS_DENIED")
    if not addresses:
        raise SourceSecurityError("SOURCE_ADDRESS_DENIED")
    return addresses


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float) -> None:
        self.tls_context = ssl.create_default_context()
        super().__init__(host, timeout=timeout, context=self.tls_context)
        self.address = address

    def connect(self) -> None:
        connection = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self.tls_context.wrap_socket(connection, server_hostname=self.host)
        except BaseException:
            connection.close()
            raise


@dataclass(frozen=True)
class FetchResult:
    content: bytes
    mime: str
    # Deliberately omit URL: API query strings can contain credentials.


class SafeFetcher:
    def __init__(
        self,
        allowed_hosts: frozenset[str],
        *,
        maximum_bytes: int = 2_000_000,
        timeout_seconds: float = 20,
        maximum_redirects: int = 2,
        user_agent: str = "Money-research/0.2",
    ) -> None:
        if not allowed_hosts or not 0 < maximum_bytes <= 20_000_000:
            raise ValueError("bounded source policy required")
        if not 0 < timeout_seconds <= 120 or not 0 <= maximum_redirects <= 3:
            raise ValueError("bounded source deadline required")
        if not 1 <= len(user_agent) <= 200 or any(
            ord(character) < 32 or ord(character) > 126 for character in user_agent
        ):
            raise SourceSecurityError("SOURCE_USER_AGENT_DENIED")
        self.user_agent = user_agent
        self.allowed_hosts = allowed_hosts
        self.maximum_bytes = maximum_bytes
        self.timeout_seconds = timeout_seconds
        self.maximum_redirects = maximum_redirects

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        mime_types: tuple[str, ...] = ("application/json",),
    ) -> FetchResult:
        deadline = time.monotonic() + self.timeout_seconds
        request_headers = {"Accept-Encoding": "identity", "User-Agent": self.user_agent}
        if any(
            key.lower() not in {"authorization", "accept"}
            or len(value) > 8192
            or any(ord(character) < 32 or ord(character) > 126 for character in value)
            for key, value in (headers or {}).items()
        ):
            raise SourceSecurityError("SOURCE_HEADER_DENIED")
        request_headers.update(headers or {})
        for redirect in range(self.maximum_redirects + 1):
            host, target = validate_url(url, self.allowed_hosts)
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                address = public_addresses(host, min(remaining, 5))[0]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                connection = _PinnedHTTPS(host, address, min(remaining, 5))
                header_deadline: threading.Timer | None = None
                try:
                    connection.request("GET", target, headers=request_headers)
                    assert connection.sock is not None
                    stream_socket = connection.sock
                    stream_socket.settimeout(max(0.001, deadline - time.monotonic()))

                    def interrupt_slow_headers(target_socket: socket.socket = stream_socket) -> None:
                        # A peer can drip bytes forever without an idle timeout.
                        # Shutdown interrupts getresponse's header read at the
                        # absolute deadline, even when the peer remains active.
                        try:
                            target_socket.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass

                    header_deadline = threading.Timer(
                        max(0.001, deadline - time.monotonic()), interrupt_slow_headers
                    )
                    header_deadline.daemon = True
                    header_deadline.start()
                    response = connection.getresponse()
                    if response.status in (301, 302, 303, 307, 308):
                        if redirect == self.maximum_redirects:
                            raise SourceSecurityError("SOURCE_REDIRECT_LIMIT")
                        location = response.getheader("Location")
                        if not location:
                            raise SourceSecurityError("SOURCE_REDIRECT_INVALID")
                        redirected = urljoin(url, location)
                        next_host, _ = validate_url(redirected, self.allowed_hosts)
                        if next_host != host:
                            # Never forward credentials (including query tokens) cross-origin.
                            raise SourceSecurityError("SOURCE_CROSS_ORIGIN_REDIRECT_DENIED")
                        url = redirected
                        continue
                    if response.status != 200:
                        raise ProviderFailure(
                            "PROVIDER_UNAVAILABLE",
                            retryable=response.status >= 500 or response.status == 429,
                            http_status=response.status,
                        )
                    encoding = response.getheader("Content-Encoding", "identity").lower()
                    if encoding != "identity":
                        raise SourceSecurityError("SOURCE_COMPRESSION_DENIED")
                    mime = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
                    if mime not in mime_types:
                        raise SourceSecurityError("SOURCE_MIME_DENIED")
                    length = response.getheader("Content-Length")
                    if length and (not length.isdecimal() or int(length) > self.maximum_bytes):
                        raise SourceSecurityError("SOURCE_SIZE_LIMIT")
                    chunks, size = [], 0
                    while True:
                        if time.monotonic() >= deadline:
                            raise TimeoutError
                        stream_socket.settimeout(max(0.001, deadline - time.monotonic()))
                        chunk = response.read1(min(65536, self.maximum_bytes + 1 - size))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > self.maximum_bytes:
                            raise SourceSecurityError("SOURCE_SIZE_LIMIT")
                    return FetchResult(b"".join(chunks), mime)
                finally:
                    if header_deadline:
                        header_deadline.cancel()
                    connection.close()
            except TimeoutError as error:
                raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True) from error
            except (OSError, http.client.HTTPException) as error:
                code = "PROVIDER_TIMEOUT" if time.monotonic() >= deadline else "PROVIDER_UNAVAILABLE"
                raise ProviderFailure(code, retryable=True) from error
        raise SourceSecurityError("SOURCE_REDIRECT_LIMIT")

    def json(self, url: str, *, headers: dict[str, str] | None = None) -> Any:
        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result = dict(items)
            if len(result) != len(items):
                raise SourceSecurityError("SOURCE_DUPLICATE_JSON_KEY")
            return result

        def invalid_constant(_: str) -> Any:
            raise SourceSecurityError("SOURCE_NONFINITE_JSON")

        try:
            return json.loads(
                self.get(url, headers=headers).content.decode("utf-8"),
                object_pairs_hook=pairs,
                parse_constant=invalid_constant,
            )
        except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise SourceSecurityError("SOURCE_INVALID_JSON") from error


class _TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        style = (attributes.get("style") or "").replace(" ", "").lower()
        if (
            self.hidden
            or tag in {"script", "style", "noscript", "iframe", "object", "svg", "template"}
            or "hidden" in attributes
            or "display:none" in style
            or "visibility:hidden" in style
            or attributes.get("aria-hidden") == "true"
        ):
            if tag not in {"br", "img", "input", "meta", "link", "hr"}:
                self.hidden.append(tag)
        elif tag in {"p", "br", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        elif not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def untrusted_text(value: str, *, maximum_characters: int = 12000) -> str:
    if len(value) > 200_000 or not 0 < maximum_characters <= 20000:
        raise SourceSecurityError("DOCUMENT_SIZE_LIMIT")
    parser = _TextOnly()
    parser.feed(value)
    text = unicodedata.normalize("NFKC", " ".join(parser.parts))
    text = "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))
    return " ".join(text.split())[:maximum_characters]


def bounded_zip_members(
    archive: bytes, *, maximum_expanded: int = 20_000_000
) -> tuple[tuple[str, bytes], ...]:
    if not archive or len(archive) > 10_000_000 or not 0 < maximum_expanded <= 20_000_000:
        raise SourceSecurityError("ARCHIVE_SIZE_LIMIT")
    try:
        with ZipFile(BytesIO(archive)) as bundle:
            entries = bundle.infolist()
            if len(entries) > 100 or sum(i.file_size for i in entries) > maximum_expanded:
                raise SourceSecurityError("ARCHIVE_EXPANSION_LIMIT")
            if len({i.filename for i in entries}) != len(entries):
                raise SourceSecurityError("ARCHIVE_DUPLICATE_MEMBER")
            result = []
            for item in entries:
                if item.is_dir():
                    continue
                if (
                    item.flag_bits & 1
                    or item.file_size > 5_000_000
                    or item.file_size > max(1, item.compress_size) * 100
                    or ".." in item.filename.split("/")
                    or item.filename.startswith("/")
                    or "\\" in item.filename
                    or ":" in item.filename
                    or any(ord(character) < 32 for character in item.filename)
                    or stat.S_ISLNK(item.external_attr >> 16)
                ):
                    raise SourceSecurityError("ARCHIVE_MEMBER_DENIED")
                with bundle.open(item) as member:
                    data = member.read(min(item.file_size, maximum_expanded) + 1)
                    if len(data) != item.file_size:
                        raise SourceSecurityError("ARCHIVE_SIZE_MISMATCH")
                    result.append((item.filename, data))
            return tuple(result)
    except (BadZipFile, RuntimeError, OSError) as error:
        raise SourceSecurityError("ARCHIVE_INVALID") from error
