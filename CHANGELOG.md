# Changelog

## v1.3.2

IP Ban Manager 1.3.2 tightens the local-network lockout safety check for managed blocked networks.

### Fixed

- Blocking a detected local Home Assistant network now requires an allowed entry that covers that detected local network, not just one allowlisted host inside it.
- Mixed IPv4/IPv6 allowlists and blocked-network entries are handled cleanly during the local-network safety check.
- Added regression coverage for the single-host allowlist case so the UI cannot accept a local subnet block that could still lock users out.

## v1.3.1

IP Ban Manager 1.3.1 improves legacy cleanup for systems that previously installed the old `ban_allowlist` folder.

### Fixed

- Opening the new **IP Ban Manager** config flow now absorbs an existing legacy `ban_allowlist` config entry instead of leaving users stuck on the old domain.
- Once the new integration is running, a verified stale `custom_components/ban_allowlist` folder is moved out of Home Assistant's loader path so future restarts do not keep loading the old integration.
- Added regression coverage for both the legacy config-entry absorption path and stale-folder cleanup path.

## v1.3.0

IP Ban Manager 1.3.0 focuses on safer defaults and cleaner protection around real lockout risks.

### Added

- Default the detected local network checkbox on during first setup, so new users are less likely to lock themselves out when using local-only or block-heavy configurations.
- Reject managed blocked-network settings that would cover a detected local Home Assistant network without a matching allowed local entry.

### Improved

- Capture more Home Assistant failed-login notification paths so IP Ban Manager can normalize the messages earlier and more consistently.
- Narrow the clear-ban confirmation so it only appears when clearing multiple exact bans at once. Clearing the only remaining ban now saves directly.
- Keep routine allowlist edits frictionless while still blocking settings that could realistically cut off local access.

## v1.2.15

IP Ban Manager 1.2.15 fixes HACS installation by shipping only the real `ip_ban_manager` integration folder.

### Fixed

- Removed the standalone legacy `custom_components/ban_allowlist` compatibility folder from the release package. HACS only supports one integration folder per repository and was installing `ban_allowlist` instead of `ip_ban_manager` for some users.
- Kept legacy `ban_allowlist:` YAML absorption and stale old-domain config-entry cleanup inside the main `ip_ban_manager` integration.
- Added regression coverage so the repository cannot accidentally ship more than one HACS-managed integration folder again.
- Bumped the manifest to `1.2.15` for HACS update detection.

## v1.2.14

IP Ban Manager 1.2.14 fixes blank banned-IP submissions and adds a safety confirmation before clearing every exact ban.

### Fixed

- Fixed the **Banned entries** field so it can be submitted empty, matching the UI text that says leaving it empty clears exact bans.
- Added a dedicated confirmation step before an empty **Banned entries** submit can remove every current exact IP ban from Home Assistant.
- Added regression coverage for the optional banned-IP field, the clear-all confirmation flow, and branded exact-ban notifications from the allowlisted auto-ban path.
- Bumped both manifests to `1.2.14` for HACS update detection.

## v1.2.13

IP Ban Manager 1.2.13 adds an opt-in backend mode for broad allowlists where individual bad-login sources should still become exact Home Assistant bans.

### Added

- Added **Allow automatic bans inside Allowed IPs** to setup and Configure. When enabled, an allowed subnet can still bypass managed blocked networks, but a specific IP inside that subnet can become an exact Home Assistant ban after failed logins.

### Kept

- The default behavior is unchanged: allowed IPs and networks remain fully trusted and do not become automatic bans unless this new option is enabled.
- Bumped both manifests to `1.2.13` for HACS update detection.

## v1.2.12

IP Ban Manager 1.2.12 tightens the legacy `ban_allowlist` migration cleanup so old cards are removed safely without extra retry churn.

### Fixed

