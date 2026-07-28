## 🛡️ IP Ban Manager v1.8.1

This release ships **everything in v1.8.0** below, plus a small hotfix: if the live panel went blank after updating from 1.7.x, **restart Home Assistant Core** after this HACS update so the panel re-registers with the versioned script URL.

### ⚡ Automation events

- Home Assistant events for exact IP bans and unbans, login-threshold reached (before auto-ban), allowlisted-login escalation, and allowlist/blocked-network add/remove.

### 🧰 Services

- Added `ip_ban_manager.add_blocked_network`.
- Added `ip_ban_manager.remove_blocked_network`.
- Added `ip_ban_manager.update_geoip`.

### 📓 Logbook

- Successful ban, allowlist, blocked-network, and GeoIP database changes now write Home Assistant logbook entries.

### 🌍 Translations

- Full integration i18n for config flow, options, services, exceptions, repairs, selectors, entity names, and the live panel in **26 languages** (English plus 25 translated locales). Unsupported Home Assistant languages fall back to English.
- Config-flow and options selectors now use Home Assistant `translation_key` labels instead of hardcoded English option text.
- Repair health summaries use localized issue templates, including structured `ip_bans.yaml` access messages.

### 🌐 IPv6 wildcards

- IPv6 wildcard shorthand for allowlisted and blocked networks, such as `2001:db8:1:2:*` (expands to `/64`) and `2001:db8::*` (expands to `/32`), matching the existing IPv4 `192.168.1.*` flow.

### 🧭 Live panel

- Installed integration version shown beside the **IP Ban Manager** panel title.
- Entry metadata (`added_at`, `source`) on allowlist and blocked-network rows for backup import/export and internal tracking.
- Date/time and source labels on **Blocked network** and **Blocked IP** rows, including legacy **Added before tracking** rows where metadata is missing.
- Panel dates and times follow the signed-in Home Assistant user's locale, timezone, and 12/24-hour preference (with safe fallbacks for non-IANA timezone sentinels such as `local`).
- **Allowed IP** rows show the address only; metadata is still stored for backup/export.

### 🔗 Notifications

- Ban and failed-login persistent notifications now link to `/ip_ban_manager` (the live panel) rather than the integration config-entry URL under Settings.
- When notifications are rewritten, old **Open settings** and **Open integrations** link lines are removed before the current panel link is added.

### 🐛 Panel reliability

- The live panel no longer stays on **Loading…** when Home Assistant passes a non-IANA timezone (for example `local`) to date formatting, or when a render error would previously abort the UI silently.

### 🔧 Hotfix (v1.8.1)

- Clears stale frontend panel registrations on setup so upgrades from 1.7.x load the current versioned panel script instead of a blank sidebar panel.
