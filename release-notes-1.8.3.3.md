## 🛡️ IP Ban Manager v1.8.3.3

This hotfix cleans up branded Home Assistant HTTP login notifications so the visible message matches the actual source address and available GeoIP data.

### 🧭 Notification location labels

- Rewritten Home Assistant `http-login` notifications now include GeoIP location details when local GeoIP is enabled and the address is found in the local database.

### 🔔 Correct notification actions

- Public or otherwise non-allowlisted failed-login notifications no longer show the allowlisted-only **Don't show for this address again** action.
- Stale action links are removed before IP Ban Manager rebuilds notification headings and links, so old notification content cannot keep the wrong action attached.
