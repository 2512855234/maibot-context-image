"""Network validation for provider-returned image URLs."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from .errors import UpstreamError


def _is_forbidden(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def validate_public_https_url(
    url: str,
    *,
    block_private_networks: bool = True,
    allowed_hosts: tuple[str, ...] | list[str] = (),
) -> None:
    parsed = urlsplit(str(url))
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise UpstreamError("图片下载地址必须使用 HTTPS。")
    if parsed.username or parsed.password:
        raise UpstreamError("图片下载地址不能包含认证信息。")

    host = parsed.hostname.rstrip(".").lower()
    normalized_allowed_hosts = {
        str(item).strip().rstrip(".").lower()
        for item in allowed_hosts
        if str(item).strip()
    }
    if not block_private_networks or host in normalized_allowed_hosts:
        return
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            info = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise UpstreamError("图片下载地址无法解析。") from exc
        addresses = []
        for item in info:
            try:
                addresses.append(ipaddress.ip_address(item[4][0]))
            except ValueError:
                continue
    if not addresses or any(_is_forbidden(address) for address in addresses):
        raise UpstreamError("图片下载地址指向受限制的网络。")
