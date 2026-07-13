# Changelog

## v1.6.1

IP Ban Manager 1.6.1 is a hardening patch. It closes a notification-token security gap, stops duplicate live-panel API routes after integration reload, and brings panel allowlist-add in line with the lockout safety checks that already existed elsewhere. No new user-facing features.

### Fixed

- Closed a gap where a notification action token could globally disable all allowlisted-login notifications without an administrator session. Per-address **Don't show for this address again** behavior is unchanged.
- Reloading the integration no longer registers duplicate status, manage, or silence HTTP endpoints. Setup now skips paths that are already registered for the Home Assistant process.
- Adding an allowlist entry from the live panel now runs the same lockout safety checks already used when removing allowlist entries or editing blocked networks, and still rejects networks that would cover an exact banned IP.
- Panel boolean settings now use Home Assistant's boolean parsing instead of raw Python `bool()`, so string values like `"false"` are not treated as enabled.

## v1.6.0

IP Ban Manager 1.6.0 adds manual backup/restore for managed settings and exact bans, and fixes default-deny access when a local Home Assistant hostname resolves to IPv6 link-local instead of IPv4.

### Added

- Added **Export** and **Import** buttons to the live panel. Export writes `/config/ip_ban_manager/ip-ban-manager-backup.yaml`; Import validates and restores that same file manually.
- Added `ip_ban_manager.export_config` and `ip_ban_manager.import_config` services for automations/scripts that want the same manual backup/restore behavior.
- Backup files include IP Ban Manager's managed settings plus a timestamp-preserving copy of Home Assistant's exact IP bans.

### Fixed

- Treats enabled-adapter IPv6 link-local networks as local access paths, so the safe local-network default can include them.
- Keeps enabled-adapter IPv6 link-local access out of managed blocks/default-deny at runtime, matching the behavior users expect when `homeassistant.local` resolves to IPv6 on the same LAN.

## v1.5.6

IP Ban Manager 1.5.6 cleans up the bad nested folder layout left behind by the broken `v1.5.2` package.

### Fixed

- Deletes the invalid `custom_components/ip_ban_manager/custom_components/` folder if a previous bad package left it behind.
- Keeps the legacy `.cleanup` path only for old legacy folders that may contain user-modified files.

## v1.5.5

IP Ban Manager 1.5.5 widens the hidden Home Assistant internal bypass used by managed network blocks and default-deny mode.

### Fixed

- Default-deny mode now bypasses the full Home Assistant Supervisor Docker parent network (`172.30.0.0/16`) instead of only the narrower Supervisor readiness subnet.
- This keeps add-ons and other Home Assistant OS internal callers out of IP Ban Manager's managed block/default-deny path without adding those internal addresses to the visible allowlist.

## v1.5.4

IP Ban Manager 1.5.4 corrects the default-deny safety path so valid local allowlists are not rejected just because Home Assistant reports another adapter path.

### Fixed

- Protected Home Assistant's own exact interface addresses and Supervisor/internal paths from managed blocked networks and default-deny checks without adding those addresses to the visible allowlist.
- Relaxed default-deny validation so it requires a real local access path, not every detected Home Assistant-facing subnet.
- Kept explicit blocked-network validation strict when a managed block overlaps a detected local access path without an allowed route back in.
- Reworded setup, Configure, and panel/API safety errors so they describe the actual local access-path risk instead of blaming a vague detected Home Assistant network.

### Validation

- Added regression coverage for default-deny setups with one valid visible local path, empty detected-subnet results, explicit local block rejection, and Home Assistant self-address bypass.
- Bumped the manifest version to `1.5.4` for HACS update detection.

## v1.5.3

IP Ban Manager 1.5.3 is a clean packaging recovery release for HACS installs after the first `v1.5.2` zip was generated with the wrong folder layout.

### Fixed

- Fixed the HACS release zip layout so `manifest.json` and the integration files are at the zip root, where HACS expects them for `zip_release` installs.
- Added release-zip validation to the GitHub release workflow so future packages fail before upload if they contain `custom_components/` nesting, miss required root files, or include Python cache files.
- Bumped the manifest version to `1.5.3` so HACS users get a clean update path instead of wondering whether they have the corrected `v1.5.2` asset.

## v1.5.2

IP Ban Manager 1.5.2 is a packaging-only release that switches HACS updates to a dedicated release zip so GitHub can count future HACS downloads correctly.

### Fixed

- Added HACS `zip_release` metadata for `ip-ban-manager.zip`.
- Added an automated release asset workflow so future GitHub releases attach the HACS zip consistently.

## v1.5.1

IP Ban Manager 1.5.1 is a focused fix release for numeric diagnostics, Supervisor update compatibility, and the default-deny panel option.

