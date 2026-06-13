<div align="center">

<img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/custom_components/ban_allowlist/icon.png" width="112" alt="IP Ban Manager icon">

# Home Assistant IP Ban Manager

### Live IP ban and allowlist management for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-CUSTOM-FD7E14?style=for-the-badge&logo=home-assistant&logoColor=white&labelColor=555555)](https://github.com/hacs/integration)
[![Home Assistant 2025.1.4+](https://img.shields.io/badge/HOME%20ASSISTANT-2025.1.4%2B-41BDF5?style=for-the-badge&logo=home-assistant&logoColor=white&labelColor=555555)](https://www.home-assistant.io/)
[![Latest release](https://img.shields.io/github/v/release/Wheemer/ip-ban-manager?style=for-the-badge&logo=github&logoColor=white&label=RELEASE&labelColor=555555&color=22C55E)](https://github.com/Wheemer/ip-ban-manager/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Wheemer/ip-ban-manager/total?style=for-the-badge&logo=github&logoColor=white&label=DOWNLOADS&labelColor=555555&color=8A2BE2)](https://github.com/Wheemer/ip-ban-manager/releases)
[![License](https://img.shields.io/github/license/Wheemer/ip-ban-manager?style=for-the-badge&labelColor=555555&color=64748B)](LICENSE)

<p>
  <strong>Version 1.0.0:</strong><br>
  Polished setup, live allowlist and ban management, safer edits, diagnostics, services, and Home Assistant notification links.
</p>

</div>

Originally created by [palfrey](https://github.com/palfrey) as [`ban_allowlist`](https://github.com/palfrey/ban_allowlist). This fork builds on that work with a live Home Assistant IP ban and allowlist manager UI.

> [!WARNING]
> **THIS IS A HACK. USE AT YOUR OWN RISK.** Home Assistant does not provide a public integration API for changing the HTTP IP ban manager at runtime, so this integration uses a small internal hook around Home Assistant's built-in ban manager.

IP Ban Manager gives Home Assistant's built-in [IP filtering and banning](https://www.home-assistant.io/integrations/http/#ip-filtering-and-banning) the management UI it has always needed: trusted networks, live ban review and removal, automatic-ban controls, diagnostics, services, and a proper integration icon.

## What's New In v1.0.0

Version 1.0.0 turns the original YAML-only allowlist wrapper into a practical management panel for Home Assistant IP banning:

- polished config-flow setup with YAML import for existing `ban_allowlist` users
- first-run checkboxes for automatic banning, `127.0.0.1`, and Home Assistant's detected local subnet
- live editable **Allowed IPs** and **Banned IPs** lists from the integration options
- automatic-ban enable/disable and login-attempt threshold controls under **Banned IPs**
- IPv4 wildcard shorthand for allowed networks, such as `192.168.1.*`
- immediate add, remove, and clear actions without restarting Home Assistant
- banned IP timestamps shown as readable local times in the UI and preserved when unchanged
- `ip_bans.yaml` rewrites kept oldest-first so new bans appear at the bottom
- stale Home Assistant ban/login notifications dismissed when the matching IP is unbanned
- safety checks for dangerous or contradictory edits before anything is written
- diagnostic sensors for active bans, allowlisted networks, and failed-login sources
- automation/script services for ban and allowlist management
- shipped integration icon and updated HACS/repository metadata

## Examples

<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
  <div>
    <strong>Setup</strong><br>
    <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/docs/images/setup-flow.png" width="100%">
  </div>
  <div>
    <strong>Allowed IPs</strong><br>
    <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/docs/images/allowed-ips-options.png" width="100%">
  </div>
  <div>
    <strong>Banned IPs</strong><br>
    <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/docs/images/banned-ips-options.png" width="100%">
  </div>
</div>

## Hack Warning

This is a **HACK** because Home Assistant does not provide a public integration API for changing the HTTP IP ban manager at runtime. IP Ban Manager wraps Home Assistant's internal HTTP ban manager and failed-login handling so Home Assistant's built-in ban middleware still does the actual blocking, while this integration adds allowlists and live management on top.

That internal hook is intentionally small and covered by tests, but it is still internal Home Assistant behavior. Check release notes and test after Home Assistant updates, especially major releases.

Home Assistant has a very useful IP banning feature, which is nice for a private but externally facing instance. The missing feature is IP allowlists. Without an allowlist, your own home IP can get banned when something inside the house uses your external hostname. The position of the core devs appears to be ["this is a bug with something else that we shouldn't workaround"](https://github.com/home-assistant/core/pull/52334), but this integration keeps that workaround available and manageable.

This includes a focused unit test suite for the runtime hooks, config flow, services, and file handling. Test after Home Assistant updates, especially major releases, because this integration intentionally touches internal HTTP ban manager behavior.

## Install

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wheemer&repository=ip-ban-manager&category=integration)

If the button does not work, add `Wheemer/ip-ban-manager` to HACS manually as a custom integration repository.

## Config

After installing, restart Home Assistant once so the custom integration is loaded. Then add the integration from **Settings > Devices & services > Add integration**. Setup starts with the important controls only: automatic bans, the login-attempt threshold, and allowlist safe defaults for `127.0.0.1` plus, when detected, Home Assistant's local subnet. `127.0.0.1` is selected by default; the detected local subnet is available but not selected by default. Add or remove trusted LAN and remote IPs from **Configure** after setup.

Existing YAML configuration is imported automatically:

```
ban_allowlist:
  ip_addresses: ["my.ip.address", "192.168.1.0/24", "192.168.2.*"]
```

Home Assistant's built-in HTTP banning must still be enabled:

```
http:
  ip_ban_enabled: true
  login_attempts_threshold: 5
```

If IP banning is not enabled, IP Ban Manager creates a Home Assistant repair warning with the required YAML and a link to the official HTTP documentation. It does not edit `configuration.yaml` automatically; that keeps existing `http:` settings, includes, comments, proxy configuration, and package layouts safe.

## Live Management

The options UI is the main workspace. Allowlist, ban list, and automatic-ban setting changes apply immediately; Home Assistant does not need to restart. The integration stores automatic-ban settings in its config entry and reapplies them when Home Assistant starts.

Open **Settings > Devices & services > IP Ban Manager > Configure** to:

- add safe defaults with checkboxes inside **Allowed IPs**
- edit **Allowed IPs**, one IP address, CIDR network, or IPv4 wildcard network per line
- enable or disable new automatic bans and adjust the login-attempt threshold under **Banned IPs**
- edit **Banned IPs**, one exact IP address per line
- view existing ban timestamps as readable local times in **Banned IPs**
- clear every ban by leaving the **Banned IPs** list empty and submitting

Allowed IP wildcard entries such as `192.168.1.*` are saved as `192.168.1.0/24`. Wildcards are only supported for allowed networks, not banned IPs.

Existing banned IP rows are shown as `IP - local ban time`, oldest first. You can leave those timestamps in place when submitting; IP Ban Manager preserves the original ban date for unchanged bans. New banned IP rows can be entered as just the IP address, and Home Assistant records the current ban time when they are submitted. When the ban file is rewritten, entries are written oldest first so new bans appear at the bottom in both the UI and `ip_bans.yaml`.

The options UI validates edits before changing Home Assistant. It rejects all-Internet allowlist entries, IPs that are both allowed and banned, and malformed entries. Service calls use the same safety posture for risky operations, including typo removals, allowlist networks that contain active bans, and clear-all ban requests without `confirm: true`.

The live hooks are installed at setup even if the initial allowlist is empty, so adding your first allowed IP later works immediately. If the integration is unloaded, those hooks are restored so Home Assistant is left in its normal state.

The integration also adds services for automations and scripts:

- `ban_allowlist.add_ip_ban`
- `ban_allowlist.remove_ip_ban`
- `ban_allowlist.remove_all_ip_bans`
- `ban_allowlist.add_allowlist_network`
- `ban_allowlist.remove_allowlist_network`

Adding an IP ban updates Home Assistant's live ban manager and persists to `ip_bans.yaml`. Removing a ban updates the live ban manager, clears any failed-login counter for that IP, and rewrites `ip_bans.yaml`. Clearing every ban requires `confirm: true`.

## Diagnostic Sensors

IP Ban Manager adds diagnostic sensors with count states and detailed attributes:

- `sensor.ip_ban_manager_active_bans`
- `sensor.ip_ban_manager_allowlisted_networks`
- `sensor.ip_ban_manager_failed_login_sources`
