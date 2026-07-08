#include "sd_logger.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "sdmmc_cmd.h"
#include "driver/sdspi_host.h"
#include "driver/spi_common.h"
#include "driver/gpio.h"
#include <errno.h>
#include <string.h>
#include <sys/stat.h>
#include <dirent.h>
#include <time.h>
#include <sys/time.h>
#include <unistd.h>
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "SD_LOGGER";

static sdmmc_card_t *card = NULL;
static FILE *current_file = NULL;
static char current_filepath[128] = {0};
static bool is_mounted = false;
static bool is_recording = false;
static bool bus_initialized = false;

#define WRITE_BUFFER_SIZE 4096
#define SD_FLUSH_INTERVAL_MS 1000
#define SD_MOUNT_MAX_FREQ_KHZ 400
#define SD_MOUNT_ATTEMPTS 2
#define SD_MOUNT_RETRY_DELAY_MS 250
#define SD_PIN_SETTLE_MS 2

static char write_buffer[WRITE_BUFFER_SIZE];
static size_t buffer_pos = 0;
static int64_t last_sync_ms = 0;

static int64_t now_ms(void)
{
    return (int64_t)(esp_timer_get_time() / 1000);
}

static void format_timestamp_seconds(int64_t timestamp_ms, char *out, size_t out_size)
{
    if (out == NULL || out_size == 0) {
        return;
    }

    time_t seconds = (time_t)(timestamp_ms / 1000);
    struct tm timeinfo;
    localtime_r(&seconds, &timeinfo);

    if (strftime(out, out_size, "%Y-%m-%d %H:%M:%S", &timeinfo) == 0) {
        out[0] = '\0';
    }
}

static void sync_current_file(void)
{
    if (!current_file) {
        return;
    }

    int flush_ret = fflush(current_file);
    if (flush_ret != 0) {
        ESP_LOGW(TAG, "fflush failed: %d", flush_ret);
        return;
    }

    int fd = fileno(current_file);
    if (fd >= 0) {
        int sync_ret = fsync(fd);
        if (sync_ret != 0) {
            ESP_LOGW(TAG, "fsync failed: %d", sync_ret);
        }
    }
    last_sync_ms = now_ms();
}

static void flush_buffer(void)
{
    if (current_file && buffer_pos > 0) {
        size_t written = fwrite(write_buffer, 1, buffer_pos, current_file);
        if (written != buffer_pos) {
            ESP_LOGW(TAG, "Partial write: expected %zu, wrote %zu", buffer_pos, written);
        }
        sync_current_file();
        buffer_pos = 0;
    }
}

static void buffer_write(const char *data, size_t len)
{
    if (data == NULL || len == 0) {
        return;
    }

    if (buffer_pos + len >= WRITE_BUFFER_SIZE) {
        flush_buffer();
    }
    if (len >= WRITE_BUFFER_SIZE) {
        if (current_file) {
            size_t written = fwrite(data, 1, len, current_file);
            if (written != len) {
                ESP_LOGW(TAG, "Direct write partial: expected %zu, wrote %zu", len, written);
            }
            sync_current_file();
        }
    } else {
        memcpy(write_buffer + buffer_pos, data, len);
        buffer_pos += len;
    }
}

static esp_err_t sd_logger_ensure_log_dir(void)
{
    struct stat st;
    if (stat(SD_LOG_DIR, &st) == 0) {
        if ((st.st_mode & S_IFDIR) != 0) {
            return ESP_OK;
        }
        ESP_LOGE(TAG, "%s exists but is not a directory", SD_LOG_DIR);
        return ESP_FAIL;
    }

    int saved_errno = errno;
    errno = 0;
    if (mkdir(SD_LOG_DIR, 0775) == 0) {
        ESP_LOGI(TAG, "Created log directory: %s", SD_LOG_DIR);
        return ESP_OK;
    }

    ESP_LOGE(TAG, "Failed to create log directory %s: errno=%d (%s), stat_errno=%d",
             SD_LOG_DIR, errno, strerror(errno), saved_errno);
    return ESP_FAIL;
}

static void sd_logger_release_bus(void)
{
    if (!bus_initialized) {
        return;
    }

    esp_err_t ret = spi_bus_free(SPI3_HOST);
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "Failed to free SD SPI bus: %s", esp_err_to_name(ret));
    }
    bus_initialized = false;
}

