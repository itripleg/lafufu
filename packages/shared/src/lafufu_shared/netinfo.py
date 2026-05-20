"""Network introspection: the Pi's own primary LAN IP address.

Shared by the agent's "what's your IP" voice intent and the Bluetooth
broadcaster (deploy/lafufu-btcast.sh), which calls the module entry
point: ``python -m lafufu_shared.netinfo``.
"""

import socket


def primary_lan_ip() -> str | None:
    """Return the primary LAN IPv4 address, or None when offline.

    Opens a UDP socket and 'connects' it toward a public address so the
    OS picks the outbound interface; no packets are actually sent. Reads
    back that interface's local address.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


if __name__ == "__main__":
    _ip = primary_lan_ip()
    print(_ip if _ip else "")
