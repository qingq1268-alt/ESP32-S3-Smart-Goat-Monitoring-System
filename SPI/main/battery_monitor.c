#include "battery_monitor.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_log.h"
#include <math.h>

static const char *TAG = "BATTERY";

static adc_oneshot_unit_handle_t adc1_handle = NULL;
static adc_cali_handle_t adc1_cali_handle = NULL;
static bool adc_calibrated = false;

/**
 * 初始化ADC校准
 */
static bool adc_calibration_init(void)
{
    esp_err_t ret = ESP_FAIL;
    bool calibrated = false;

#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
    adc_cali_curve_fitting_config_t cali_config = {
        .unit_id = ADC_UNIT_1,
        .atten = BATTERY_ADC_ATTEN,
        .bitwidth = BATTERY_ADC_WIDTH,
    };
    ret = adc_cali_create_scheme_curve_fitting(&cali_config, &adc1_cali_handle);
    if (ret == ESP_OK) {
        calibrated = true;
        ESP_LOGI(TAG, "ADC calibration: Curve Fitting");
    }
#endif

#if ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED
    if (!calibrated) {
        adc_cali_line_fitting_config_t cali_config = {
            .unit_id = ADC_UNIT_1,
            .atten = BATTERY_ADC_ATTEN,
            .bitwidth = BATTERY_ADC_WIDTH,
        };
        ret = adc_cali_create_scheme_line_fitting(&cali_config, &adc1_cali_handle);
        if (ret == ESP_OK) {
            calibrated = true;
            ESP_LOGI(TAG, "ADC calibration: Line Fitting");
        }
    }
#endif

    return calibrated;
}

void battery_monitor_init(void)
{
    // 配置ADC
    adc_oneshot_unit_init_cfg_t init_config = {
        .unit_id = ADC_UNIT_1,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_config, &adc1_handle));

    // 配置通道
    adc_oneshot_chan_cfg_t config = {
        .bitwidth = BATTERY_ADC_WIDTH,
        .atten = BATTERY_ADC_ATTEN,
    };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc1_handle, BATTERY_ADC_CHANNEL, &config));

    // 初始化校准
    adc_calibrated = adc_calibration_init();
    if (adc_calibrated) {
        ESP_LOGI(TAG, "Battery monitor initialized with calibration");
    } else {
        ESP_LOGW(TAG, "Battery monitor initialized without calibration");
    }
}

uint32_t battery_get_voltage(void)
{
    int adc_raw = 0;
    int voltage_mv = 0;
    uint32_t sum = 0;

    // 多次采样取平均，减少噪声
    for (int i = 0; i < BATTERY_SAMPLE_COUNT; i++) {
        ESP_ERROR_CHECK(adc_oneshot_read(adc1_handle, BATTERY_ADC_CHANNEL, &adc_raw));

        if (adc_calibrated) {
            ESP_ERROR_CHECK(adc_cali_raw_to_voltage(adc1_cali_handle, adc_raw, &voltage_mv));
        } else {
            // 无校准时使用线性转换：12位ADC，3.3V量程
            voltage_mv = (adc_raw * 3300) / 4095;
        }

        sum += voltage_mv;
    }

    // 计算平均值
    voltage_mv = sum / BATTERY_SAMPLE_COUNT;

    // 还原实际电池电压（考虑分压比和实测校准）
    uint32_t battery_voltage = (uint32_t)(
        voltage_mv * BATTERY_VOLTAGE_DIVIDER * BATTERY_CALIBRATION_GAIN
    ) + BATTERY_CALIBRATION_OFFSET_MV;

    // 限制在合理范围内
    if (battery_voltage > BATTERY_VOLTAGE_MAX) {
        battery_voltage = BATTERY_VOLTAGE_MAX;
    }
    if (battery_voltage < BATTERY_VOLTAGE_MIN) {
        battery_voltage = BATTERY_VOLTAGE_MIN;
    }

    return battery_voltage;
}

uint8_t battery_voltage_to_percentage(uint32_t voltage)
{
    // 单节锂电池在 ESP32 + WiFi + SD 负载下会有压降。
    // 这里显示的是“运行中电量估算”，不是库仑计意义上的精确 SOC。
    uint8_t percentage;

    if (voltage >= 4050) {
        // 满电电池一上负载常会从 4.20V 回落，4.05V 以上按满电显示。
        percentage = 100;
    } else if (voltage >= 4000) {
        // 4.00V-4.05V: 90%-100%
        percentage = 90 + ((voltage - 4000) * 10) / 50;
    } else if (voltage >= 3900) {
        // 3.90V-4.00V: 75%-90%
        percentage = 75 + ((voltage - 3900) * 15) / 100;
    } else if (voltage >= 3800) {
        // 3.80V-3.90V: 55%-75%
        percentage = 55 + ((voltage - 3800) * 20) / 100;
    } else if (voltage >= 3700) {
        // 3.70V-3.80V: 35%-55%
        percentage = 35 + ((voltage - 3700) * 20) / 100;
    } else if (voltage >= 3600) {
        // 3.60V-3.70V: 20%-35%
        percentage = 20 + ((voltage - 3600) * 15) / 100;
    } else if (voltage >= 3500) {
        // 3.50V-3.60V: 10%-20%
        percentage = 10 + ((voltage - 3500) * 10) / 100;
    } else if (voltage >= 3300) {
        // 3.30V-3.50V: 0%-10%
        percentage = ((voltage - 3300) * 10) / 200;
    } else {
        percentage = 0;
    }

    if (percentage > 100) percentage = 100;

    return percentage;
}

const char* battery_percentage_to_status(uint8_t percentage)
{
    if (percentage >= 95) {
        return "Full";
    } else if (percentage <= 15) {
        return "Low";
    } else {
        return "Normal";
    }
}

uint8_t battery_get_percentage(void)
{
    return battery_voltage_to_percentage(battery_get_voltage());
}

const char* battery_get_status(void)
{
    return battery_percentage_to_status(battery_get_percentage());
}
