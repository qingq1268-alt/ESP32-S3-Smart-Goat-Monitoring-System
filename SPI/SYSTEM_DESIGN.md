# ESP32-S3 Goat Behavior Recognition System

本文档说明本项目的硬件连接、固件设计、PC 端软件设计、系统数据流、通信协议、SD 卡日志格式和常见排障方法。当前系统基于 ESP32-S3、LSM6DSO 三轴加速度计、microSD 卡模块和 PyQt5 上位机界面，实现山羊行为实时识别、波形显示、SD 卡记录、历史数据查看和健康风险提示。

## 1. 系统目标

系统采集佩戴在动物身上的三轴加速度数据，在 ESP32-S3 端完成轻量 CNN 行为识别，并通过 WiFi SoftAP 向 PC 上位机实时发送两类 UDP 数据：

- 原始三轴加速度波形，用于实时波形显示。
- 行为识别结果，用于当前行为、置信度、行为统计、健康风险和历史记录。

同时，ESP32-S3 可把原始加速度点和推理结果写入 SD 卡 CSV 文件。CSV 中保留毫秒时间戳和可读本地时间，便于后续 Excel、Python 或统计软件分析。

## 2. 硬件组成

| 模块 | 作用 | 关键要求 |
| --- | --- | --- |
| ESP32-S3 开发板 | 主控、WiFi SoftAP、模型推理、SPI 采集、SD 记录 | 8MB PSRAM 已识别，运行 ESP-IDF 5.5 |
| LSM6DSO 加速度计 | 采集三轴加速度 | SPI 模式 3，3.3V 供电，INT1 中断 |
| microSD 卡模块 | 本地 CSV 记录 | 当前使用带 AMS1117 的模块，VCC 接 5V，VOUT 输出 3.3V |
| microSD 卡 | 存储 CSV | 建议 FAT32 格式，插拔后可用 UI 重新挂载 |
| 电池电压分压 | 电量估算 | 两个 10k 电阻分压到 ADC1_CH3 |
| PC 上位机 | PyQt5 实时监控界面 | 连接 ESP32-S3 热点后监听 UDP |

## 3. 硬件连接

### 3.1 LSM6DSO 与 ESP32-S3

固件使用 ESP32-S3 的 SPI2_HOST 连接 LSM6DSO。

| LSM6DSO 引脚 | ESP32-S3 GPIO | 固件宏 | 说明 |
| --- | ---: | --- | --- |
| SDO / MISO | GPIO13 | `PIN_NUM_MISO` | LSM6DSO 数据输出 |
| SDA / MOSI | GPIO11 | `PIN_NUM_MOSI` | ESP32 数据输出 |
| SCL / SCLK | GPIO12 | `PIN_NUM_CLK` | SPI 时钟 |
| CS | GPIO10 | `PIN_NUM_CS` | 片选 |
| INT1 | GPIO2 | `PIN_NUM_INT1` | FIFO 水位中断 |
| VCC | 3.3V | - | 不要接 5V |
| GND | GND | - | 必须与 ESP32 共地 |

LSM6DSO 配置：

- SPI mode: 3
- SPI speed: 1 MHz
- 加速度量程: ±2g
- 加速度 ODR: 26 Hz
- FIFO BDR: 26 Hz
- FIFO watermark: 13 samples
- FIFO mode: continuous
- INT1: FIFO threshold interrupt

GPIO2 用作 INT1，是为了避开 GPIO4 上的电池 ADC 分压输入。

### 3.2 microSD 模块与 ESP32-S3

固件使用 SPI3_HOST 连接 SD 卡，避免与 LSM6DSO 的 SPI2 总线冲突。

| SD 模块引脚 | ESP32-S3 GPIO | 固件宏 | 说明 |
| --- | ---: | --- | --- |
| DO / MISO | GPIO5 | `SD_PIN_MISO` | SD 卡数据输出 |
| DI / MOSI | GPIO6 | `SD_PIN_MOSI` | ESP32 数据输出 |
| CLK / SCK | GPIO7 | `SD_PIN_CLK` | SPI 时钟 |
| CS | GPIO15 | `SD_PIN_CS` | 片选 |
| VCC | 5V | - | 当前 AMS1117 模块应接 5V |
| GND | GND | - | 必须与 ESP32 共地 |

