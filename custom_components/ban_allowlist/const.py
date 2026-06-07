"""Constants for the Ban Allowlist integration."""

ATTR_BANNED_IPS = "banned_ips"
ATTR_CONFIRM = "confirm"
ATTR_FAILED_LOGIN_ATTEMPTS = "failed_login_attempts"
ATTR_IP_ADDRESS = "ip_address"
ATTR_NETWORK = "network"
ATTR_NETWORKS = "networks"

CONF_ALLOWED_IPS = "allowed_ips"
CONF_BANNED_IPS = "banned_ips"
CONF_IP_ADDRESSES = "ip_addresses"
DOMAIN = "ban_allowlist"

SERVICE_ADD_ALLOWLIST_NETWORK = "add_allowlist_network"
SERVICE_ADD_IP_BAN = "add_ip_ban"
SERVICE_REMOVE_ALL_IP_BANS = "remove_all_ip_bans"
SERVICE_REMOVE_ALLOWLIST_NETWORK = "remove_allowlist_network"
SERVICE_REMOVE_IP_BAN = "remove_ip_ban"
