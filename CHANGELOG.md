# Changelog

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
