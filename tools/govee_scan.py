#!/usr/bin/env python3
"""Standalone Govee LAN tester — works without Home Assistant.

Run it on a computer in the same network as the lamps:

    python govee_scan.py                     # discover lamps (5 s)
    python govee_scan.py --ip 192.168.1.50 --status
    python govee_scan.py --ip 192.168.1.50 --on
    python govee_scan.py --ip 192.168.1.50 --off
    python govee_scan.py --ip 192.168.1.50 --brightness 50
    python govee_scan.py --ip 192.168.1.50 --color 255 0 0
    python govee_scan.py --ip 192.168.1.50 --kelvin 4000

Protocol: https://app-h5.govee.com/user-manual/wlan-guide
"""
import argparse
import json
import socket
import sys
import time

MULTICAST = ("239.255.255.250", 4001)
LISTEN_PORT = 4002
CONTROL_PORT = 4003


def open_listener() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", LISTEN_PORT))
    except OSError as err:
        sys.exit(
            f"Cannot bind UDP port {LISTEN_PORT}: {err}\n"
            "Is Home Assistant or another Govee tool running on this machine?"
        )
    try:
        mreq = socket.inet_aton(MULTICAST[0]) + socket.inet_aton("0.0.0.0")
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError:
        pass
    sock.settimeout(0.5)
    return sock


def send(sock: socket.socket, cmd: str, data: dict, addr) -> None:
    sock.sendto(json.dumps({"msg": {"cmd": cmd, "data": data}}).encode(), addr)


def collect(sock: socket.socket, duration: float):
    end = time.time() + duration
    while time.time() < end:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        try:
            msg = json.loads(data.decode())["msg"]
        except (ValueError, KeyError):
            continue
        yield addr[0], msg.get("cmd"), msg.get("data") or {}


def cmd_scan(sock: socket.socket, duration: float) -> None:
    print(f"Scanning for {duration:.0f} s (multicast {MULTICAST[0]}:{MULTICAST[1]})...")
    send(sock, "scan", {"account_topic": "reserve"}, MULTICAST)
    found = {}
    for ip, cmd, payload in collect(sock, duration):
        if cmd == "scan" and payload.get("device"):
            found[payload["device"]] = payload
    if not found:
        print(
            "No devices found.\n"
            "- Is LAN Control enabled in the Govee Home app (device settings)?\n"
            "- Is this computer on the same network/VLAN as the lamps?\n"
            "- Does your router allow multicast (IGMP)?"
        )
        return
    print(f"Found {len(found)} device(s):")
    for device_id, payload in found.items():
        print(
            f"  IP {payload.get('ip'):<15}  SKU {payload.get('sku', '?'):<8}"
            f"  ID {device_id}"
        )


def cmd_control(sock: socket.socket, args) -> None:
    target = (args.ip, CONTROL_PORT)
    if args.on:
        send(sock, "turn", {"value": 1}, target)
        print("Sent: turn on")
    if args.off:
        send(sock, "turn", {"value": 0}, target)
        print("Sent: turn off")
    if args.brightness is not None:
        send(sock, "brightness", {"value": max(1, min(100, args.brightness))}, target)
        print(f"Sent: brightness {args.brightness}%")
    if args.color is not None:
        r, g, b = args.color
        send(
            sock,
            "colorwc",
            {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0},
            target,
        )
        print(f"Sent: color rgb({r},{g},{b})")
    if args.kelvin is not None:
        send(
            sock,
            "colorwc",
            {"color": {"r": 0, "g": 0, "b": 0},
             "colorTemInKelvin": max(2000, min(9000, args.kelvin))},
            target,
        )
        print(f"Sent: color temperature {args.kelvin} K")

    send(sock, "devStatus", {}, target)
    got_reply = False
    for ip, cmd, payload in collect(sock, 2.0):
        if ip == args.ip and cmd == "devStatus":
            print(f"Status: {json.dumps(payload)}")
            got_reply = True
            break
    if not got_reply:
        print(
            "No status reply. Check the IP, LAN Control setting and firewall "
            "(UDP 4001-4003)."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ip", help="lamp IP address (control mode)")
    parser.add_argument("--duration", type=float, default=5.0,
                        help="scan duration in seconds (default 5)")
    parser.add_argument("--status", action="store_true", help="request status")
    parser.add_argument("--on", action="store_true", help="turn on")
    parser.add_argument("--off", action="store_true", help="turn off")
    parser.add_argument("--brightness", type=int, metavar="1-100")
    parser.add_argument("--color", type=int, nargs=3, metavar=("R", "G", "B"))
    parser.add_argument("--kelvin", type=int, metavar="2000-9000")
    args = parser.parse_args()

    sock = open_listener()
    try:
        if args.ip:
            cmd_control(sock, args)
        else:
            cmd_scan(sock, args.duration)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
