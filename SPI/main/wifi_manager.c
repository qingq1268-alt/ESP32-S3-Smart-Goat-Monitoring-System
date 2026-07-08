#include "wifi_manager.h"

#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "lwip/sockets.h"

static const char *TAG = "WIFI_MGR";

static bool ap_started = false;
static bool sta_started = false;
static bool sta_got_ip = false;
static int sta_retry_count = 0;
static esp_netif_t *ap_netif = NULL;
static esp_netif_t *sta_netif = NULL;
static char device_id[16] = "ESP-000000";
static int active_sta_index = -1;
static esp_timer_handle_t fallback_sta_retry_timer = NULL;
static bool fallback_sta_retry_timer_armed = false;
static int64_t last_sta_connect_attempt_ms = 0;

static void reconnect_sta(void);

typedef struct {
    const char *ssid;
    const char *password;
} sta_credential_t;

static const sta_credential_t STA_NETWORKS[] = {
    {WIFI_STA_SSID, WIFI_STA_PASS},
    {WIFI_STA_BACKUP_SSID, WIFI_STA_BACKUP_PASS},
};

static bool sta_credential_enabled(size_t index)
{
    return index < (sizeof(STA_NETWORKS) / sizeof(STA_NETWORKS[0])) &&
           STA_NETWORKS[index].ssid != NULL &&
           STA_NETWORKS[index].ssid[0] != '\0';
}

static int first_sta_index(void)
{
    for (size_t i = 0; i < (sizeof(STA_NETWORKS) / sizeof(STA_NETWORKS[0])); ++i) {
        if (sta_credential_enabled(i)) {
            return (int)i;
        }
    }
    return -1;
}

static int next_sta_index(int current_index)
{
    const size_t count = sizeof(STA_NETWORKS) / sizeof(STA_NETWORKS[0]);
    for (size_t offset = 1; offset <= count; ++offset) {
        size_t candidate = current_index < 0 ?
                           offset - 1 :
                           ((size_t)current_index + offset) % count;
        if (sta_credential_enabled(candidate)) {
            return (int)candidate;
        }
    }
    return -1;
}

static const sta_credential_t *current_sta_credential(void)
{
    if (!sta_credential_enabled((size_t)active_sta_index)) {
        active_sta_index = first_sta_index();
    }
    if (active_sta_index < 0) {
        return NULL;
    }
    return &STA_NETWORKS[active_sta_index];
}

static bool wifi_sta_enabled(void)
{
    return first_sta_index() >= 0;
}

static void log_ip_info(const char *prefix, const esp_netif_ip_info_t *ip_info)
{
    ESP_LOGI(TAG, "%s IP:" IPSTR " mask:" IPSTR " gw:" IPSTR,
             prefix,
             IP2STR(&ip_info->ip),
             IP2STR(&ip_info->netmask),
             IP2STR(&ip_info->gw));
}

static void init_device_id(void)
{
    uint8_t mac[6] = {0};
    esp_err_t err = esp_read_mac(mac, ESP_MAC_WIFI_STA);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to read WiFi MAC: %s", esp_err_to_name(err));
        return;
    }

    snprintf(device_id, sizeof(device_id), "ESP-%02X%02X%02X",
             mac[3], mac[4], mac[5]);
    ESP_LOGI(TAG, "Device id: %s", device_id);
}

static int64_t now_ms(void)
{
    return esp_timer_get_time() / 1000;
}

static void fallback_sta_retry_cb(void *arg)
{
    (void)arg;
    fallback_sta_retry_timer_armed = false;
    if (ap_started && !sta_got_ip && wifi_sta_enabled()) {
        ESP_LOGI(TAG, "Fallback SoftAP active; probing configured STA networks");
        reconnect_sta();
    }
}

static void schedule_fallback_sta_retry(int64_t delay_ms)
{
    if (delay_ms < 1000) {
        delay_ms = 1000;
    }

    if (fallback_sta_retry_timer == NULL) {
        const esp_timer_create_args_t timer_args = {
            .callback = fallback_sta_retry_cb,
            .arg = NULL,
            .dispatch_method = ESP_TIMER_TASK,
            .name = "sta_retry",
            .skip_unhandled_events = true,
        };
        esp_err_t err = esp_timer_create(&timer_args, &fallback_sta_retry_timer);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "Failed to create STA retry timer: %s", esp_err_to_name(err));
            return;
        }
    }

    if (fallback_sta_retry_timer_armed) {
        return;
    }

    esp_err_t err = esp_timer_start_once(fallback_sta_retry_timer, delay_ms * 1000);
    if (err == ESP_OK) {
        fallback_sta_retry_timer_armed = true;
        ESP_LOGI(TAG, "Next external hotspot probe in %lld ms", (long long)delay_ms);
    } else {
        ESP_LOGW(TAG, "Failed to schedule STA retry: %s", esp_err_to_name(err));
    }
}

