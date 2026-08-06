## 🛡️ IP Ban Manager v1.8.3

This is a maintenance release for the 1.8.x line. It does not add new user-facing controls; it tightens the codebase so future fixes are easier to review and less likely to disturb unrelated behavior.

### 🧹 Codebase cleanup

- Split shared Home Assistant runtime keys into a dedicated storage-key module.
- Moved config-entry option helpers, login-threshold handling, YAML emergency-disable parsing, metrics, reverse DNS, internal network detection, file-store helpers, GeoIP handling, and panel asset serving into focused modules.
- Moved Home Assistant HTTP patches, branded notifications, panel/API views, live panel orchestration, ban operations, network policy, backup/import, legacy cleanup, health checks, services, and status payloads into dedicated modules.
- Reduced the main integration module to setup, config-entry setup, unload orchestration, and compatibility re-exports.

### 🧪 Validation

- Full Linux Home Assistant custom-component test suite passes.
- Formatting, lint, and type checks pass on the refactored files.
- Reverse-DNS tests now patch the extracted helper module directly, matching the new structure.