- Removed stale old-domain `ban_allowlist` config entries from the full runtime config-entry list once **IP Ban Manager** exists.
- Added a startup cleanup pass using Home Assistant's started helper, which runs at startup or immediately if Home Assistant is already running.
- Added a safety guard so legacy entries are not removed unless a real `ip_ban_manager` config entry exists, preserving first-time imports.
- Removed the unnecessary delayed retry sweep from the cleanup path.
- Added regression coverage for full-entry cleanup, started-state cleanup, and the no-target safety guard.
- Bumped both manifests to `1.2.12` for HACS update detection.

## v1.2.11

IP Ban Manager 1.2.11 removes stale old-domain entries from the new config-entry setup path.

### Fixed

- Removed leftover `ban_allowlist` config entries when the active **IP Ban Manager** config entry starts.
- Kept first-time old-domain imports safe by letting either migration path remove the old entry without racing or hanging setup.
- Added regression coverage for config-entry startup cleanup.
- Bumped both manifests to `1.2.11` for HACS update detection.

## v1.2.10

IP Ban Manager 1.2.10 removes stale old-domain config entries when a new `ip_ban_manager` entry already exists.

### Fixed

- Added startup cleanup for leftover `ban_allowlist` config entries after the new **IP Ban Manager** entry exists.
- Kept first-time old-domain migration safe by allowing the legacy loader to import old data before the old entry is removed.
- Added regression coverage for stale old-domain entry cleanup.
- Bumped both manifests to `1.2.10` for HACS update detection.

## v1.2.9

IP Ban Manager 1.2.9 is the clean CI release for the legacy migration loader.

### Fixed

- Applied the same Black/isort formatting that GitHub Actions requires for the new legacy migration tests and loader.
- Removed an unused import from the legacy `ban_allowlist` compatibility loader.
- Bumped both manifests to `1.2.9` for HACS update detection.

## v1.2.8

IP Ban Manager 1.2.8 restores the old-domain compatibility loader so existing `ban_allowlist` entries can migrate instead of staying stuck as **Not loaded**.

### Fixed

- Added a tiny legacy `ban_allowlist` loader that imports old stored entries into **IP Ban Manager** and removes the stale old entry.
- Kept new installs and normal setup on the `ip_ban_manager` domain while preserving an upgrade path for users who still have a stored `ban_allowlist` config entry.
- Added regression tests for old-domain entry import and cleanup.
- Bumped the manifest version to `1.2.8` for HACS update detection.

## v1.2.7

IP Ban Manager 1.2.7 completes the visible migration from the old integration name.

### Fixed

- Existing config entries titled `ban_allowlist` or **IP Ban Allowlist** are now renamed to **IP Ban Manager** during setup.
- Added regression coverage so old entry titles cannot keep showing on the Home Assistant Integrations page after the domain migration.
- Bumped the manifest version to `1.2.7` for HACS update detection.

## v1.2.6

IP Ban Manager 1.2.6 unifies the allowlisted-login wording with Home Assistant's notification terminology.

### Changed

- Renamed the **Allowlisted login notices** setup and Configure label to **Allowlisted login notifications**.
- Updated matching README, changelog, notification-action, and test wording so the integration consistently uses "notifications" instead of "notices".
- Bumped the manifest version to `1.2.6` for HACS update detection.

## v1.2.5

IP Ban Manager 1.2.5 is a polish release for the public setup and documentation flow.

### Fixed

- Fixed the first-run setup label for **Allowlisted login notifications** so Home Assistant no longer shows the internal option key.
- Refreshed the README example screenshots from a live Home Assistant install with the current setup, allowlist, and ban-management UI.
- Bumped the manifest version to `1.2.5` for HACS update detection.

## v1.2.4

IP Ban Manager 1.2.4 adds a quieter path for trusted sources that fail authentication without being banned.

### Added

- Added **Allowlisted login notifications** as a setup and Configure option so allowlisted failed-login notifications can be silenced without disabling real ban notifications.
- Added an **Allowlisted login notifications** link directly on allowlisted failed-login notifications. Selecting it silences those low-priority notifications and dismisses the current notification.

### Changed

