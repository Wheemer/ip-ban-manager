"""Local GeoIP database download, reader, and lookup helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import ssl
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from gzip import BadGzipFile, GzipFile
from http.client import HTTPResponse
from ipaddress import ip_address
from pathlib import Path
from socket import getaddrinfo
from tempfile import NamedTemporaryFile
from typing import Any, cast
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from aiohttp import ClientError, ClientTimeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .ban_lookup import _normalize_remote_addr
from .const import (
    ALLOWED_REGION_ANYWHERE,
    ALLOWED_REGION_COUNTRY,
    ALLOWED_REGION_SUBDIVISION,
    ATTR_GEOIP_ATTRIBUTION,
    ATTR_GEOIP_DATABASE_PRESENT,
    ATTR_GEOIP_DATABASE_SOURCE,
    ATTR_GEOIP_DATABASE_UPDATED,
    ATTR_GEOIP_ENABLED,
)
from .entry_helpers import entry_geoip_enabled
from .file_store import file_updated, geoip_database_path, path_is_file
from .metrics import metric_increment
from .storage_keys import (
    KEY_CONFIG_ENTRY,
    KEY_GEOIP_READER,
    KEY_GEOIP_READER_MTIME,
    KEY_LOCAL_GEOIP_REGION_CACHE,
    IPAddress,
)

_LOGGER = logging.getLogger(__name__)

DBIP_ATTRIBUTION = "IP geolocation by DB-IP.com"
DBIP_DOWNLOAD_MAX_BYTES = 250 * 1024 * 1024
DBIP_DOWNLOAD_TIMEOUT = 120
DBIP_DOWNLOAD_USER_AGENT = "IPBanManager/1.6.2"
DBIP_SOURCE_NAME = "DB-IP City Lite"
MAXMINDDB_VENDOR_PATH = Path(__file__).with_name("vendor")
LOCAL_REGION_CACHE_TTL = timedelta(hours=12)
PUBLIC_IP_LOOKUP_TIMEOUT = ClientTimeout(total=4)
PUBLIC_IP_LOOKUP_URLS = (
    "https://cloudflare.com/cdn-cgi/trace",
    "https://api.ipify.org",
    "https://icanhazip.com",
)
LOCAL_REGION_LOOKUP_TIMEOUT = ClientTimeout(total=8)
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_USER_AGENT = "IPBanManager/1.8 (+https://github.com/Wheemer/ip-ban-manager)"
DNS_OVER_HTTPS_URL = (
    "https://cloudflare-dns.com/dns-query?name=download.db-ip.com&type=A"
)
SUBDIVISION_SHORT_CODES_BY_COUNTRY = {
    "CA": {
        "Alberta": "AB",
        "British Columbia": "BC",
        "Manitoba": "MB",
        "New Brunswick": "NB",
        "Newfoundland and Labrador": "NL",
        "Northwest Territories": "NT",
        "Nova Scotia": "NS",
        "Nunavut": "NU",
        "Ontario": "ON",
        "Prince Edward Island": "PE",
        "Quebec": "QC",
        "Saskatchewan": "SK",
        "Yukon": "YT",
    },
    "US": {
        "Alabama": "AL",
        "Alaska": "AK",
        "Arizona": "AZ",
        "Arkansas": "AR",
        "California": "CA",
        "Colorado": "CO",
        "Connecticut": "CT",
        "Delaware": "DE",
        "District of Columbia": "DC",
        "Florida": "FL",
        "Georgia": "GA",
        "Hawaii": "HI",
        "Idaho": "ID",
        "Illinois": "IL",
        "Indiana": "IN",
        "Iowa": "IA",
        "Kansas": "KS",
        "Kentucky": "KY",
        "Louisiana": "LA",
        "Maine": "ME",
        "Maryland": "MD",
        "Massachusetts": "MA",
        "Michigan": "MI",
        "Minnesota": "MN",
        "Mississippi": "MS",
        "Missouri": "MO",
        "Montana": "MT",
        "Nebraska": "NE",
        "Nevada": "NV",
        "New Hampshire": "NH",
        "New Jersey": "NJ",
        "New Mexico": "NM",
        "New York": "NY",
        "North Carolina": "NC",
        "North Dakota": "ND",
        "Ohio": "OH",
        "Oklahoma": "OK",
        "Oregon": "OR",
        "Pennsylvania": "PA",
        "Rhode Island": "RI",
        "South Carolina": "SC",
        "South Dakota": "SD",
        "Tennessee": "TN",
        "Texas": "TX",
        "Utah": "UT",
        "Vermont": "VT",
        "Virginia": "VA",
        "Washington": "WA",
        "West Virginia": "WV",
        "Wisconsin": "WI",
        "Wyoming": "WY",
    },
}


@dataclass(frozen=True)
class GeoIPLocation:
    """Normalized GeoIP location details used by display and policy checks."""

    display: str | None
    country_code: str | None
    subdivision_code: str | None
    country_name: str | None = None
    subdivision_label: str | None = None


@dataclass(frozen=True)
class LocalGeoIPRegionCacheEntry:
    """Cached configured-location region for the Home Assistant instance."""

    value: dict[str, str | None]
    expires_at: datetime


def geoip_download_months(now: datetime | None = None) -> list[str]:
    """Return current and previous DB-IP release month strings."""
    now = now or dt_util.utcnow()
    current = now.strftime("%Y-%m")
    previous_day = now.replace(day=1) - timedelta(days=1)
    previous = previous_day.strftime("%Y-%m")
    return [current] if current == previous else [current, previous]


def geoip_download_urls(now: datetime | None = None) -> list[str]:
    """Return DB-IP Lite MMDB download URLs to try."""
    return [
        f"https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz"
        for month in geoip_download_months(now)
    ]


def geoip_download_host_is_blocked(host: str) -> bool:
    """Return whether DNS resolved the download host to unusable sinkhole addresses."""
    try:
        addresses = {item[4][0] for item in getaddrinfo(host, 443)}
    except OSError:
        return False
    return bool(addresses) and all(
        address in {"0.0.0.0", "::"} for address in addresses
    )


def geoip_resolve_download_host_via_https() -> list[str]:
    """Resolve the DB-IP download host without using Home Assistant's local DNS."""
    request = UrlRequest(
        DNS_OVER_HTTPS_URL,
        headers={
            "Accept": "application/dns-json",
            "User-Agent": DBIP_DOWNLOAD_USER_AGENT,
        },
    )
    with urlopen(request, timeout=DBIP_DOWNLOAD_TIMEOUT) as response:
        payload = json.loads(response.read().decode())

    addresses = []
    for answer in payload.get("Answer", []):
        address = answer.get("data")
        if answer.get("type") == 1 and isinstance(address, str):
            addresses.append(address)
    if not addresses:
        raise HomeAssistantError("Could not resolve the GeoIP download host.")
    return addresses


