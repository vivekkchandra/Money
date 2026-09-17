"""Python capability regressions, never evidence of OS-level egress isolation."""

from __future__ import annotations

import socket
from dataclasses import dataclass

import pytest

from money.adapters.native_process import (
    BoundedNativeRunner,
    NativeProcessPolicy,
    ProviderEscapeDenied,
)
from money.schemas.contracts import Contract


class NetworkProbeResult(Contract):
    reached: bool


@dataclass(frozen=True)
class NetworkProbe:
    operation: str

    def __call__(self) -> NetworkProbeResult:
        if self.operation == "gethostbyname":
            socket.gethostbyname("localhost")
        elif self.operation == "gethostbyname_ex":
            socket.gethostbyname_ex("localhost")
        elif self.operation == "gethostbyaddr":
            socket.gethostbyaddr("127.0.0.1")
        elif self.operation == "getnameinfo":
            socket.getnameinfo(("127.0.0.1", 443), 0)
        else:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
                if self.operation == "sendto":
                    channel.sendto(b"capability-test", ("127.0.0.1", 9))
                elif self.operation == "sendmsg":
                    channel.sendmsg([b"capability-test"], [], 0, ("127.0.0.1", 9))
                elif self.operation == "connect":
                    channel.connect(("127.0.0.1", 443))
                else:
                    raise AssertionError("unknown probe")
        return NetworkProbeResult(reached=True)


@pytest.mark.parametrize("operation,reason", [
    ("gethostbyname", "DNS lookup outside"),
    ("gethostbyname_ex", "DNS lookup outside"),
    ("gethostbyaddr", "reverse DNS capability denied"),
    ("getnameinfo", "reverse DNS capability denied"),
    ("sendto", "datagram/message capability denied"),
    ("sendmsg", "datagram/message capability denied"),
])
def test_alternate_socket_interfaces_cannot_escape(operation: str, reason: str) -> None:
    runner = BoundedNativeRunner(
        NetworkProbe(operation), NetworkProbeResult, NativeProcessPolicy(timeout_seconds=20),
    )
    with pytest.raises(ProviderEscapeDenied, match=reason):
        runner()


def test_gateway_allowlist_does_not_enable_udp() -> None:
    runner = BoundedNativeRunner(
        NetworkProbe("connect"), NetworkProbeResult,
        NativeProcessPolicy(gateway_hosts=("127.0.0.1",), timeout_seconds=20),
    )
    with pytest.raises(ProviderEscapeDenied, match="network connection outside"):
        runner()