static int sd_logger_read_miso_with_pull(gpio_pull_mode_t pull)
{
    gpio_set_direction(SD_PIN_MISO, GPIO_MODE_INPUT);
    gpio_set_pull_mode(SD_PIN_MISO, pull);
    vTaskDelay(pdMS_TO_TICKS(SD_PIN_SETTLE_MS));
    return gpio_get_level(SD_PIN_MISO);
}

static void sd_logger_log_miso_pull_diagnostic(void)
{
    int level_pulldown = sd_logger_read_miso_with_pull(GPIO_PULLDOWN_ONLY);
    int level_pullup = sd_logger_read_miso_with_pull(GPIO_PULLUP_ONLY);

    ESP_LOGI(TAG, "SD MISO pull diag: pulldown=%d pullup=%d",
             level_pulldown, level_pullup);

    if (level_pullup == 0) {
        ESP_LOGW(TAG, "SD MISO stays low with pull-up; check DO/MISO wiring or shorts.");
    } else if (level_pulldown == 0) {
        ESP_LOGW(TAG, "SD MISO has no strong external pull-up; add/verify 10k pull-up on DO.");
    }
}

static void sd_logger_prepare_pins(void)
{
    gpio_reset_pin(SD_PIN_MISO);
    gpio_reset_pin(SD_PIN_MOSI);
    gpio_reset_pin(SD_PIN_CLK);
    gpio_reset_pin(SD_PIN_CS);

    gpio_set_direction(SD_PIN_MISO, GPIO_MODE_INPUT);
    gpio_set_pull_mode(SD_PIN_MISO, GPIO_PULLUP_ONLY);

    gpio_set_direction(SD_PIN_MOSI, GPIO_MODE_INPUT_OUTPUT);
    gpio_set_pull_mode(SD_PIN_MOSI, GPIO_PULLUP_ONLY);
    gpio_set_level(SD_PIN_MOSI, 1);

    gpio_set_direction(SD_PIN_CLK, GPIO_MODE_INPUT_OUTPUT);
    gpio_set_pull_mode(SD_PIN_CLK, GPIO_PULLUP_ONLY);
    gpio_set_level(SD_PIN_CLK, 0);

    gpio_set_direction(SD_PIN_CS, GPIO_MODE_INPUT_OUTPUT);
    gpio_set_pull_mode(SD_PIN_CS, GPIO_PULLUP_ONLY);
    gpio_set_level(SD_PIN_CS, 1);

    vTaskDelay(pdMS_TO_TICKS(10));
}

static void sd_logger_log_pin_levels(const char *stage)
{
    ESP_LOGI(TAG,
             "SD pins %s: MISO=GPIO%d level=%d, MOSI=GPIO%d level=%d, "
             "CLK=GPIO%d level=%d, CS=GPIO%d level=%d",
             stage,
             SD_PIN_MISO, gpio_get_level(SD_PIN_MISO),
             SD_PIN_MOSI, gpio_get_level(SD_PIN_MOSI),
             SD_PIN_CLK, gpio_get_level(SD_PIN_CLK),
             SD_PIN_CS, gpio_get_level(SD_PIN_CS));
}

static void sd_logger_log_mount_hint(esp_err_t ret)
{
    if (ret == ESP_ERR_TIMEOUT) {
        ESP_LOGE(TAG,
                 "SD init timed out before FAT mount. Check 3.0-3.3V at the card, "
                 "common GND, DO->GPIO5, DI->GPIO6, CLK->GPIO7, CS->GPIO15.");
        ESP_LOGE(TAG,
                 "If the module has an onboard regulator, 3.3V on its VCC may be too low; "
                 "use the module 3V3 pad or a 5V-compatible module with 3.3V-safe IO.");
    } else if (ret == ESP_FAIL) {
        ESP_LOGE(TAG, "SD card initialized but FAT mount failed; check FAT32 formatting.");
    }
}

void sd_logger_log_diagnostics(void)
{
    if (is_mounted) {
        ESP_LOGI(TAG, "SD card is already mounted: %s", SD_MOUNT_POINT);
        return;
    }

    ESP_LOGI(TAG, "SD config: SPI3 MISO=GPIO%d MOSI=GPIO%d CLK=GPIO%d CS=GPIO%d",
             SD_PIN_MISO, SD_PIN_MOSI, SD_PIN_CLK, SD_PIN_CS);
    sd_logger_prepare_pins();
    sd_logger_log_pin_levels("idle");
    sd_logger_log_miso_pull_diagnostic();
}

