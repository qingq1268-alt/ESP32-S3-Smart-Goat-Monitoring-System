#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <sys/time.h>
#include <time.h>

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_log.h"
#include "esp_system.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"
#include "esp_timer.h"
#include "esp_pm.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "goat_behavior_model.h"
#include "lsm6dso.h"
#include "wifi_manager.h"
#include "sd_logger.h"
#include "battery_monitor.h"

static const char *TAG = "MAIN_APP";

#define PIN_NUM_MISO 13
#define PIN_NUM_MOSI 11
#define PIN_NUM_CLK  12
#define PIN_NUM_CS   10
#define PIN_NUM_INT1 2   

#define TARGET_PORT 5005
#define CONTROL_PORT 6006
#define ACCEL_PORT 5007

#define UDP_BUF_SIZE 1500
#define ACCEL_LOG_INTERVAL_MS 5000
#define FIFO_TAG_ACCEL 0x02
#define BUFFER_RESERVE 64
#define MAX_FIFO_READS 1024
#define FIFO_EMPTY_RECOVER_LOOPS 75  // 75 loops x 40ms ~= 3s
#define PAUSED_STATUS_INTERVAL_MS 500


#define RESAMPLE_STEP (26.0 / 24.4)  
typedef struct {
    float prev[3];
    float curr[3];
    double phase;     /* position of next output, in continuous input units */
    int input_count;  /* how many input samples have been pushed so far */
} accel_resampler_t;

static void accel_resampler_init(accel_resampler_t *r)
{
    r->prev[0] = r->prev[1] = r->prev[2] = 0.0f;
    r->curr[0] = r->curr[1] = r->curr[2] = 0.0f;
    r->phase = 0.0;
    r->input_count = 0;
}

/* Push a raw input sample. Returns 1 if a resampled output was produced
 * (in out_xyz), else 0. step > 1 guarantees at most one output per push. */
static int accel_resampler_push(accel_resampler_t *r,
                                float ax, float ay, float az,
                                float out_xyz[3])
{
    r->input_count++;

    if (r->input_count == 1) {
        /* First sample: output[0] = input[0] at phase=0 */
        r->curr[0] = ax; r->curr[1] = ay; r->curr[2] = az;
        out_xyz[0] = ax; out_xyz[1] = ay; out_xyz[2] = az;
        r->phase = RESAMPLE_STEP;
        return 1;
    }

    /* Shift the interval: prev <- old curr, curr <- new sample */
    r->prev[0] = r->curr[0]; r->prev[1] = r->curr[1]; r->prev[2] = r->curr[2];
    r->curr[0] = ax;         r->curr[1] = ay;         r->curr[2] = az;

    /* New interval covers input positions [N-2, N-1]. Emit if phase falls in it. */
    double n_minus_1 = (double)(r->input_count - 1);
    if (r->phase <= n_minus_1) {
        double local = r->phase - (n_minus_1 - 1.0);  /* in [0, 1] */
        if (local < 0.0) local = 0.0;
        if (local > 1.0) local = 1.0;
        float fl = (float)local;
        float inv = 1.0f - fl;
        out_xyz[0] = inv * r->prev[0] + fl * r->curr[0];
        out_xyz[1] = inv * r->prev[1] + fl * r->curr[1];
        out_xyz[2] = inv * r->prev[2] + fl * r->curr[2];
        r->phase += RESAMPLE_STEP;
        return 1;
    }
    return 0;
}

int udp_sock = -1;
int ctrl_sock = -1;
int accel_sock = -1;
static TaskHandle_t lsm_task_handle = NULL;
static SemaphoreHandle_t stream_mutex = NULL;
volatile bool g_stream_enabled = true;
static volatile bool g_accel_udp_enabled = false;
static volatile int64_t g_time_offset_ms = 0;
static volatile bool g_time_synced = false;
static volatile uint32_t g_last_controller_ip = 0;
extern const float ACCEL_SENSITIVITY;

static void init_nvs(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
}

