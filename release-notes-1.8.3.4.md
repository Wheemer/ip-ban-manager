## 🛡️ IP Ban Manager v1.8.3.4

This hotfix improves failed-login notification details so the source address, reverse DNS, and GeoIP location are clearer and less dependent on local resolver behavior.

### 🌐 Public reverse DNS

- Public IP reverse-DNS labels now come from DNS-over-HTTPS instead of the Home Assistant host resolver.
- Failed-login notifications keep the numeric IP as the visible source and show reverse DNS separately.

### 🧭 Compact GeoIP locations

- Location labels now include subdivision or region information when the local DB-IP database provides it.
- Country labels use short ISO codes, and known Canada/US subdivision names are safely shortened when the database does not include a subdivision code.

