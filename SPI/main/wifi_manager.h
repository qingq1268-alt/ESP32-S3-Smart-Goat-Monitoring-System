#ifndef __WIFI_MANAGER_H__
#define __WIFI_MANAGER_H__

#include <stdbool.h>
#include <stdint.h>

#include "lwip/sockets.h"


#define WIFI_AP_SSID "LXSPI-ESP32S3"
#define WIFI_AP_PASS "12345678"
#define WIFI_AP_CHANNEL 6
#define WIFI_AP_MAX_CONNECTIONS 4
/* Fill these locally for Station mode; keep empty for SoftAP-only mode. */
#define WIFI_STA_SSID ""
#define WIFI_STA_PASS ""
#define WIFI_STA_BACKUP_SSID ""
#define WIFI_STA_BACKUP_PASS ""
#define WIFI_STA_RETRY_BEFORE_NEXT_NETWORK 3


#define WIFI_STA_FALLBACK_SCAN_INTERVAL_MS 30000

/* Start Wi-Fi in Station-first mode, or SoftAP-only when both station SSIDs are empty. */
void wifi_init(void);

/* Backward-compatible alias for older call sites. */
void wifi_init_ap(void);

/* Returns true once any Wi-Fi interface is ready to send UDP traffic. */
bool wifi_is_connected(void);

/* Stable board id derived from the Wi-Fi MAC address, e.g. ESP-A1B2C3. */
const char *wifi_get_device_id(void);

/* Fills a UDP broadcast destination for the active subnet. */
bool wifi_get_udp_target(struct sockaddr_in *dest_addr, uint16_t port);

/* Fills the STA gateway destination. Useful when the phone is the hotspot host. */
bool wifi_get_udp_gateway_target(struct sockaddr_in *dest_addr, uint16_t port);

#endif
