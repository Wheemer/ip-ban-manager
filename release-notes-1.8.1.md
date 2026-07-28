## 🛡️ IP Ban Manager v1.8.1 — hotfix

**Hotfix for a blank IP Ban Manager panel after upgrading to 1.8.0.**

If you updated from 1.7.x, clicking **IP Ban Manager** in the sidebar could show a completely blank panel. The integration itself still worked — sensors, bans, and services were fine — but the UI never loaded because Home Assistant kept the old panel registration from before 1.8.0 renamed the script URL and web component.

1.8.1 fixes that the intended way: on setup it removes any stale frontend panel registration and re-registers the panel with the versioned module URL (`panel.js?v=<installed-version>&t=<mtime>`), which is how the integration bypasses cached scripts after HACS updates.

### 🧭 Live panel

- Removes stale frontend panel registrations before registering the current panel.
- Keeps the dynamic `panel.js?v=…&t=…` module URL tied to the installed integration version.
- Shows **Loading…** in the panel shell immediately instead of a blank content area before the first status request completes.

### ⬆️ Upgrade notes

- Update through HACS and **restart Home Assistant Core** so the panel re-registers with the new module URL.
- The version label beside **IP Ban Manager** should show **v1.8.1** after the update.