static void cleanup_sockets(void)
{
    if (udp_sock >= 0) {
        close(udp_sock);
        udp_sock = -1;
        ESP_LOGI(TAG, "UDP socket closed");
    }
    if (ctrl_sock >= 0) {
        close(ctrl_sock);
        ctrl_sock = -1;
        ESP_LOGI(TAG, "Control socket closed");
    }
    if (accel_sock >= 0) {
        close(accel_sock);
        accel_sock = -1;
        ESP_LOGI(TAG, "Accel socket closed");
    }
}

static int64_t get_synced_ms(void)
{
    int64_t local_ms = (int64_t)(esp_timer_get_time() / 1000);
    if (g_time_synced) {
        return local_ms + g_time_offset_ms;
    }
    return 0;
}

static bool append_to_buffer(char *buf, size_t buf_size, int *pos, const char *fmt, ...)
{
    if (buf == NULL || pos == NULL || fmt == NULL) return false;
    if (*pos < 0 || *pos >= (int)buf_size) {
        return false;
    }

    va_list args;
    va_start(args, fmt);
    int available = (int)(buf_size - (size_t)(*pos));
    int written = vsnprintf(buf + *pos, available, fmt, args);
    va_end(args);

    if (written < 0) {
        return false;
    }

    if (written >= available) {
        // indicate buffer full
        *pos = (int)buf_size - 1;
        buf[*pos] = '\0';
        return false;
    }

    *pos += written;
    return true;
}

static void send_udp_to_current_targets(int sock, const char *buf, size_t len, uint16_t port)
{
    if (sock < 0 || buf == NULL || len == 0) {
        return;
    }

    struct sockaddr_in dest_addr = {0};
    bool sent_broadcast = wifi_get_udp_target(&dest_addr, port);
    if (sent_broadcast) {
        sendto(sock, buf, len, 0, (struct sockaddr *)&dest_addr, sizeof(dest_addr));
    }

    struct sockaddr_in gateway_addr = {0};
    if (wifi_get_udp_gateway_target(&gateway_addr, port) &&
        (!sent_broadcast || gateway_addr.sin_addr.s_addr != dest_addr.sin_addr.s_addr)) {
        sendto(sock, buf, len, 0, (struct sockaddr *)&gateway_addr, sizeof(gateway_addr));
    }

    uint32_t controller_ip = g_last_controller_ip;
    if (controller_ip != 0 && controller_ip != INADDR_BROADCAST) {
        struct sockaddr_in unicast_addr = {0};
        unicast_addr.sin_family = AF_INET;
        unicast_addr.sin_port = htons(port);
        unicast_addr.sin_addr.s_addr = controller_ip;
        sendto(sock, buf, len, 0, (struct sockaddr *)&unicast_addr, sizeof(unicast_addr));
    }
}

static void send_accel_status_packet(bool stream_enabled)
{
    if (accel_sock == -1) {
        return;
    }

    char status_buf[256];
    int pos = 0;
    int64_t ts = get_synced_ms();
    append_to_buffer(
        status_buf, sizeof(status_buf), &pos,
        "{\"type\":\"accel\",\"dev\":\"%s\",\"ts\":%lld,\"acc\":[],\"n\":0,"
        "\"stream\":%s,\"sd\":{\"mounted\":%s,\"recording\":%s}}",
        wifi_get_device_id(),
        (long long)ts,
        stream_enabled ? "true" : "false",
        sd_logger_is_mounted() ? "true" : "false",
        sd_logger_is_recording() ? "true" : "false");

    send_udp_to_current_targets(accel_sock, status_buf, strlen(status_buf), ACCEL_PORT);
}

static void IRAM_ATTR lsm6dso_isr_handler(void *arg)
{
    BaseType_t high_task_wakeup = pdFALSE;
    vTaskNotifyGiveFromISR(lsm_task_handle, &high_task_wakeup);
    if (high_task_wakeup) {
        portYIELD_FROM_ISR();
    }
}

