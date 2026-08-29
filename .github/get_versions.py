"""Select the oldest supported and latest stable Home Assistant releases."""

import json
import re

import requests
from awesomeversion import AwesomeVersion

data = requests.get("https://pypi.org/simple/homeassistant/", timeout=30)
data.raise_for_status()

min_supported = AwesomeVersion("2024.7.4")

raw = data.text
version_pattern = re.compile("homeassistant-([^#-]+).tar.gz")
versions = sorted(
    {
        version
        for raw_version in version_pattern.findall(raw)
        if (version := AwesomeVersion(raw_version)).simple and version >= min_supported
    }
)
latest_stable = versions[-1]
output_versions = list(dict.fromkeys((min_supported.string, latest_stable.string)))
print(f"versions={json.dumps(output_versions)}")
