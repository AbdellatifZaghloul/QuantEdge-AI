"""Minimal HTTP transport shared by provider adapters (stdlib only, injectable for tests)."""

from __future__ import annotations

import http.client
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

CONNECT_TIMEOUT_S = 10.0
USER_AGENT = "quantedge-research/0.2"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def header(self, name: str) -> str | None:
        lowered = {k.lower(): v for k, v in self.headers.items()}
        return lowered.get(name.lower())


Transport = Callable[[str, float], HttpResponse]


class _IPv4FirstHTTPSConnection(http.client.HTTPSConnection):  # pragma: no cover - network
    """HTTPS connection that tries IPv4 addresses before IPv6, each with a short connect timeout.

    Python 3.12's ``socket.create_connection`` tries addresses sequentially with the full timeout,
    which stalls for minutes on hosts with broken IPv6 routing.
    """

    def connect(self) -> None:
        infos = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        infos.sort(key=lambda info: info[0] != socket.AF_INET)
        last_error: OSError | None = None
        for family, kind, proto, _, address in infos:
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(CONNECT_TIMEOUT_S)
                sock.connect(address)
            except OSError as exc:
                last_error = exc
                sock.close()
                continue
            sock.settimeout(self.timeout if isinstance(self.timeout, float | int) else None)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]
            return
        raise last_error or OSError(f"cannot connect to {self.host}")


class _IPv4FirstHTTPSHandler(urllib.request.HTTPSHandler):  # pragma: no cover - network
    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_IPv4FirstHTTPSConnection, req)


def ipv4_first_opener() -> urllib.request.OpenerDirector:  # pragma: no cover - network
    return urllib.request.build_opener(_IPv4FirstHTTPSHandler())


def urllib_transport(url: str, timeout: float) -> HttpResponse:  # pragma: no cover - network
    """GET ``url`` and return status, headers and body, including for HTTP error statuses."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with ipv4_first_opener().open(request, timeout=timeout) as response:
            return HttpResponse(response.status, dict(response.headers.items()), response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, dict(exc.headers.items()), exc.read())