void control_task(void *pvParameters)
{
    (void)pvParameters;

    char rx[64];
    struct sockaddr_in listen_addr = {0};
    struct sockaddr_in src_addr = {0};
    socklen_t src_len = sizeof(src_addr);

    listen_addr.sin_family = AF_INET;
    listen_addr.sin_addr.s_addr = htonl(INADDR_ANY);
    listen_addr.sin_port = htons(CONTROL_PORT);

    ctrl_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (ctrl_sock < 0) {
        ESP_LOGE(TAG, "Failed to create control UDP socket.");
        cleanup_sockets();
        vTaskDelete(NULL);
        return;
    }

    if (bind(ctrl_sock, (struct sockaddr *)&listen_addr, sizeof(listen_addr)) != 0) {
        ESP_LOGE(TAG, "Failed to bind control UDP socket on port %d.", CONTROL_PORT);
        cleanup_sockets();
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "Control UDP socket is listening on port %d.", CONTROL_PORT);

    while (1) {
        int len = recvfrom(ctrl_sock, rx, sizeof(rx) - 1, 0,
                           (struct sockaddr *)&src_addr, &src_len);
        if (len <= 0) {
            continue;
        }
        rx[len] = '\0';
        if (src_addr.sin_addr.s_addr != 0 && src_addr.sin_addr.s_addr != INADDR_BROADCAST) {
            uint32_t old_controller_ip = g_last_controller_ip;
            g_last_controller_ip = src_addr.sin_addr.s_addr;
            if (old_controller_ip != g_last_controller_ip) {
                ESP_LOGI(TAG, "Telemetry unicast target updated: %s", inet_ntoa(src_addr.sin_addr));
            }
        }
    
        ESP_LOGI(TAG, "CTRL RX: '%s' (from %s)", rx, inet_ntoa(src_addr.sin_addr));

        if (strstr(rx, "START") != NULL) {
            xSemaphoreTake(stream_mutex, portMAX_DELAY);
            g_stream_enabled = true;
            xSemaphoreGive(stream_mutex);
            ESP_LOGI(TAG, "Received control command: START");
            // 开始SD卡记录
            if (!sd_logger_is_recording()) {
                esp_err_t ret = sd_logger_start_session();
                if (ret == ESP_OK) {
                    ESP_LOGI(TAG, "SD card logging started: %s", sd_logger_get_current_file());
                } else {
                    ESP_LOGE(TAG, "SD card logging FAILED to start: %s "
                                  "(check SD card insertion and FAT format)",
                             esp_err_to_name(ret));
                }
            }
        } else if (strstr(rx, "ACCELON") != NULL) {
            xSemaphoreTake(stream_mutex, portMAX_DELAY);
            g_accel_udp_enabled = true;
            xSemaphoreGive(stream_mutex);
            ESP_LOGI(TAG, "Received control command: ACCELON");
            send_accel_status_packet(true);
        } else if (strstr(rx, "ACCELOFF") != NULL) {
            xSemaphoreTake(stream_mutex, portMAX_DELAY);
            g_accel_udp_enabled = false;
            xSemaphoreGive(stream_mutex);
            ESP_LOGI(TAG, "Received control command: ACCELOFF");
            send_accel_status_packet(false);
        } else if (strstr(rx, "STOPREC") != NULL) {
            ESP_LOGI(TAG, "Received control command: STOPREC (stop SD logging only)");
            if (sd_logger_is_recording()) {
                sd_logger_stop_session();
                ESP_LOGI(TAG, "SD card logging stopped");
            } else {
                ESP_LOGI(TAG, "SD card logging already stopped");
            }
        } else if (strstr(rx, "PAUSE") != NULL) {
            xSemaphoreTake(stream_mutex, portMAX_DELAY);
            g_stream_enabled = false;
            g_accel_udp_enabled = false;
            xSemaphoreGive(stream_mutex);
            ESP_LOGI(TAG, "Received control command: PAUSE");
            // 停止SD卡记录
            if (sd_logger_is_recording()) {
                sd_logger_stop_session();
                ESP_LOGI(TAG, "SD card logging stopped");
            }
        } else if (strstr(rx, "SDDIAG") != NULL) {
            ESP_LOGI(TAG, "Received control command: SDDIAG");
            sd_logger_log_diagnostics();
        } else if (strstr(rx, "MOUNT") != NULL) {
            // 用于卡片重新插入 / 重新格式化后，不重启就重挂载。
            // 顺序：若正在记录则停掉 → 若已挂载则 deinit → 再 init。
            ESP_LOGI(TAG, "Received control command: MOUNT (SD remount)");
            if (sd_logger_is_recording()) {
                sd_logger_stop_session();
            }
            if (sd_logger_is_mounted()) {
                sd_logger_deinit();
            }
            esp_err_t mount_ret = sd_logger_init();
            if (mount_ret == ESP_OK) {
                int64_t free_kb = sd_logger_get_free_space_kb();
                ESP_LOGI(TAG, "SD remount OK, free=%lld KB", (long long)free_kb);
            } else {
                ESP_LOGE(TAG, "SD remount FAILED: %s (check FAT32 format / card seating)",
                         esp_err_to_name(mount_ret));
            }
        } else if (strncmp(rx, "SYNC:", 5) == 0) {
            long long remote_ms = 0;
            if (sscanf(rx + 5, "%lld", &remote_ms) == 1 && remote_ms > 0) {
                int64_t local_ms = (int64_t)(esp_timer_get_time() / 1000);

                xSemaphoreTake(stream_mutex, portMAX_DELAY);
                g_time_offset_ms = (int64_t)remote_ms - local_ms;
                g_time_synced = true;
                xSemaphoreGive(stream_mutex);

                // 设置系统时间
                struct timeval tv;
                tv.tv_sec = remote_ms / 1000;
                tv.tv_usec = (remote_ms % 1000) * 1000;
                settimeofday(&tv, NULL);

                ESP_LOGI(TAG, "Time synced: remote=%lld local=%lld offset=%lld",
                         (long long)remote_ms, (long long)local_ms,
                         (long long)g_time_offset_ms);
                ESP_LOGI(TAG, "System time set to: %lld seconds", (long long)tv.tv_sec);
            } else {
                ESP_LOGW(TAG, "Invalid SYNC payload: %s", rx);
            }
        } else if (strstr(rx, "RESET") != NULL) {
            ESP_LOGW(TAG, "Received control command: RESET, restarting in 200ms...");
            // 给一点时间让日志输出，并把可能开着的 SD 卡 session 关掉
            if (sd_logger_is_recording()) {
                sd_logger_stop_session();
            }
            vTaskDelay(pdMS_TO_TICKS(200));
            esp_restart();
        }
    }
}