static void cancel_fallback_sta_retry(void)
{
    if (fallback_sta_retry_timer != NULL && fallback_sta_retry_timer_armed) {
        esp_timer_stop(fallback_sta_retry_timer);
    }
    fallback_sta_retry_timer_armed = false;
}

static esp_err_t apply_ap_config(void)
{
    wifi_config_t ap_config = {0};
    snprintf((char *)ap_config.ap.ssid, sizeof(ap_config.ap.ssid),
             "%s", WIFI_AP_SSID);
    snprintf((char *)ap_config.ap.password, sizeof(ap_config.ap.password),
             "%s", WIFI_AP_PASS);
    ap_config.ap.ssid_len = strlen(WIFI_AP_SSID);
    ap_config.ap.channel = WIFI_AP_CHANNEL;
    ap_config.ap.max_connection = WIFI_AP_MAX_CONNECTIONS;
    ap_config.ap.authmode = strlen(WIFI_AP_PASS) == 0 ?
                            WIFI_AUTH_OPEN : WIFI_AUTH_WPA2_PSK;
    ap_config.ap.pmf_cfg.required = false;

    return esp_wifi_set_config(WIFI_IF_AP, &ap_config);
}

static esp_err_t apply_sta_config(void)
{
    const sta_credential_t *credential = current_sta_credential();
    if (credential == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    wifi_config_t sta_config = {0};
    snprintf((char *)sta_config.sta.ssid, sizeof(sta_config.sta.ssid),
             "%s", credential->ssid);
    snprintf((char *)sta_config.sta.password, sizeof(sta_config.sta.password),
             "%s", credential->password);
    sta_config.sta.threshold.authmode = strlen(credential->password) == 0 ?
                                        WIFI_AUTH_OPEN : WIFI_AUTH_WPA2_PSK;
    sta_config.sta.pmf_cfg.capable = true;
    sta_config.sta.pmf_cfg.required = false;

    ESP_LOGI(TAG, "Applying STA network #%d: %s", active_sta_index + 1, credential->ssid);
    return esp_wifi_set_config(WIFI_IF_STA, &sta_config);
}

static void open_fallback_ap(void)
{
    if (ap_started) {
        return;
    }

    ESP_LOGW(TAG, "Opening fallback SoftAP: %s", WIFI_AP_SSID);

    esp_err_t err = esp_wifi_set_mode(WIFI_MODE_APSTA);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to switch to AP+STA mode: %s", esp_err_to_name(err));
        return;
    }

    err = apply_ap_config();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to apply SoftAP config: %s", esp_err_to_name(err));
    }
}

static void close_fallback_ap(void)
{
    if (!wifi_sta_enabled()) {
        return;
    }

    wifi_mode_t mode = WIFI_MODE_NULL;
    esp_err_t err = esp_wifi_get_mode(&mode);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to read WiFi mode: %s", esp_err_to_name(err));
        return;
    }

    if (mode == WIFI_MODE_APSTA || mode == WIFI_MODE_AP) {
        ESP_LOGI(TAG, "External hotspot connected; closing fallback SoftAP");
        err = esp_wifi_set_mode(WIFI_MODE_STA);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "Failed to close fallback SoftAP: %s", esp_err_to_name(err));
        }
    }
}

static void reconnect_sta(void)
{
    if (!wifi_sta_enabled() || sta_got_ip) {
        return;
    }

    if (ap_started && last_sta_connect_attempt_ms > 0) {
        int64_t elapsed_ms = now_ms() - last_sta_connect_attempt_ms;
        if (elapsed_ms < WIFI_STA_FALLBACK_SCAN_INTERVAL_MS) {
            schedule_fallback_sta_retry(WIFI_STA_FALLBACK_SCAN_INTERVAL_MS - elapsed_ms);
            return;
        }
    }

    last_sta_connect_attempt_ms = now_ms();
    esp_err_t err = esp_wifi_connect();
    if (err != ESP_OK && err != ESP_ERR_WIFI_CONN) {
        ESP_LOGW(TAG, "WiFi STA reconnect failed to start: %s",
                 esp_err_to_name(err));
    }
}