### Fixed

- Diagnostic count sensors now advertise `state_class: measurement` and an empty unit so Home Assistant treats them as numeric and can graph them.
- Default-deny safety checks now ignore Home Assistant Supervisor's internal Docker network while still preserving Supervisor readiness checks during Core updates.
- The live panel now surfaces backend validation text instead of a raw `Response error: 400` when a safety check rejects a change.

## v1.5.0

IP Ban Manager 1.5.0 adds optional local GeoIP location labels for public IP addresses and tightens the allowlisted-login notification action.

### Added

- Added a **GeoIP location labels** option to the live panel and Configure flow.
- When enabled, IP Ban Manager downloads DB-IP City Lite to `/config/ip_ban_manager/geoip/dbip-city-lite.mmdb` and reads it locally.
- Blocked-IP rows and IP Ban Manager notifications can show approximate city/country labels for public IPs, with a quiet DB-IP attribution footer in notifications and a linked DB-IP City Lite credit in the panel.
- Added a lightweight panel health check for IP-ban setup, panel registration, legacy cleanup, GeoIP readiness, and ban-file access.
- Added small rolling snapshots before IP Ban Manager rewrites `ip_bans.yaml`.
- Added internal panel/API metrics so diagnostics can show write activity, API errors, snapshots, GeoIP lookups, and reverse-DNS cache use.

### Fixed

- Hardened **Don't show for this address again** so it dismisses matching allowlisted-login notifications even when the visible message has been rebranded, rewritten, or only carries the address inside the action URL.
- Silenced allowlisted addresses now stay quiet even after repeated failed-login escalation would normally create a stronger notification.
- New **Don't show for this address again** links now open the admin-only IP Ban Manager panel and use the admin-protected manage API; the token endpoint remains only as a compatibility fallback for older notifications.
- The bundled panel is registered with Home Assistant's `require_admin` flag, so non-admin users cannot open the IP Ban Manager panel.
- Bumped the bundled panel web component and static asset URL to `panel-v18.js` so Home Assistant loads the current GeoIP panel wording, credit area, quiet background refreshes, and admin-only notification action.
- Moved legacy-folder cleanup and GeoIP reader warmup off the startup-critical setup path while keeping the ban hooks and safety checks applied immediately.
- Panel API responses now return a consistent success/error shape so the UI can show clean messages without falling back to raw object errors.
- Reverse-DNS names are cached briefly for allowlisted-login notifications so repeated failures do not keep hitting the resolver.
- Health checks now read panel registration from Home Assistant's real panel state and stay quiet when everything is healthy.
- Snapshot metrics are updated on the Home Assistant event loop, not from the file-writing executor thread.
- Repeated panel option saves now skip unchanged config-entry writes and apply follow-up live settings from the updated entry state.
- Allowlisted-login silence actions now use the same guarded option writer, so repeated clicks can dismiss matching notifications without rewriting unchanged options.

### Privacy

- No online IP lookup is made while handling logins, bans, notifications, or panel status.
- Private, loopback, and local-network addresses are skipped.

### Validation

- Added regression coverage for GeoIP panel status, enabling the option, notification location text, and encoded allowlisted-login notification action dismissal.
- Added regression coverage for panel health, structured panel API errors, live API payloads, and ban-file snapshots.
- Added regression coverage for unchanged panel option saves so they do not churn config storage.
- Added regression coverage for repeated per-address silence actions dismissing notices without extra config writes.
- Bumped the manifest version to `1.5.0` for HACS update detection.

## v1.4.8

IP Ban Manager 1.4.8 makes the already-published IPv4/IPv6 UI wording reliably visible after HACS updates.

### Fixed

- Bumped the bundled panel web component and static asset URL from `panel-v9.js` to `panel-v10.js`, forcing Home Assistant and browsers to load the current panel instead of a stale cached copy.
- Updated the Configure dialog helper text so **Allowed IPs**, **Blocked IPs**, and **Blocked networks** all explicitly describe IPv4/IPv6 support.

### Validation

- Verified the panel asset served from Home Assistant includes the IPv4/IPv6 wording and `ip-ban-manager-panel-v10` after installation.
- Bumped the manifest version to `1.4.8` for HACS update detection.

## v1.4.7

IP Ban Manager 1.4.7 tightens the legacy upgrade path so old installs finish migrating without leaving a dead `ban_allowlist` card behind.

### Fixed

- When the UI setup flow absorbs an old `ban_allowlist` config entry, IP Ban Manager now records that exact legacy entry and removes it automatically after the new `ip_ban_manager` entry starts.
- The one-time migration marker is scrubbed from the new config entry immediately after setup, so the stored entry stays clean after migration.
- Existing fallback cleanup for leftover legacy entries and stale `custom_components/ban_allowlist` folders remains in place.