- Allowlisted failed-login notifications no longer include the settings link, because no IP was blocked and there is usually nothing urgent to manage.
- Silenced allowlisted failed-login notifications still escalate after repeated failures, so a trusted source that keeps failing authentication is still surfaced.
- Bumped the manifest version to `1.2.4` for HACS update detection.

## v1.2.3

IP Ban Manager 1.2.3 fixes one last notification polish issue found after the 1.2.2 release.

### Fixed

- Existing Home Assistant HTTP login/ban notifications are now normalized into the current IP Ban Manager branded format as soon as the integration starts, instead of waiting for the next failed-login event.
- Bumped the manifest version to `1.2.3` for HACS update detection.

## v1.2.2

IP Ban Manager 1.2.2 polishes the Home Assistant repair and persistent notification experience for the public release.

### Fixed

- Switched branded persistent notifications to an embedded icon so the logo does not depend on Home Assistant external/internal URL routing or the old/new integration domain during migration.
- Cleaned the HTTP IP banning repair message so it only shows the required `http.ip_ban_enabled: true` setting. The login-attempt threshold remains managed by IP Ban Manager setup and Configure.
- Kept repair-style notification previews visually consistent with the normal IP Ban Manager login/ban notifications by using a blank Home Assistant notification title and the branded body header.
- Removed a duplicate notification-dismiss call from automatic notification cleanup.
- Bumped the manifest version to `1.2.2` for HACS update detection.

## v1.2.1

IP Ban Manager 1.2.1 fixes the HACS packaging layout so new installs load the real `ip_ban_manager` integration instead of the old migration shim.

### Fixed

- Fixed the packaging path so HACS installs the real `ip_ban_manager` integration cleanly.
- Added direct absorption of leftover `ban_allowlist:` YAML inside the `ip_ban_manager` integration.
- Changed YAML imports to be one-time only: once a UI config entry exists, leftover YAML is ignored instead of overwriting UI-managed settings on restart.
- Bumped the manifest version to `1.2.1` so HACS users get a clean update prompt.

### Upgrade note

- If Home Assistant says "This integration cannot be added from the UI", update to `v1.2.1`, restart Home Assistant, then add **IP Ban Manager** from the UI again. Existing Home Assistant `http:` IP-ban settings can stay in `configuration.yaml`.
- Leftover `ban_allowlist:` YAML is absorbed automatically when IP Ban Manager first loads. After it imports, remove that YAML key and restart; if it is left behind, IP Ban Manager silently ignores it once a UI entry already exists.

## v1.2.0

IP Ban Manager 1.2.0 is the public-ready release for the expanded integration: live exact-IP bans, allowlists, managed network blocks, automatic-ban controls, branded notifications, and the completed `ip_ban_manager` domain migration.

### Highlights

- Manage Home Assistant exact IP bans from the UI without restarting Home Assistant.
- Add managed **Blocked networks** for CIDR ranges and IPv4 wildcard shorthand, such as `192.168.1.0/24` or `192.168.1.*`.
- Keep allowlisted IPs and networks trusted even when they fall inside a managed blocked network.
- Enable or disable automatic bans, automatic-ban notifications, and the login-attempt threshold from setup and Configure.
- Show branded IP Ban Manager login/ban notifications with a compact local icon header and a direct settings link.
- Keep diagnostics for exact bans, allowlisted networks, managed blocked networks, and failed-login sources.

### Changed

- Migrated the integration domain, config URL, entity platform, and service namespace to `ip_ban_manager`.
- Updated HACS-facing metadata, README examples, YAML examples, service names, tests, integration-test fixtures, and development targets for the new domain.
- Refined the README header so the product name is simply **IP Ban Manager** with the icon beside it.

### Improved

- Added a local static icon route for persistent notifications so the icon does not depend on external README or brand asset URLs.
- Removed leftover migration cleanup code from the new integration path; old-domain handling now lives only in the compatibility loader.
- Lowered the documented and HACS minimum Home Assistant version to `2024.7.4` after testing the integration there.

### Compatibility

- Existing config entries are migrated to `ip_ban_manager` automatically on restart.
- Existing YAML is imported into the new domain by the compatibility loader, but new documentation uses `ip_ban_manager:`.
- Services now live under `ip_ban_manager.*`; update automations or scripts that call the older service namespace.