static esp_err_t sd_logger_mount_once(int attempt)
{
    esp_err_t ret;

    esp_vfs_fat_sdmmc_mount_config_t mount_config = {
        .format_if_mount_failed = false,
        .max_files = 5,
        .allocation_unit_size = 16 * 1024
    };

    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = SPI3_HOST;
    // Keep SD SPI slow; the logger bandwidth is tiny and this is much more tolerant
    // of long jumper wires, marginal sockets, and weak pull-ups.
    host.max_freq_khz = SD_MOUNT_MAX_FREQ_KHZ;

    ESP_LOGI(TAG, "Mount attempt %d/%d", attempt, SD_MOUNT_ATTEMPTS);
    sd_logger_log_diagnostics();

    spi_bus_config_t bus_cfg = {
        .miso_io_num = SD_PIN_MISO,
        .mosi_io_num = SD_PIN_MOSI,
        .sclk_io_num = SD_PIN_CLK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 4000,
    };
    ret = spi_bus_initialize(host.slot, &bus_cfg, SDSPI_DEFAULT_DMA);
    if (ret == ESP_OK || ret == ESP_ERR_INVALID_STATE) {
        bus_initialized = true;
    } else {
        ESP_LOGE(TAG, "Failed to initialize SPI bus for SD card: %s", esp_err_to_name(ret));
        return ret;
    }

    sdspi_device_config_t slot_config = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot_config.gpio_cs = SD_PIN_CS;
    slot_config.host_id = host.slot;

    ret = esp_vfs_fat_sdspi_mount(SD_MOUNT_POINT, &host, &slot_config, &mount_config, &card);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to mount SD card: %s", esp_err_to_name(ret));
        sd_logger_release_bus();
        return ret;
    }

    is_mounted = true;
    last_sync_ms = now_ms();
    ESP_LOGI(TAG, "SD card mounted successfully");

    sdmmc_card_print_info(stdout, card);

    if (sd_logger_ensure_log_dir() != ESP_OK) {
        ESP_LOGW(TAG, "SD mounted, but log directory is not ready.");
    }

    return ESP_OK;
}

esp_err_t sd_logger_init(void)
{
    if (is_mounted) {
        return ESP_OK;
    }

    esp_err_t last_ret = ESP_FAIL;
    for (int attempt = 1; attempt <= SD_MOUNT_ATTEMPTS; ++attempt) {
        last_ret = sd_logger_mount_once(attempt);
        if (last_ret == ESP_OK) {
            return ESP_OK;
        }

        if (attempt < SD_MOUNT_ATTEMPTS) {
            ESP_LOGW(TAG, "Retrying SD mount after %d ms...", SD_MOUNT_RETRY_DELAY_MS);
            vTaskDelay(pdMS_TO_TICKS(SD_MOUNT_RETRY_DELAY_MS));
        }
    }

    sd_logger_log_mount_hint(last_ret);
    return last_ret;
}

void sd_logger_deinit(void)
{
    if (is_recording) {
        sd_logger_stop_session();
    }
    if (is_mounted) {
        esp_vfs_fat_sdcard_unmount(SD_MOUNT_POINT, card);
        card = NULL;
        is_mounted = false;
        ESP_LOGI(TAG, "SD card unmounted");
    }
    sd_logger_release_bus();
}