### Validation

- Added regression coverage for config-flow legacy absorption, exact old-entry removal, and migration-marker cleanup.
- Bumped the manifest version to `1.4.7` for HACS update detection.

## v1.4.6

IP Ban Manager 1.4.6 tightens the notification action path and completes the IPv4/IPv6 polish pass before the next public release.

### Fixed

- The **Don't show for this address again** notification action now preserves the saved silenced-address order instead of rebuilding it from an unordered set.
- Per-address allowlisted-login notification silencing now dismisses only matching notifications for that IP address.
- Rewritten allowlisted-login notifications now detect IPv6 addresses when adding the per-address silence action.
- Exact blocked-IP validation text now correctly says exact IPv4 or IPv6 addresses only; CIDR ranges and wildcards belong in **Blocked networks**.

### Improved

- First-run local-network detection now considers useful IPv4 and IPv6 subnets from Home Assistant's enabled/default adapters.
- Loopback, link-local, multicast, and unspecified detected networks are still ignored so the automatic safe default stays conservative.
- README and service descriptions now spell out IPv4/IPv6 support clearly, while keeping IPv4 wildcard shorthand documented as IPv4-only.

### Validation

- Added regression coverage for dual-stack local subnet detection and first-run setup imports.
- Added regression coverage for IPv6 Allowed IPs, exact Blocked IPs, Blocked Networks, services, and allowlisted-login notification actions.
- Bumped the manifest version to `1.4.6` for HACS update detection.

## v1.4.5

IP Ban Manager 1.4.5 locks down the live management API used by the bundled panel and notification actions.

### Security

- The live status endpoint now explicitly requires a Home Assistant administrator.
- The allowlisted-login notification silence endpoint now explicitly requires a Home Assistant administrator before changing integration options.

### Validation

- Added regression coverage for admin-only access on the status and notification-silence endpoints.
- Bumped the manifest version to `1.4.5` for HACS update detection.

## v1.4.4

IP Ban Manager 1.4.4 tightens the safety rails around default-deny mode, service calls, and direct panel/API option writes.

### Safety

- Prevented the `remove_allowlist_network` service from removing the only local allowlist path when default-deny mode or managed network blocks would leave Home Assistant unreachable.
- Default-deny mode now refuses to enable unless Home Assistant can detect a local subnet and verify that the subnet stays allowed.
- Login-attempt thresholds are now clamped in backend code from every entry point, so setup, Configure, and the live panel/API all enforce the same `0` to `100` range.

### Improved

- Cleaned up internal emergency-disable naming so the code reflects both supported recovery paths: `ip_ban_manager: disabled` and `/config/ip_ban_manager.disabled`.

### Validation

- Added regression coverage for service-level allowlist lockout prevention, default-deny without a detected local subnet, and backend threshold clamping.
- Bumped the manifest version to `1.4.4` for HACS update detection.

## v1.4.3

IP Ban Manager 1.4.3 adds a second emergency recovery path for users who can reach Home Assistant files over SMB, Studio Code Server, terminal, or another local file-access method.

### Added

- Added `/config/ip_ban_manager.disabled` as an emergency disable file. If the file exists when Home Assistant starts, IP Ban Manager stands down without installing runtime hooks, panel, services, sensors, managed blocked networks, or default-deny handling.
- Kept `ip_ban_manager: disabled` as the YAML emergency option. Either recovery path works by itself; if both are present, IP Ban Manager still stands down.

### Improved

- Updated the Repair message to tell users how to re-enable IP Ban Manager from either emergency path.
- Updated the README emergency section to prefer the file-based escape hatch for SMB-friendly recovery, while still documenting the YAML option.

### Validation

- Added regression coverage for the emergency disable file by itself and together with the YAML disable option.
- Bumped the manifest version to `1.4.3` for HACS update detection.

## v1.4.2

IP Ban Manager 1.4.2 cleans up the emergency recovery YAML so the documented escape hatch is simple and obvious.

### Fixed

- Replaced the awkward emergency-disable example with the cleaner supported form:

  ```yaml
  ip_ban_manager: disabled
  ```

- Updated the Home Assistant Repair text to show the same clean recovery key.
- Clarified in the README that the emergency switch only disables IP Ban Manager. It does not uninstall the integration or remove Home Assistant's native exact bans from `ip_bans.yaml`.

### Compatibility

- Kept the earlier `disable_ban_manager: true` emergency key accepted quietly, so anyone who already copied the old snippet is not stranded during recovery.

### Validation

- Added regression coverage for the clean emergency-disable YAML form and the legacy compatibility key.
- Bumped the manifest version to `1.4.2` for HACS update detection.

