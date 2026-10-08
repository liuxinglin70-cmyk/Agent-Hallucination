"""Loopback-only network boundary inherited by offline Python workers."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def _data_boundary(event: str, args: tuple) -> None:
    if event != "open" or os.environ.get("CODE_ONLY_VERIFICATION") != "1":
        return
    if not isinstance(args[0], (str, bytes, os.PathLike)):
        return
    path = Path(os.fsdecode(args[0])).resolve()
    for forbidden in (ROOT / "datasets/raw", ROOT / "artifacts"):
        if path.is_relative_to(forbidden) and not path.is_relative_to(ROOT / "artifacts/code_quality"):
            raise PermissionError("code_only verification cannot access raw data or historical artifacts")


def _loopback(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(str(host))
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def _network_boundary(event: str, args: tuple) -> None:
    if event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
        host = args[0]
        if host is None:
            return
    elif event in {"socket.connect", "socket.sendto"}:
        address = args[1] if event == "socket.connect" else args[-1]
        if not isinstance(address, tuple):
            return
        host = address[0]
    else:
        return
    if not _loopback(host):
        raise PermissionError("offline verification permits only loopback networking")


sys.addaudithook(_network_boundary)
sys.addaudithook(_data_boundary)
