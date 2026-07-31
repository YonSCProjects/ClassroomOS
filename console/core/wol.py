"""
ClassroomOS — Wake-on-LAN Module
==================================
Sends a WOL magic packet to power on a remote machine.

Requirements:
  - Target PC must have Wake-on-LAN enabled in BIOS/UEFI
  - Target PC's NIC must have WOL enabled in Windows Device Manager
  - Both machines must be on the same LAN subnet (or router must forward directed broadcast)

Usage:
    from console.core.wol import wake
    wake("AA:BB:CC:DD:EE:FF")          # Wake by MAC
    wake_all([("AA:BB:CC:DD:EE:FF", "PC-01"), ...])  # Wake multiple
"""

import socket
import logging


logger = logging.getLogger("wol")

BROADCAST_IP  = "255.255.255.255"
WOL_PORT      = 9            # Standard WOL port; some use 7


def _build_magic_packet(mac_address: str) -> bytes:
    """
    Build a Wake-on-LAN magic packet for the given MAC address.
    
    A magic packet is:
      6 bytes of 0xFF
      followed by 16 repetitions of the target MAC address
    """
    # Normalise MAC — accept "AA:BB:CC:DD:EE:FF" or "AA-BB-CC-DD-EE-FF"
    mac_clean = mac_address.replace(":", "").replace("-", "").upper()
    if len(mac_clean) != 12:
        raise ValueError(f"Invalid MAC address: {mac_address!r}")
    
    mac_bytes = bytes.fromhex(mac_clean)
    return b"\xff" * 6 + mac_bytes * 16


def wake(mac_address: str, broadcast_ip: str = BROADCAST_IP, port: int = WOL_PORT) -> bool:
    """
    Send a WOL magic packet to wake a single machine.
    
    Args:
        mac_address:  Target MAC address (colon or dash separated)
        broadcast_ip: Broadcast address (default: 255.255.255.255)
        port:         UDP port to send to (default: 9)
    
    Returns:
        True on success, False on failure.
    """
    try:
        packet = _build_magic_packet(mac_address)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(packet, (broadcast_ip, port))
        logger.info(f"WOL packet sent to {mac_address}")
        return True
    except Exception as e:
        logger.error(f"WOL failed for {mac_address}: {e}")
        return False


def wake_all(clients: list) -> dict[str, bool]:
    """
    Wake multiple machines at once.

    Args:
        clients: List of ClientInfo objects OR dicts, each with name and mac fields.

    Returns:
        Dict mapping machine name → success bool
    """
    results = {}
    for client in clients:
        # Support both ClientInfo dataclass and plain dicts
        if hasattr(client, "name"):
            name = client.name
            mac  = client.mac
        else:
            name = client.get("name", client.get("mac", "unknown"))
            mac  = client.get("mac", "")
        if not mac:
            logger.warning(f"No MAC address for {name}, skipping WOL")
            results[name] = False
            continue
        results[name] = wake(mac)
    return results
