"""Compare the same public-address workloads against a real local MMDB."""

import argparse
import json
import random
import statistics
from ipaddress import ip_address
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any, cast

from homeassistant.core import HomeAssistant

from custom_components.ip_ban_manager import geoip
from custom_components.ip_ban_manager.storage_keys import (
    KEY_GEOIP_READER,
    KEY_METRICS,
    IPAddress,
)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "database",
    type=Path,
    help="Existing MMDB file; read-only, never downloaded or changed",
)
database = parser.parse_args().database
opened = geoip.open_geoip_reader(database)
if opened is None:
    raise SystemExit("Could not open the supplied MMDB file")
reader = cast(Any, opened[0])
rng = random.Random(9143)
diverse: list[IPAddress] = []
while len(diverse) < 1200:
    address = ip_address(rng.randrange(1 << 32))
    if address.is_global:
        diverse.append(address)
diverse += [ip_address(f"2606:4700:{index:x}::1111") for index in range(100)]
nearby = [ip_address(int(ip_address("8.8.0.0")) + index) for index in range(2048)]
output = {"database_bytes": database.stat().st_size, "reader": type(reader).__module__}
try:
    for name, addresses in [
        ("cached", [ip_address("8.8.8.8")] * 5000),
        ("nearby", nearby),
        ("diverse", diverse),
    ]:
        hass = cast(
            HomeAssistant,
            SimpleNamespace(
                data={}, http=SimpleNamespace(app={KEY_GEOIP_READER: reader})
            ),
        )
        durations = []
        values = []
        for address in addresses:
            start = perf_counter()
            values.append(geoip.geoip_location_details_for_ip(hass, address))
            durations.append((perf_counter() - start) * 1000000)
        for address, value in zip(addresses, values, strict=True):
            raw = reader.get(str(address))
            expected = (
                geoip.geoip_location_details_from_result(raw)
                if isinstance(raw, dict)
                else None
            )
            assert value == expected, (address, value, expected)
        output[name] = {
            "samples": len(addresses),
            "mean_us": round(statistics.mean(durations), 3),
            "p95_us": round(sorted(durations)[int(len(durations) * 0.95)], 3),
            "decodes": hass.data[KEY_METRICS]["geoip_lookups"],
            "all_results_match_raw_mmdb": True,
        }
finally:
    reader.close()
print(json.dumps(output, indent=2))
