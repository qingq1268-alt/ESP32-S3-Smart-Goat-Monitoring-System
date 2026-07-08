#include "lsm6dso.h"
#include "esp_log.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "LSM6DSO_DRV";
const float ACCEL_SENSITIVITY = 0.061 / 1000.0;
static const uint8_t LSM6DSO_CTRL1_XL_26HZ_2G = 0x20;
static const uint8_t LSM6DSO_FIFO_BDR_XL_26HZ = 0x02;
static const uint16_t LSM6DSO_FIFO_WATERMARK = 13;

#define LSM6DSO_RESET_DELAY_MS 15
#define LSM6DSO_BYPASS_DELAY_MS 5
#define LSM6DSO_MAX_READ_LEN 256

void lsm6dso_write_reg(spi_device_handle_t spi, uint8_t reg, uint8_t val) {
    // SPI 写操作：最高位为 0
    uint8_t tx_data[2] = {reg & 0x7F, val}; 
    spi_transaction_t t = {
        .length = 16,
        .tx_buffer = tx_data,
    };
    spi_device_polling_transmit(spi, &t);
}

void lsm6dso_read_regs(spi_device_handle_t spi, uint8_t reg, uint8_t *data, size_t len) {
    // 防止缓冲区溢出
    if (data == NULL || len == 0 || len > LSM6DSO_MAX_READ_LEN) {
        ESP_LOGE(TAG, "Invalid read: data=%p len=%zu", data, len);
        return;
    }

    uint8_t tx_data[LSM6DSO_MAX_READ_LEN + 1] = {0};
    uint8_t rx_data[LSM6DSO_MAX_READ_LEN + 1] = {0};

    // SPI 读操作：最高位为 1
    tx_data[0] = reg | 0x80;

    spi_transaction_t t = {
        .length = (len + 1) * 8,
        .tx_buffer = tx_data,
        .rx_buffer = rx_data,
    };
    spi_device_polling_transmit(spi, &t);
    // 跳过接收缓冲区的第一个无用字节
    memcpy(data, &rx_data[1], len);
}

static esp_err_t lsm6dso_configure_accel(spi_device_handle_t spi) {
    // 软件复位，清除传感器内部状态机和旧 FIFO 状态。
    lsm6dso_write_reg(spi, 0x12, 0x01);
    vTaskDelay(LSM6DSO_RESET_DELAY_MS / portTICK_PERIOD_MS);

    uint8_t who_am_i = 0;
    lsm6dso_read_regs(spi, LSM6DSO_WHO_AM_I, &who_am_i, 1);
    if (who_am_i != 0x6C) {
        ESP_LOGE(TAG, "错误：找不到 LSM6DSO 模块！读取到的 WHO_AM_I: 0x%02X", who_am_i);
        return ESP_FAIL;
    }

    
    lsm6dso_write_reg(spi, LSM6DSO_CTRL1_XL, LSM6DSO_CTRL1_XL_26HZ_2G);
    return ESP_OK;
}

esp_err_t lsm6dso_init(spi_host_device_t host_id, int cs_pin, spi_device_handle_t *out_handle) {
    esp_err_t ret;
    spi_device_interface_config_t devcfg = {
        .clock_speed_hz = 1 * 1000 * 1000, // 1 MHz 通信速度
        .mode = 3,                         // SPI 模式 3
        .spics_io_num = cs_pin,
        .queue_size = 7,
    };

    // 将设备挂载到 SPI 总线上
    ret = spi_bus_add_device(host_id, &devcfg, out_handle);
    if (ret != ESP_OK) return ret;

    ret = lsm6dso_configure_accel(*out_handle);
    if (ret != ESP_OK) return ret;
    ESP_LOGI(TAG, "LSM6DSO 基础初始化配置成功！");
    
    return ESP_OK;
}

esp_err_t lsm6dso_recover(spi_device_handle_t spi) {
    ESP_LOGW(TAG, "Recovering LSM6DSO with software reset...");
    esp_err_t ret = lsm6dso_configure_accel(spi);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "LSM6DSO recovery failed: %s", esp_err_to_name(ret));
        return ret;
    }

    lsm6dso_fifo_init(spi);
    ESP_LOGI(TAG, "LSM6DSO recovery complete");
    return ESP_OK;
}

// 核心新增：配置 FIFO 和 INT1 引脚中断
void lsm6dso_fifo_init(spi_device_handle_t spi) {
    // 1. 配置 CTRL3_C (12h)：开启 BDU (块数据更新) 和 IF_INC (地址自动递增) 
    lsm6dso_write_reg(spi, 0x12, 0x44); 

    // 2. 配置 FIFO 水位线阈值
    // 设定为 50，表示 FIFO 存满 50 个样本时触发一次硬件中断
    // Keep FIFO wakeups close to the old latency budget after lowering ODR.
    lsm6dso_write_reg(spi, 0x07, (uint8_t)(LSM6DSO_FIFO_WATERMARK & 0xFF));
    lsm6dso_write_reg(spi, 0x08, 0x00); // FIFO_CTRL2: WTM_FIFO[8] = 0

    // 3. 配置 FIFO 批量写入速率 (BDR)
    // 设定加速度计写入速率为 104Hz (0x04)
    // Match the FIFO batch rate to the accelerometer output data rate.
    lsm6dso_write_reg(spi, 0x09, LSM6DSO_FIFO_BDR_XL_26HZ);
    lsm6dso_write_reg(spi, 0x0A, 0x00);
    vTaskDelay(LSM6DSO_BYPASS_DELAY_MS / portTICK_PERIOD_MS); 
    lsm6dso_write_reg(spi, 0x0A, 0x06); // FIFO_CTRL4: FIFO_MODE = Continuous
    lsm6dso_write_reg(spi, 0x0D, 0x08); // INT1_CTRL: INT1_FIFO_TH = 1

    ESP_LOGI(TAG, "LSM6DSO 的 FIFO 和 INT1 硬件中断配置完成！");
}

bool lsm6dso_get_accel(spi_device_handle_t spi, float *acc_x, float *acc_y, float *acc_z) {
    uint8_t raw_data[6];
    lsm6dso_read_regs(spi, LSM6DSO_OUTX_L_A, raw_data, 6);

    int16_t acc_x_raw = (int16_t)((raw_data[1] << 8) | raw_data[0]);
    int16_t acc_y_raw = (int16_t)((raw_data[3] << 8) | raw_data[2]);
    int16_t acc_z_raw = (int16_t)((raw_data[5] << 8) | raw_data[4]);

    *acc_x = acc_x_raw * ACCEL_SENSITIVITY;
    *acc_y = acc_y_raw * ACCEL_SENSITIVITY;
    *acc_z = acc_z_raw * ACCEL_SENSITIVITY;

    return true;
}
