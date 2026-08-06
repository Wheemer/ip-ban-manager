"""Home Assistant self-network discovery helpers."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv6Network, ip_interface, ip_network

from homeassistant.components.network import async_get_adapters
from homeassistant.core import HomeAssistant

from .ban_lookup import _supervisor_internal_networks
from .storage_keys import IPNetwork


async def async_home_assistant_self_networks(
    hass: HomeAssistant,
) -> tuple[IPNetwork, ...]:
    """Return exact Home Assistant-owned addresses that managed rules must not block."""
    networks = list(_supervisor_internal_networks())

    for adapter in await async_get_adapters(hass):
        if not adapter["enabled"]:
            continue

        for address in (*adapter["ipv4"], *adapter["ipv6"]):
            interface = ip_interface(
                f"{address['address']}/{address['network_prefix']}"
            )
            if (
                isinstance(interface.network, IPv6Network)
                and interface.network.is_link_local
            ):
                networks.append(interface.network)
                continue
            host_prefix = 32 if isinstance(interface.ip, IPv4Address) else 128
            networks.append(ip_network(f"{interface.ip}/{host_prefix}"))

    return tuple(dict.fromkeys(networks))
