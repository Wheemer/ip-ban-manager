# Release Summary

This page is the quick release map. Click any version for the full notes in [CHANGELOG.md](CHANGELOG.md).

| Release | Highlights |
| --- | --- |
| [v1.6.1](CHANGELOG.md#v161) | Hardening patch: closes a notification-token security gap, stops duplicate panel API routes on reload, and aligns live-panel allowlist-add with existing lockout checks. |
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
