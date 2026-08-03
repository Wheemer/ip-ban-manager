## 🛡️ IP Ban Manager v1.8.2

This is a focused Home Assistant 2026.8 compatibility hotfix. It keeps the 1.8.x feature set intact while removing startup warnings caused by synchronous file access inside async Home Assistant paths.

### ⚙️ Event-loop safety

- Live-panel translation JSON is now loaded through Home Assistant executor jobs.
- Repair health issue translation loading now uses the same executor-safe path.
- `panel.js` serving and the versioned panel URL cache token no longer read or stat files directly from the event loop.
- Backup and GeoIP metadata used by the panel status payload are collected outside the event loop.

### 🧪 Validation

- Added regression tests confirming the async translation helpers use `hass.async_add_executor_job()`.
- Focused i18n tests pass locally.
- Formatting and lint hooks pass on the touched files.
