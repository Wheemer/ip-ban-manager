# Changelog

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

- Config flow setup with automatic YAML import for existing `ban_allowlist` configuration.
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

- Existing `ban_allowlist:` YAML configuration is still imported.
- Service IDs remain under `ban_allowlist.*` so Home Assistant service metadata continues to work with the integration domain.
