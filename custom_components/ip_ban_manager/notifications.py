"""Persistent notification helpers for IP Ban Manager."""

from __future__ import annotations

import re
from contextlib import suppress
from ipaddress import ip_address
from urllib.parse import quote, urlencode

from aiohttp.web import Response
from homeassistant.components.http.ban import (
    KEY_FAILED_LOGIN_ATTEMPTS,
    KEY_LOGIN_THRESHOLD,
    NOTIFICATION_ID_BAN,
    NOTIFICATION_ID_LOGIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .audit import record_allowlisted_login_escalated
from .ban_lookup import _is_allowed
from .const import (
    CONF_BAN_NOTIFICATIONS_ENABLED,
    CONF_SILENCED_ALLOWLISTED_LOGIN_IPS,
    DOMAIN,
)
from .entry_helpers import (
    entry_allowlisted_login_notifications_enabled,
    update_entry_options,
)
from .geoip import DBIP_ATTRIBUTION, geoip_location_for_ip
from .storage_keys import (
    KEY_ALLOWLIST,
    KEY_CONFIG_ENTRY,
    KEY_PANEL_REGISTERED,
    IPAddress,
)

ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD = 10
INTEGRATION_CONFIG_URL = f"/config/integrations/integration/{DOMAIN}"
NOTIFICATION_LINK_LABEL = "Open settings"
ALLOWLISTED_LOGIN_SILENCE_LABEL = "Don't show for this address again"
ALLOWLISTED_LOGIN_SILENCE_URL = f"/api/{DOMAIN}/silence_allowlisted_login_notifications"
PANEL_ACTION_SILENCE_ALLOWLISTED_LOGIN = "silence_allowlisted_login"
PANEL_ACTION_UNSILENCE_ALLOWLISTED_LOGIN = "unsilence_allowlisted_login"
ATTR_NOTIFICATION_ID = "notification_id"
NOTIFICATION_TITLE = " "
NOTIFICATION_ICON_URL = f"/api/{DOMAIN}/icon.png"
NOTIFICATION_ICON_DATA_URL = (
    "data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20"
    "viewBox='0%200%2064%2064'%3E%3Cpath%20fill='%231ea8d1'%20"
    "d='M32%204L56%2014v17c0%2015-10%2025-24%2029C18%2056%208%2046%208%2031V14z'/%3E"
    "%3Cpath%20fill='%233fb6dc'%20d='M32%204l24%2010v17c0%2015-10%2025-24%2029z'/%3E"
    "%3Cpath%20stroke='%23fff'%20stroke-width='7'%20stroke-linecap='round'%20"
    "d='M20%2032h24M32%2020v24'/%3E%3Cpath%20stroke='%230b4d78'%20"
    "stroke-width='7'%20stroke-linecap='round'%20d='M17%2050L49%2014'/%3E%3C/svg%3E"
)
IPV4_IN_TEXT = re.compile(
    r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?=$|[^\d.]|\.(?:\s|$))"
)
IPV6_IN_TEXT = re.compile(
    r"(?<![0-9A-Fa-f:.])(?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f:.%]*(?![0-9A-Fa-f:.])"
)
REMOTE_HOST_WITH_ADDR_IN_TEXT = re.compile(
    r"\bfrom\s+(?P<host>[^()\n]+?)\s+\((?P<addr>[^()\n]+)\)"
)


def format_remote_display(remote_host: str | None, remote_addr: IPAddress) -> str:
    """Return a compact, non-duplicated host/address label."""
    remote_addr_text = str(remote_addr)
    if remote_host and remote_host != remote_addr_text:
        return f"{remote_host} ({remote_addr_text})"
    return remote_addr_text


