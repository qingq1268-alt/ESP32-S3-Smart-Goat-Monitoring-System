idf_component_register(
    SRCS "main.c"
         "lsm6dso.c"
         "goat_behavior_model.cpp"
         "sd_logger.c"
         "battery_monitor.c"
         "wifi_manager.c"
    INCLUDE_DIRS "."
    REQUIRES driver esp_wifi nvs_flash esp_timer esp_adc esp_pm esp_system fatfs
    PRIV_REQUIRES vfs sdmmc
    EMBED_FILES "model/goat_cnn_5cls_s3_int8.espdl"
)