esp_err_t sd_logger_start_session(void)
{
    if (!is_mounted) {
        ESP_LOGW(TAG, "SD card not mounted, retrying mount before starting session");
        esp_err_t mount_ret = sd_logger_init();
        if (mount_ret != ESP_OK) {
            ESP_LOGE(TAG, "SD card mount retry failed: %s", esp_err_to_name(mount_ret));
            return mount_ret;
        }
    }
    if (is_recording) {
        ESP_LOGW(TAG, "Already recording, stopping current session first");
        sd_logger_stop_session();
    }

    esp_err_t dir_ret = sd_logger_ensure_log_dir();
    if (dir_ret != ESP_OK) {
        return dir_ret;
    }

    time_t now;
    struct tm timeinfo;
    time(&now);
    localtime_r(&now, &timeinfo);

    snprintf(current_filepath, sizeof(current_filepath),
             "%s/%04d%02d%02d_%02d%02d%02d.csv",
             SD_LOG_DIR,
             timeinfo.tm_year + 1900, timeinfo.tm_mon + 1, timeinfo.tm_mday,
             timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec);

    errno = 0;
    current_file = fopen(current_filepath, "w");
    if (!current_file) {
        ESP_LOGE(TAG, "Failed to create file: %s, errno=%d (%s)",
                 current_filepath, errno, strerror(errno));
        return ESP_FAIL;
    }

    const char *header = "timestamp_ms,datetime,type,accel_x,accel_y,accel_z,behavior,confidence\n";
    size_t header_len = strlen(header);
    size_t header_written = fwrite(header, 1, header_len, current_file);
    if (header_written != header_len) {
        ESP_LOGE(TAG, "Failed to write CSV header: expected %zu, wrote %zu", header_len, header_written);
        fclose(current_file);
        current_file = NULL;
        return ESP_FAIL;
    }
    sync_current_file();

    buffer_pos = 0;
    last_sync_ms = now_ms();
    is_recording = true;
    ESP_LOGI(TAG, "SD logging started: %s", current_filepath);

    return ESP_OK;
}

void sd_logger_stop_session(void)
{
    if (!is_recording) {
        return;
    }

    flush_buffer();

    if (current_file) {
        fclose(current_file);
        current_file = NULL;
    }

    is_recording = false;
    ESP_LOGI(TAG, "SD logging stopped: %s", current_filepath);
}

esp_err_t sd_logger_write_accel(int64_t timestamp_ms, float accel_x, float accel_y, float accel_z)
{
    if (!is_recording || !current_file) {
        return ESP_FAIL;
    }

    char datetime[24];
    format_timestamp_seconds(timestamp_ms, datetime, sizeof(datetime));

    char line[160];
    int len = snprintf(line, sizeof(line),
                       "%lld,%s,accel,%.6f,%.6f,%.6f,,\n",
                       timestamp_ms, datetime, accel_x, accel_y, accel_z);
    if (len < 0 || len >= (int)sizeof(line)) {
        ESP_LOGW(TAG, "snprintf truncated or failed: %d", len);
        return ESP_FAIL;
    }
    buffer_write(line, len);

    if ((now_ms() - last_sync_ms) >= SD_FLUSH_INTERVAL_MS) {
        flush_buffer();
    }

    return ESP_OK;
}

esp_err_t sd_logger_write_inference(int64_t timestamp_ms, const char *behavior, float confidence)
{
    if (!is_recording || !current_file) {
        return ESP_FAIL;
    }

    flush_buffer();

    char datetime[24];
    format_timestamp_seconds(timestamp_ms, datetime, sizeof(datetime));

    char line[160];
    int len = snprintf(line, sizeof(line),
                       "%lld,%s,inference,,,,%s,%f\n",
                       timestamp_ms, datetime, behavior, confidence);
    if (len < 0 || len >= (int)sizeof(line)) {
        ESP_LOGW(TAG, "snprintf truncated or failed: %d", len);
        return ESP_FAIL;
    }

    size_t written = fwrite(line, 1, len, current_file);
    if (written != (size_t)len) {
        ESP_LOGW(TAG, "Inference write partial: expected %d, wrote %zu", len, written);
        return ESP_FAIL;
    }

    sync_current_file();

    return ESP_OK;
}

bool sd_logger_is_mounted(void)
{
    return is_mounted;
}

bool sd_logger_is_recording(void)
{
    return is_recording;
}

void sd_logger_flush(void)
{
    flush_buffer();
    sync_current_file();
}

const char* sd_logger_get_current_file(void)
{
    if (is_recording) {
        return current_filepath;
    }
    return NULL;
}

int64_t sd_logger_get_free_space_kb(void)
{
    if (!is_mounted) {
        return -1;
    }

    FATFS *fs;
    DWORD fre_clust;
    FRESULT res = f_getfree("0:", &fre_clust, &fs);
    if (res != FR_OK) {
        return -1;
    }

    uint64_t free_bytes = (uint64_t)fre_clust * fs->csize * 512;
    return (int64_t)(free_bytes / 1024);
}
