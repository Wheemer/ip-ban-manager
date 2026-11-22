"""Local GeoIP database download, reader, and lookup helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import ssl
import struct
import sys
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from gzip import BadGzipFile, GzipFile
from http.client import HTTPResponse
from ipaddress import ip_address
from pathlib import Path
from socket import getaddrinfo
from tempfile import NamedTemporaryFile
from threading import RLock
from time import monotonic
from typing import Any, cast
from urllib.error import HTTPError, URLError
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
from .entry_helpers import (
    entry_auto_ban_enabled,
    entry_geoip_enabled,
    entry_login_threshold,
    entry_regional_login_thresholds,
)
from .file_store import file_updated, geoip_database_path, path_is_file
from .geoip_cache import GeoIPPrefixCache
from .metrics import metric_increment
from .panel_assets import async_integration_version, integration_version
from .storage_keys import (
    KEY_CONFIG_ENTRY,
    KEY_GEOIP_READER,
    KEY_GEOIP_READER_MTIME,
    KEY_LOCAL_GEOIP_REGION_CACHE,
    IPAddress,
)

_LOGGER = logging.getLogger(__name__)
_READER_LOCK = globals().get("_READER_LOCK", RLock())
_IMPORT_LOCK = globals().get("_IMPORT_LOCK", RLock())
_DOWNLOAD_LOCK = globals().get("_DOWNLOAD_LOCK", RLock())
_LOOKUP_CACHE_KEY = "ip_ban_manager_geoip_lookup_cache"
_PREFIX_CACHE_KEY = "ip_ban_manager_geoip_prefix_cache"
_READER_LEASE_KEY = "ip_ban_manager_geoip_reader_lease"
GEOIP_CACHE_MAX_ENTRIES = 1024
GEOIP_CACHE_SECONDS = 600
GEOIP_NEGATIVE_CACHE_SECONDS = 30
GEOIP_ASYNC_MAX_LOOKUPS = 4
_ASYNC_LOOKUPS_KEY = "ip_ban_manager_geoip_async_lookups"

DBIP_ATTRIBUTION = "IP geolocation by DB-IP.com"
DBIP_DOWNLOAD_MAX_BYTES = 250 * 1024 * 1024
DBIP_DOWNLOAD_TIMEOUT = 120
DBIP_SOURCE_NAME = "DB-IP City Lite"
MAXMINDDB_VENDOR_PATH = Path(__file__).with_name("vendor")
LOCAL_REGION_CACHE_TTL = timedelta(hours=12)
LOCAL_REGION_RETRY_TTL = timedelta(minutes=1)
PUBLIC_IP_LOOKUP_TIMEOUT = ClientTimeout(total=4)
PUBLIC_IP_LOOKUP_URLS = (
    "https://cloudflare.com/cdn-cgi/trace",
    "https://api.ipify.org",
    "https://icanhazip.com",
)
LOCAL_REGION_LOOKUP_TIMEOUT = ClientTimeout(total=8)
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
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


@dataclass
class _ReaderLease:
    reader: Any
    active: int = 0
    retired: bool = False


@dataclass(frozen=True)
class LocalGeoIPRegionCacheEntry:
    """Cached configured-location region for the Home Assistant instance."""

    value: dict[str, str | None] | None
    expires_at: datetime
    signature: tuple[object, ...] = ()


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
            "User-Agent": f"IPBanManager/{integration_version()}",
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
        request = UrlRequest(
            url, headers={"User-Agent": f"IPBanManager/{integration_version()}"}
        )
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
                    f"User-Agent: IPBanManager/{integration_version()}\r\n"
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


CONF_GEOIP_AUTO_UPDATE = "geoip_auto_update"
_UPDATE_AVAILABLE = "ip_ban_manager_geoip_update_available"
_UPDATE_TASK = "ip_ban_manager_geoip_update_task"


def _database_build_epoch(path: Path) -> int | None:
    opened = open_geoip_reader(path)
    if opened is None:
        return None
    reader = cast(Any, opened[0])
    try:
        return int(reader.metadata().build_epoch)
    finally:
        reader.close()


def geoip_update_url(path: Path) -> str | None:
    """Probe release URLs without downloading the database."""
    epoch = _database_build_epoch(path)
    if epoch is None:
        return None
    installed_month = datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m")
    now = dt_util.utcnow()
    for month, url in zip(
        geoip_download_months(now), geoip_download_urls(now), strict=True
    ):
        if month <= installed_month:
            return None
        try:
            request = UrlRequest(
                url,
                method="HEAD",
                headers={"User-Agent": f"IPBanManager/{integration_version()}"},
            )
            with urlopen(request, timeout=DBIP_DOWNLOAD_TIMEOUT):
                return url
        except HTTPError as err:
            if err.code != 404:
                raise
    return None


async def async_check_geoip_update(hass: HomeAssistant) -> None:
    """Refresh availability and apply an opted-in update."""
    url = await hass.async_add_executor_job(geoip_update_url, geoip_database_path(hass))
    hass.data[_UPDATE_AVAILABLE] = url is not None
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if url and entry and entry.options.get(CONF_GEOIP_AUTO_UPDATE) is True:
        await async_download_geoip_database(hass, update_url=url)


async def _async_periodic_geoip_updates(hass: HomeAssistant) -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await async_check_geoip_update(hass)
        except (OSError, HomeAssistantError, ValueError):
            _LOGGER.debug("Could not check or update the GeoIP database", exc_info=True)
        await asyncio.sleep(86400)


def start_geoip_updates(hass: HomeAssistant) -> None:
    """Check after startup, then daily for the lifetime of the integration."""
    if _UPDATE_TASK not in hass.data:
        hass.data[_UPDATE_TASK] = hass.async_create_background_task(
            _async_periodic_geoip_updates(hass), "IP Ban Manager GeoIP updates"
        )


async def stop_geoip_updates(hass: HomeAssistant) -> None:
    """Stop the background refresh and clear its availability state."""
    task = hass.data.pop(_UPDATE_TASK, None)
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    hass.data.pop(_UPDATE_AVAILABLE, None)


def download_geoip_database_to_path(path: Path, update_url: str | None = None) -> None:
    """Download and install the DB-IP City Lite database atomically."""
    with _DOWNLOAD_LOCK:
        if update_url is None:
            _download_validated_geoip_database(path)
        else:
            epoch = _database_build_epoch(path)
            if epoch is not None:
                _download_validated_geoip_database(path, update_url, epoch)


def _download_validated_geoip_database(
    path: Path, update_url: str | None = None, minimum_epoch: int = 0
) -> None:
    """Keep the previous database until the replacement opens successfully."""
    path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None

    for url in [update_url] if update_url else geoip_download_urls():
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
            validated = open_geoip_reader(Path(temp_path))
            if validated is None:
                raise HomeAssistantError("Downloaded GeoIP database is invalid.")
            candidate, _mtime = validated
            build_epoch = 0
            try:
                if update_url:
                    build_epoch = cast(Any, candidate).metadata().build_epoch
                if total < 1024:
                    raise HomeAssistantError("Downloaded GeoIP database is too small.")
                probes = [
                    cast(Any, candidate).get(address)
                    for address in ("1.1.1.1", "8.8.8.8", "9.9.9.9")
                ]
                if not all(probes):
                    raise HomeAssistantError(
                        "Downloaded GeoIP database has no usable probe records."
                    )
            finally:
                cast(Any, candidate).close()
            if update_url and build_epoch <= minimum_epoch:
                os.unlink(temp_path)
                return
            previous = path.with_name(f".{path.name}.previous")
            had_previous = path.is_file()
            if had_previous:
                shutil.copyfile(path, previous)
            os.replace(temp_path, path)
            try:
                reopened = open_geoip_reader(path)
                if reopened is None:
                    raise HomeAssistantError(
                        "Installed GeoIP database could not be reopened."
                    )
                reader, _mtime = reopened
                try:
                    if not all(
                        cast(Any, reader).get(address)
                        for address in ("1.1.1.1", "8.8.8.8", "9.9.9.9")
                    ):
                        raise HomeAssistantError(
                            "Installed GeoIP database failed validation."
                        )
                finally:
                    cast(Any, reader).close()
            except Exception:
                if had_previous:
                    os.replace(previous, path)
                else:
                    path.unlink(missing_ok=True)
                raise
            if not had_previous:
                previous.unlink(missing_ok=True)
            return
        except (
            BadGzipFile,
            EOFError,
            OSError,
            URLError,
            HomeAssistantError,
            ValueError,
            RuntimeError,
            struct.error,
        ) as err:
            last_error = err
            _LOGGER.warning("GeoIP database download failed from %s: %s", url, err)
            if temp_path is not None and os.path.exists(temp_path):
                os.unlink(temp_path)

    raise HomeAssistantError(
        f"Could not download the GeoIP database: {last_error}"
    ) from last_error


async def async_download_geoip_database(
    hass: HomeAssistant, *, update_url: str | None = None
) -> None:
    """Download the local GeoIP database without blocking the event loop."""
    path = geoip_database_path(hass)
    args = (path,) if update_url is None else (path, update_url)
    pending = hass.async_add_executor_job(download_geoip_database_to_path, *args)
    try:
        await asyncio.shield(pending)
    except asyncio.CancelledError:
        await pending
        raise
    await async_prepare_geoip_reader(hass)
    hass.data.pop(_UPDATE_AVAILABLE, None)


def close_geoip_reader(hass: HomeAssistant) -> None:
    """Close any cached GeoIP database reader."""
    with _READER_LOCK:
        reader = hass.http.app.pop(KEY_GEOIP_READER, None)
        lease = hass.http.app.pop(_READER_LEASE_KEY, None)
        if lease is not None and lease.reader is reader:
            lease.retired = True
            if lease.active:
                reader = None
        hass.http.app.pop(KEY_GEOIP_READER_MTIME, None)
        hass.http.app.pop(_LOOKUP_CACHE_KEY, None)
        hass.http.app.pop(_PREFIX_CACHE_KEY, None)
        hass.http.app.pop(KEY_LOCAL_GEOIP_REGION_CACHE, None)
        hass.data.pop("ip_ban_manager_activity_location_cache", None)
        close = getattr(reader, "close", None)
        if callable(close):
            close()


def _maxminddb_module() -> Any:
    """Serialize temporary import-path changes across executor workers."""
    with _IMPORT_LOCK:
        return _import_maxminddb_module()


def _import_maxminddb_module() -> Any:
    """Return the bundled MMDB reader module without requiring runtime pip."""
    try:
        import maxminddb  # type: ignore[import-not-found]

        return maxminddb
    except ImportError:
        pass

    vendor_path = str(MAXMINDDB_VENDOR_PATH)
    inserted = vendor_path not in sys.path
    if inserted:
        sys.path.insert(0, vendor_path)

    if sys.modules.get("maxminddb") is None:
        sys.modules.pop("maxminddb", None)

    try:
        import maxminddb

        return maxminddb
    finally:
        if inserted:
            sys.path.remove(vendor_path)


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
    except (ImportError, OSError, RuntimeError, ValueError, struct.error):
        _LOGGER.warning("Could not open GeoIP database %s", path, exc_info=True)
        return None
    return reader, mtime


async def async_prepare_geoip_reader(hass: HomeAssistant) -> None:
    """Prepare the local MMDB reader without blocking the event loop."""
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    pending = hass.async_add_executor_job(open_geoip_reader, geoip_database_path(hass))
    try:
        result = await asyncio.shield(pending)
    except asyncio.CancelledError:
        result = await pending
        if result is not None:
            await hass.async_add_executor_job(cast(Any, result[0]).close)
        raise
    if hass.data.get("ip_ban_manager_local_region_stopping") or (
        entry is not None and hass.http.app.get(KEY_CONFIG_ENTRY) is not entry
    ):
        if result is not None:
            await hass.async_add_executor_job(cast(Any, result[0]).close)
        return
    with _READER_LOCK:
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
    """Share local-region enrichment across concurrent panel requests."""
    if hass.data.get("ip_ban_manager_local_region_stopping"):
        return None
    key = "ip_ban_manager_local_region_task"
    task = hass.data.get(key)
    if task is None or task.done():
        task = hass.async_create_task(
            _async_local_geoip_region(hass), "IP Ban Manager local region"
        )
        hass.data[key] = task
    try:
        return await asyncio.shield(task)
    finally:
        if task.done() and hass.data.get(key) is task:
            hass.data.pop(key, None)


async def async_stop_local_region(hass: HomeAssistant) -> None:
    """Drain the shared enrichment task before runtime teardown."""
    hass.data["ip_ban_manager_local_region_stopping"] = True
    task = hass.data.pop("ip_ban_manager_local_region_task", None)
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _async_local_geoip_region(
    hass: HomeAssistant,
) -> dict[str, str | None] | None:
    """Return Home Assistant's configured home region, when detectable."""
    now = dt_util.utcnow()
    signature = (_homeassistant_country_code(hass), _homeassistant_coordinates(hass))
    cached = hass.http.app.get(KEY_LOCAL_GEOIP_REGION_CACHE)
    if cached is not None and cached.expires_at > now and cached.signature == signature:
        return cached.value

    if geoip_reader(hass) is None:
        geoip_path = geoip_database_path(hass)
        if await hass.async_add_executor_job(path_is_file, geoip_path):
            await async_prepare_geoip_reader(hass)
        else:
            return None

    reader = geoip_reader(hass)
    if reader is None:
        return None

    value = await _async_homeassistant_config_region(hass)
    if geoip_reader(hass) is not reader:
        return None

    hass.http.app[KEY_LOCAL_GEOIP_REGION_CACHE] = LocalGeoIPRegionCacheEntry(
        value=value,
        expires_at=now
        + (
            LOCAL_REGION_CACHE_TTL
            if value and value.get("subdivision_code")
            else LOCAL_REGION_RETRY_TTL
        ),
        signature=signature,
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
    version = await async_integration_version(hass)
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
            headers={
                "User-Agent": f"IPBanManager/{version} (+https://github.com/Wheemer/ip-ban-manager)"
            },
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

    with _READER_LOCK:
        reader = geoip_reader(hass)
        if reader is None:
            return None
        cache = hass.http.app.setdefault(_LOOKUP_CACHE_KEY, OrderedDict())
        now = monotonic()
        cached = cache.get(normalized_addr)
        if cached is not None and cached[0] > now:
            cache.move_to_end(normalized_addr)
            return cached[1]
        prefixes = hass.http.app.setdefault(
            _PREFIX_CACHE_KEY, GeoIPPrefixCache[GeoIPLocation](GEOIP_CACHE_MAX_ENTRIES)
        )
        if details := prefixes.get(normalized_addr, now):
            return details
        lease = hass.http.app.get(_READER_LEASE_KEY)
        if lease is None or lease.reader is not reader:
            lease = _ReaderLease(reader)
            hass.http.app[_READER_LEASE_KEY] = lease
        lease.active += 1
        metric_increment(hass, "geoip_lookups")
    prefix = None
    try:
        try:
            lookup = getattr(reader, "get_with_prefix_len", None)
            decoded = lookup(str(normalized_addr)) if callable(lookup) else None
            if (
                isinstance(decoded, tuple)
                and len(decoded) == 2
                and type(decoded[1]) is int
            ):
                result, prefix = decoded
            else:
                result = cast(Any, reader).get(str(normalized_addr))
        except (ValueError, OSError, RuntimeError, struct.error):
            result = None
        details = (
            geoip_location_details_from_result(result)
            if isinstance(result, dict)
            else None
        )
    finally:
        with _READER_LOCK:
            lease.active -= 1
            if lease.retired and not lease.active:
                lease.reader.close()
    with _READER_LOCK:
        if (
            geoip_reader(hass) is not reader
            or hass.http.app.get(_LOOKUP_CACHE_KEY) is not cache
        ):
            return None
        ttl = (
            GEOIP_CACHE_SECONDS if details is not None else GEOIP_NEGATIVE_CACHE_SECONDS
        )
        cache[normalized_addr] = (now + ttl, details)
        if details is not None and prefix is not None:
            prefixes.put(normalized_addr, prefix, now + ttl, details)
        cache.move_to_end(normalized_addr)
        while len(cache) > GEOIP_CACHE_MAX_ENTRIES:
            cache.popitem(last=False)
        return details


def geoip_location_for_ip(hass: HomeAssistant, remote_addr: IPAddress) -> str | None:
    """Return a human-readable local GeoIP location for a public IP address."""
    details = geoip_location_details_for_ip(hass, remote_addr)
    return details.display if details else None


async def async_warm_geoip_for_ip(hass: HomeAssistant, remote_addr: IPAddress) -> bool:
    """Warm a cold auth lookup off-loop without an unbounded executor queue."""
    address = _normalize_remote_addr(remote_addr)
    if address.is_private or address.is_loopback:
        return True
    with _READER_LOCK:
        reader = geoip_reader(hass)
        if reader is None or not callable(getattr(reader, "get", None)):
            return True
        now = monotonic()
        cached = hass.http.app.get(_LOOKUP_CACHE_KEY, {}).get(address)
        if cached is not None and cached[0] > now:
            return True
        prefixes = hass.http.app.get(_PREFIX_CACHE_KEY)
        if prefixes is not None and prefixes.get(address, now) is not None:
            return True
    pending = hass.data.setdefault(_ASYNC_LOOKUPS_KEY, {})
    future = pending.get(address)
    if future is None:
        if len(pending) >= GEOIP_ASYNC_MAX_LOOKUPS:
            return False
        future = hass.async_add_executor_job(
            geoip_location_details_for_ip, hass, address
        )
        pending[address] = future

        def finished(done: asyncio.Future[object]) -> None:
            if pending.get(address) is done:
                pending.pop(address, None)

        future.add_done_callback(finished)
    try:
        await asyncio.shield(future)
    except asyncio.CancelledError:
        await future
        raise
    return True


def regional_login_threshold_for_ip(
    hass: HomeAssistant, remote_addr: IPAddress
) -> int | None:
    """Return the most-specific configured threshold for a public IP."""
    normalized_addr = _normalize_remote_addr(remote_addr)
    if normalized_addr.is_private or normalized_addr.is_loopback:
        return None

    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is None or not entry_auto_ban_enabled(entry):
        return None
    from .region_rules import entry_public_region_settings, has_public_region_settings

    if has_public_region_settings(entry):
        region_settings = entry_public_region_settings(hass, entry)
        thresholds = (
            region_settings["public_region_rules"]
            if region_settings["public_region_enabled"]
            else {}
        )
    else:
        thresholds = entry_regional_login_thresholds(entry)
    if not thresholds:
        return None

    details = geoip_location_details_for_ip(hass, normalized_addr)
    if details is None:
        return None
    if details.subdivision_code in thresholds:
        return thresholds[details.subdivision_code]
    if details.country_code in thresholds:
        return thresholds[details.country_code]
    return None


def effective_login_threshold_for_ip(
    hass: HomeAssistant, remote_addr: IPAddress
) -> int:
    """Return the regional override or the configured global threshold."""
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is None or not entry_auto_ban_enabled(entry):
        return 0
    override = regional_login_threshold_for_ip(hass, remote_addr)
    return entry_login_threshold(entry, hass) if override is None else override


def geoip_regions_allow_ip(
    hass: HomeAssistant, remote_addr: IPAddress, regions: frozenset[str]
) -> bool:
    """Match a public address against any saved country or subdivision."""
    normalized = _normalize_remote_addr(remote_addr)
    if normalized.is_private or normalized.is_loopback:
        return True
    details = geoip_location_details_for_ip(hass, normalized)
    return details is not None and (
        details.subdivision_code in regions or details.country_code in regions
    )


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

    return False


def geoip_status(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, object]:
    """Return GeoIP state for the live panel."""
    database_path = geoip_database_path(hass)
    return {
        ATTR_GEOIP_ENABLED: entry_geoip_enabled(entry),
        CONF_GEOIP_AUTO_UPDATE: entry.options.get(CONF_GEOIP_AUTO_UPDATE) is True,
        "geoip_update_available": hass.data.get(_UPDATE_AVAILABLE, False),
        ATTR_GEOIP_DATABASE_PRESENT: database_path.is_file(),
        ATTR_GEOIP_DATABASE_SOURCE: DBIP_SOURCE_NAME,
        ATTR_GEOIP_DATABASE_UPDATED: file_updated(database_path),
        ATTR_GEOIP_ATTRIBUTION: DBIP_ATTRIBUTION,
    }
