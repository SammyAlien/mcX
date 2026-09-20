#include <Arduino.h>
#include <U8g2lib.h>
#include <Wire.h>
#include <BleKeyboard.h>

#define OLED_SDA_PIN 12
#define OLED_SCL_PIN 13

BleKeyboard bleKeyboard("mcX", "Sammy", 100);
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, /* reset=*/ U8X8_PIN_NONE);

int mediaStatus = 0; 
int posSec = 0;
int durSec = 0;
int volume = 0; 

unsigned long lastDataTime = 0;
const unsigned long TIMEOUT_MS = 10000; 
bool isSerialConnected = false;

void drawUI() {
  u8g2.firstPage();
  do {
    u8g2.setFontMode(1);    
    u8g2.setFont(u8g2_font_6x10_tf);
    
    // 1. 頂部狀態列
    u8g2.setCursor(0, 10);
    if (mediaStatus == 1 && isSerialConnected) {
      u8g2.print("PLAYING");
    } else {
      u8g2.print("PAUSED");
    }

    u8g2.setCursor(78, 10);
    if (isSerialConnected) {
      u8g2.print("mcX USB");
    } else {
      u8g2.print("SEARCH");
    }

    u8g2.drawHLine(0, 13, 128); 

    // 2. PSP 風格圖形化音量條 (固定座標 Y = 28)
    if (isSerialConnected) {
      int totalBars = 32; 
      int activeBars = (volume * totalBars) / 100; 
      int barWidth = 2; 
      int gap = 2;      
      int totalWidth = totalBars * barWidth + (totalBars - 1) * gap; 
      int startX = (128 - totalWidth) / 2; 
      
      for (int i = 0; i < totalBars; i++) {
        int x = startX + i * (barWidth + gap);
        if (i < activeBars) {
          u8g2.drawBox(x, 27, barWidth, 8);
        } else {
          u8g2.drawBox(x, 30, 1, 2); 
        }
      }
    }

    // 3. 歌曲進度條
    u8g2.drawFrame(0, 48, 128, 4);
    if (durSec > 0 && isSerialConnected) {
      long progressWidth = ((long)posSec * 128L) / durSec;
      if (progressWidth > 128) progressWidth = 128;
      if (progressWidth < 0) progressWidth = 0;
      u8g2.drawBox(0, 48, (int)progressWidth, 4);
    }

    // 4. 時間軸
    char timeBuffer[16];
    snprintf(timeBuffer, sizeof(timeBuffer), "%02d:%02d / %02d:%02d", posSec/60, posSec%60, durSec/60, durSec%60);
    int textWidth = u8g2.getStrWidth(timeBuffer);
    int centerX = (128 - textWidth) / 2;
    u8g2.setCursor(centerX, 62);
    u8g2.print(timeBuffer);

  } while (u8g2.nextPage());
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(20);
  Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN);
  Wire.setClock(400000);
  bleKeyboard.begin();
  u8g2.begin();
}

void loop() {
  // 輕量化字串解析協議
  if (Serial.available() > 0) {
    String line = Serial.readStringUntil('\n');
    line.trim();

    if (line.startsWith("V")) {
      int sIdx = line.indexOf('S');
      int pIdx = line.indexOf('P');
      int dIdx = line.indexOf('D');

      if (sIdx != -1 && pIdx != -1 && dIdx != -1) {
        volume = line.substring(1, sIdx).toInt();
        mediaStatus = line.substring(sIdx + 1, pIdx).toInt();
        posSec = line.substring(pIdx + 1, dIdx).toInt();
        durSec = line.substring(dIdx + 1).toInt();

        lastDataTime = millis();
        isSerialConnected = true;
      }
    }
  }

  // 10秒沒收到封包則判定為斷線
  bool currentConn = (millis() - lastDataTime <= TIMEOUT_MS);
  if (isSerialConnected != currentConn) {
    isSerialConnected = currentConn;
    if (!isSerialConnected) {
      mediaStatus = 0;
      posSec = 0;
      durSec = 0;
      volume = 0;
    }
  }

  drawUI();
  delay(40);
}