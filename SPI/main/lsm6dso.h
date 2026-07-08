#ifndef __LSM6DSO_H__
#define __LSM6DSO_H__

#include "driver/spi_master.h"
#include "esp_err.h"

// LSM6DSO寄存器地址
#define LSM6DSO_WHO_AM_I   0x0F
#define LSM6DSO_CTRL1_XL   0x10
#define LSM6DSO_OUTX_L_A   0x28
#define LSM6DSO_FIFO_STATUS1 0x3A

// 加速度灵敏度 (±2g, 0.061 mg/LSB)
extern const float ACCEL_SENSITIVITY;

void lsm6dso_write_reg(spi_device_handle_t spi, uint8_t reg, uint8_t val);
void lsm6dso_read_regs(spi_device_handle_t spi, uint8_t reg, uint8_t *data, size_t len);

esp_err_t lsm6dso_init(spi_host_device_t host_id, int cs_pin, spi_device_handle_t *out_handle);
esp_err_t lsm6dso_recover(spi_device_handle_t spi);
void lsm6dso_fifo_init(spi_device_handle_t spi);
bool lsm6dso_get_accel(spi_device_handle_t spi, float *acc_x, float *acc_y, float *acc_z);

#endif // __LSM6DSO_H__