当前模块带 AMS1117 线性稳压器。实测当 VCC 接 3.3V 时，AMS1117 压降导致 SD 卡实际供电不足，表现为：

- `ESP_ERR_TIMEOUT`
- `ESP_ERR_INVALID_RESPONSE`
- `SD MISO stays low with pull-up`
- 开机首次挂载失败但重新挂载可能成功

因此当前接法是 SD 模块 VCC 接 5V，模块稳压输出约 3.3V。若更换为裸卡座或无稳压模块，则只能使用 3.3V，并确认 IO 电平安全。

SD SPI 配置：

- SPI host: SPI3_HOST
- 初始化频率: 400 kHz
- FAT mount point: `/sdcard`
- 日志目录: `/sdcard/logs`
- 文件名格式: `YYYYMMDD_HHMMSS.csv`

### 3.3 电池电压检测

固件使用 ADC1_CH3 读取电池分压。

| 信号 | ESP32-S3 GPIO | 固件宏 | 说明 |
| --- | ---: | --- | --- |
| 电池分压中点 | GPIO4 | `BATTERY_ADC_CHANNEL ADC_CHANNEL_3` | ADC1_CH3 |

推荐分压：

```text
Battery+ -> 10k -> GPIO4/ADC1_CH3 -> 10k -> GND
```

注意：

- GPIO4 输入电压不得超过 3.3V。
- 固件按分压比 `2.0` 还原电池电压。
- 电量百分比为分段估算，适合趋势显示，不等同于精密电量计。

### 3.4 供电与地线

所有模块必须共地。SD 模块、传感器模块和 ESP32 的 GND 必须连接在一起。

建议：

- SD 卡模块和 ESP32 间 SPI 线尽量短。
- SD 卡供电旁边加 10uF 到 47uF 电容可改善插卡和写入瞬间电流波动。
- 若 SD MISO 空闲电平经常为 0，优先检查 DO/MISO 线、卡座接触、供电和模块电平转换。

## 4. 固件架构

固件主目录为 `SPI/main`。核心文件如下：

| 文件 | 作用 |
| --- | --- |
| `main.c` | 系统启动、任务创建、控制命令、传感器采集、UDP 发送、模型推理调度 |
| `lsm6dso.c/.h` | LSM6DSO SPI 驱动、FIFO 初始化、传感器恢复 |
| `goat_behavior_model.cpp/.h` | ESP-DL 模型加载、输入打包、推理、输出解析 |
| `sd_logger.c/.h` | SD 卡挂载、CSV 创建、加速度与推理结果写入 |
| `wifi_manager.c/.h` | ESP32-S3 SoftAP 初始化和 UDP 广播目标生成 |
| `battery_monitor.c/.h` | ADC 采样、电池电压和百分比估算 |
| `model/model_norm.h` | 训练阶段导出的归一化参数、类别标签、模型输入配置 |
| `model/goat_cnn_5cls_s3_int8.espdl` | ESP-DL int8 模型文件，作为固件资源嵌入 |

### 4.1 启动流程

`app_main()` 的核心顺序：

1. 设置时区为 `CST-8`，用于北京时间文件名和 CSV `datetime` 列。
2. 初始化 NVS。
3. 创建 `stream_mutex`。
4. 开启动态频率调节 DFS，CPU 40 MHz 到 160 MHz，关闭 Light Sleep。
5. 初始化 CNN 模型。
6. 做一次全零窗口 warmup 推理，提前完成模型内部内存分配。
7. 启动 WiFi SoftAP。
8. 初始化电池 ADC。
9. 初始化 SD 卡。
10. 创建行为推理 UDP socket 和加速度 UDP socket。
11. 初始化 LSM6DSO SPI 和 FIFO。
12. 配置 INT1 中断。
13. 创建 `LSM_TASK` 和 `CTRL_TASK`。

warmup 推理不是模型准确率测试，而是生产路径的一部分。它能在 WiFi 占用更多 SRAM 前完成 ESP-DL 内部缓冲区分配，降低首次真实推理内存失败风险。

### 4.2 FreeRTOS 任务

| 任务 | Core | 优先级 | 作用 |
| --- | ---: | ---: | --- |
| `LSM_TASK` | 1 | 10 | 读取 LSM6DSO FIFO、写 SD、模型推理、发送 UDP |
| `CTRL_TASK` | 0 | 9 | 监听 PC 端控制命令 |