## v1.4.1

IP Ban Manager 1.4.1 tightens the new default-deny and sidebar behavior, and adds a local-file emergency disable option for recovery.

### Added

- Added a YAML emergency kill switch: `ip_ban_manager: disable_ban_manager: true`. When enabled, IP Ban Manager skips its runtime hooks, panel, services, sensors, managed blocked networks, and default-deny handling, then creates a Repair warning so the disabled state is visible after Home Assistant starts.

### Fixed

- Fixed default-deny allowlist matching for IPv4-mapped IPv6 addresses, so hostname access paths like `::ffff:192.168.1.x` still match IPv4 Allowed IPs such as `192.168.1.0/24`.
- Fixed **Show in sidebar** so it hides only the left-menu entry. The IP Ban Manager panel remains registered for **Settings > Devices & services > IP Ban Manager > Configure**.

### Validation

- Added regression coverage for the emergency YAML disable path, IPv4-mapped IPv6 allowlist matching, and sidebar-hidden Configure access.

## v1.4.0

IP Ban Manager 1.4.0 adds a guarded default-deny mode for users who want to allow only trusted addresses and networks, while separating lockout-sensitive controls from everyday options.

### Added

- Added **Block everything outside Allowed IPs** to setup and Configure.
- Default-deny mode blocks any source address that is not covered by Allowed IPs, while exact IP bans and specific Blocked networks continue to work normally.
- The Blocked Networks diagnostic sensor now reports whether default-deny mode is enabled.
- Split potentially disruptive controls into an **Advanced** group in setup, Configure, and the live panel so risky choices stand apart from normal notification/sidebar options.

### Safety

- Default-deny mode refuses to save when it would block a detected local Home Assistant subnet without a matching Allowed IPs entry.
- `127.0.0.1` remains available as a setup safe default, and the detected local subnet checkbox is selected by default on first setup.
- The live panel marks risky controls with short **Be careful** descriptions instead of mixing them in with routine settings.

### Validation

- Added regression coverage for setup and Configure default-deny safety, live lookup behavior, allowlist precedence, and diagnostic attributes.
- Bumped the manifest version to `1.4.0` for HACS update detection.

## v1.3.5

IP Ban Manager 1.3.5 makes allowlisted failed-login notifications less noisy without hiding real trouble.

### Added

- Allowlisted failed-login notifications now include a **Don't show for this address again** link for the specific source IP.
- Silenced allowlisted addresses are saved in the integration options, so the choice survives restarts and future Configure saves.

### Safety

- Per-address silencing only suppresses low-priority allowlisted login notices. IP Ban Manager still escalates if that trusted address repeatedly fails authentication.

### Validation

- Added regression coverage for per-address notification silencing, preserving notifications for other allowlisted addresses, and repeated-failure escalation.
- Bumped the manifest version to `1.3.5` for HACS update detection.

## v1.3.4

IP Ban Manager 1.3.4 tightens the runtime ordering around Home Assistant's native IP ban manager.

### Fixed

- Managed blocked networks now keep working after Home Assistant reloads `ip_bans.yaml`. Home Assistant replaces its internal exact-ban lookup during that load, so IP Ban Manager now reapplies its network-aware lookup immediately afterward.
- Network-only block lists now stay active even when there are no exact IP bans in `ip_bans.yaml`.

### Improved

- Integration setup no longer rewrites or deletes `ip_bans.yaml` just because IP Ban Manager loaded. Exact ban file writes now happen only when the user changes exact bans through the UI or services.
- Added unload cleanup for the new ban-load hook so integration reloads restore Home Assistant internals cleanly.

### Validation

- Added regression coverage for network-only blocking, Home Assistant ban-file reload ordering, and hook restoration on unload.
- Bumped the manifest version to `1.3.4` for HACS update detection.

## v1.3.3

IP Ban Manager 1.3.3 adds Home Assistant Repairs for migration cleanup issues that need user attention.

### Added

- Added a Repair when old `ban_allowlist:` YAML is still present after IP Ban Manager has already imported it into the UI config entry.
- Added a Repair when a stale legacy folder cannot be moved out of Home Assistant's loader path, including the affected path list.

### Improved

- Legacy cleanup files now stay contained under `custom_components/ip_ban_manager/.cleanup`.
- Old top-level `ip_ban_manager_legacy_backup` folders are moved into the integration cleanup folder when possible.
- Cleanup destinations are collision-safe, so repeated cleanup runs do not overwrite earlier saved folders.
- Added regression coverage for legacy YAML Repairs, cleanup-failure Repairs, cleanup Repair clearing, and cleanup destination collisions.
- Bumped the manifest version to `1.3.3` for HACS update detection.

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