def clarify_remote_source_text(message: str) -> tuple[str, str | None]:
    """Make source text IP-first and return a reverse-DNS detail when present."""
    reverse_host: str | None = None

    def replace(match: re.Match[str]) -> str:
        nonlocal reverse_host
        host = match.group("host").strip()
        addr = match.group("addr").strip()
        normalized_addr = addr.strip("[]").split("%", 1)[0]
        with suppress(ValueError):
            ip_address(normalized_addr)
            if host != addr and reverse_host is None:
                reverse_host = host
            return f"from {addr}"
        return match.group(0)

    return REMOTE_HOST_WITH_ADDR_IN_TEXT.sub(replace, message, count=1), reverse_host


def append_reverse_dns_detail(message: str, reverse_host: str | None) -> str:
    """Append reverse-DNS detail without making it look like the source identity."""
    if not reverse_host or "Reverse DNS:" in message:
        return message
    return f"{message}\n\nReverse DNS: {reverse_host}"


def notifications_enabled(hass: HomeAssistant) -> bool:
    """Return whether automatic ban/login notifications should be shown."""
    http = getattr(hass, "http", None)
    app = getattr(http, "app", {})
    entry = app.get(KEY_CONFIG_ENTRY)
    if entry is None:
        return True
    return bool(
        entry.options.get(
            CONF_BAN_NOTIFICATIONS_ENABLED,
            entry.data.get(CONF_BAN_NOTIFICATIONS_ENABLED, True),
        )
    )


def handle_http_notifications(hass: HomeAssistant) -> None:
    """Add manager links to, or suppress, Home Assistant HTTP notifications."""
    if notifications_enabled(hass):
        add_manager_links_to_http_notifications(hass)
        return

    dismiss_http_notifications(hass)


def dismiss_http_notifications(hass: HomeAssistant) -> None:
    """Dismiss Home Assistant HTTP ban/login notifications."""
    from homeassistant.components import persistent_notification

    persistent_notification.async_dismiss(hass, NOTIFICATION_ID_LOGIN)
    persistent_notification.async_dismiss(hass, NOTIFICATION_ID_BAN)


def manager_config_url(hass: HomeAssistant) -> str:
    """Return the best local config URL for this integration."""
    if hass.data.get(KEY_PANEL_REGISTERED, False):
        return f"/{DOMAIN}"
    return INTEGRATION_CONFIG_URL


def manager_notification_link(hass: HomeAssistant) -> str:
    """Return the markdown link to the IP Ban Manager live panel."""
    return f"[{NOTIFICATION_LINK_LABEL}]({manager_config_url(hass)})"


def with_manager_link(hass: HomeAssistant, message: str) -> str:
    """Append or refresh the manager panel link."""
    cleaned = strip_notification_action_links(message)
    return f"{cleaned}\n\n{manager_notification_link(hass)}"


def allowlisted_login_silence_panel_url(
    remote_addr: IPAddress,
    notification_id: str = NOTIFICATION_ID_LOGIN,
) -> str:
    """Return the panel URL that silences one allowlisted-login address."""
    query = urlencode(
        {
            "action": PANEL_ACTION_SILENCE_ALLOWLISTED_LOGIN,
            "ip_address": str(remote_addr),
            ATTR_NOTIFICATION_ID: notification_id,
        }
    )
    return f"/{DOMAIN}?{query}"


def notification_action_response() -> Response:
    """Acknowledge notification-link actions without navigating the frontend."""
    return Response(status=204)


def with_allowlisted_login_silence_link(
    entry: ConfigEntry,
    message: str,
    remote_addr: IPAddress,
    notification_id: str = NOTIFICATION_ID_LOGIN,
) -> str:
    """Append the allowlisted-login silence link once."""
    message = strip_notification_action_links(message)
    if ALLOWLISTED_LOGIN_SILENCE_LABEL in message:
        return message
    return (
        f"{message}\n\n"
        f"[{ALLOWLISTED_LOGIN_SILENCE_LABEL}]"
        f"({allowlisted_login_silence_panel_url(remote_addr, notification_id)})"
    )