### 4.3 采样与恢复机制

LSM6DSO 正常通过 FIFO watermark 中断唤醒 `LSM_TASK`。同时固件保留 40 ms 轮询超时，防止中断边沿丢失造成数据流停住。

恢复策略：

- 如果 FIFO 连续约 3 秒为空，执行 LSM6DSO 软件复位并重新配置 FIFO。
- 如果备用直读加速度值连续约 3 秒完全不变，也执行传感器恢复。
- 恢复后清空模型窗口和重采样器，避免旧窗口污染推理。

### 4.4 模型输入和推理

模型配置：

- 输入窗口: 120 个点
- 模型输入通道: 4 个，分别为 X、Y、Z、|a|
- 输出类别: 5 类
  - `Displacement`
  - `Grazing`
  - `Ruminating_Chewing`
  - `Other`
  - `Resting`
- 模型文件: `model/goat_cnn_5cls_s3_int8.espdl`
- 推理库: ESP-DL
- 推理模式: single core

传感器原始采样约 26 Hz。训练数据采样率约 24.4 Hz，因此固件只在模型输入路径做线性重采样：

```text
原始 LSM6DSO 数据: 26 Hz
模型输入数据:     24.4 Hz
UI 波形和 SD 原始加速度: 仍使用原始 26 Hz 数据
```

行为识别延迟：

- 首次结果需要 `120 / 24.4 = 4.92 s` 窗口。
- 后续滑动步长为 `60 / 24.4 = 2.46 s`。
- UI 指标里的 `23 ms` 是模型性能展示值，不代表端到端行为识别延迟。

### 4.5 UDP 通信协议

ESP32-S3 作为 SoftAP：

- SSID: `LXSPI-ESP32S3`
- Password: `12345678`
- Channel: 6
- 默认 AP IP: `192.168.4.1`
- PC 通常获取 IP: `192.168.4.2`

UDP 端口：

| 端口 | 方向 | 用途 |
| ---: | --- | --- |
| 5005 | ESP32 -> PC | 行为识别结果 |
| 5007 | ESP32 -> PC | 加速度波形 |
| 6006 | PC -> ESP32 | 控制命令 |

行为识别包示例：

```json
{
  "type": "infer",
  "ts": 1778584882766,
  "act": "Resting",
  "conf": 0.970,
  "scores": {
    "Displacement": 0.000,
    "Grazing": 0.000,
    "Ruminating_Chewing": 0.029,
    "Other": 0.000,
    "Resting": 0.970
  },
  "battery": {
    "voltage": 4050,
    "percentage": 82,
    "status": "Normal"
  },
  "sd": {
    "mounted": true,
    "recording": true
  }
}
```

加速度包示例：

```json
{
  "type": "accel",
  "ts": 1778584882766,
  "acc": [
    [-0.106, 0.695, -0.694],
    [-0.105, 0.694, -0.693]
  ],
  "n": 2,
  "stream": true,
  "sd": {
    "mounted": true,
    "recording": true
  }
}
```

暂停采集后仍会发送状态心跳包，避免 UI 误判设备离线：

```json
{
  "type": "accel",
  "ts": 1778584882766,
  "acc": [],
  "n": 0,
  "stream": false,
  "sd": {
    "mounted": true,
    "recording": false
  }
}
```

### 4.6 控制命令

PC 上位机通过 UDP 6006 发送纯文本命令。

| 命令 | 作用 |
| --- | --- |
| `SYNC:<unix_ms>` | 同步 PC 时间到 ESP32，单位毫秒 |
| `START` | 开启采集流，同时尝试启动 SD 记录 |
| `STOPREC` | 只停止 SD 记录，采集和波形继续 |
| `PAUSE` | 暂停采集流，同时停止 SD 记录 |
| `MOUNT` | 重新挂载 SD 卡 |
| `SDDIAG` | 打印 SD 引脚和 MISO 诊断信息 |
| `RESET` | 软重启 ESP32 |

当前已移除旧的 `TEST` 控制命令和嵌入式测试窗口，避免误触发测试推理。

### 4.7 SD 卡记录格式

日志目录：

```text
/sdcard/logs
```

文件名：

```text
YYYYMMDD_HHMMSS.csv
```

