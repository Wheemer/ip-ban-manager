## 🛡️ IP Ban Manager v1.8.3.6

This hotfix fixes GeoIP startup on Docker installs where Home Assistant cannot write runtime Python packages into the container's site-packages directory.

### 🌍 GeoIP install reliability

- GeoIP no longer relies on Home Assistant installing `maxminddb` from `manifest.json` during startup.
- IP Ban Manager now ships the pure-Python MaxMind DB reader it needs, so GeoIP labels continue working across Home Assistant OS, Supervised, Container, Core, and restricted Docker installs.
- Existing systems that already have `maxminddb` installed keep working; new installs use the bundled reader without modifying Home Assistant's Python environment.

### ✅ Validation

- Full Linux test suite passed.
- Pre-commit, lint, formatting, and type checks passed.
- HACS-style release zip validation confirmed the bundled reader is included.