def strip_notification_action_links(message: str) -> str:
    """Remove old action links before adding the current action."""
    action_labels = (
        NOTIFICATION_LINK_LABEL,
        "Open integrations",
        "Allowlisted login notifications",
        ALLOWLISTED_LOGIN_SILENCE_LABEL,
    )
    return "\n".join(
        line
        for line in message.splitlines()
        if not any(line.startswith(f"[{label}](") for label in action_labels)
    ).rstrip()


def first_ip_address_in_text(message: str) -> IPAddress | None:
    """Return the first IP address in notification text."""
    for match in IPV4_IN_TEXT.findall(message):
        with suppress(ValueError):
            return ip_address(match)
    for match in IPV6_IN_TEXT.findall(message):
        with suppress(ValueError):
            return ip_address(match.strip("[]").split("%", 1)[0])
    return None


def dismiss_allowlisted_login_notifications(
    hass: HomeAssistant, remote_addr: IPAddress | None = None
) -> None:
    """Dismiss allowlisted-login notifications, including rewritten variants."""
    from homeassistant.components import persistent_notification

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    matching_ids = set()
    for notification_id, notification in notifications.items():
        message = notification["message"]
        message_lower = message.lower()
        if notification_id == NOTIFICATION_ID_LOGIN:
            matching_ids.add(notification_id)
            continue
        if remote_addr is None:
            if (
                ALLOWLISTED_LOGIN_SILENCE_URL in message
                or ALLOWLISTED_LOGIN_SILENCE_LABEL in message
                or "allowlisted login" in message_lower
            ):
                matching_ids.add(notification_id)
            continue

        remote_addr_text = str(remote_addr)
        encoded_remote_addr = quote(remote_addr_text, safe="")
        if (
            remote_addr_text in message
            or f"ip_address={encoded_remote_addr}" in message
        ) and (
            ALLOWLISTED_LOGIN_SILENCE_URL in message
            or ALLOWLISTED_LOGIN_SILENCE_LABEL in message
            or "allowlisted login" in message_lower
            or "is allowlisted" in message_lower
            or "will not be banned" in message_lower
        ):
            matching_ids.add(notification_id)

    for notification_id in matching_ids:
        persistent_notification.async_dismiss(hass, notification_id)


def silence_allowlisted_login_notifications(
    hass: HomeAssistant,
    entry: ConfigEntry,
    remote_addr: IPAddress,
    notification_id: str | None = None,
) -> None:
    """Persist per-address silence and dismiss matching notifications."""
    silenced_ips = entry_silenced_allowlisted_login_ip_strings(entry)
    if str(remote_addr) not in silenced_ips:
        silenced_ips.append(str(remote_addr))

    update_entry_options(hass, **{CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: silenced_ips})
    if notification_id:
        from homeassistant.components import persistent_notification

        persistent_notification.async_dismiss(hass, notification_id)
    dismiss_allowlisted_login_notifications(hass, remote_addr)


def unsilence_allowlisted_login_notifications(
    hass: HomeAssistant,
    entry: ConfigEntry,
    remote_addr: IPAddress,
) -> None:
    """Remove per-address silence for allowlisted login notifications."""
    silenced_ips = [
        ip_value
        for ip_value in entry_silenced_allowlisted_login_ip_strings(entry)
        if ip_value != str(remote_addr)
    ]
    update_entry_options(hass, **{CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: silenced_ips})


def notification_heading(notification_id: str, message: str) -> str:
    """Return the short message heading for a Home Assistant HTTP notification."""
    if notification_id == NOTIFICATION_ID_BAN:
        return "IP banned"
    message_lower = message.lower()
    if "allowlisted" in message_lower:
        if (
            "repeated allowlisted login failures" in message_lower
            or "trusted source should be reviewed" in message_lower
            or "threshold" in message_lower
        ):
            return "Repeated allowlisted login failures"
        return "Allowlisted login failed"
    return "Login attempt failed"


def notification_brand_header() -> str:
    """Return the compact branded header used in persistent notifications."""
    return (
        f'## <img src="{NOTIFICATION_ICON_DATA_URL}" width="28" height="28" '
        'alt="">&nbsp;&nbsp;IP Ban Manager'
    )


