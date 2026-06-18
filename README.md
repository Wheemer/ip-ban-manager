<div align="center">

<h1>
  <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/custom_components/ip_ban_manager/icon.png" width="64" alt="IP Ban Manager icon" align="center">
  IP Ban Manager
</h1>

### Live IP ban and allowlist management for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-CUSTOM-FD7E14?style=for-the-badge&logo=home-assistant&logoColor=white&labelColor=555555)](https://github.com/hacs/integration)
[![Home Assistant 2024.7.4+](https://img.shields.io/badge/HOME%20ASSISTANT-2024.7.4%2B-41BDF5?style=for-the-badge&logo=home-assistant&logoColor=white&labelColor=555555)](https://www.home-assistant.io/)
[![Latest release](https://img.shields.io/github/v/release/Wheemer/ip-ban-manager?style=for-the-badge&logo=github&logoColor=white&label=RELEASE&labelColor=555555&color=22C55E)](https://github.com/Wheemer/ip-ban-manager/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Wheemer/ip-ban-manager/total?style=for-the-badge&logo=github&logoColor=white&label=DOWNLOADS&labelColor=555555&color=8A2BE2)](https://github.com/Wheemer/ip-ban-manager/releases)
[![License: AGPL-3.0-only](https://img.shields.io/badge/LICENSE-AGPL--3.0--only-64748B?style=for-the-badge&labelColor=555555)](LICENSE)


</div>

Originally created by [palfrey](https://github.com/palfrey). This fork builds on that allowlist work with a live Home Assistant IP ban and allowlist manager UI.

> [!WARNING]
> **THIS IS A HACK. USE AT YOUR OWN RISK.** Home Assistant does not provide a public integration API for changing the HTTP IP ban manager at runtime, so this integration uses a small internal hook around Home Assistant's built-in ban manager.

IP Ban Manager gives Home Assistant's built-in [IP filtering and banning](https://www.home-assistant.io/integrations/http/#ip-filtering-and-banning) the management UI it has always needed: trusted networks, live ban review and removal, automatic-ban controls, diagnostics, services, and a proper integration icon.

## Changelog At A Glance

IP Ban Manager turns the original YAML-only allowlist wrapper into a practical management panel for Home Assistant IP banning. Exact IP bans stay in Home Assistant's native live ban manager and `ip_bans.yaml` workflow; IP Ban Manager adds the UI, allowlist, managed network blocks, safety checks, diagnostics, and services around it.

| Release | Highlights |
| --- | --- |
| **v1.2.0** | Public-ready release with managed **Blocked networks**, allowlist precedence, automatic-ban notification controls, diagnostics, branded notifications, full `ip_ban_manager` domain migration, and automatic upgrade shim for existing installs. |
| **v1.1.2** | README and HACS display polish, including a more reliable license badge. |
| **v1.1.1** | Repository brand assets so HACS and Home Assistant can discover the integration icon where supported. |
| **v1.1.0** | Managed **Blocked networks** for CIDR ranges and IPv4 wildcard shorthand, allowlist precedence over blocked networks, automatic-ban notification control, and blocked-network diagnostics. |
| **v1.0.0** | First public IP Ban Manager release with config-flow setup, YAML import, live **Allowed IPs** and **Banned IPs** editing, automatic-ban controls, services, diagnostics, and safer file handling. |

Core management features include:

- **Setup:** UI setup with automatic-ban controls, `127.0.0.1` safe default, optional detected local subnet, and YAML import for existing users.
- **Allowed IPs:** live editable trusted IPs, CIDR networks, and IPv4 wildcard networks like `192.168.1.*`.
- **Banned IPs:** live exact-IP ban review, add, remove, and clear actions without restarting Home Assistant. Existing ban timestamps are shown as readable local times and preserved when unchanged.
- **Blocked networks:** managed CIDR or wildcard network blocks, enforced behind Home Assistant's native ban lookup without pretending `ip_bans.yaml` supports ranges.
- **Ordering and persistence:** `ip_bans.yaml` rewrites stay oldest-first so new exact bans appear at the bottom, matching Home Assistant's normal file behavior.
- **Notifications:** branded IP Ban Manager login/ban notices include a compact icon header, direct settings link, stale-notice cleanup when bans are removed, and optional automatic-ban notification suppression.
- **Safety checks:** malformed entries, all-Internet allowlist or block entries, exactly banned IPs that are also allowed, risky typo removals, and unconfirmed clear-all service calls are rejected before anything is written.
- **Automation:** `ip_ban_manager.*` services for adding, removing, and clearing exact bans plus adding and removing allowlist entries.
- **Diagnostics:** sensors for active bans, allowlisted networks, managed blocked networks, and failed-login sources.

## Examples

<table>
  <tr>
    <td width="50%">
      <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/docs/images/setup-flow.png" width="100%">
    </td>
    <td width="50%">
      <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/docs/images/allowed-ips-options.png" width="100%">
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/docs/images/banned-ips-options.png" width="100%">
    </td>
    <td width="50%"></td>
  </tr>
</table>

## Hack Warning

This is a **HACK** because Home Assistant does not provide a public integration API for changing the HTTP IP ban manager at runtime. IP Ban Manager wraps Home Assistant's internal HTTP ban manager and failed-login handling so Home Assistant's built-in ban middleware still does the actual blocking, while this integration adds allowlists and live management on top.

That internal hook is intentionally small and covered by tests, but it is still internal Home Assistant behavior. Check release notes and test after Home Assistant updates, especially major releases.

Home Assistant has a very useful IP banning feature, which is nice for a private but externally facing instance. The missing feature is IP allowlists. Without an allowlist, your own home IP can get banned when something inside the house uses your external hostname. The position of the core devs appears to be ["this is a bug with something else that we shouldn't workaround"](https://github.com/home-assistant/core/pull/52334), but this integration keeps that workaround available and manageable.


## Install

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wheemer&repository=ip-ban-manager&category=integration)

If the button does not work, add `Wheemer/ip-ban-manager` to HACS manually as a custom integration repository.

## Config

After installing, restart Home Assistant once so the custom integration is loaded. Then add the integration from **Settings > Devices & services > Add integration**. Setup starts with the important controls only: automatic bans, the login-attempt threshold, and allowlist safe defaults for `127.0.0.1` plus, when detected, Home Assistant's local subnet. `127.0.0.1` is selected by default; the detected local subnet is available but not selected by default. Add or remove trusted LAN and remote IPs from **Configure** after setup.

Existing installs from the previous domain are migrated automatically on restart. The visible integration name is **IP Ban Manager**, YAML examples use `ip_ban_manager:`, and automation/service calls use `ip_ban_manager.*`.

YAML configuration is imported automatically:

```
ip_ban_manager:
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
- enable or disable new automatic bans, automatic ban notifications, and the login-attempt threshold under **Banned IPs**
- edit **Banned entries**, one exact IP address per line
- edit **Blocked networks**, one CIDR network or IPv4 wildcard network per line
- view existing ban timestamps as readable local times in **Banned IPs**
- clear exact bans or managed blocked networks by emptying the matching field and submitting

Wildcard blocked-network entries such as `192.168.1.*` are saved as `192.168.1.0/24`. Exact banned IPs stay in Home Assistant's native live ban manager and `ip_bans.yaml`; CIDR and wildcard blocked networks are stored by IP Ban Manager and enforced behind the same native ban lookup. Allowed entries win over managed blocked networks, so you can block a subnet while keeping a trusted address allowed.

This gives you the practical behavior people expect from subnet banning without pretending Home Assistant's native `ip_bans.yaml` supports ranges. Exact IPs remain ordinary Home Assistant bans; managed networks are a small runtime layer that checks the same request path and still respects the allowlist first.

Existing exact banned IP rows are shown as `IP - local ban time`, oldest first. You can leave those timestamps in place when submitting; IP Ban Manager preserves the original ban date for unchanged bans. New exact banned IP rows can be entered as just the IP address, and Home Assistant records the current ban time when they are submitted. When the ban file is rewritten, entries are written oldest first so new exact bans appear at the bottom in both the UI and `ip_bans.yaml`.

The options UI validates edits before changing Home Assistant. It rejects all-Internet allowlist or blocked-network entries, IPs that are both allowed and exactly banned, and malformed entries. Service calls use the same safety posture for risky operations, including typo removals, allowlist networks that contain active exact bans, and clear-all ban requests without `confirm: true`.

The live hooks are installed at setup even if the initial allowlist is empty, so adding your first allowed IP later works immediately. If the integration is unloaded, those hooks are restored so Home Assistant is left in its normal state.

The integration also adds services for automations and scripts:

- `ip_ban_manager.add_ip_ban`
- `ip_ban_manager.remove_ip_ban`
- `ip_ban_manager.remove_all_ip_bans`
- `ip_ban_manager.add_allowlist_network`
- `ip_ban_manager.remove_allowlist_network`

Adding an IP ban updates Home Assistant's live ban manager and persists to `ip_bans.yaml`. Removing a ban updates the live ban manager, clears any failed-login counter for that IP, and rewrites `ip_bans.yaml`. Clearing every ban requires `confirm: true`.

## Diagnostic Sensors

IP Ban Manager adds diagnostic sensors with count states and detailed attributes:

- `sensor.ip_ban_manager_active_bans`
- `sensor.ip_ban_manager_allowlisted_networks`
- `sensor.ip_ban_manager_blocked_networks`
- `sensor.ip_ban_manager_failed_login_sources`