void lsm_read_task(void *pvParameters)
{
    spi_device_handle_t spi = (spi_device_handle_t)pvParameters;
    uint8_t status_data[2];
    uint8_t fifo_data[7];
    char accel_buf[UDP_BUF_SIZE];
    char infer_buf[512];

    const size_t window_size = goat_behavior_model_get_window_size();
    const size_t window_step = goat_behavior_model_get_window_step();
    float *window = (float *)calloc(window_size * 3, sizeof(float));
    if (!window) {
        ESP_LOGE(TAG, "Failed to allocate inference window buffer.");
        vTaskDelete(NULL);
        return;
    }

    int win_count = 0;
    int since_last_infer = 0;
    char act_label[GOAT_BEHAVIOR_LABEL_MAX] = "Warmup";
    float conf = 0.0f;
    goat_behavior_model_result_t infer_result = {0};

    TickType_t last_accel_log = 0;
    int accel_sample_count = 0;
    int64_t last_heartbeat_ms = 0;
    int64_t last_paused_status_ms = 0;
    uint32_t loop_count = 0;
    uint32_t fifo_empty_count = 0;
    uint32_t direct_stale_count = 0;
    bool have_direct_sample = false;
    float last_direct_ax = 0.0f;
    float last_direct_ay = 0.0f;
    float last_direct_az = 0.0f;

    
    accel_resampler_t resampler;
    accel_resampler_init(&resampler);

    while (1) {
        // 200ms 超时：边沿丢失（INT1 长时间保持高电平，软件清空后没有新的上升沿）
        // 时最多损失 200ms 就强制 poll 一次 FIFO 并把 INT1 拉低，下一次水位到达就能恢复
        // 正常中断。原本的 1000ms 在走动 + 推理偶发 + WiFi tx 排队叠加时仍会造成
        // 总循环 > 2.5s，触发上位机的「信号不良」红灯。
        // Poll near the sensor ODR as a fallback when INT1/FIFO stops waking us.
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(40));
        loop_count++;

        // 心跳日志（5s 一次）：用来定位卡死时是这个 task 整体冻住，还是后续推理 / sendto 卡住
        int64_t hb_now_ms = (int64_t)(esp_timer_get_time() / 1000);
        if (hb_now_ms - last_heartbeat_ms >= 5000) {
            ESP_LOGI(TAG, "ALIVE loop=%lu win=%d since_infer=%d stream=%d accel_udp=%d sd_rec=%d sd_mounted=%d",
                     (unsigned long)loop_count, win_count, since_last_infer,
                     (int)g_stream_enabled, (int)g_accel_udp_enabled,
                     (int)sd_logger_is_recording(),
                     (int)sd_logger_is_mounted());
            last_heartbeat_ms = hb_now_ms;
        }

        xSemaphoreTake(stream_mutex, portMAX_DELAY);
        bool stream_enabled = g_stream_enabled;
        bool accel_udp_enabled = g_accel_udp_enabled;
        xSemaphoreGive(stream_mutex);

        if (!stream_enabled) {
            int64_t now_ms = (int64_t)(esp_timer_get_time() / 1000);
            if (now_ms - last_paused_status_ms >= PAUSED_STATUS_INTERVAL_MS) {
                send_accel_status_packet(false);
                last_paused_status_ms = now_ms;
            }
            continue;
        }

        bool inference_just_ran = false;

        /* --- Build accel packet header --- */
        int apos = 0;
        int64_t accel_ts = get_synced_ms();
        if (accel_udp_enabled) {
            append_to_buffer(accel_buf, sizeof(accel_buf), &apos,
                             "{\"type\":\"accel\",\"dev\":\"%s\",\"ts\":%lld,\"acc\":[",
                             wifi_get_device_id(), (long long)accel_ts);
        }
        bool first_point = true;
        int accel_count = 0;

        /* --- Read FIFO --- */
        lsm6dso_read_regs(spi, 0x3A, status_data, 2);
        uint16_t diff_fifo = status_data[0] | ((status_data[1] & 0x03) << 8);

        // 防止FIFO读取过多导致缓冲区溢出
        if (diff_fifo > MAX_FIFO_READS) {
            ESP_LOGW(TAG, "FIFO count %d exceeds max %d, clamping", diff_fifo, MAX_FIFO_READS);
            diff_fifo = MAX_FIFO_READS;
        }

        /* FIFO 看门狗：连续 5 秒空 FIFO → 重新初始化传感器
         * WiFi 上线瞬态电流尖峰可能打乱 LSM6DSO 内部状态机，导致 FIFO 停止输出。
         * 检测到长时间无数据后自动恢复，避免需要硬件复位。 */
        bool use_direct_sample = false;
        float direct_ax = 0.0f;
        float direct_ay = 0.0f;
        float direct_az = 0.0f;

        if (diff_fifo == 0) {
            fifo_empty_count++;
            if (fifo_empty_count >= FIFO_EMPTY_RECOVER_LOOPS) {
                ESP_LOGW(TAG, "FIFO empty for ~3s, recovering LSM6DSO...");
                if (lsm6dso_recover(spi) == ESP_OK) {
                    accel_resampler_init(&resampler);
                    win_count = 0;
                    since_last_infer = 0;
                    have_direct_sample = false;
                    direct_stale_count = 0;
                }
                fifo_empty_count = 0;
            }
            if (lsm6dso_get_accel(spi, &direct_ax, &direct_ay, &direct_az)) {
                use_direct_sample = true;
                diff_fifo = 1;
            }
        } else {
            fifo_empty_count = 0;  // 有数据就重置计数器
            have_direct_sample = false;
            direct_stale_count = 0;
        }

        for (int i = 0; i < diff_fifo; i++) {
            float ax;
            float ay;
            float az;
            if (use_direct_sample) {
                ax = direct_ax;
                ay = direct_ay;
                az = direct_az;

                bool unchanged =
                    have_direct_sample &&
                    ax == last_direct_ax &&
                    ay == last_direct_ay &&
                    az == last_direct_az;
                direct_stale_count = unchanged ? direct_stale_count + 1 : 0;
                have_direct_sample = true;
                last_direct_ax = ax;
                last_direct_ay = ay;
                last_direct_az = az;

                if (direct_stale_count >= FIFO_EMPTY_RECOVER_LOOPS) {
                    ESP_LOGW(TAG, "Direct accel sample unchanged for ~3s, recovering LSM6DSO...");
                    if (lsm6dso_recover(spi) == ESP_OK) {
                        accel_resampler_init(&resampler);
                        win_count = 0;
                        since_last_infer = 0;
                    }
                    have_direct_sample = false;
                    direct_stale_count = 0;
                    continue;
                }
            } else {
                lsm6dso_read_regs(spi, 0x78, fifo_data, 7);
                if ((fifo_data[0] >> 3) != FIFO_TAG_ACCEL) {
                    continue;
                }

                ax = ((int16_t)((fifo_data[2] << 8) | fifo_data[1])) * ACCEL_SENSITIVITY;
                ay = ((int16_t)((fifo_data[4] << 8) | fifo_data[3])) * ACCEL_SENSITIVITY;
                az = ((int16_t)((fifo_data[6] << 8) | fifo_data[5])) * ACCEL_SENSITIVITY;
            }

            // 记录加速度数据到SD卡
            if (sd_logger_is_recording()) {
                int64_t ts = get_synced_ms();
                sd_logger_write_accel(ts, ax, ay, az);
            }

            accel_sample_count++;
            TickType_t now_tick = xTaskGetTickCount();
            if ((now_tick - last_accel_log) >= pdMS_TO_TICKS(ACCEL_LOG_INTERVAL_MS)) {
                ESP_LOGI(TAG, "Accel: ax=%.3f ay=%.3f az=%.3f (samples=%d, fifo=%d)",
                         ax, ay, az, accel_sample_count, diff_fifo);
                accel_sample_count = 0;
                last_accel_log = now_tick;
            }

        
            float rs_xyz[3];
            if (accel_resampler_push(&resampler, ax, ay, az, rs_xyz)) {
                if (win_count < (int)window_size) {
                    window[win_count * 3 + 0] = rs_xyz[0];
                    window[win_count * 3 + 1] = rs_xyz[1];
                    window[win_count * 3 + 2] = rs_xyz[2];
                    win_count++;
                } else {
                    memmove(window, window + 3, sizeof(float) * (window_size - 1) * 3);
                    window[(window_size - 1) * 3 + 0] = rs_xyz[0];
                    window[(window_size - 1) * 3 + 1] = rs_xyz[1];
                    window[(window_size - 1) * 3 + 2] = rs_xyz[2];
                }

                since_last_infer++;
                if (win_count == (int)window_size && since_last_infer >= (int)window_step) {
                    
                    ESP_LOGI(TAG, "WIN[0]: %.4f %.4f %.4f  WIN[%d]: %.4f %.4f %.4f",
                             window[0], window[1], window[2],
                             (int)window_size-1,
                             window[(window_size-1)*3+0],
                             window[(window_size-1)*3+1],
                             window[(window_size-1)*3+2]);
                    if (goat_behavior_model_infer(window, window_size, &infer_result)) {
                        snprintf(act_label, sizeof(act_label), "%s", infer_result.label);
                        conf = infer_result.confidence;
                        inference_just_ran = true;

                        // 记录推理结果到SD卡
                        if (sd_logger_is_recording()) {
                            int64_t ts = get_synced_ms();
                            sd_logger_write_inference(ts, act_label, conf);
                        }
                    } else {
                        ESP_LOGE(TAG, "CNN inference failed for the current window.");
                    }
                    since_last_infer = 0;
                }
            }

            if (accel_udp_enabled) {
               
                if (!first_point) {
                    append_to_buffer(accel_buf, sizeof(accel_buf), &apos, ",");
                }
                append_to_buffer(accel_buf, sizeof(accel_buf), &apos, "[%.3f,%.3f,%.3f]", ax, ay, az);
                first_point = false;
                accel_count++;

                if (apos >= (int)sizeof(accel_buf) - BUFFER_RESERVE) {
                    ESP_LOGW(TAG, "Accel buffer near full, stopping FIFO read");
                    break;
                }
            }
        }

        if (accel_udp_enabled) {
           
            append_to_buffer(
                accel_buf, sizeof(accel_buf), &apos,
                "],\"n\":%d,\"stream\":true,\"sd\":{\"mounted\":%s,\"recording\":%s}}",
                accel_count,
                sd_logger_is_mounted() ? "true" : "false",
                sd_logger_is_recording() ? "true" : "false");

            if (accel_sock != -1) {
                send_udp_to_current_targets(accel_sock, accel_buf, strlen(accel_buf), ACCEL_PORT);
            }
        }

        
        if (inference_just_ran && udp_sock != -1) {
            int ipos = 0;
            int64_t infer_ts = get_synced_ms();

            // 一次电压采样后复用，避免 battery_get_percentage / battery_get_status 内部重复触发 ADC 采样
            uint32_t battery_voltage = battery_get_voltage();
            uint8_t battery_percentage = battery_voltage_to_percentage(battery_voltage);
            const char* battery_status = battery_percentage_to_status(battery_percentage);

            append_to_buffer(infer_buf, sizeof(infer_buf), &ipos,
                             "{\"type\":\"infer\",\"dev\":\"%s\",\"ts\":%lld,\"act\":\"%s\","
                             "\"conf\":%.3f,\"scores\":{",
                             wifi_get_device_id(), (long long)infer_ts, act_label, conf);
            for (size_t score_idx = 0; score_idx < goat_behavior_model_get_num_classes(); ++score_idx) {
                append_to_buffer(infer_buf, sizeof(infer_buf), &ipos,
                                 "%s\"%s\":%.3f",
                                 score_idx == 0 ? "" : ",",
                                 goat_behavior_model_get_label(score_idx),
                                 infer_result.scores[score_idx]);
            }
            append_to_buffer(infer_buf, sizeof(infer_buf), &ipos,
                             "},\"battery\":{\"voltage\":%u,\"percentage\":%u,\"status\":\"%s\"},"
                             "\"sd\":{\"mounted\":%s,\"recording\":%s}}",
                             battery_voltage, battery_percentage, battery_status,
                             sd_logger_is_mounted() ? "true" : "false",
                             sd_logger_is_recording() ? "true" : "false");

            send_udp_to_current_targets(udp_sock, infer_buf, strlen(infer_buf), TARGET_PORT);
        }
    }
}

