"""Asyncio UDP client for the Govee LAN control API.

Protocol reference: https://app-h5.govee.com/user-manual/wlan-guide

- Scan requests are sent to multicast 239.255.255.250:4001.
- Devices reply (scan results and status reports) to UDP port 4002 of the sender.
- Control commands are sent to the device IP at UDP port 4003.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass

from .const import (
    CONTROL_PORT,
    DISCOVERY_PORT,
    LISTEN_PORT,
    MULTICAST_ADDR,
    RESCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

SCAN_MESSAGE = {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}
STATUS_MESSAGE = {"msg": {"cmd": "devStatus", "data": {}}}


@dataclass
class GoveeDiscoveredDevice:
    """A device that answered a scan request."""

    ip: str
    device_id: str
    sku: str


class _Listener:
    """Registration of one entity interested in a device."""

    __slots__ = ("ip", "device_id", "state_cb", "ip_cb")

    def __init__(
        self,
        ip: str,
        device_id: str,
        state_cb: Callable[[dict | None], None],
        ip_cb: Callable[[str], None] | None,
    ) -> None:
        self.ip = ip
        self.device_id = device_id
        self.state_cb = state_cb
        self.ip_cb = ip_cb


class GoveeLanController(asyncio.DatagramProtocol):
    """Shared UDP endpoint that talks to every Govee device on the LAN."""

    def __init__(self) -> None:
        self._transport: asyncio.DatagramTransport | None = None
        self._listeners: list[_Listener] = []
        self._discovered: dict[str, GoveeDiscoveredDevice] = {}
        self._probes: dict[str, list[asyncio.Future]] = {}
        self._rescan_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ setup

    async def start(self) -> None:
        """Bind the shared listening socket. Raises OSError if 4002 is taken."""
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)
        sock.bind(("0.0.0.0", LISTEN_PORT))
        # Best effort: also receive replies addressed to the multicast group.
        try:
            mreq = socket.inet_aton(MULTICAST_ADDR) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            _LOGGER.debug("Could not join multicast group %s", MULTICAST_ADDR)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        except OSError:
            pass
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: self, sock=sock
        )
        self._rescan_task = loop.create_task(self._rescan_loop())

    def stop(self) -> None:
        """Close the socket and stop background tasks."""
        if self._rescan_task is not None:
            self._rescan_task.cancel()
            self._rescan_task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    async def _rescan_loop(self) -> None:
        """Periodically rescan so IP changes (DHCP) are picked up."""
        while True:
            await asyncio.sleep(RESCAN_INTERVAL)
            self.send_scan()

    # -------------------------------------------------------------- listeners

    def register(
        self,
        ip: str,
        device_id: str,
        state_cb: Callable[[dict | None], None],
        ip_cb: Callable[[str], None] | None = None,
    ) -> Callable[[], None]:
        """Register an entity; returns a callable that unregisters it.

        state_cb receives a parsed status dict, or None when the device was
        merely seen on the network (availability heartbeat).
        ip_cb is called with the new IP when the device shows up elsewhere.
        """
        listener = _Listener(ip, device_id, state_cb, ip_cb)
        self._listeners.append(listener)

        def unregister() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unregister

    # ---------------------------------------------------------------- receive

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        src_ip = addr[0]
        try:
            msg = json.loads(data.decode("utf-8"))["msg"]
            cmd = msg["cmd"]
            payload = msg.get("data") or {}
        except (ValueError, KeyError, UnicodeDecodeError):
            _LOGGER.debug("Malformed packet from %s: %r", src_ip, data)
            return
        if cmd == "scan":
            self._handle_scan(src_ip, payload)
        elif cmd == "devStatus":
            self._handle_status(src_ip, payload)
        else:
            _LOGGER.debug("Unhandled cmd %s from %s: %s", cmd, src_ip, payload)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("UDP error: %s", exc)

    def _handle_scan(self, src_ip: str, payload: dict) -> None:
        device_id = payload.get("device")
        if not device_id:
            return
        ip = payload.get("ip") or src_ip
        sku = payload.get("sku") or ""
        self._discovered[device_id] = GoveeDiscoveredDevice(
            ip=ip, device_id=device_id, sku=sku
        )
        for listener in self._listeners:
            if listener.device_id != device_id:
                continue
            if listener.ip != ip:
                _LOGGER.info(
                    "Govee %s moved from %s to %s", device_id, listener.ip, ip
                )
                listener.ip = ip
                if listener.ip_cb is not None:
                    listener.ip_cb(ip)
            listener.state_cb(None)  # heartbeat: device is reachable
        self._resolve_probes(ip)

    def _handle_status(self, src_ip: str, payload: dict) -> None:
        status: dict = {
            "on": payload.get("onOff") == 1,
            "brightness": payload.get("brightness"),
            "rgb": None,
            "color_temp": payload.get("colorTemInKelvin") or 0,
        }
        color = payload.get("color")
        if isinstance(color, dict):
            status["rgb"] = (
                int(color.get("r", 0)),
                int(color.get("g", 0)),
                int(color.get("b", 0)),
            )
        for listener in self._listeners:
            if listener.ip == src_ip:
                listener.state_cb(status)
        self._resolve_probes(src_ip)

    # ------------------------------------------------------------------- send

    def _send(self, obj: dict, ip: str, port: int) -> None:
        if self._transport is None or self._transport.is_closing():
            return
        self._transport.sendto(json.dumps(obj).encode("utf-8"), (ip, port))

    def send_command(self, ip: str, cmd: str, data: dict) -> None:
        self._send({"msg": {"cmd": cmd, "data": data}}, ip, CONTROL_PORT)

    def send_scan(self, ip: str | None = None) -> None:
        """Multicast scan request, or unicast to a specific IP."""
        self._send(SCAN_MESSAGE, ip or MULTICAST_ADDR, DISCOVERY_PORT)

    def request_status(self, ip: str) -> None:
        self._send(STATUS_MESSAGE, ip, CONTROL_PORT)

    def turn(self, ip: str, on: bool) -> None:
        self.send_command(ip, "turn", {"value": 1 if on else 0})

    def set_brightness(self, ip: str, percent: int) -> None:
        self.send_command(ip, "brightness", {"value": max(1, min(100, percent))})

    def set_rgb(self, ip: str, red: int, green: int, blue: int) -> None:
        self.send_command(
            ip,
            "colorwc",
            {"color": {"r": red, "g": green, "b": blue}, "colorTemInKelvin": 0},
        )

    def set_color_temp(self, ip: str, kelvin: int) -> None:
        kelvin = max(2000, min(9000, kelvin))
        self.send_command(
            ip,
            "colorwc",
            {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": kelvin},
        )

    # -------------------------------------------------------------- discovery

    async def discover(self, duration: float = 4.0) -> dict[str, GoveeDiscoveredDevice]:
        """Multicast scan and collect replies for `duration` seconds."""
        self.send_scan()
        await asyncio.sleep(duration / 2)
        self.send_scan()
        await asyncio.sleep(duration / 2)
        return dict(self._discovered)

    async def probe(
        self, ip: str, timeout: float = 3.0
    ) -> GoveeDiscoveredDevice | bool | None:
        """Check that a manually entered IP is a Govee device.

        Returns the discovered device (if it answered the scan request with
        its ID), True (if it only answered devStatus), or None on timeout.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._probes.setdefault(ip, []).append(fut)
        try:
            attempts = 3
            for _ in range(attempts):
                self.send_scan(ip)
                self.request_status(ip)
                try:
                    await asyncio.wait_for(asyncio.shield(fut), timeout / attempts)
                    break
                except asyncio.TimeoutError:
                    continue
            if not fut.done():
                return None
            result = fut.result()
            if result is True:
                # devStatus answered first; give the scan reply (which carries
                # the device ID) a moment to arrive.
                await asyncio.sleep(0.3)
                device = self._device_by_ip(ip)
                if device is not None:
                    return device
            return result
        finally:
            probes = self._probes.get(ip)
            if probes is not None:
                if fut in probes:
                    probes.remove(fut)
                if not probes:
                    del self._probes[ip]

    def _device_by_ip(self, ip: str) -> GoveeDiscoveredDevice | None:
        for device in self._discovered.values():
            if device.ip == ip:
                return device
        return None

    def _resolve_probes(self, ip: str) -> None:
        futures = self._probes.get(ip)
        if not futures:
            return
        result = self._device_by_ip(ip) or True
        for fut in futures:
            if not fut.done():
                fut.set_result(result)
