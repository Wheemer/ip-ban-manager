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
[![License](https://img.shields.io/badge/LICENSE-AGPL--3.0-64748B?style=for-the-badge&labelColor=555555)](https://github.com/Wheemer/ip-ban-manager/blob/main/LICENSE)

[Install](#install) • [Configure](#configure) • [Live Panel](#live-panel) • [Backup And Restore](#backup-and-restore) • [Translations](docs/translations.md) • [Release Summary](RELEASES.md) • [Changelog](CHANGELOG.md)

</div>

Originally created by [palfrey](https://github.com/palfrey). This fork builds on that allowlist work with a live Home Assistant IP ban and allowlist manager UI.

> [!WARNING]
> This integration uses Home Assistant's internal HTTP ban interfaces because there is no public API for these controls. Review your Allowed IPs before enabling restrictive settings, and keep the [emergency-disable instructions](#emergency-disable) accessible.

IP Ban Manager provides a live interface for Home Assistant's built-in [IP filtering and banning](https://www.home-assistant.io/integrations/http/#ip-filtering-and-banning), with allowlists, network blocks, region restrictions, notifications, and automation support.

Requires Home Assistant `2024.7.4` or newer. Designed for Home Assistant OS, Container, and existing Core and Supervised installations.

## What It Does

- **Allowed IPs:** trust IPv4/IPv6 addresses, CIDR networks, wildcard networks like `192.168.1.*` or `2001:db8:1:2:*`, and DNS hostnames. Hostnames are resolved in the background and refreshed periodically, so a DDNS name can follow a changing address.
- **Blocked IPs:** add, remove, review, and clear Home Assistant's native exact IP bans without restarting.
- **Blocked networks:** block CIDR networks and wildcard ranges without pretending `ip_bans.yaml` supports ranges.
- **Default deny:** optionally block everything outside Allowed IPs with guardrails to avoid locking out Home Assistant itself.
- **Live panel:** manage the whole integration from a dedicated page, with optional sidebar access.
- **Incoming activity:** review observed failed logins and optional blocked requests, plus access-log activity from connected NGINX Proxy Manager hosts. Local history is retained for 24 hours.
- **Notifications:** replace Home Assistant's raw ban messages with IP Ban Manager notifications, optional allowlisted-login alerts, and stale-notification cleanup.
- **GeoIP labels and limits:** optionally download a local DB-IP City Lite database for approximate public-IP location labels and country/province access limits.
- **NGINX Proxy Manager edge protection:** mirror rules to the Home Assistant proxy host, or optionally all active NPM proxy hosts.
- **Backup and restore:** save/restore a readable YAML backup under `/config`, or download/upload a backup in the browser.
- **Diagnostics and automation:** numeric sensors, `ip_ban_manager.*` services, and Home Assistant events for scripts and automations.
- **Translations:** the Home Assistant UI and panel ship English plus 25 translated locales, with English fallback. Some notification and runtime messages remain in English. See [Translations](docs/translations.md).

## Screenshots

### Setup

<p align="center">
  <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/docs/images/setup-flow.png" alt="IP Ban Manager setup" width="52%">
</p>

### Live panel

<table>
  <tr>
    <td>
      <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/docs/images/panel-overview.png" alt="IP Ban Manager options and allowed IPs" width="100%">
    </td>
  </tr>
  <tr>
    <td>
      <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/docs/images/panel-rules.png" alt="IP Ban Manager blocked IPs, public region lock, and blocked networks" width="100%">
    </td>
  </tr>
</table>

### Edge protection, GeoIP, and backups

<table>
  <tr>
    <td width="50%">
      <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/docs/images/panel-nginx-proxy-manager.png" alt="IP Ban Manager NGINX Proxy Manager settings" width="100%">
    </td>
    <td width="50%">
      <img src="https://raw.githubusercontent.com/Wheemer/ip-ban-manager/main/docs/images/panel-geoip-backup.png" alt="IP Ban Manager GeoIP and backup tools" width="100%">
    </td>
  </tr>
</table>

## Install

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wheemer&repository=ip-ban-manager&category=integration)

If the button does not work:

1. Open HACS.
2. In **Custom repositories**, add `https://github.com/Wheemer/ip-ban-manager` with category **Integration**.
3. Install **IP Ban Manager**.
4. Restart Home Assistant once so the custom integration is loaded.
5. Add it from **Settings > Devices & services > Add integration**.

Restart Home Assistant Core after a HACS installation or update. Reloading the integration tears down and restores its hooks, services, and panel API, and reloads its runtime Python modules. A reload does not replace every imported module, so it is not a substitute for the post-update Core restart.

## Configure

Normal setup is done from the UI. The integration name is **IP Ban Manager** and service calls use `ip_ban_manager.*`.

Home Assistant's built-in HTTP IP banning must still be enabled.

On Home Assistant `2026.8` and newer, open **Settings > System > Network**, find **HTTP server**, turn on **Enable IP banning**, set **Login attempts before ban** to a positive value, and save. Home Assistant restarts and asks an administrator to confirm the new HTTP settings.

On Home Assistant `2026.7` and older, add this to `configuration.yaml` and restart:

```yaml
http:
  ip_ban_enabled: true
```

The login-attempt threshold is managed by IP Ban Manager after setup. If IP banning is not enabled, IP Ban Manager creates a Home Assistant Repair with a link to Home Assistant's HTTP documentation. It does not edit Home Assistant's HTTP settings or `configuration.yaml` automatically.

Existing `ban_allowlist:` YAML is treated as a one-time migration path. IP Ban Manager absorbs that old allowlist when it first loads; after that, remove the old YAML key and restart Home Assistant. If the old key is left behind, IP Ban Manager ignores it once the UI config entry exists.

## Live Panel

Open **Settings > Devices & services > IP Ban Manager > Configure**. The panel is
administrator-only. **Show in sidebar** adds a menu entry; turning it off does not
remove access through **Configure**.

The panel groups options, allowlists and blocks, incoming activity, Public Region
Lock, NPM edge protection, GeoIP, and backups into their own sections.

List **Add**, **Block**, and **Remove** actions apply immediately. For options, Public Region Lock, and NPM settings, select the section's **Apply** button. These changes do not require a Home Assistant restart.

### Incoming activity

**Incoming Activity** groups requests by source IP and updates automatically. **Recent**
covers the last 15 minutes; **History** retains up to 1,000 sanitized events for 24 hours,
with up to 200 grouped rows displayed. Allowed IPs are excluded from both views.

Home Assistant alone provides failed-login activity and, when enabled, managed blocked
requests. It is not a complete access log or a list of logged-in sessions. Connecting
NGINX Proxy Manager adds access-log entries from the managed proxy hosts.

By default, requests rejected by the current IP Ban Manager policy are hidden. Turn on
**Show blocked requests** to include them in both tabs and log HA-side denials. This
records the effective client IP, reason, method, and path. Repeated HA log messages for
the same IP and reason are rate-limited. Requests rejected before the integration's
HTTP hook cannot be recorded by that hook.

History omits query strings, headers, cookies, and request bodies. Paths and IP addresses
are still retained locally. NPM HTTP 403 responses are marked as possible policy denials;
the access log does not establish which rule or upstream service produced the response.

Blocked-network rows show when each entry was added and how it was created (panel, service, backup restore, and similar sources). Legacy managed-network rows added before tracking show **Added before tracking**. Exact blocked-IP rows show Home Assistant's native ban time and, when GeoIP labels are enabled, an approximate location. Allowed IP rows show the address only.

### Allowed IPs

Allowed IPs are trusted addresses and networks that should not be blocked. Supported entries:

- IPv4 address: `192.168.1.10`
- IPv6 address: `2001:db8::10`
- CIDR network: `192.168.1.0/24`
- IPv6 CIDR network: `2001:db8::/64`
- IPv4 wildcard network: `192.168.1.*`
- IPv6 wildcard network: `2001:db8:1:2:*` (expands to `/64`; `2001:db8::*` expands to `/32`)
- DNS hostname: `phone.example.org` (forward-resolved to its current IPv4/IPv6 addresses; reverse DNS is not used)

DNS hostnames refresh every minute. If resolution fails, the last successful
addresses remain trusted for at most five minutes, then expire. Restoring a
backup refreshes hostname entries immediately; connected NPM rules follow changes
to their resolved addresses automatically.

Allowed entries override managed blocked networks, default-deny mode, and Public Region
Lock. Exact IP bans take precedence; **Bans inside Allowed IPs** permits automatic bans
of trusted sources. With that option off, setup removes pre-existing bans covered by
the allowlist.

The setup and Configure safe-default option can add Home Assistant's detected
local access paths plus relevant internal container/Supervisor addresses. Those
entries are shown in **Allowed IPs** so they are visible, editable, and backed up
with the rest of the integration settings.

### Blocked IPs

Blocked IPs are exact Home Assistant bans. They stay in Home Assistant's native live ban manager and `ip_bans.yaml`.

Existing rows show the IP address and local ban time, plus an approximate location when GeoIP labels and the local database are available. New rows can be entered as just the IP address. When `ip_bans.yaml` is rewritten, the original ban timestamps are preserved and entries stay oldest-first so new bans appear at the bottom, matching Home Assistant's normal file behavior.

### Blocked Networks

Blocked networks are managed by IP Ban Manager. They support IPv4/IPv6 CIDR networks and IPv4/IPv6 wildcard networks. They are enforced behind Home Assistant's normal request path, but they are not written into `ip_bans.yaml` because Home Assistant's native file only supports exact IP bans.

### Safety Checks

IP Ban Manager checks entry formats, conflicting lists, and detected local access paths before applying changes. Individual `/0` allowlist and blocked-network entries are rejected. Clearing multiple exact bans requires confirmation. These checks reduce accidental lockouts but cannot guarantee access through every network or proxy configuration.

Home Assistant's own exact interface addresses, including its IPv6 link-local addresses, are protected internally. Other local clients and detected container/Supervisor networks belong in **Allowed IPs**, where the safe-default option makes those allowances visible and editable.

**Protect integration callbacks** is on by default. It exempts `/auth/token`, `/api/webhook/` routes, and supported integration-specific callback routes from managed network, default-deny, and region restrictions. Integration-specific routes are enabled only when that integration is loaded. Exact IP bans and Home Assistant's own authentication checks still apply.

### NGINX Proxy Manager Edge Protection

IP Ban Manager can optionally enforce its managed rules at NGINX Proxy Manager before unwanted requests reach Home Assistant. Connect with the NPM URL, account email, and password. The password is exchanged for an API token and is not stored by IP Ban Manager.

Set Home Assistant's external URL before connecting. IP Ban Manager matches its exact hostname against NPM proxy hosts. A single match is selected automatically; otherwise, select the appropriate host in the panel. Enabling edge protection mirrors Allowed IPs, exact bans, blocked networks, and default-deny rules. Public Region Lock uses an authorization subrequest to Home Assistant.

Policy changes synchronize automatically. **Protect all NPM domains** applies the policy to every active Proxy Host found during synchronization; newly added hosts are included on the next sync. Home Assistant callback exceptions remain limited to the selected Home Assistant host.

On Home Assistant OS and Supervised installations, the panel detects the Supervisor-managed Nginx Proxy Manager app and pre-fills its local address when available. The NPM email and password are still entered by the user; IP Ban Manager never guesses credentials or reads the NPM database. Container and external-Docker installations continue to use the manual URL field.

Only the clearly marked IP Ban Manager block in each protected host's advanced configuration is changed. Existing NPM settings and unrelated advanced configuration are preserved. Disabling or disconnecting removes our marked blocks from every host IP Ban Manager managed; disconnecting also removes the stored token.

> [!WARNING]
> NPM evaluates edge rules before a request reaches Home Assistant. When **Protect integration callbacks** is enabled, IP Ban Manager mirrors the same protected callback routes to the selected Home Assistant Proxy Host. Exact IP bans still apply to those routes.

## Backup And Restore

The live panel can **Save**, **Restore**, **Download**, and **Upload** a YAML backup.

**Save** / **Restore** use the on-disk file:

```text
/config/ip_ban_manager/ip-ban-manager-backup.yaml
```

**Download** builds the same YAML from the current live settings and saves it through the browser. **Upload** restores from a file you pick on this device.

The backup includes IP Ban Manager settings and a timestamp-preserving copy of Home Assistant's exact IP bans. Invalid backups are rejected before their settings are saved. If applying a validated backup fails, the integration attempts to restore the previous configuration and reports any rollback failure. The GeoIP database and activity history are not included.

Backups also include NGINX Proxy Manager connection settings, the selected proxy host, edge-protection state, and API token. **Treat the backup as a private credential file; do not attach it to public issues.** The account password is not stored or exported. Cached host lists and runtime status are not included.

Restoring the same NPM server and account retains its current token rather than replacing a renewed token with an older backup copy. A new connection uses the backed-up token.

Older backups without NPM settings leave the current NPM connection unchanged. To disable edge protection through a backup, set `settings.nginx_proxy_manager.enabled` to `false` and restore it. Restoring a disabled or different connection first removes our rules from the currently enabled proxy host. If NPM rejects that cleanup, restore stops and reports the error; it does not pretend that remote rules were removed. Restored enabled connections synchronize automatically, including imports through the service. Expired or revoked tokens may require reconnecting through the panel.

## GeoIP Labels

GeoIP labels are optional. Use the GeoIP section's **Download** button to install the free DB-IP City Lite database, then enable location labels and select **Apply**. Enabling the checkbox alone does not download a database. The file is stored at:

```text
/config/ip_ban_manager/geoip/dbip-city-lite.mmdb
```

Remote public-IP locations are looked up locally in the downloaded database. Private and loopback addresses are not geolocated. Location data is approximate and provided by DB-IP.com. The bundled pure-Python MaxMind DB reader avoids installing Python packages inside the Home Assistant container at startup.

The current-region shortcuts use Home Assistant's configured country and home coordinates. Resolving those coordinates to a province/state uses an online OpenStreetMap Nominatim request, cached for 12 hours. This is separate from local lookups of incoming IP addresses.

Reverse-DNS names shown in notifications are separate from GeoIP. Public-address PTR records are resolved through Cloudflare DNS-over-HTTPS, while private and local addresses use Home Assistant's local resolver. Results are cached for 10 minutes.

### Public Region Lock

Public Region Lock has an on/off checkbox and a saved list of countries or provinces/states. Enter an ISO country code such as `CA`, or a subdivision code such as `CA-NL`, set its failed-login threshold, then select **Add**. Adding an existing code updates its threshold. Edit an existing row and select its **Apply** button to save changes, or **Remove** to delete it. Quick-add buttons use Home Assistant's detected current country or province/state.

Select **Enable Public Region Lock** and **Apply** to activate the list. This is a restrictive policy: public IPs outside every listed region, including unknown locations, are blocked. Allowed IPs, local/private traffic, Home Assistant's internal access paths, and the **Protect integration callbacks** option are still respected.

Turning the lock off retains the list but disables both its access restriction and regional retry overrides. Other IP bans and the global failed-login threshold are unchanged. Only the checkbox turns the lock off: turn it off and apply before removing the last region. An empty list cannot be enabled. Enabling or changing an active restriction requires confirmation and a working GeoIP database.

While enabled, province/state thresholds override country thresholds. Zero disables automatic login bans for that matching region, not its access restriction. Listing a country permits the whole country even when a province in it has a different retry threshold. GeoIP locations are approximate, so a legitimate user's ISP may be located in a different region.

Existing single-region settings retain their original access boundary when converted to a list. Manual backups include the list and enabled state. New backups use format 3 to prevent older integration versions from silently ignoring these rules; format 1 and 2 backups remain importable.

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

This does not uninstall the integration or remove Home Assistant's native exact bans from `ip_bans.yaml`. Rules already synchronized to NPM also remain on the proxy; the emergency switch cannot remove those while the integration is disabled.

## Services

IP Ban Manager adds services for scripts and automations. Calls made by signed-in
users require an administrator account; internal automation calls continue to work
without a user context.

All action names below use the `ip_ban_manager.` prefix.

| Action | Required data | Purpose |
| --- | --- | --- |
| `add_ip_ban` | `ip_address` | Ban one IPv4 or IPv6 address. |
| `remove_ip_ban` | `ip_address` | Remove one exact ban and its failed-login counter. |
| `remove_all_ip_bans` | `confirm: true` | Clear all exact bans. |
| `add_allowlist_network` | `network` | Allow an IP, CIDR, wildcard range, or DNS hostname. |
| `remove_allowlist_network` | `network` | Remove an allowed entry. |
| `add_blocked_network` | `network` | Block a CIDR or wildcard range. |
| `remove_blocked_network` | `network` | Remove a managed network block. |
| `update_geoip` | None | Download or update the local GeoIP database. |
| `export_config` | None | Save the on-disk YAML backup. |
| `import_config` | None | Restore the on-disk YAML backup. |

Adding a ban updates Home Assistant's live ban manager and persists to `ip_bans.yaml`. Removing a ban updates the live ban manager, clears that IP's failed-login counter, and rewrites `ip_bans.yaml`. Clearing every ban requires `confirm: true`.

## Automation Events

IP Ban Manager fires small, stable Home Assistant events you can use in automations:

For a ready-made mobile-app automation, import the IP Ban Manager security notifications blueprint:

[![Open your Home Assistant instance and show the blueprint import dialog](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FWheemer%2Fip-ban-manager%2Fmain%2Fblueprints%2Fautomation%2Fip_ban_manager%2Fip_ban_manager_notifications.yaml)

- `ip_ban_manager_ip_banned` — after an exact IP ban is written (`ip_address`, `source`)
- `ip_ban_manager_ip_unbanned` — after an exact IP ban is removed (`ip_address`, `source`)
- `ip_ban_manager_login_failed` — when IP Ban Manager would show a failed-login notification (`ip_address`, `message`, `source`); it respects the notification settings and per-address allowlisted-login silencing.
- `ip_ban_manager_login_threshold_reached` — after an automatic ban is written for a source that reached its threshold (`ip_address`, `attempts`, `threshold`, `source`)
- `ip_ban_manager_allowlisted_login_escalated` — when repeated allowlisted-login failures cross the escalation threshold (`ip_address`, `attempts`, `source`)
- `ip_ban_manager_allowlist_network_added` / `ip_ban_manager_allowlist_network_removed` — allowlist changes (`network`, `source`)
- `ip_ban_manager_blocked_network_added` / `ip_ban_manager_blocked_network_removed` — blocked-network changes (`network`, `source`)

`source` is one of `auto`, `panel`, `service`, or `setup`. Successful **state changes** (ban/unban, allowlist/blocked-network mutations, GeoIP updates) also appear in the Home Assistant logbook. Threshold and escalation events are automation signals only and do not write logbook entries.

Example automation trigger:

```yaml
trigger:
  - platform: event
    event_type: ip_ban_manager_ip_banned
    event_data:
      ip_address: "203.0.113.10"
action:
  - service: ip_ban_manager.remove_ip_ban
    data:
      ip_address: "{{ trigger.event.data.ip_address }}"
```

On startup, IP Ban Manager also reconciles Home Assistant's exact ban list against Allowed IPs. If Home Assistant bans a trusted address before IP Ban Manager finishes loading, the ban is removed automatically after setup unless **Bans inside Allowed IPs** is enabled.

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