CSV 表头：

```csv
timestamp_ms,datetime,type,accel_x,accel_y,accel_z,behavior,confidence
```

加速度行：

```csv
1778584882766,2026-05-12 19:21:22,accel,-0.106000,0.695000,-0.694000,,
```

推理行：

```csv
1778584885310,2026-05-12 19:21:25,inference,,,,Resting,0.970000
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `timestamp_ms` | 同步后的 Unix 毫秒时间戳 |
| `datetime` | 本地时间字符串，格式 `YYYY-MM-DD HH:MM:SS` |
| `type` | `accel` 或 `inference` |
| `accel_x/y/z` | 三轴加速度，单位 g |
| `behavior` | 行为标签，仅推理行有值 |
| `confidence` | 置信度，仅推理行有值 |

SD 写入策略：

- 加速度行先进入 RAM 缓冲区。
- 每约 1 秒 flush + fsync。
- 推理行会先 flush 当前加速度缓冲，再立即写入并同步，减少推理结果丢失风险。

## 5. PC 上位机架构

上位机入口：

```text
SPI/main/visualize.py
```

依赖：

```text
PyQt5
pyqtgraph
numpy
```

安装依赖：

```powershell
pip install -r SPI\main\requirements-ui.txt
```

主要模块：

| 文件 | 作用 |
| --- | --- |
| `visualize.py` | 主窗口、UDP 监听、控制按钮、状态栏、图表联动 |
| `model_performance_widget.py` | 模型指标卡、实时波形、实时行为区域、操作记录 |
| `behavior_monitor.py` | 行为标签归一化、健康评分、风险告警 |
| `data_storage.py` | SQLite 历史数据存储 |
| `history_viewer.py` | 历史数据查看和导出 |
| `health_config.json` | 健康风险阈值配置 |

### 5.1 UI 功能区

主界面包括：

- 顶部当前行为、置信度、设备 IP、连接状态。
- 模型性能指标卡。
- 三轴加速度实时波形。
- 实时行为识别大图标和置信度环。
- 最近识别结果时间轴。
- 操作记录区域。
- 行为统计环形图。
- 统计信息、电池状态、SD 状态。
- 三大健康风险指标。
- 最近告警区域。
- 底部控制栏。

底部按钮：

| 按钮 | 命令 | 说明 |
| --- | --- | --- |
| 启动监听 | - | PC 开始监听 UDP |
| 停止监听 | - | PC 停止监听 UDP |
| 启动采集 | `SYNC` 后延迟发送 `START` | 自动先同步时间 |
| 暂停记录 | `STOPREC` | 只停止 SD 记录，波形继续 |
| 暂停采集 | `PAUSE` | 停止采集流，波形会停 |
| 时间同步 | `SYNC:<unix_ms>` | 单独同步 ESP32 时间 |
| 重置设备 | `RESET` | ESP32 软重启 |
| 重新挂载SD | `MOUNT` | 重新挂载 SD 卡 |
| 历史数据 | - | 打开历史查看器 |

### 5.2 UI 数据处理

PC 端用两个 UDP 监听线程接收数据：

- 行为识别包更新当前行为、置信度、行为统计、健康风险、电池状态和 SD 状态。
- 加速度包更新实时波形，并按每 10 个点保存一次 SQLite 加速度记录。

PC 本地 SQLite 默认位置：

```text
<repo>/livestock_data.db
```

SQLite 表：

| 表 | 内容 |
| --- | --- |
| `behavior_records` | 行为记录 |
| `accel_data` | 抽样保存的加速度数据 |
| `alerts` | 健康风险告警 |
| `daily_stats` | 每日统计 |

## 6. 系统数据流

```text
LSM6DSO
  |
  | SPI2, FIFO, INT1
  v
ESP32-S3 LSM_TASK
  |
  | 原始 26 Hz 加速度
  |---> SD CSV: accel rows
  |---> UDP 5007: realtime waveform
  |
  | 26 Hz -> 24.4 Hz resample
  | 120-point window, 60-point step
  v
ESP-DL CNN model
  |
  | behavior + confidence + scores
  |---> SD CSV: inference rows
  |---> UDP 5005: realtime behavior
  v
PC PyQt UI
  |
  | behavior monitor + SQLite
  v
