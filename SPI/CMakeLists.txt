# The following five lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.16)

# Workaround: GCC 14.2.0 内部编译器错误 (ICE) in esp_lcd/rgb/esp_lcd_panel_rgb.c。
# 项目不使用 LCD，直接从依赖中排除该组件。
set(EXCLUDE_COMPONENTS esp_lcd)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(SPI)
