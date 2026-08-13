"""Constants for the Govee LAN Control integration."""

DOMAIN = "govee_lan"

CONF_DEVICE_ID = "device_id"
CONF_SKU = "sku"

# Govee LAN API ports
MULTICAST_ADDR = "239.255.255.250"
DISCOVERY_PORT = 4001  # devices listen for scan requests here
LISTEN_PORT = 4002  # devices send replies to this port on our host
CONTROL_PORT = 4003  # devices listen for control commands here

DISCOVERY_TIMEOUT = 4.0  # seconds to wait for scan replies in the config flow
PROBE_TIMEOUT = 3.0  # seconds to wait when verifying a manually entered IP
RESCAN_INTERVAL = 60.0  # periodic multicast scan to track IP changes
AVAILABILITY_TIMEOUT = 60.0  # no packets for this long -> entity unavailable