@contextmanager
def open_geoip_download_url(url: str) -> Iterator[HTTPResponse]:
    """Open a GeoIP download URL, bypassing local DNS only when it is sinkholed."""
    parsed = urlsplit(url)
    host = parsed.hostname or "download.db-ip.com"
    if not geoip_download_host_is_blocked(host):
        request = UrlRequest(url, headers={"User-Agent": DBIP_DOWNLOAD_USER_AGENT})
        with urlopen(request, timeout=DBIP_DOWNLOAD_TIMEOUT) as response:
            yield response
        return

    _LOGGER.warning(
        "GeoIP database download host %s resolves to 0.0.0.0 or ::; "
        "resolving with DNS-over-HTTPS fallback",
        host,
    )
    last_error: Exception | None = None
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    for address in geoip_resolve_download_host_via_https():
        fallback_response: HTTPResponse | None = None
        tls_socket = None
        try:
            raw_socket = socket.create_connection(
                (address, parsed.port or 443), timeout=DBIP_DOWNLOAD_TIMEOUT
            )
            tls_socket = context.wrap_socket(raw_socket, server_hostname=host)
            tls_socket.sendall(
                (
                    f"GET {target} HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    f"User-Agent: {DBIP_DOWNLOAD_USER_AGENT}\r\n"
                    "Accept: application/octet-stream\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
            )
            fallback_response = HTTPResponse(tls_socket)
            fallback_response.begin()
            if fallback_response.status != 200:
                raise HomeAssistantError(
                    "GeoIP database download returned HTTP "
                    f"{fallback_response.status}."
                )
            yield fallback_response
            return
        except (OSError, HomeAssistantError) as err:
            last_error = err
            if fallback_response is not None:
                fallback_response.close()
            elif tls_socket is not None:
                tls_socket.close()

    raise HomeAssistantError(
        f"Could not connect to the GeoIP download host: {last_error}"
    ) from last_error


def download_geoip_database_to_path(path: Path) -> None:
    """Download and install the DB-IP City Lite database atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None

    for url in geoip_download_urls():
        temp_path: str | None = None
        try:
            with (
                open_geoip_download_url(url) as response,
                GzipFile(fileobj=response) as gzip_file,
                NamedTemporaryFile(
                    "wb",
                    dir=path.parent,
                    prefix=f".{path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temp_file,
            ):
                temp_path = temp_file.name
                total = 0
                while chunk := gzip_file.read(1024 * 1024):
                    total += len(chunk)
                    if total > DBIP_DOWNLOAD_MAX_BYTES:
                        raise HomeAssistantError(
                            "GeoIP database download is too large."
                        )
                    temp_file.write(chunk)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, path)
            return
        except (BadGzipFile, EOFError, OSError, URLError, HomeAssistantError) as err:
            last_error = err
            _LOGGER.warning("GeoIP database download failed from %s: %s", url, err)
            if temp_path is not None and os.path.exists(temp_path):
                os.unlink(temp_path)

    raise HomeAssistantError(
        f"Could not download the GeoIP database: {last_error}"
    ) from last_error


async def async_download_geoip_database(hass: HomeAssistant) -> None:
    """Download the local GeoIP database without blocking the event loop."""
    path = geoip_database_path(hass)
    await hass.async_add_executor_job(download_geoip_database_to_path, path)
    await async_prepare_geoip_reader(hass)


def close_geoip_reader(hass: HomeAssistant) -> None:
    """Close any cached GeoIP database reader."""
    reader = hass.http.app.pop(KEY_GEOIP_READER, None)
    hass.http.app.pop(KEY_GEOIP_READER_MTIME, None)
    close = getattr(reader, "close", None)
    if callable(close):
        close()


def _maxminddb_module() -> Any:
    """Return the bundled MMDB reader module without requiring runtime pip."""
    try:
        import maxminddb  # type: ignore[import-not-found]

        return maxminddb
    except ImportError:
        pass

    vendor_path = str(MAXMINDDB_VENDOR_PATH)
    if vendor_path not in sys.path:
        sys.path.insert(0, vendor_path)

    if sys.modules.get("maxminddb") is None:
        sys.modules.pop("maxminddb", None)

    import maxminddb

    return maxminddb


def open_geoip_reader(path: Path) -> tuple[object, float] | None:
    """Open the local MMDB reader from an executor thread."""
    if not path.is_file():
        return None

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    try:
        maxminddb = _maxminddb_module()
        reader = maxminddb.open_database(str(path))
    except (ImportError, OSError, RuntimeError):
        _LOGGER.warning("Could not open GeoIP database %s", path, exc_info=True)
        return None
    return reader, mtime


async def async_prepare_geoip_reader(hass: HomeAssistant) -> None:
    """Prepare the local MMDB reader without blocking the event loop."""
    result = await hass.async_add_executor_job(
        open_geoip_reader, geoip_database_path(hass)
    )
    close_geoip_reader(hass)
    if result is None:
        return

    reader, mtime = result
    hass.http.app[KEY_GEOIP_READER] = reader
    hass.http.app[KEY_GEOIP_READER_MTIME] = mtime


def geoip_reader(hass: HomeAssistant) -> object | None:
    """Return the prepared MMDB reader if it is available."""
    return hass.http.app.get(KEY_GEOIP_READER)


async def async_local_geoip_region(hass: HomeAssistant) -> dict[str, str | None] | None:
    """Return Home Assistant's configured home region, when detectable."""
    now = dt_util.utcnow()
    cached = hass.http.app.get(KEY_LOCAL_GEOIP_REGION_CACHE)
    if cached is not None and cached.expires_at > now:
        return cached.value

    if geoip_reader(hass) is None:
        geoip_path = geoip_database_path(hass)
        if await hass.async_add_executor_job(path_is_file, geoip_path):
            await async_prepare_geoip_reader(hass)
        else:
            return None

    if geoip_reader(hass) is None:
        return None

    value = await _async_homeassistant_config_region(hass)
    if value is None:
        return None

    hass.http.app[KEY_LOCAL_GEOIP_REGION_CACHE] = LocalGeoIPRegionCacheEntry(
        value=value,
        expires_at=now + LOCAL_REGION_CACHE_TTL,
    )
    return value


async def _async_homeassistant_config_region(
    hass: HomeAssistant,
) -> dict[str, str | None] | None:
    """Return the region for Home Assistant's configured home coordinates."""
    country_code = _homeassistant_country_code(hass)
    country_name = _country_name_from_code(country_code)
    reverse = None
    coordinates = _homeassistant_coordinates(hass)
    if coordinates is not None:
        reverse = await _async_reverse_geocode_home(hass, *coordinates)

    if reverse is not None:
        country_code = reverse.country_code or country_code
        country_name = reverse.country_name or country_name
        subdivision_code = reverse.subdivision_code
        subdivision_label = reverse.subdivision_label
        location = reverse.display
    else:
        subdivision_code = None
        subdivision_label = None
        location = country_name or country_code

    if not country_code:
        return None

    return {
        "ip_address": None,
        "location": location,
        "country_code": country_code,
        "subdivision_code": subdivision_code,
        "country_name": country_name,
        "subdivision_label": subdivision_label,
    }


def _homeassistant_country_code(hass: HomeAssistant) -> str | None:
    """Return Home Assistant's configured country code."""
    country = getattr(hass.config, "country", None)
    if not isinstance(country, str):
        return None
    country = country.strip().upper()
    if len(country) == 2 and country.isalpha():
        return country
    return None


def _homeassistant_coordinates(hass: HomeAssistant) -> tuple[float, float] | None:
    """Return valid Home Assistant configured home coordinates."""
    try:
        latitude = float(hass.config.latitude)
        longitude = float(hass.config.longitude)
    except (TypeError, ValueError):
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


async def _async_reverse_geocode_home(
    hass: HomeAssistant, latitude: float, longitude: float
) -> GeoIPLocation | None:
    """Reverse-geocode Home Assistant's configured home coordinates."""
    session = async_get_clientsession(hass)
    query = urlencode(
        {
            "format": "jsonv2",
            "lat": f"{latitude:.7f}",
            "lon": f"{longitude:.7f}",
            "zoom": "10",
            "addressdetails": "1",
        }
    )
    try:
        async with session.get(
            f"{NOMINATIM_REVERSE_URL}?{query}",
            headers={"User-Agent": NOMINATIM_USER_AGENT},
            timeout=LOCAL_REGION_LOOKUP_TIMEOUT,
        ) as response:
            if response.status != 200:
                return None
            result = await response.json(content_type=None)
    except (asyncio.TimeoutError, ClientError, OSError, ValueError, TypeError):
        return None

    if not isinstance(result, dict):
        return None
    return local_region_details_from_reverse_geocode(result)


def local_region_details_from_reverse_geocode(
    result: dict[str, object],
) -> GeoIPLocation | None:
    """Return normalized local-region details from a reverse-geocoder result."""
    address = result.get("address")
    if not isinstance(address, dict):
        return None

    country_code = _string_value(address.get("country_code"))
    country_code = country_code.upper() if country_code else None
    country_name = _string_value(address.get("country")) or _country_name_from_code(
        country_code
    )
    raw_subdivision_label = (
        _string_value(address.get("state"))
        or _string_value(address.get("province"))
        or _string_value(address.get("region"))
    )
    subdivision_label = raw_subdivision_label
    subdivision_code = _string_value(address.get("ISO3166-2-lvl4"))
    if subdivision_code:
        subdivision_code = subdivision_code.upper()
    elif country_code and raw_subdivision_label:
        short_code = SUBDIVISION_SHORT_CODES_BY_COUNTRY.get(country_code, {}).get(
            raw_subdivision_label
        )
        subdivision_code = f"{country_code}-{short_code}" if short_code else None

    city = _geoip_city_label(
        _string_value(address.get("city"))
        or _string_value(address.get("town"))
        or _string_value(address.get("village"))
        or _string_value(address.get("municipality"))
    )
    parts = _geoip_parts_without_redundant_subdivision(
        city, subdivision_label, country_code
    )
    return GeoIPLocation(
        display=", ".join(parts) if parts else None,
        country_code=country_code,
        subdivision_code=subdivision_code,
        country_name=country_name,
        subdivision_label=subdivision_label,
    )


def _string_value(value: object) -> str | None:
    """Return a stripped string value."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _country_name_from_code(country_code: str | None) -> str | None:
    """Return a common country display name for known local-region codes."""
    if country_code == "CA":
        return "Canada"
    if country_code == "US":
        return "United States"
    return country_code


async def _async_public_ip_address(hass: HomeAssistant) -> IPAddress | None:
    """Return the public address Home Assistant uses for outbound traffic."""
    session = async_get_clientsession(hass)
    for url in PUBLIC_IP_LOOKUP_URLS:
        try:
            async with session.get(url, timeout=PUBLIC_IP_LOOKUP_TIMEOUT) as response:
                if response.status != 200:
                    continue
                public_ip = public_ip_address_from_response(await response.text())
        except (asyncio.TimeoutError, ClientError, OSError, ValueError):
            continue
        if public_ip is not None:
            return public_ip
    return None


def public_ip_address_from_response(text: str) -> IPAddress | None:
    """Return a global IP address parsed from a public-IP service response."""
    for line in text.splitlines():
        value = line.strip()
        if not value:
            continue
        if line.startswith("ip="):
            value = line.split("=", 1)[1].strip()
        try:
            public_ip = ip_address(value)
        except ValueError:
            continue
        if public_ip.is_global:
            return public_ip
    return None


def localized_geoip_name(value: object) -> str | None:
    """Return an English GeoIP display name from a DB-IP/MaxMind-style field."""
    if not isinstance(value, dict):
        return None
    names = value.get("names")
    if isinstance(names, dict):
        name = names.get("en") or next(
            (item for item in names.values() if isinstance(item, str) and item),
            None,
        )
        if isinstance(name, str) and name:
            return name
    name = value.get("name")
    return name if isinstance(name, str) and name else None


def geoip_iso_code(value: object) -> str | None:
    """Return an ISO code from a DB-IP/MaxMind-style field."""
    if not isinstance(value, dict):
        return None
    iso_code = value.get("iso_code")
    return iso_code if isinstance(iso_code, str) and iso_code else None


def geoip_subdivision_name(value: object, country_code: str | None) -> str | None:
    """Return a short subdivision label from a DB-IP/MaxMind-style field."""
    name = localized_geoip_name(value)
    if name is None:
        return None
    if country_code is None:
        return name
    return SUBDIVISION_SHORT_CODES_BY_COUNTRY.get(country_code, {}).get(name, name)


def geoip_subdivision_code(value: object, country_code: str | None) -> str | None:
    """Return an ISO 3166-2 subdivision code when the record exposes one."""
    raw_code = geoip_iso_code(value)
    if country_code and raw_code:
        return f"{country_code}-{raw_code}"
    if country_code is None:
        return raw_code

    name = localized_geoip_name(value)
    if name is None:
        return None

    short_code = SUBDIVISION_SHORT_CODES_BY_COUNTRY.get(country_code, {}).get(name)
    return f"{country_code}-{short_code}" if short_code else None


def _geoip_label_key(value: str) -> str:
    """Return a loose comparison key for human GeoIP labels."""
    return "".join(char.lower() for char in value if char.isalnum())


def _geoip_city_label(value: str | None) -> str | None:
    """Return a compact city label for GeoIP display."""
    if value == "Washington, D.C.":
        return "Washington DC"
    return value


def _geoip_parts_without_redundant_subdivision(
    city: str | None, subdivision: str | None, country_code: str | None
) -> list[str]:
    """Return display location parts without repeating city-level regions."""
    parts = [part for part in (city, subdivision, country_code) if part]
    if city is None or subdivision is None:
        return parts

    city_key = _geoip_label_key(city)
    subdivision_key = _geoip_label_key(subdivision)
    if not subdivision_key:
        return parts

    if city_key == subdivision_key or city_key.endswith(subdivision_key):
        return [part for part in (city, country_code) if part]

    return parts


def geoip_location_from_result(result: dict[str, object]) -> str | None:
    """Return a human-readable GeoIP location from an MMDB record."""
    return geoip_location_details_from_result(result).display


def geoip_location_details_from_result(result: dict[str, object]) -> GeoIPLocation:
    """Return normalized location details from an MMDB record."""
    city = _geoip_city_label(localized_geoip_name(result.get("city")))

    subdivision_data = None
    subdivision = None
    subdivisions = result.get("subdivisions")
    if isinstance(subdivisions, list) and subdivisions:
        subdivision_data = subdivisions[0]
    elif isinstance(subdivisions, dict):
        subdivision_data = subdivisions
    country_data = result.get("country")
    country_code = geoip_iso_code(country_data)
    country_name = localized_geoip_name(country_data)
    subdivision = geoip_iso_code(subdivision_data) or geoip_subdivision_name(
        subdivision_data, country_code
    )
    subdivision_code = geoip_subdivision_code(subdivision_data, country_code)

    parts = _geoip_parts_without_redundant_subdivision(city, subdivision, country_code)
    return GeoIPLocation(
        display=", ".join(parts) if parts else None,
        country_code=country_code,
        subdivision_code=subdivision_code,
        country_name=country_name,
        subdivision_label=subdivision,
    )


def geoip_location_details_for_ip(
    hass: HomeAssistant,
    remote_addr: IPAddress,
    *,
    require_geoip_enabled: bool = True,
) -> GeoIPLocation | None:
    """Return normalized local GeoIP details for a public IP address."""
    normalized_addr = _normalize_remote_addr(remote_addr)
    if normalized_addr.is_private or normalized_addr.is_loopback:
        return None

    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if require_geoip_enabled and entry is not None and not entry_geoip_enabled(entry):
        return None

    reader = geoip_reader(hass)
    if reader is None:
        return None

    metric_increment(hass, "geoip_lookups")
    try:
        result = cast(Any, reader).get(str(normalized_addr))
    except (ValueError, OSError, RuntimeError):
        return None
    if not isinstance(result, dict):
        return None

    return geoip_location_details_from_result(result)


def geoip_location_for_ip(hass: HomeAssistant, remote_addr: IPAddress) -> str | None:
    """Return a human-readable local GeoIP location for a public IP address."""
    details = geoip_location_details_for_ip(hass, remote_addr)
    return details.display if details else None


def geoip_region_allows_ip(
    hass: HomeAssistant,
    remote_addr: IPAddress,
    mode: str,
    country_code: str,
    subdivision_code: str,
) -> bool:
    """Return whether a public IP matches the configured allowed region."""
    normalized_addr = _normalize_remote_addr(remote_addr)
    if normalized_addr.is_private or normalized_addr.is_loopback:
        return True

    if mode == ALLOWED_REGION_ANYWHERE:
        return True

    details = geoip_location_details_for_ip(hass, normalized_addr)
    if details is None:
        return False

    if mode == ALLOWED_REGION_COUNTRY:
        return bool(country_code) and details.country_code == country_code

    if mode == ALLOWED_REGION_SUBDIVISION:
        return bool(subdivision_code) and details.subdivision_code == subdivision_code

    return True


def geoip_status(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, object]:
    """Return GeoIP state for the live panel."""
    database_path = geoip_database_path(hass)
    return {
        ATTR_GEOIP_ENABLED: entry_geoip_enabled(entry),
        ATTR_GEOIP_DATABASE_PRESENT: database_path.is_file(),
        ATTR_GEOIP_DATABASE_SOURCE: DBIP_SOURCE_NAME,
        ATTR_GEOIP_DATABASE_UPDATED: file_updated(database_path),
        ATTR_GEOIP_ATTRIBUTION: DBIP_ATTRIBUTION,
    }