def strip_notification_brand_header(message: str) -> str:
    """Remove an existing IP Ban Manager markdown header before rebranding."""
    first_line, separator, rest = message.partition("\n")
    if separator and first_line.startswith("## ") and "IP Ban Manager" in first_line:
        return rest.lstrip("\n")
    return message


def with_notification_heading(heading: str, message: str) -> str:
    """Prefix a notification body with the branded header and compact heading once."""
    brand_header = notification_brand_header()
    heading_line = f"**{heading}**"
    message = strip_notification_brand_header(message)
    if message.startswith(heading_line):
        return f"{brand_header}\n\n{message}"
    return f"{brand_header}\n\n{heading_line}\n\n{message}"


def with_geoip_attribution_footer(message: str) -> str:
    """Append the DB-IP attribution as a quiet notification footer."""
    if DBIP_ATTRIBUTION in message:
        return message
    return f"{message}\n\n<small><sub>{DBIP_ATTRIBUTION}</sub></small>"


def geoip_notification_detail(
    hass: HomeAssistant, remote_addr: IPAddress | None
) -> str | None:
    """Return a GeoIP notification detail line when local data is available."""
    if remote_addr is None:
        return None
    location = geoip_location_for_ip(hass, remote_addr)
    if location is None:
        return None
    return f"Location: {location}"


def is_allowlisted_source(hass: HomeAssistant, remote_addr: IPAddress) -> bool:
    """Return whether a source is covered by IP Ban Manager's allowlist."""
    http = getattr(hass, "http", None)
    app = getattr(http, "app", {})
    allowlist = app.get(KEY_ALLOWLIST, ())
    return isinstance(allowlist, tuple) and _is_allowed(remote_addr, allowlist)


def create_manager_notification(
    hass: HomeAssistant, message: str, notification_id: str
) -> None:
    """Create a branded IP Ban Manager persistent notification."""
    from homeassistant.components import persistent_notification

    persistent_notification.async_create(
        hass,
        message,
        NOTIFICATION_TITLE,
        notification_id,
    )


def entry_silenced_allowlisted_login_ip_strings(entry: ConfigEntry) -> list[str]:
    """Return normalized silenced allowlisted-login addresses in stored order."""
    values = entry.options.get(
        CONF_SILENCED_ALLOWLISTED_LOGIN_IPS,
        entry.data.get(CONF_SILENCED_ALLOWLISTED_LOGIN_IPS, []),
    )
    silenced: list[str] = []
    seen: set[IPAddress] = set()
    for value in values if isinstance(values, list) else []:
        with suppress(ValueError):
            address = ip_address(value)
            if address not in seen:
                silenced.append(str(address))
                seen.add(address)
    return silenced


def entry_silenced_allowlisted_login_ips(entry: ConfigEntry) -> set[IPAddress]:
    """Return allowlisted addresses with login notices silenced."""
    return {
        ip_address(address)
        for address in entry_silenced_allowlisted_login_ip_strings(entry)
    }


def should_notify_allowlisted_login(
    hass: HomeAssistant, remote_addr: IPAddress, attempts: int
) -> bool:
    """Return whether an allowlisted failed login should notify the user."""
    http = getattr(hass, "http", None)
    app = getattr(http, "app", {})
    entry = app.get(KEY_CONFIG_ENTRY)
    if entry is None:
        return True

    if remote_addr in entry_silenced_allowlisted_login_ips(entry):
        return False

    if attempts >= ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD:
        return True

    return entry_allowlisted_login_notifications_enabled(entry)


