"""Client allow-list: the app only answers loopback and the hotspot subnet."""

import ipaddress

ALLOWED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("10.42.0.0/24"),
)


def is_allowed_client(host: str | None) -> bool:
    if not host:
        return False
    if ":" in host:
        # IPv6: drop a zone id suffix such as "%eth0".
        host = host.partition("%")[0]
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return any(addr in net for net in ALLOWED_NETWORKS if net.version == addr.version)
