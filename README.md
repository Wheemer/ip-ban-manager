# Home Assistant IP Ban Manager

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wheemer&repository=ip-ban-manager&category=integration)

Status: **THIS IS A HACK. USE AT YOUR OWN RISK.**

IP Ban Manager extends Home Assistant's [IP filtering and banning](https://www.home-assistant.io/integrations/http/#ip-filtering-and-banning) with a UI for trusted networks, live IP ban management, diagnostics, services, and a proper integration icon.

## What's New In v1.0.0

IP Ban Manager is now a full management integration instead of a YAML-only allowlist wrapper:

- config flow setup with YAML import for existing `ban_allowlist` users
- live editable **Allowed IPs** and **Banned IPs** lists from the integration options
- immediate add, remove, and clear actions without restarting Home Assistant
- banned IP timestamps shown in the UI and preserved when unchanged
- stale Home Assistant ban/login notifications dismissed when the matching IP is unbanned
- diagnostic sensors for active bans, allowlisted networks, and failed-login sources
- automation/script services for ban and allowlist management
- shipped integration icon and updated HACS/repository metadata

## Hack Warning

This is a **HACK** because Home Assistant does not provide a public integration API for changing the HTTP IP ban manager at runtime. IP Ban Manager wraps Home Assistant's internal HTTP ban manager and failed-login handling so Home Assistant's built-in ban middleware still does the actual blocking, while this integration adds allowlists and live management on top.

That internal hook is intentionally small and covered by tests, but it is still internal Home Assistant behavior. Check release notes and test after Home Assistant updates, especially major releases.

Home Assistant has a very useful IP banning feature, which is nice for a private but externally facing instance. The missing feature is IP allowlists. Without an allowlist, your own home IP can get banned when something inside the house uses your external hostname. The position of the core devs appears to be ["this is a bug with something else that we shouldn't workaround"](https://github.com/home-assistant/core/pull/52334), but this integration keeps that workaround available and manageable.

This has a unit test suite and is explicitly integration tested against every latest patch version of HA from 2025.1.4 up (i.e. for every `x.y.z` version, we test all values of `x.y` using the latest `z` value).

## Install

If the button does not work, add `Wheemer/ip-ban-manager` to HACS manually as a custom integration repository.

## Config

After installing, restart Home Assistant once so the custom integration is loaded. Then add the integration from **Settings > Devices & services > Add integration** and enter trusted IP addresses or CIDR networks.

Existing YAML configuration is imported automatically:

```
ban_allowlist:
  ip_addresses: ["my.ip.address", "another.network.address/24"]
```

Home Assistant's built-in HTTP banning must still be enabled:

```
http:
  ip_ban_enabled: true
  login_attempts_threshold: 5
```

## Live Management

Allowlist and ban changes made from the integration options apply immediately; Home Assistant does not need to restart.

Open **Settings > Devices & services > IP Ban Manager > Configure** to:

- edit **Allowed IPs**, one IP address or CIDR network per line
- edit **Banned IPs**, one IP address per line
- view existing ban timestamps in the `banned_ips` list

Existing banned IP rows are shown as `IP - banned_at`. You can leave those timestamps in place when saving; IP Ban Manager preserves the original ban date for unchanged bans. New banned IP rows can be entered as just the IP address, and Home Assistant records the current ban time when they are saved.

The integration also adds services for automations and scripts:

- `ban_allowlist.add_ip_ban`
- `ban_allowlist.remove_ip_ban`
- `ban_allowlist.remove_all_ip_bans`
- `ban_allowlist.add_allowlist_network`
- `ban_allowlist.remove_allowlist_network`

Adding an IP ban updates Home Assistant's live ban manager and persists to `ip_bans.yaml`. Removing a ban updates the live ban manager, clears any failed-login counter for that IP, and rewrites `ip_bans.yaml`.

## Diagnostic Sensors

IP Ban Manager adds diagnostic sensors with count states and detailed attributes:

- `sensor.ip_ban_manager_active_bans`
- `sensor.ip_ban_manager_allowlisted_networks`
- `sensor.ip_ban_manager_failed_login_sources`
