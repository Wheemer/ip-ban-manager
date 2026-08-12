## 🛡️ IP Ban Manager v1.8.3.5

This hotfix handles an early-startup edge case where Home Assistant can write an exact IP ban before IP Ban Manager has finished loading its allowlist protection.

### 🚪 Startup allowlist recovery

- If Home Assistant bans an address during early startup and that address is covered by **Allowed IPs**, IP Ban Manager now removes the exact ban automatically after setup.
- The cleanup rewrites `ip_bans.yaml`, clears the matching failed-login counter, dismisses the matching Home Assistant HTTP notification, and records `ip_ban_manager_ip_unbanned` with `source: setup`.
- The advanced **Bans inside Allowed IPs** option is respected. If that option is enabled, preexisting allowlisted bans are left in place.

### 🔔 Notification polish

- Home Assistant login notifications created later in the same event-loop turn get a second rewrite pass, so branded IP Ban Manager formatting and links are less likely to be missed.
- Rebuilt login notifications strip stale generated location and attribution lines before recalculating current details.

### 📘 Docs

- README automation examples now use the current `ip_ban_manager.remove_ip_ban` service and document the new `setup` event source.
