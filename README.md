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

IP Ban Manager turns the original YAML-only allowlist wrapper into a practical management panel for Home Assistant IP banning. Exact IP bans stay in Home Assistant's native live ban manager and `ip_bans.yaml` workflow; IP Ban Manager adds the live panel, optional sidebar access, allowlist, managed network blocks, safety checks, diagnostics, and services around it.

| Release | Highlights |
| --- | --- |
| **v1.5.6** | Deletes the invalid nested `custom_components` folder left behind by the broken `v1.5.2` package layout. |
| **v1.5.5** | Keeps Home Assistant OS add-ons and Supervisor Docker traffic out of managed blocks/default-deny without exposing those internal addresses in Allowed IPs. |
| **v1.5.4** | Corrects default-deny safety validation so Home Assistant's own addresses are protected internally while valid local allowlists are not rejected for unrelated detected adapter paths. |
| **v1.5.3** | Clean HACS packaging recovery release: ships the release zip with the integration files at the zip root and adds workflow validation so bad zip layouts cannot be uploaded silently. |
| **v1.5.0** | Adds optional local GeoIP location labels for public IPs using a downloaded DB-IP City Lite database and hardens the **Don't show for this address again** notification action. No live IP lookups are made during login or ban handling. |
| **v1.4.8** | Bumps the bundled panel asset so Home Assistant reloads the IPv4/IPv6 panel wording, and aligns Configure helper text with the live panel. |
| **v1.4.7** | Tightens the legacy upgrade path so old `ban_allowlist` config entries are absorbed and removed automatically after IP Ban Manager starts. |
| **v1.4.6** | Completes the IPv4/IPv6 polish pass with dual-stack local subnet detection, IPv6 notification actions, clearer exact-IP wording, and tighter per-address notification silencing. |
| **v1.4.5** | Locks down the live panel status API and notification-silence action so both explicitly require a Home Assistant administrator. |
| **v1.4.4** | Tightens backend lockout safety for default-deny mode, service calls, and panel/API option writes, with server-side threshold clamping. |
| **v1.4.3** | Adds the `/config/ip_ban_manager.disabled` emergency file alongside `ip_ban_manager: disabled`, so SMB/file access can disable only IP Ban Manager without editing YAML. |
| **v1.4.2** | Cleans up the emergency YAML disable path to `ip_ban_manager: disabled`, updates the Repair/README wording, and keeps the earlier emergency key accepted for compatibility. |
| **v1.4.1** | Adds an emergency YAML disable switch, fixes hostname/default-deny matching for IPv4-mapped addresses, and keeps Configure opening the panel when the sidebar entry is hidden. |
| **v1.4.0** | Adds the dedicated live management panel, optional sidebar access, guarded default-deny mode, local-subnet lockout protection, and a separate **Advanced** area for riskier controls. |
| **v1.3.5** | Adds per-address muting for low-priority allowlisted failed-login notifications, while still escalating repeated failures from trusted sources. |
| **v1.3.4** | Fixes managed blocked-network enforcement after Home Assistant reloads `ip_bans.yaml`, and avoids rewriting the native ban file during integration setup. |
| **v1.3.3** | Adds Home Assistant Repairs for leftover legacy YAML and failed legacy-folder cleanup, while keeping cleanup files contained under `custom_components/ip_ban_manager/.cleanup`. |
| **v1.3.2** | Tightens local-network lockout safety: blocking a detected local network now requires an allowlist entry that keeps that detected network reachable, not just one host inside it. |
| **v1.3.1** | Improves legacy cleanup by absorbing old `ban_allowlist` config entries from the new config flow and moving stale old folders out of Home Assistant's loader path. |
| **v1.3.0** | Safer first-run defaults, local-network lockout validation, earlier failed-login notification capture, and smarter clear-ban confirmation only when multiple bans would be removed. |
| **v1.2.15** | Fixes HACS installs by shipping only the real `ip_ban_manager` integration folder; old `ban_allowlist` YAML is still absorbed by IP Ban Manager. |
| **v1.2.14** | Fixes blank **Banned entries** submissions and adds a confirmation screen before clearing every exact IP ban. |
| **v1.2.13** | Optional **Allow automatic bans inside Allowed IPs** setting for carrier/VPN subnet allowlists where individual failed-login sources should still become exact Home Assistant bans. |
| **v1.2.12** | Safer legacy `ban_allowlist` cleanup: removes stale old-domain cards only after **IP Ban Manager** exists, with startup cleanup and regression tests. |
| **v1.2.11** | Removes stale old-domain `ban_allowlist` cards from the new **IP Ban Manager** config-entry startup path. |
| **v1.2.10** | Removes stale old-domain `ban_allowlist` cards once the new **IP Ban Manager** entry exists, while preserving first-time migration. |
| **v1.2.9** | Clean CI release for the legacy `ban_allowlist` migration loader, with GitHub Actions formatting/lint fixes. |
| **v1.2.8** | Restores a tiny old-domain compatibility loader so existing `ban_allowlist` entries migrate into **IP Ban Manager** instead of staying **Not loaded**. |
| **v1.2.7** | Completes the visible migration by renaming old `ban_allowlist` / **IP Ban Allowlist** config entries to **IP Ban Manager** during setup. |
| **v1.2.6** | Unified allowlisted-login wording with Home Assistant terminology: **Allowlisted login notifications** everywhere. |
| **v1.2.5** | Setup polish for the **Allowlisted login notifications** label plus refreshed live Home Assistant screenshots for setup, allowlist management, and ban management. |
| **v1.2.4** | Quieter allowlisted failed-login notifications with a matching **Allowlisted login notifications** option and notification link, while repeated failures still escalate. |
| **v1.2.3** | Startup notification cleanup so existing Home Assistant HTTP notifications are normalized into the current branded format immediately. |
| **v1.2.2** | Repair-message cleanup and embedded notification logo so branded IP Ban Manager notifications do not depend on Home Assistant URL routing. |
| **v1.2.1** | HACS packaging fix so new installs load the real `ip_ban_manager` integration cleanly and absorb leftover YAML. |
| **v1.2.0** | Public-ready release with managed **Blocked networks**, allowlist precedence, automatic-ban notification controls, diagnostics, branded notifications, and full `ip_ban_manager` domain migration. |
| **v1.1.2** | README and HACS display polish, including a more reliable license badge. |
| **v1.1.1** | Repository brand assets so HACS and Home Assistant can discover the integration icon where supported. |
| **v1.1.0** | Managed **Blocked networks** for CIDR ranges and IPv4 wildcard shorthand, allowlist precedence over blocked networks, automatic-ban notification control, and blocked-network diagnostics. |
| **v1.0.0** | First public IP Ban Manager release with config-flow setup, YAML import, live **Allowed IPs** and **Blocked IPs** editing, automatic-ban controls, services, diagnostics, and safer file handling. |