Dashboard, health alerts, history viewer
```

控制流：

```text
PC UI buttons
  |
  | UDP 6006 commands
  v
ESP32-S3 CTRL_TASK
  |
  | START / STOPREC / PAUSE / MOUNT / RESET / SYNC
  v
采集、记录、时间和设备状态改变
```

## 7. 实时性和延迟

加速度波形：

- 采样率约 26 Hz。
- 每个点间隔约 38.5 ms。
- 正常 UI 波形延迟约 40 到 100 ms，取决于 FIFO 批量、WiFi 队列和 PC 调度。
- 波形控件保留最近 200 点，也就是约 7.7 秒历史。

行为识别：

- 模型窗口 120 点。
- 模型输入采样率 24.4 Hz。
- 首次推理需要约 4.92 秒数据。
- 后续每 60 点滑动一次，即约 2.46 秒更新一次。
- UI 性能卡中的 23 ms 是模型推理耗时展示值，不是端到端行为识别延迟。

SD 记录：

- 加速度行通常 1 秒左右 flush。
- 推理行会立即同步。
- 如果断电，最近不足 1 秒的加速度缓冲可能丢失，但推理行风险较低。

## 8. 构建和运行

ESP-IDF 环境准备后，在项目目录执行：

```powershell
cd SPI
idf.py build
idf.py -p COMx flash monitor
```

PC UI：

```powershell
python SPI\main\visualize.py
```

使用顺序：

1. ESP32-S3 上电。
2. PC 连接 WiFi 热点 `LXSPI-ESP32S3`。
3. 打开 UI，点击启动监听。
4. 等待设备 IP 显示。
5. 点击启动采集。UI 会先发送时间同步，再发送 START。
6. 若只想停止 SD 写入，点击暂停记录。
7. 若要停止采集和波形，点击暂停采集。
8. 若 SD 插拔或首次挂载失败，点击重新挂载SD。

## 9. 常见问题

### 9.1 SD 首次挂载失败

现象：

```text
SD MISO stays low with pull-up
ESP_ERR_INVALID_RESPONSE
ESP_ERR_TIMEOUT
```

优先检查：

- SD 模块 VCC 是否接到 5V。
- AMS1117 VOUT 是否约 3.3V。
- GND 是否共地。
- DO/MISO 是否接 GPIO5。
- 卡座是否接触良好。
- SD 卡是否 FAT32。

如果重新挂载成功，说明文件系统通常没问题，更多是上电瞬态、接触或线缆问题。

### 9.2 波形突然变直线

可能原因：

- 设备处于静止，真实加速度变化很小。
- 传感器 FIFO 或 INT1 状态异常。
- WiFi 队列阻塞导致上位机短时间收不到包。

固件已有恢复：

- 40 ms 轮询兜底。
- FIFO 约 3 秒为空自动恢复 LSM6DSO。
- 直读值约 3 秒完全不变自动恢复 LSM6DSO。

### 9.3 点击暂停采集后 UI 红灯或波形停止

这是预期行为。`PAUSE` 会停止采集流。若只想停止 SD 记录，应点击 `暂停记录`，它发送 `STOPREC`，波形仍继续。

### 9.4 CSV 时间不对

必须先同步时间。UI 的启动采集会自动发送 `SYNC:<unix_ms>`。固件设置了 `CST-8` 时区，新文件名和 CSV `datetime` 应显示北京时间。

## 10. 本次清理说明

已清理的旧测试/冗余内容：

- 删除旧 UI 测试脚本：
  - `SPI/main/test_ui_final.py`
  - `SPI/main/test_new_ui.py`
  - `SPI/main/test_health_alerts.py`
- 删除嵌入式测试窗口：
  - `SPI/main/model/test_windows.h`
- 删除固件 `TEST` 控制命令。
- 删除开机 `model->test()` 运行时自测。
- 删除模型推理过程中的 `INP`、`PRE-RUN`、`QOUT`、`OUT` 大量调试打印。
- 删除串口 `Pred:` / `Scores:` 每轮推理打印。

保留的维护功能：

- `SDDIAG` 命令和 SD 挂载诊断仍保留，因为它用于现场排查接线、MISO 电平和 SD 初始化问题。
- 模型 warmup 推理保留，因为它用于稳定生产运行时的内存分配，不属于测试按钮或旧测试路径。