void app_main(void)
{
    
    setenv("TZ", "CST-8", 1);
    tzset();
    ESP_LOGI(TAG, "Timezone set to CST-8 (UTC+8)");

    init_nvs();

    // ========== 创建互斥锁保护共享变量 ==========
    stream_mutex = xSemaphoreCreateMutex();
    if (stream_mutex == NULL) {
        ESP_LOGE(TAG, "Failed to create stream mutex");
        return;
    }
    ESP_LOGI(TAG, "Stream mutex created");
    
    esp_pm_config_t pm_config = {
        .max_freq_mhz = 160,        // 推理时最高频率
        .min_freq_mhz = 40,         // 空闲时最低频率
        .light_sleep_enable = false // 关闭 Light Sleep（避免 INT1 中断丢失）
    };
    esp_err_t pm_err = esp_pm_configure(&pm_config);
    if (pm_err == ESP_OK) {
        ESP_LOGI(TAG, "Power management enabled: DFS (40-160MHz), Light Sleep DISABLED");
        ESP_LOGI(TAG, "Expected power saving: ~25mA, battery life +25%%");
    } else {
        ESP_LOGW(TAG, "Power management config failed: %s (check menuconfig)",
                 esp_err_to_name(pm_err));
    }
    // =====================================================================

    ESP_ERROR_CHECK(goat_behavior_model_init());

    size_t warm_win = goat_behavior_model_get_window_size();
    float *warm_buf = (float *)calloc(warm_win * 3, sizeof(float));
    if (!warm_buf) {
        ESP_LOGE(TAG, "Failed to allocate model warmup buffer (%zu floats)", warm_win * 3);
        vSemaphoreDelete(stream_mutex);
        return;
    }
    goat_behavior_model_result_t warm_result = {0};
    if (goat_behavior_model_infer(warm_buf, warm_win, &warm_result)) {
        ESP_LOGI(TAG, "Model warmup succeeded: %s (conf=%.3f)",
                 warm_result.label, warm_result.confidence);
    } else {
        ESP_LOGW(TAG, "Model warmup inference failed.");
    }
    free(warm_buf);

    wifi_init();

    // 初始化电池监测
    battery_monitor_init();
    ESP_LOGI(TAG, "Battery monitor initialized");
    uint32_t init_voltage = battery_get_voltage();
    uint8_t init_percentage = battery_get_percentage();
    ESP_LOGI(TAG, "Battery: %u mV (%u%%)", init_voltage, init_percentage);

    // 初始化SD卡
    esp_err_t sd_ret = sd_logger_init();
    if (sd_ret == ESP_OK) {
        ESP_LOGI(TAG, "SD card initialized successfully");
        int64_t free_kb = sd_logger_get_free_space_kb();
        if (free_kb >= 0) {
            ESP_LOGI(TAG, "SD card free space: %lld KB", (long long)free_kb);
        }
    } else {
        ESP_LOGW(TAG, "SD card initialization failed: %s (continuing without SD logging)",
                 esp_err_to_name(sd_ret));
    }

    udp_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (udp_sock >= 0) {
        int broadcast_en = 1;
        setsockopt(udp_sock, SOL_SOCKET, SO_BROADCAST, &broadcast_en, sizeof(broadcast_en));
        
        struct timeval send_to = { .tv_sec = 0, .tv_usec = 100 * 1000 };
        setsockopt(udp_sock, SOL_SOCKET, SO_SNDTIMEO, &send_to, sizeof(send_to));
    } else {
        ESP_LOGE(TAG, "Failed to create data UDP socket.");
    }

    accel_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (accel_sock >= 0) {
        int broadcast_en = 1;
        setsockopt(accel_sock, SOL_SOCKET, SO_BROADCAST, &broadcast_en, sizeof(broadcast_en));
        struct timeval send_to = { .tv_sec = 0, .tv_usec = 100 * 1000 };
        setsockopt(accel_sock, SOL_SOCKET, SO_SNDTIMEO, &send_to, sizeof(send_to));
    } else {
        ESP_LOGE(TAG, "Failed to create accel UDP socket.");
    }

    spi_bus_config_t buscfg = {
        .miso_io_num = PIN_NUM_MISO,
        .mosi_io_num = PIN_NUM_MOSI,
        .sclk_io_num = PIN_NUM_CLK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 32
    };
   
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_DISABLED));

    spi_device_handle_t lsm_spi_handle;
    ESP_ERROR_CHECK(lsm6dso_init(SPI2_HOST, PIN_NUM_CS, &lsm_spi_handle));
    lsm6dso_fifo_init(lsm_spi_handle);

    gpio_config_t io_conf = {
        .intr_type = GPIO_INTR_POSEDGE,
        .pin_bit_mask = (1ULL << PIN_NUM_INT1),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = 0,
        .pull_down_en = 1,
    };
    gpio_config(&io_conf);
    gpio_install_isr_service(0);
    gpio_isr_handler_add(PIN_NUM_INT1, lsm6dso_isr_handler, NULL);

    xTaskCreatePinnedToCore(lsm_read_task, "LSM_TASK", 8192,
                            (void *)lsm_spi_handle, 10, &lsm_task_handle, 1);

    xTaskCreatePinnedToCore(control_task, "CTRL_TASK", 4096,
                            NULL, 9, NULL, 0);

    ESP_LOGI(TAG, "System startup complete. Sensor/model tasks keep running while WiFi serves clients.");
    while (1) {
        vTaskDelay(portMAX_DELAY);
    }
}
