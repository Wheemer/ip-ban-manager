## 🛡️ IP Ban Manager v1.8.3.1

This is a stable hotfix release for the 1.8.3 line. It keeps the same user-facing behavior while tightening runtime safety and repository security posture.

### 🔒 Runtime hardening

- Restricts live-panel translation loading to bundled locale files.
- Requires TLS 1.2 or newer for the manual GeoIP database download fallback.

### 🧰 Repository security

- Pins GitHub Actions to immutable commit SHAs.
- Tightens workflow token permissions and release-asset upload credentials.
- Adds CodeQL, Scorecard, Renovate, and repository security reporting configuration.

### ✅ Validation

- GitHub Actions checks passed on the hardening PR.
- Code scanning, Dependabot, and secret scanning have no open alerts.
