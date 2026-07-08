#ifndef __BATTERY_MONITOR_H__
#define __BATTERY_MONITOR_H__

#include <stdint.h>
#include <stdbool.h>
#include "esp_adc/adc_oneshot.h"

/**
 * 电池电压监测模块
 * 使用ADC读取分压电路的电压来估算电池电量
 */

// ADC配置
#define BATTERY_ADC_CHANNEL  ADC_CHANNEL_3   // ADC1_CH3
#define BATTERY_ADC_ATTEN    ADC_ATTEN_DB_12
#define BATTERY_ADC_WIDTH    ADC_BITWIDTH_12

// 分压比（两个10kΩ电阻等分）
#define BATTERY_VOLTAGE_DIVIDER 2.0f


#define BATTERY_CALIBRATION_GAIN 1.018f
#define BATTERY_CALIBRATION_OFFSET_MV 0

// 采样次数（取平均减少噪声）
#define BATTERY_SAMPLE_COUNT 16

// 电池电压范围（mV）
#define BATTERY_VOLTAGE_MAX 4200
#define BATTERY_VOLTAGE_MIN 3000

/**
 * 初始化电池监测模块
 */
void battery_monitor_init(void);

/**
 * 获取电池电压（mV）。会触发 16 次 ADC 采样。
 * @return 电池电压，单位mV
 */
uint32_t battery_get_voltage(void);

/**
 * 把电压换算成电量百分比（纯计算，不触发 ADC 采样）。
 * @param voltage_mv 电池电压（mV）
 * @return 0-100
 */
uint8_t battery_voltage_to_percentage(uint32_t voltage_mv);

/**
 
 * @param percentage 0-100
 * @return "Full", "Normal", "Low"
 */
const char* battery_percentage_to_status(uint8_t percentage);


uint8_t battery_get_percentage(void);


const char* battery_get_status(void);

#endif // __BATTERY_MONITOR_H__