Core management features include:

- **Live panel:** manage Allowed IPs, Blocked IPs, Blocked networks, and options from a dedicated IP Ban Manager page, with optional Home Assistant sidebar access.
- **Setup:** UI setup with automatic-ban controls, `127.0.0.1` safe default, detected local IPv4/IPv6 subnets selected by default, and YAML import for existing users.
- **Allowed IPs:** live editable trusted IPv4/IPv6 addresses, CIDR networks, and IPv4 wildcard networks like `192.168.1.*`.
- **Blocked IPs:** live exact IPv4/IPv6 block review, add, remove, and clear actions without restarting Home Assistant. Existing block timestamps are shown as readable local times and preserved when unchanged, with confirmation before clearing every blocked IP.
- **Blocked networks:** managed IPv4/IPv6 CIDR or IPv4 wildcard network blocks, enforced behind Home Assistant's native ban lookup without pretending `ip_bans.yaml` supports ranges.
- **Default deny:** optionally block everything outside **Allowed IPs** with a guarded checkbox instead of manually entering global block ranges.
- **Allowed subnet auto-bans:** optional exact automatic bans for failed logins inside allowed IP ranges, useful when a broad trusted carrier/VPN subnet should bypass network blocks but individual bad-login sources should still be banned.
- **Advanced controls:** lockout-sensitive choices are separated from everyday options and marked clearly in the live panel.
- **Emergency disable:** `ip_ban_manager: disabled` or `/config/ip_ban_manager.disabled` lets local file access disable IP Ban Manager if you need a recovery path after a bad setting.
- **Ordering and persistence:** `ip_bans.yaml` rewrites stay oldest-first so new exact bans appear at the bottom, matching Home Assistant's normal file behavior.
- **Notifications:** branded IP Ban Manager login/ban notifications include an embedded compact icon header, direct settings link where action is useful, stale-notification cleanup when bans are removed, optional automatic-ban notification suppression, earlier failed-login capture, and quieter allowlisted-login notifications that can still escalate if a trusted source keeps failing authentication.
- **GeoIP labels:** optional local DB-IP City Lite lookups can show approximate city/country labels for public IPs in notifications and the live panel, with no online lookup during request handling.
- **Safety checks:** malformed entries, all-Internet allowlist or block entries, exactly banned IPs that are also allowed, local-network lockout risks, unsafe default-deny changes, and unconfirmed multi-ban clear actions are rejected before anything is written. Home Assistant's own exact interface addresses plus Supervisor/add-on internals are protected without turning the whole local network into a hidden allowlist.
- **Automation:** `ip_ban_manager.*` services for adding, removing, and clearing exact bans plus adding and removing allowlist entries.
- **Diagnostics:** sensors for active bans, allowlisted networks, managed blocked networks, and failed-login sources.

