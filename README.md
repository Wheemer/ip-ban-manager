<div align="center">

<h1>
  <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/custom_components/ip_ban_manager/icon.png" width="56" alt="IP Ban Manager icon" align="center">
  IP Ban Manager
</h1>

### Live IP ban and allowlist management for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-CUSTOM-FD7E14?style=for-the-badge&logo=home-assistant&logoColor=white&labelColor=555555)](https://github.com/hacs/integration)
[![Home Assistant 2024.7.4+](https://img.shields.io/badge/HOME%20ASSISTANT-2024.7.4%2B-41BDF5?style=for-the-badge&logo=home-assistant&logoColor=white&labelColor=555555)](https://www.home-assistant.io/)
[![Latest release](https://img.shields.io/github/v/release/Wheemer/ip-ban-manager?style=for-the-badge&logo=github&logoColor=white&label=RELEASE&labelColor=555555&color=22C55E)](https://github.com/Wheemer/ip-ban-manager/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Wheemer/ip-ban-manager/total?style=for-the-badge&logo=github&logoColor=white&label=DOWNLOADS&labelColor=555555&color=8A2BE2)](https://github.com/Wheemer/ip-ban-manager/releases)
[![License: AGPL-3.0-only](https://img.shields.io/badge/LICENSE-AGPL--3.0--only-64748B?style=for-the-badge&labelColor=555555)](LICENSE)

[Install](#install) • [Configure](#configure) • [Live Panel](#live-panel) • [Backup And Restore](#backup-and-restore) • [Changelog](CHANGELOG.md)

</div>

Originally created by [palfrey](https://github.com/palfrey). This fork builds on that allowlist work with a live Home Assistant IP ban and allowlist manager UI.

> [!WARNING]
> **THIS IS A HACK. USE AT YOUR OWN RISK.** Home Assistant does not provide a public integration API for changing the HTTP IP ban manager at runtime, so this integration uses a small internal hook around Home Assistant's built-in ban manager.

IP Ban Manager gives Home Assistant's built-in [IP filtering and banning](https://www.home-assistant.io/integrations/http/#ip-filtering-and-banning) the management UI it has always needed: trusted IPs and networks, live ban review and removal, managed network blocks, default-deny controls, GeoIP labels, backup/restore, services, diagnostics, and branded notifications.

## Release Notes

| Release | Highlights |
| --- | --- |
| [v1.6.0](CHANGELOG.md#v160) | Adds manual export/import buttons and services for `/config/ip_ban_manager/ip-ban-manager-backup.yaml`, plus the IPv6 link-local default-deny fix. |
| [v1.5.6](CHANGELOG.md#v156) | Deletes the invalid nested `custom_components` folder left behind by the broken `v1.5.2` package layout. |
| [v1.5.5](CHANGELOG.md#v155) | Keeps Home Assistant OS add-ons and Supervisor Docker traffic out of managed blocks/default-deny without exposing those internal addresses in Allowed IPs. |
| [v1.5.4](CHANGELOG.md#v154) | Corrects default-deny safety validation so Home Assistant's own addresses are protected internally while valid local allowlists are not rejected for unrelated detected adapter paths. |
| [v1.5.3](CHANGELOG.md#v153) | Clean HACS packaging recovery release: ships the release zip with the integration files at the zip root and adds workflow validation so bad zip layouts cannot be uploaded silently. |
| [v1.5.0](CHANGELOG.md#v150) | Adds optional local GeoIP location labels for public IPs using a downloaded DB-IP City Lite database and hardens the **Don't show for this address again** notification action. |
| [v1.4.8](CHANGELOG.md#v148) | Bumps the bundled panel asset so Home Assistant reloads the IPv4/IPv6 panel wording, and aligns Configure helper text with the live panel. |
| [v1.4.7](CHANGELOG.md#v147) | Tightens the legacy upgrade path so old `ban_allowlist` config entries are absorbed and removed automatically after IP Ban Manager starts. |
| [v1.4.6](CHANGELOG.md#v146) | Completes the IPv4/IPv6 polish pass with dual-stack local subnet detection, IPv6 notification actions, clearer exact-IP wording, and tighter per-address notification silencing. |
| [v1.4.5](CHANGELOG.md#v145) | Locks down the live panel status API and notification-silence action so both explicitly require a Home Assistant administrator. |
| [v1.4.4](CHANGELOG.md#v144) | Tightens backend lockout safety for default-deny mode, service calls, and panel/API option writes, with server-side threshold clamping. |
| [v1.4.3](CHANGELOG.md#v143) | Adds the `/config/ip_ban_manager.disabled` emergency file alongside `ip_ban_manager: disabled`, so SMB/file access can disable only IP Ban Manager without editing YAML. |
| [v1.4.2](CHANGELOG.md#v142) | Cleans up the emergency YAML disable path to `ip_ban_manager: disabled`, updates the Repair/README wording, and keeps the earlier emergency key accepted for compatibility. |
| [v1.4.1](CHANGELOG.md#v141) | Adds an emergency YAML disable switch, fixes hostname/default-deny matching for IPv4-mapped addresses, and keeps Configure opening the panel when the sidebar entry is hidden. |
| [v1.4.0](CHANGELOG.md#v140) | Adds the dedicated live management panel, optional sidebar access, guarded default-deny mode, local-subnet lockout protection, and a separate **Advanced** area for riskier controls. |
| [v1.3.5](CHANGELOG.md#v135) | Adds per-address muting for low-priority allowlisted failed-login notifications, while still escalating repeated failures from trusted sources. |
| [v1.3.4](CHANGELOG.md#v134) | Fixes managed blocked-network enforcement after Home Assistant reloads `ip_bans.yaml`, and avoids rewriting the native ban file during integration setup. |
| [v1.3.3](CHANGELOG.md#v133) | Adds Home Assistant Repairs for leftover legacy YAML and failed legacy-folder cleanup, while keeping cleanup files contained under `custom_components/ip_ban_manager/.cleanup`. |
| [v1.3.2](CHANGELOG.md#v132) | Tightens local-network lockout safety: blocking a detected local network now requires an allowlist entry that keeps that detected network reachable, not just one host inside it. |
| [v1.3.1](CHANGELOG.md#v131) | Improves legacy cleanup by absorbing old `ban_allowlist` config entries from the new config flow and moving stale old folders out of Home Assistant's loader path. |
| [v1.3.0](CHANGELOG.md#v130) | Safer first-run defaults, local-network lockout validation, earlier failed-login notification capture, and smarter clear-ban confirmation only when multiple bans would be removed. |
| [v1.2.15](CHANGELOG.md#v1215) | Fixes HACS installs by shipping only the real `ip_ban_manager` integration folder; old `ban_allowlist` YAML is still absorbed by IP Ban Manager. |
| [v1.2.14](CHANGELOG.md#v1214) | Fixes blank **Banned entries** submissions and adds a confirmation screen before clearing every exact IP ban. |
| [v1.2.13](CHANGELOG.md#v1213) | Optional **Allow automatic bans inside Allowed IPs** setting for carrier/VPN subnet allowlists where individual failed-login sources should still become exact Home Assistant bans. |
| [v1.2.12](CHANGELOG.md#v1212) | Safer legacy `ban_allowlist` cleanup: removes stale old-domain cards only after **IP Ban Manager** exists, with startup cleanup and regression tests. |
| [v1.2.11](CHANGELOG.md#v1211) | Removes stale old-domain `ban_allowlist` cards from the new **IP Ban Manager** config-entry startup path. |
| [v1.2.10](CHANGELOG.md#v1210) | Removes stale old-domain `ban_allowlist` cards once the new **IP Ban Manager** entry exists, while preserving first-time migration. |
| [v1.2.9](CHANGELOG.md#v129) | Clean CI release for the legacy `ban_allowlist` migration loader, with GitHub Actions formatting/lint fixes. |
| [v1.2.8](CHANGELOG.md#v128) | Restores a tiny old-domain compatibility loader so existing `ban_allowlist` entries migrate into **IP Ban Manager** instead of staying **Not loaded**. |
| [v1.2.7](CHANGELOG.md#v127) | Completes the visible migration by renaming old `ban_allowlist` / **IP Ban Allowlist** config entries to **IP Ban Manager** during setup. |
| [v1.2.6](CHANGELOG.md#v126) | Unified allowlisted-login wording with Home Assistant terminology: **Allowlisted login notifications** everywhere. |
| [v1.2.5](CHANGELOG.md#v125) | Setup polish for the **Allowlisted login notifications** label plus refreshed live Home Assistant screenshots for setup, allowlist management, and ban management. |
| [v1.2.4](CHANGELOG.md#v124) | Quieter allowlisted failed-login notifications with a matching **Allowlisted login notifications** option and notification link, while repeated failures still escalate. |
| [v1.2.3](CHANGELOG.md#v123) | Startup notification cleanup so existing Home Assistant HTTP notifications are normalized into the current branded format immediately. |
| [v1.2.2](CHANGELOG.md#v122) | Repair-message cleanup and embedded notification logo so branded IP Ban Manager notifications do not depend on Home Assistant URL routing. |
| [v1.2.1](CHANGELOG.md#v121) | HACS packaging fix so new installs load the real `ip_ban_manager` integration cleanly and absorb leftover YAML. |
| [v1.2.0](CHANGELOG.md#v120) | Public-ready release with managed **Blocked networks**, allowlist precedence, automatic-ban notification controls, diagnostics, branded notifications, and full `ip_ban_manager` domain migration. |
| [v1.1.2](CHANGELOG.md#v112) | README and HACS display polish, including a more reliable license badge. |
| [v1.1.1](CHANGELOG.md#v111) | Repository brand assets so HACS and Home Assistant can discover the integration icon where supported. |
| [v1.1.0](CHANGELOG.md#v110) | Managed **Blocked networks** for CIDR ranges and IPv4 wildcard shorthand, allowlist precedence over blocked networks, automatic-ban notification control, and blocked-network diagnostics. |
| [v1.0.0](CHANGELOG.md#v100) | First public IP Ban Manager release with config-flow setup, YAML import, live **Allowed IPs** and **Blocked IPs** editing, automatic-ban controls, services, diagnostics, and safer file handling. |

See [CHANGELOG.md](CHANGELOG.md) for every release, upgrade note, and full change detail.

## What It Does

- **Allowed IPs:** trust IPv4/IPv6 addresses, CIDR networks, and IPv4 wildcard networks like `192.168.1.*`.
- **Blocked IPs:** add, remove, review, and clear Home Assistant's native exact IP bans without restarting.
- **Blocked networks:** block CIDR networks and IPv4 wildcard ranges without pretending `ip_bans.yaml` supports ranges.
- **Default deny:** optionally block everything outside Allowed IPs with guardrails to avoid locking out Home Assistant itself.
- **Live panel:** manage the whole integration from a dedicated page, with optional sidebar access.
- **Notifications:** replace Home Assistant's raw ban messages with IP Ban Manager notifications, optional allowlisted-login alerts, and stale-notification cleanup.
- **GeoIP labels:** optionally download a local DB-IP City Lite database for approximate public-IP location labels.
- **Backup and restore:** export and import a readable YAML backup from the panel or services.
- **Diagnostics and automation:** numeric sensors plus `ip_ban_manager.*` services for scripts and automations.

## Screenshots

<table>
  <tr>
    <td>
      <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/docs/images/live-panel-v1.4.png" alt="IP Ban Manager live panel" width="100%">
    </td>
  </tr>
</table>

## Install

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wheemer&repository=ip-ban-manager&category=integration)

If the button does not work:

1. Open HACS.
2. Add `Wheemer/ip-ban-manager` as a custom integration repository.
3. Install **IP Ban Manager**.
4. Restart Home Assistant once so the custom integration is loaded.
5. Add it from **Settings > Devices & services > Add integration**.

## Configure

Normal setup is done from the UI. The integration name is **IP Ban Manager** and service calls use `ip_ban_manager.*`.

Home Assistant's built-in HTTP IP banning must still be enabled:

```yaml
http:
  ip_ban_enabled: true
```

The login-attempt threshold is managed by IP Ban Manager after setup. If IP banning is not enabled, IP Ban Manager creates a Home Assistant Repair with the required YAML and a link to Home Assistant's HTTP documentation. It does not edit `configuration.yaml` automatically, which keeps existing comments, includes, proxy settings, and packages safe.

Existing `ban_allowlist:` YAML is treated as a one-time migration path. IP Ban Manager absorbs that old allowlist when it first loads; after that, remove the old YAML key and restart Home Assistant. If the old key is left behind, IP Ban Manager ignores it once the UI config entry exists.

## Live Panel

Open **Settings > Devices & services > IP Ban Manager > Configure** to manage:

- Allowed IPs and networks
- Blocked IPs from Home Assistant's native `ip_bans.yaml`
- Managed blocked networks
- Automatic-ban settings
- Default-deny mode
- Allowlisted-login notification settings
- Local GeoIP database download/update
- Manual backup and restore

Changes apply immediately. Home Assistant does not need to restart after list edits or option changes.

### Allowed IPs

Allowed IPs are trusted addresses and networks that should not be blocked. Supported entries:

- IPv4 address: `192.168.1.10`
- IPv6 address: `2001:db8::10`
- CIDR network: `192.168.1.0/24`
- IPv6 CIDR network: `2001:db8::/64`
- IPv4 wildcard network: `192.168.1.*`

Allowed entries win over managed blocked networks and default-deny mode.

### Blocked IPs

Blocked IPs are exact Home Assistant bans. They stay in Home Assistant's native live ban manager and `ip_bans.yaml`.

Existing rows show `IP - local ban time`. Leave the timestamp in place to preserve the original ban date. New rows can be entered as just the IP address. When `ip_bans.yaml` is rewritten, entries stay oldest-first so new bans appear at the bottom, matching Home Assistant's normal file behavior.

### Blocked Networks

Blocked networks are managed by IP Ban Manager. They support IPv4/IPv6 CIDR networks and IPv4 wildcard networks. They are enforced behind Home Assistant's normal request path, but they are not written into `ip_bans.yaml` because Home Assistant's native file only supports exact IP bans.

### Safety Checks

IP Ban Manager validates changes before writing them. It rejects malformed entries, all-Internet allowlists or network blocks, exact bans that are also allowed, risky default-deny changes, unsafe local lockout cases, and unconfirmed multi-ban clears.

Home Assistant's own exact interface addresses, IPv6 link-local access paths, Supervisor traffic, and add-on internals are protected internally without exposing those implementation details in Allowed IPs.

## Backup And Restore

The live panel includes **Export** and **Import** buttons. Export writes:

```text
/config/ip_ban_manager/ip-ban-manager-backup.yaml
```

The backup includes IP Ban Manager settings plus a timestamp-preserving copy of Home Assistant's exact IP bans. It is not written automatically; use Export when you want an offline copy over SMB, SSH, Studio Code Server, or a normal Home Assistant backup.

Import reads that same file, validates it, and applies it live. If validation fails, nothing is changed.

## GeoIP Labels

GeoIP labels are optional. When enabled, IP Ban Manager downloads the free DB-IP City Lite MMDB database to:

```text
/config/ip_ban_manager/geoip/dbip-city-lite.mmdb
```

Lookups are local only. No live online IP lookup is made while handling logins or bans. Private, loopback, and local-network addresses are not looked up. Location data is approximate and provided by DB-IP.com.

## Emergency Disable

If you accidentally lock yourself out with IP Ban Manager settings, disable only this integration with either emergency path, then restart Home Assistant.

Create this empty file:

```text
/config/ip_ban_manager.disabled
```

Or add this to `configuration.yaml`:

```yaml
ip_ban_manager: disabled
```

When either path is active, IP Ban Manager skips its runtime hooks, panel, services, sensors, managed blocked networks, and default-deny handling. Home Assistant will show a Repair warning until you remove the file or YAML key and restart.

This does not uninstall the integration and does not remove Home Assistant's native exact bans from `ip_bans.yaml`.

## Services

IP Ban Manager adds services for scripts and automations:

- `ip_ban_manager.add_ip_ban`
- `ip_ban_manager.remove_ip_ban`
- `ip_ban_manager.remove_all_ip_bans`
- `ip_ban_manager.add_allowlist_network`
- `ip_ban_manager.remove_allowlist_network`
- `ip_ban_manager.export_config`
- `ip_ban_manager.import_config`

Adding a ban updates Home Assistant's live ban manager and persists to `ip_bans.yaml`. Removing a ban updates the live ban manager, clears that IP's failed-login counter, and rewrites `ip_bans.yaml`. Clearing every ban requires `confirm: true`.

## Diagnostic Sensors

IP Ban Manager adds numeric diagnostic sensors with detailed attributes:

- `sensor.ip_ban_manager_active_bans`
- `sensor.ip_ban_manager_allowlisted_networks`
- `sensor.ip_ban_manager_blocked_networks`
- `sensor.ip_ban_manager_failed_login_sources`

## Notes For Existing Users

- Old `ban_allowlist` YAML is imported once, then should be removed.
- Old `custom_components/ban_allowlist` installs are cleaned up after IP Ban Manager starts.
- HACS installs should contain only `custom_components/ip_ban_manager`.
- Release details are in [CHANGELOG.md](CHANGELOG.md).
