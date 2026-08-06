## 🛡️ IP Ban Manager v1.8.3.2

This hotfix makes safe-default allowlists fully visible. Detected local access paths and relevant Home Assistant container/Supervisor paths are now written into **Allowed IPs**, where they can be reviewed, edited, and included in backups.

### ✨ Visible safe defaults

- First-run setup with **Detected local/internal network(s)** selected now stores localhost, detected local networks, and detected internal container/Supervisor entries immediately.
- Configure uses the same shared detection path, so adding safe defaults later adds the same visible entries.
- Existing safe-default entries gain newly detected internal entries on setup/reload without duplicating equivalent existing entries.

### 🧭 Install-aware detection

- Home Assistant OS/Supervised installs can surface the Supervisor Docker parent network (`172.30.0.0/16`) when Supervisor/internal adapters are detected.
- Home Assistant Container installs can surface the default Docker bridge gateway (`172.17.0.1/32`) when that bridge is detected.
- Custom allowlists that do not look like safe-default setups are left alone.