## Examples

<table>
  <tr>
    <td>
      <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/docs/images/live-panel-v1.4.png" width="100%">
    </td>
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

After installing, restart Home Assistant once so the custom integration is loaded. Then add the integration from **Settings > Devices & services > Add integration**. Setup starts with the important controls only: automatic bans, the login-attempt threshold, and allowlist safe defaults for `127.0.0.1` plus, when detected, Home Assistant's local IPv4/IPv6 subnets. Both safe defaults are selected by default when available. Add or remove trusted LAN and remote IPs from **Configure** after setup.

The visible integration name is **IP Ban Manager** and automation/service calls use `ip_ban_manager.*`. Normal setup is done from the UI; existing Home Assistant `http:` IP-ban settings can stay in `configuration.yaml`. Leftover `ban_allowlist:` allowlist YAML is absorbed automatically when IP Ban Manager first loads.

YAML import is optional and mainly kept as a one-time migration path for advanced/manual installs, including leftover `ban_allowlist:` allowlist YAML. After IP Ban Manager imports those settings, remove the old integration YAML key and restart Home Assistant. If the old key is left behind, IP Ban Manager ignores it once the UI config entry already exists. Most users should add and manage IP Ban Manager from the UI.

If you previously installed or manually copied an old `custom_components/ban_allowlist` folder, IP Ban Manager moves that stale loader folder into `custom_components/ip_ban_manager/.cleanup` after the new integration starts. HACS should install only `custom_components/ip_ban_manager` from this repository.

Home Assistant's built-in HTTP banning must still be enabled:

```
http:
  ip_ban_enabled: true
```

The login-attempt threshold is managed from IP Ban Manager setup and Configure after Home Assistant's native IP banning is enabled.

If IP banning is not enabled, IP Ban Manager creates a Home Assistant repair warning with the required YAML and a link to the official HTTP documentation. It does not edit `configuration.yaml` automatically; that keeps existing `http:` settings, includes, comments, proxy configuration, and package layouts safe.

### Emergency Disable

If you accidentally lock yourself out with IP Ban Manager settings, you can disable only this integration with either emergency path, then restart Home Assistant.

The simplest SMB-friendly option is to create this empty file:

```text
/config/ip_ban_manager.disabled
```

You can also disable it from `configuration.yaml`:

```yaml
ip_ban_manager: disabled
```

This is a local-file escape hatch for SMB, Studio Code Server, terminal access, or any other path that can edit files without using the Home Assistant UI. When either emergency path is active, IP Ban Manager skips its runtime hooks, panel, services, sensors, managed blocked networks, and default-deny handling. Home Assistant will show a Repair warning until you remove the file or YAML key and restart.

This only disables IP Ban Manager. It does not uninstall the integration, and it does not remove Home Assistant's native exact bans from `ip_bans.yaml`.

## Live Management

The options UI is the main workspace. Allowlist, ban list, and automatic-ban setting changes apply immediately; Home Assistant does not need to restart. The integration stores automatic-ban settings in its config entry and reapplies them when Home Assistant starts.

Open **Settings > Devices & services > IP Ban Manager > Configure** to:

- add safe defaults with checkboxes inside **Allowed IPs**
- edit **Allowed IPs**, one IPv4/IPv6 address, CIDR network, or IPv4 wildcard network per line
- enable or disable new automatic bans, automatic ban notifications, and the login-attempt threshold under **Blocked IPs**
- optionally allow automatic exact bans inside **Allowed IPs** for broad trusted subnets
- edit **Blocked entries**, one exact IP address per line
- edit **Blocked networks**, one IPv4/IPv6 CIDR network or IPv4 wildcard network per line
- enable **Block everything outside Allowed IPs** for guarded default-deny mode
- enable **GeoIP location labels** and download/update the local DB-IP City Lite database
- view existing block timestamps as readable local times in **Blocked IPs**
- clear exact bans or managed blocked networks by emptying the matching field and submitting

Wildcard blocked-network entries such as `192.168.1.*` are saved as `192.168.1.0/24`. IPv6 entries should be entered as normal addresses or CIDR networks, such as `2001:db8::1` or `2001:db8::/64`. Exact banned IPs stay in Home Assistant's native live ban manager and `ip_bans.yaml`; CIDR and wildcard blocked networks are stored by IP Ban Manager and enforced behind the same native ban lookup. Allowed entries win over managed blocked networks, so you can block a subnet while keeping a trusted address allowed.

This gives you the practical behavior people expect from subnet banning without pretending Home Assistant's native `ip_bans.yaml` supports ranges. Exact IPs remain ordinary Home Assistant bans; managed networks are a small runtime layer that checks the same request path and still respects the allowlist first.

Existing exact banned IP rows are shown as `IP - local ban time`, oldest first. You can leave those timestamps in place when submitting; IP Ban Manager preserves the original ban date for unchanged bans. New exact banned IP rows can be entered as just the IP address, and Home Assistant records the current ban time when they are submitted. When the ban file is rewritten, entries are written oldest first so new exact bans appear at the bottom in both the UI and `ip_bans.yaml`.

GeoIP location labels are optional. When enabled, IP Ban Manager downloads the free DB-IP City Lite MMDB database to `/config/ip_ban_manager/geoip/dbip-city-lite.mmdb` and reads it locally. Public IP notifications and blocked-IP rows can then show approximate location labels. Private, loopback, and local-network addresses are not looked up. The database is not bundled with the integration and no online IP lookup is made while handling logins or bans. Location data is provided by DB-IP.com and should be treated as approximate.

The options UI validates edits before changing Home Assistant. It rejects all-Internet allowlist or blocked-network entries, IPs that are both allowed and exactly banned, unsafe default-deny changes, and malformed entries. Login-attempt thresholds are clamped server-side, so setup, Configure, and the live panel/API enforce the same range. Service calls use the same safety posture for risky operations, including typo removals, removing the only detected local allowlist path, allowlist networks that contain active exact bans, and clear-all ban requests without `confirm: true`. Default-deny mode also protects Home Assistant's own exact interface addresses internally, so users do not need to expose those implementation details in **Allowed IPs**.

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
