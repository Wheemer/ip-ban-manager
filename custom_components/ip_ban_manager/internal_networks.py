"""Home Assistant self-network discovery helpers."""

from __future__ import annotations

import os
from ipaddress import IPv4Address, IPv4Network, IPv6Network, ip_interface, ip_network
from typing import cast

from homeassistant.components.network import async_get_adapters
from homeassistant.components.network.models import Adapter
from homeassistant.core import HomeAssistant

from .ban_lookup import SUPERVISOR_DOCKER_PARENT_NETWORK, _supervisor_internal_networks
from .storage_keys import IPNetwork

DEFAULT_DOCKER_BRIDGE_NETWORK = IPv4Network("172.17.0.0/16")
DEFAULT_DOCKER_BRIDGE_GATEWAY = IPv4Network("172.17.0.1/32")


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


async def async_home_assistant_allowlist_safe_defaults(
    hass: HomeAssistant,
) -> list[str]:
    """Return visible local/internal networks suitable for the safe-default option."""
    local_networks: list[str] = []
    internal_networks: list[str] = []

    adapters = await async_get_adapters(hass)
    enabled_adapters = [adapter for adapter in adapters if adapter["enabled"]]
    default_adapters = [
        adapter
        for adapter in enabled_adapters
        if adapter["default"] and (adapter["ipv4"] or adapter["ipv6"])
    ]
    candidate_adapters = default_adapters or enabled_adapters

    for adapter in candidate_adapters:
        for address in (*adapter["ipv4"], *adapter["ipv6"]):
            interface = ip_interface(
                f"{address['address']}/{address['network_prefix']}"
            )
            network = interface.network
            if _skip_visible_local_network(network):
                continue
            local_networks.append(str(network))

    for network in _adapter_internal_allowlist_networks(enabled_adapters):
        internal_networks.append(str(network))

    return list(dict.fromkeys([*local_networks, *internal_networks]))


async def async_home_assistant_internal_allowlist_networks(
    hass: HomeAssistant,
) -> list[str]:
    """Return visible internal HA/Docker entries that pair with local safe defaults."""
    return [
        str(network)
        for network in _adapter_internal_allowlist_networks(
            await async_get_adapters(hass)
        )
    ]


def _adapter_internal_allowlist_networks(
    adapters: list[Adapter],
) -> list[IPNetwork]:
    """Return HA internal/Docker access paths worth showing in Allowed IPs."""
    networks: list[IPNetwork] = []
    supervisor_seen = bool(os.environ.get("SUPERVISOR"))

    for adapter in adapters:
        if not adapter["enabled"]:
            continue
        ipv4_addresses = cast(list[dict[str, object]], adapter["ipv4"])
        for address in ipv4_addresses:
            interface = ip_interface(
                f"{address['address']}/{address['network_prefix']}"
            )
            network = interface.network
            if not isinstance(network, IPv4Network):
                continue
            if network.subnet_of(SUPERVISOR_DOCKER_PARENT_NETWORK):
                supervisor_seen = True
            if network == DEFAULT_DOCKER_BRIDGE_NETWORK:
                networks.append(DEFAULT_DOCKER_BRIDGE_GATEWAY)

    if supervisor_seen:
        networks.insert(0, SUPERVISOR_DOCKER_PARENT_NETWORK)

    return list(dict.fromkeys(networks))


def _skip_visible_local_network(network: IPNetwork) -> bool:
    """Return whether a network should stay out of user-facing local defaults."""
    return (
        network.is_loopback
        or (network.is_link_local and not isinstance(network, IPv6Network))
        or network.is_multicast
        or network.is_unspecified
        or (
            isinstance(network, IPv4Network)
            and network.subnet_of(SUPERVISOR_DOCKER_PARENT_NETWORK)
        )
        or network == DEFAULT_DOCKER_BRIDGE_NETWORK
    )
