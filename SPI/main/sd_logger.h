#ifndef __SD_LOGGER_H__
#define __SD_LOGGER_H__

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

/**
 * SD卡数据记录器
 * 用于将传感器数据和推理结果记录到SD卡
 */

// SD卡SPI引脚配置（使用SPI3总线，避免与LSM6DSO的SPI2冲突）
#define SD_PIN_MISO  5   // GPIO5
#define SD_PIN_MOSI  6   // GPIO6
#define SD_PIN_CLK   7   // GPIO7
#define SD_PIN_CS    15  // GPIO15

// 数据记录配置
#define SD_MOUNT_POINT "/sdcard"
#define SD_LOG_DIR "/sdcard/logs"
#define SD_MAX_FILES 1000

/**
 * 初始化SD卡
 * @return ESP_OK 成功, 其他值表示失败
 */
esp_err_t sd_logger_init(void);

/**
 * 打印SD卡引脚诊断信息
 */
void sd_logger_log_diagnostics(void);

/**
 * 反初始化SD卡
 */
void sd_logger_deinit(void);

/**
 * 开始新的记录会话
 * 创建新的CSV文件，文件名格式: YYYYMMDD_HHMMSS.csv
 * @return ESP_OK 成功, 其他值表示失败
 */
esp_err_t sd_logger_start_session(void);

/**
 * 停止当前记录会话
 */
void sd_logger_stop_session(void);

/**
 * 记录加速度数据
 * @param timestamp_ms 时间戳（毫秒）
 * @param accel_x X轴加速度（g）
 * @param accel_y Y轴加速度（g）
 * @param accel_z Z轴加速度（g）
 * @return ESP_OK 成功, 其他值表示失败
 */
esp_err_t sd_logger_write_accel(int64_t timestamp_ms, float accel_x, float accel_y, float accel_z);

/**
 * 记录推理结果
 * @param timestamp_ms 时间戳（毫秒）
 * @param behavior 行为标签
 * @param confidence 置信度（0.0-1.0）
 * @return ESP_OK 成功, 其他值表示失败
 */
esp_err_t sd_logger_write_inference(int64_t timestamp_ms, const char *behavior, float confidence);

/**
 * 检查SD卡是否已挂载
 * @return true 已挂载, false 未挂载
 */
bool sd_logger_is_mounted(void);

/**
 * 检查是否正在记录
 * @return true 正在记录, false 未记录
 */
bool sd_logger_is_recording(void);

/**
 * 获取当前日志文件路径
 * @return 文件路径字符串，如果未记录则返回NULL
 */
const char* sd_logger_get_current_file(void);

/**
 * 获取SD卡剩余空间（KB）
 * @return 剩余空间（KB），失败返回-1
 */
int64_t sd_logger_get_free_space_kb(void);

#endif // __SD_LOGGER_H__