static void advance_sta_network_after_failures(void)
{
    if (sta_retry_count < WIFI_STA_RETRY_BEFORE_NEXT_NETWORK) {
        return;
    }

    sta_retry_count = 0;
    int next_index = next_sta_index(active_sta_index);
    if (next_index >= 0 && next_index != active_sta_index) {
        bool wrapped_to_priority = next_index < active_sta_index;
        active_sta_index = next_index;
        ESP_LOGW(TAG, "Switching to STA network #%d: %s",
                 active_sta_index + 1, STA_NETWORKS[active_sta_index].ssid);
        if (wrapped_to_priority) {
            open_fallback_ap();
        }
        esp_err_t err = apply_sta_config();
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "Failed to apply STA config: %s", esp_err_to_name(err));
        }
        return;
    }

    open_fallback_ap();
    active_sta_index = first_sta_index();
    if (active_sta_index >= 0) {
        ESP_LOGW(TAG, "All STA networks failed; retrying priority network #%d: %s",
                 active_sta_index + 1, STA_NETWORKS[active_sta_index].ssid);
        esp_err_t err = apply_sta_config();
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "Failed to reapply priority STA config: %s", esp_err_to_name(err));
        }
    }
}

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data)
{
    (void)arg;

    if (event_base == WIFI_EVENT) {
        switch (event_id) {
            case WIFI_EVENT_AP_START:
                ap_started = true;
                ESP_LOGI(TAG, "WiFi fallback AP started. SSID: %s", WIFI_AP_SSID);
                break;
            case WIFI_EVENT_AP_STOP:
                ap_started = false;
                cancel_fallback_sta_retry();
                ESP_LOGI(TAG, "WiFi fallback AP stopped");
                break;
            case WIFI_EVENT_AP_STACONNECTED: {
                wifi_event_ap_staconnected_t *event =
                    (wifi_event_ap_staconnected_t *)event_data;
                ESP_LOGI(TAG, "SoftAP station connected, AID=%d", event->aid);
                break;
            }
            case WIFI_EVENT_AP_STADISCONNECTED: {
                wifi_event_ap_stadisconnected_t *event =
                    (wifi_event_ap_stadisconnected_t *)event_data;
                ESP_LOGI(TAG, "SoftAP station disconnected, AID=%d", event->aid);
                break;
            }
            case WIFI_EVENT_STA_START:
                sta_started = true;
                if (wifi_sta_enabled()) {
                    const sta_credential_t *credential = current_sta_credential();
                    ESP_LOGI(TAG, "WiFi STA connecting to SSID: %s",
                             credential ? credential->ssid : "--");
                    reconnect_sta();
                }
                break;
            case WIFI_EVENT_STA_STOP:
                sta_started = false;
                sta_got_ip = false;
                ESP_LOGI(TAG, "WiFi STA stopped");
                break;
            case WIFI_EVENT_STA_DISCONNECTED: {
                wifi_event_sta_disconnected_t *event =
                    (wifi_event_sta_disconnected_t *)event_data;
                sta_got_ip = false;
                if (wifi_sta_enabled()) {
                    sta_retry_count++;
                    ESP_LOGW(TAG, "WiFi STA disconnected, reason=%d, network=%d, retry=%d/%d",
                             event ? event->reason : -1,
                             active_sta_index + 1,
                             sta_retry_count,
                             WIFI_STA_RETRY_BEFORE_NEXT_NETWORK);
                    advance_sta_network_after_failures();
                    reconnect_sta();
                }
                break;
            }
            default:
                break;
        }
        return;
    }

    if (event_base == IP_EVENT) {
        switch (event_id) {
            case IP_EVENT_STA_GOT_IP: {
                ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
                sta_got_ip = true;
                sta_retry_count = 0;
                last_sta_connect_attempt_ms = 0;
                cancel_fallback_sta_retry();
                log_ip_info("WiFi STA got", &event->ip_info);
                close_fallback_ap();
                break;
            }
            case IP_EVENT_STA_LOST_IP:
                sta_got_ip = false;
                if (wifi_sta_enabled()) {
                    sta_retry_count++;
                    ESP_LOGW(TAG, "WiFi STA lost IP, network=%d, retry=%d/%d",
                             active_sta_index + 1,
                             sta_retry_count,
                             WIFI_STA_RETRY_BEFORE_NEXT_NETWORK);
                    advance_sta_network_after_failures();
                    reconnect_sta();
                }
                break;
            default:
                break;
        }
    }
}