## v1.1.2

### Fixed

- Replaced the dynamic GitHub license badge with a static AGPL-3.0-only Shields badge so HACS does not render a broken license image in cached README views.

### Note

- HACS update/download dialogs may still show the generic "icon not available" placeholder for custom integrations because HACS currently reads those icons from the public brands CDN instead of Home Assistant's local custom-integration brand API. IP Ban Manager ships the correct local brand assets under `custom_components/ip_ban_manager/brand/` and repository-level `brand/`.

## v1.1.1

### Fixed

- Added the repository-level `brand/icon.png` and `brand/logo.png` assets expected by HACS for integration cards and update dialogs.
- Bumped the manifest version so HACS installations can refresh cleanly from the public release instead of staying on cached 1.0.0/1.1.0 metadata.

## v1.1.0

IP Ban Manager 1.1.0 expands the ban-management UI beyond exact IP entries while keeping Home Assistant's native ban manager in charge of exact bans.

### Added

- Separate **Blocked networks** field for CIDR networks and IPv4 wildcard shorthand, such as `192.168.1.*`.
- Allowlist precedence over managed blocked networks, so trusted addresses can stay allowed inside a blocked subnet.
- Optional suppression of Home Assistant's automatic ban/login persistent notifications.
- Diagnostic sensor coverage for managed blocked networks.

### Improved

- First-run setup stores the automatic-ban notification preference correctly.
- First-run setup uses a clean **Automatic ban notifications** checkbox heading instead of exposing the internal option key.
- The options form can submit successfully with an empty **Blocked networks** field.
- Documentation now distinguishes exact Home Assistant bans from IP Ban Manager's managed blocked networks.

## v1.0.0

IP Ban Manager 1.0.0 is the first public release of the expanded integration. It keeps Home Assistant's native IP ban file and ban manager in charge, then adds the UI and safety rails that were missing: setup from the UI, live allowlist edits, live ban review/removal, automatic-ban controls, diagnostics, and scriptable services.

This is still intentionally marked as a **HACK** because Home Assistant does not expose a supported public API for every part of this workflow. The implementation keeps that internal touch point small, tested, and reversible on unload.

### Highlights

- Config flow setup with automatic YAML import.
- Polished UI setup shows automatic-ban controls and allowlist safe-default checkboxes for `127.0.0.1` and Home Assistant's detected local subnet.
- Live **Allowed IPs** and **Banned IPs** management from the integration options, with safe defaults, inline guidance, and readable ban timestamps.
- IPv4 wildcard shorthand for allowlisted networks, such as `192.168.1.*`.
- Immediate ban and allowlist updates without restarting Home Assistant.
- Banned IP timestamp display as readable local times, with timestamps preserved when existing bans remain.
- Atomic writes to Home Assistant's native `ip_bans.yaml` file.
- Oldest-first `ip_bans.yaml` rewrites so new bans appear at the bottom.
- Safety warnings that reject all-Internet allowlist entries, banning allowlisted IPs, typo removals, allowlist networks containing active bans, and unconfirmed clear-all ban service calls before anything is written.
- Consistent live hook installation even when the integration starts with an empty allowlist, plus clean hook restoration when the integration is unloaded.
- Services for adding, removing, and clearing IP bans and allowlist entries.
- Diagnostic sensors for active bans, allowlisted networks, and failed-login sources.
- Cleanup of stale Home Assistant ban/login persistent notifications when the matching IP is unbanned.
- Integration icon, README screenshots, and updated HACS/repository metadata.

### Notes

- Visible integration name is now **IP Ban Manager**.
- Documentation now clearly warns that this integration is a **HACK** because it wraps internal Home Assistant HTTP ban manager behavior.
- Documentation and issue links now point to `Wheemer/ip-ban-manager`.

### Compatibility

- YAML configuration is still imported under the new integration domain.
- Service IDs now use `ip_ban_manager.*` to match the integration domain.
