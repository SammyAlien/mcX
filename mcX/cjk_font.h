#ifndef CJK_FONT_H
#define CJK_FONT_H

#include <Arduino.h>
#include <U8g2lib.h>

// 定義高相容性全繁簡 CJK / 日文點陣字庫 (使用 WQY12 全覆蓋編碼表)
// 包含繁體中文常用字（含「亞細亞」、「國」、「臺」等）、簡體中文與日文假名
extern const uint8_t u8g2_font_wqy12_t_gb2312a[] U8G2_FONT_SECTION("u8g2_font_wqy12_t_gb2312a");

#endif