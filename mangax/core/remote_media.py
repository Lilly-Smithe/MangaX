"""Reader ve Full tarafında güvenli, sınırlı uzak görsel erişimi."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx


MAX_REDIRECTS = 5
MAX_IMAGE_BYTES = 20 * 1024 * 1024
ALLOWED_IMAGE_TYPES = frozenset({
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class UnsafeRemoteUrl(ValueError):
    pass


class RemoteResponseTooLarge(ValueError):
    pass


class RemoteContentTypeRejected(ValueError):
    pass


@dataclass(frozen=True)
class FetchedResource:
    content: bytes
    content_type: str
    final_url: str


@dataclass(frozen=True)
class ProbedResource:
    status_code: int
    final_url: str


Resolver = Callable[..., Iterable[tuple]]


def _resolved_addresses(hostname: str, port: int, resolver: Resolver) -> set:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            records = resolver(hostname, port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise UnsafeRemoteUrl("Uzak adres çözümlenemedi.") from error
        addresses = set()
        for record in records:
            try:
                addresses.add(ipaddress.ip_address(record[4][0].split("%", 1)[0]))
            except (IndexError, TypeError, ValueError):
                continue
        if not addresses:
            raise UnsafeRemoteUrl("Uzak adres çözümlenemedi.")
        return addresses
    return {literal}


def validate_public_http_url(url: str, *, resolver: Resolver = socket.getaddrinfo) -> str:
    raw = str(url or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise UnsafeRemoteUrl("Geçersiz uzak adres.") from error
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeRemoteUrl("Yalnızca public HTTP/HTTPS adresleri kullanılabilir.")
    if parsed.username or parsed.password:
        raise UnsafeRemoteUrl("Kullanıcı bilgisi içeren URL kullanılamaz.")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise UnsafeRemoteUrl("Yerel veya özel ağ adreslerine erişim reddedildi.")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise UnsafeRemoteUrl("Geçersiz uzak adres.") from error

    effective_port = port or (443 if scheme == "https" else 80)
    for address in _resolved_addresses(ascii_hostname, effective_port, resolver):
        if not address.is_global or any((
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )):
            raise UnsafeRemoteUrl("Yerel veya özel ağ adreslerine erişim reddedildi.")

    host_part = f"[{ascii_hostname}]" if ":" in ascii_hostname else ascii_hostname
    netloc = f"{host_part}:{port}" if port is not None else host_part
    return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))


def validated_redirect_url(current_url: str, location: str, *, resolver: Resolver = socket.getaddrinfo) -> str:
    if not str(location or "").strip():
        raise UnsafeRemoteUrl("Geçersiz yönlendirme hedefi.")
    return validate_public_http_url(urljoin(current_url, location), resolver=resolver)


def _validate_image_response_headers(response: httpx.Response, max_bytes: int) -> str:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise RemoteContentTypeRejected("Uzak yanıt desteklenen bir görsel değil.")
    try:
        declared_size = int(response.headers.get("content-length", "0"))
    except ValueError:
        declared_size = 0
    if declared_size > max_bytes:
        raise RemoteResponseTooLarge("Uzak görsel izin verilen boyutu aşıyor.")
    return content_type


async def fetch_public_image(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    cookies=None,
    resolver: Resolver = socket.getaddrinfo,
    transport: httpx.AsyncBaseTransport | None = None,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> FetchedResource:
    current = await asyncio.to_thread(validate_public_http_url, url, resolver=resolver)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0), follow_redirects=False, trust_env=False, transport=transport,
    ) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            current = await asyncio.to_thread(validate_public_http_url, current, resolver=resolver)
            async with client.stream("GET", current, headers=headers, cookies=cookies) as response:
                if response.status_code in REDIRECT_STATUSES:
                    if redirect_count >= MAX_REDIRECTS:
                        raise UnsafeRemoteUrl("Çok fazla yönlendirme.")
                    current = await asyncio.to_thread(
                        validated_redirect_url, current, response.headers.get("location", ""), resolver=resolver,
                    )
                    continue
                response.raise_for_status()
                content_type = _validate_image_response_headers(response, max_bytes)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise RemoteResponseTooLarge("Uzak görsel izin verilen boyutu aşıyor.")
                return FetchedResource(bytes(body), content_type, current)
    raise UnsafeRemoteUrl("Uzak görsel yönlendirmesi tamamlanamadı.")


def fetch_public_image_sync(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    cookies=None,
    resolver: Resolver = socket.getaddrinfo,
    transport: httpx.BaseTransport | None = None,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> FetchedResource:
    current = validate_public_http_url(url, resolver=resolver)
    with httpx.Client(
        timeout=httpx.Timeout(30.0), follow_redirects=False, trust_env=False, transport=transport,
    ) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            current = validate_public_http_url(current, resolver=resolver)
            with client.stream("GET", current, headers=headers, cookies=cookies) as response:
                if response.status_code in REDIRECT_STATUSES:
                    if redirect_count >= MAX_REDIRECTS:
                        raise UnsafeRemoteUrl("Çok fazla yönlendirme.")
                    current = validated_redirect_url(
                        current, response.headers.get("location", ""), resolver=resolver,
                    )
                    continue
                response.raise_for_status()
                content_type = _validate_image_response_headers(response, max_bytes)
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise RemoteResponseTooLarge("Uzak görsel izin verilen boyutu aşıyor.")
                return FetchedResource(bytes(body), content_type, current)
    raise UnsafeRemoteUrl("Uzak görsel yönlendirmesi tamamlanamadı.")


def probe_public_http_url_sync(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    resolver: Resolver = socket.getaddrinfo,
    transport: httpx.BaseTransport | None = None,
) -> ProbedResource:
    """Probe a public URL without following an unchecked redirect or reading its body."""
    current = validate_public_http_url(url, resolver=resolver)
    with httpx.Client(
        timeout=httpx.Timeout(8.0), follow_redirects=False, trust_env=False, transport=transport,
    ) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            current = validate_public_http_url(current, resolver=resolver)
            with client.stream("GET", current, headers=headers) as response:
                if response.status_code in REDIRECT_STATUSES:
                    if redirect_count >= MAX_REDIRECTS:
                        raise UnsafeRemoteUrl("Çok fazla yönlendirme.")
                    current = validated_redirect_url(
                        current, response.headers.get("location", ""), resolver=resolver,
                    )
                    continue
                return ProbedResource(response.status_code, current)
    raise UnsafeRemoteUrl("Uzak adres yönlendirmesi tamamlanamadı.")