void wifi_init(void)
{
    bool sta_enabled = wifi_sta_enabled();
    active_sta_index = first_sta_index();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    ap_netif = esp_netif_create_default_wifi_ap();
    if (sta_enabled) {
        sta_netif = esp_netif_create_default_wifi_sta();
    }

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    init_device_id();

    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT,
                                                        ESP_EVENT_ANY_ID,
                                                        &wifi_event_handler,
                                                        NULL,
                                                        NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT,
                                                        ESP_EVENT_ANY_ID,
                                                        &wifi_event_handler,
                                                        NULL,
                                                        NULL));

    if (sta_enabled) {
        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));
        ESP_ERROR_CHECK(apply_ap_config());
        ESP_ERROR_CHECK(apply_sta_config());
        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    } else {
        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
        ESP_ERROR_CHECK(apply_ap_config());
    }

    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_ERROR_CHECK(esp_wifi_set_max_tx_power(44));  /* 11 dBm, unit: 0.25 dBm */
    ESP_LOGI(TAG, "WiFi TX power set to 11dBm");

    if (sta_enabled) {
        esp_err_t ps_err = esp_wifi_set_ps(WIFI_PS_NONE);
        if (ps_err == ESP_OK) {
            ESP_LOGI(TAG, "WiFi STA power save disabled for lower UDP latency");
        } else {
            ESP_LOGW(TAG, "Failed to disable WiFi STA power save: %s",
                     esp_err_to_name(ps_err));
        }
        const sta_credential_t *credential = current_sta_credential();
        ESP_LOGI(TAG, "WiFi station-first initialized. Priority STA:%s fallback AP:%s",
                 credential ? credential->ssid : "--", WIFI_AP_SSID);
    } else {
        ESP_LOGI(TAG, "WiFi SoftAP initialized. SSID:%s password:%s channel:%d",
                 WIFI_AP_SSID, WIFI_AP_PASS, WIFI_AP_CHANNEL);
    }
}

void wifi_init_ap(void)
{
    wifi_init();
}

bool wifi_is_connected(void)
{
    return sta_got_ip || ap_started;
}

const char *wifi_get_device_id(void)
{
    return device_id;
}

bool wifi_get_udp_target(struct sockaddr_in *dest_addr, uint16_t port)
{
    esp_netif_t *netif = NULL;

    if (sta_got_ip && sta_netif) {
        netif = sta_netif;
    } else if (ap_started && ap_netif) {
        netif = ap_netif;
    } else {
        return false;
    }

    esp_netif_ip_info_t ip_info;
    esp_err_t err = esp_netif_get_ip_info(netif, &ip_info);
    if (err != ESP_OK) {
        return false;
    }

    uint32_t ip = ntohl(ip_info.ip.addr);
    uint32_t netmask = ntohl(ip_info.netmask.addr);
    uint32_t broadcast = htonl(ip | ~netmask);

    memset(dest_addr, 0, sizeof(*dest_addr));
    dest_addr->sin_family = AF_INET;
    dest_addr->sin_port = htons(port);
    dest_addr->sin_addr.s_addr = broadcast;

    return true;
}

bool wifi_get_udp_gateway_target(struct sockaddr_in *dest_addr, uint16_t port)
{
    if (!sta_got_ip || !sta_netif || dest_addr == NULL) {
        return false;
    }

    esp_netif_ip_info_t ip_info;
    esp_err_t err = esp_netif_get_ip_info(sta_netif, &ip_info);
    if (err != ESP_OK || ip_info.gw.addr == 0 || ip_info.gw.addr == ip_info.ip.addr) {
        return false;
    }

    memset(dest_addr, 0, sizeof(*dest_addr));
    dest_addr->sin_family = AF_INET;
    dest_addr->sin_port = htons(port);
    dest_addr->sin_addr.s_addr = ip_info.gw.addr;

    return true;
}