def create_allowlisted_login_notification(
    hass: HomeAssistant, remote_addr: IPAddress, base_message: str
) -> None:
    """Create an IP Ban Manager failed-login notification for an allowlisted source."""
    failed_attempts = hass.http.app.get(KEY_FAILED_LOGIN_ATTEMPTS, {})
    attempts = int(failed_attempts.get(remote_addr, 0))
    threshold = int(hass.http.app.get(KEY_LOGIN_THRESHOLD, 0))
    if (
        attempts >= ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD
        and attempts - 1 < ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD
    ):
        record_allowlisted_login_escalated(
            hass,
            str(remote_addr),
            attempts=attempts,
        )
    if not should_notify_allowlisted_login(hass, remote_addr, attempts):
        return

    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    base_message, reverse_host = clarify_remote_source_text(base_message)
    details = [base_message]
    if reverse_host:
        details.append(f"Reverse DNS: {reverse_host}")
    has_geoip_detail = False
    if geoip_detail := geoip_notification_detail(hass, remote_addr):
        details.append(geoip_detail)
        has_geoip_detail = True

    if attempts >= ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD:
        heading = "Repeated allowlisted login failures"
        details.append(
            "This allowlisted source has failed authentication "
            f"{attempts} times. It was not banned because {remote_addr} is trusted, "
            "but repeated failures from a trusted source should be reviewed."
        )
    else:
        heading = "Allowlisted login failed"
        count_detail = (
            f"Current failed-login count: {attempts}/{threshold}. "
            f"{remote_addr} is allowlisted, so it will not be banned."
        )
        details.append(count_detail)

    message = with_notification_heading(heading, "\n\n".join(details))
    if entry is not None:
        message = with_allowlisted_login_silence_link(
            entry,
            message,
            remote_addr,
        )
    if has_geoip_detail:
        message = with_geoip_attribution_footer(message)

    create_manager_notification(hass, message, NOTIFICATION_ID_LOGIN)


def add_manager_links_to_http_notifications(hass: HomeAssistant) -> None:
    """Rewrite Home Assistant HTTP notifications with manager context."""
    from homeassistant.components import persistent_notification

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    http = getattr(hass, "http", None)
    app = getattr(http, "app", {})
    entry = app.get(KEY_CONFIG_ENTRY)
    for notification_id in (NOTIFICATION_ID_LOGIN, NOTIFICATION_ID_BAN):
        notification = notifications.get(notification_id)
        if notification is None:
            continue
        notification_message = strip_notification_action_links(notification["message"])
        notification_message, reverse_host = clarify_remote_source_text(
            notification_message
        )
        remote_addr = first_ip_address_in_text(notification_message)
        heading = notification_heading(notification_id, notification_message)
        notification_message = append_reverse_dns_detail(
            notification_message, reverse_host
        )
        message = with_notification_heading(heading, notification_message)
        has_geoip_detail = False
        if (
            remote_addr is not None
            and (geoip_detail := geoip_notification_detail(hass, remote_addr))
            and geoip_detail not in message
        ):
            message = f"{message}\n\n{geoip_detail}"
            has_geoip_detail = True
        if notification_id == NOTIFICATION_ID_LOGIN and remote_addr is not None:
            failed_attempts = app.get(KEY_FAILED_LOGIN_ATTEMPTS, {})
            attempts = int(failed_attempts.get(remote_addr, 0))
            if attempts >= ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD:
                heading = "Repeated allowlisted login failures"
                message = with_notification_heading(heading, message)
            if entry is not None:
                source_is_allowlisted = is_allowlisted_source(hass, remote_addr)
                if source_is_allowlisted and should_notify_allowlisted_login(
                    hass, remote_addr, attempts
                ):
                    message = with_allowlisted_login_silence_link(
                        entry, message, remote_addr, notification_id
                    )
                elif not source_is_allowlisted:
                    message = with_manager_link(hass, message)
                else:
                    persistent_notification.async_dismiss(hass, notification_id)
                    continue
            else:
                message = with_manager_link(hass, message)
        elif notification_id == NOTIFICATION_ID_BAN:
            message = with_manager_link(hass, message)
        if has_geoip_detail:
            message = with_geoip_attribution_footer(message)
        if (
            message != notification["message"]
            or notification["title"] == NOTIFICATION_TITLE
        ):
            persistent_notification.async_create(
                hass,
                message,
                NOTIFICATION_TITLE,
                notification_id,
            )
