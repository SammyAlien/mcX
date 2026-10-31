#include <Arduino.h>
#include <U8g2lib.h>
#include <Wire.h>
#include <BleKeyboard.h>
#include "USB.h"
#include "USBHIDConsumerControl.h"
#include "esp_sleep.h"
#include "esp_wifi.h"
#include <WiFi.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

#define OLED_SDA_PIN 12
#define OLED_SCL_PIN 13
#define BUTTON_PIN 11

BleKeyboard bleKeyboard("mcX", "Sammy", 100);
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, /* reset=*/ U8X8_PIN_NONE);
USBHIDConsumerControl ConsumerControl;

// --- 定義專屬的 BLE UART 資料接收通道 ---
#define SERVICE_UUID           "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_RX "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"

int mediaStatus = 0; 
int posSec = 0;
int durSec = 0;
int volume = 0; 

unsigned long lastDataTime = 0;
const unsigned long TIMEOUT_MS = 10000; 
bool isDataConnected = false; 
bool isUsbMode = false;       

unsigned long lastActivityTime = 0;
const unsigned long IDLE_SLEEP_MS = 300000; 
unsigned long lastDebounceTime = 0;
const unsigned long debounceDelay = 50;
int lastButtonState = LOW;
int buttonState = LOW;

// --- 處理接收到的字串 (USB 與 藍牙 共用) ---
void processMediaData(String line) {
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
      isDataConnected = true;
      if (mediaStatus == 1) lastActivityTime = millis();
    }
  }
}

// --- 藍牙接收回呼函式 ---
class MyCallbacks: public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *pCharacteristic) {
    std::string rxValue = pCharacteristic->getValue();
    if (rxValue.length() > 0) {
      isUsbMode = false; // 收到藍牙訊號，切換為 BLE 顯示模式
      processMediaData(String(rxValue.c_str()));
    }
  }
};

void drawUI() {
  u8g2.firstPage();
  do {
    u8g2.setFontMode(1);    
    u8g2.setFont(u8g2_font_6x10_tf);
    
    u8g2.setCursor(0, 10);
    if (isDataConnected) {
      if (mediaStatus == 1) u8g2.print("PLAYING");
      else u8g2.print("PAUSED");
    } else {
      u8g2.print("WIRELESS");
    }

    u8g2.setCursor(78, 10);
    if (isDataConnected && isUsbMode) {
      u8g2.print("mcX USB");
    } else if (bleKeyboard.isConnected()) {
      u8g2.print("mcX BLE");
    } else {
      u8g2.print("PAIRING");
    }

    u8g2.drawHLine(0, 13, 128); 

    if (isDataConnected) {
      u8g2.drawFrame(0, 26, 128, 4);
      if (durSec > 0) {
        long progressWidth = ((long)posSec * 128L) / durSec;
        if (progressWidth > 128) progressWidth = 128;
        if (progressWidth < 0) progressWidth = 0;
        u8g2.drawBox(0, 26, (int)progressWidth, 4);
      }

      char timeBuffer[16];
      snprintf(timeBuffer, sizeof(timeBuffer), "%02d:%02d / %02d:%02d", posSec/60, posSec%60, durSec/60, durSec%60);
      int textWidth = u8g2.getStrWidth(timeBuffer);
      int centerX = (128 - textWidth) / 2;
      u8g2.setCursor(centerX, 42);
      u8g2.print(timeBuffer);

      int totalBars = 32; 
      int activeBars = (volume * totalBars) / 100; 
      int barWidth = 2; 
      int gap = 2;      
      int totalWidth = totalBars * barWidth + (totalBars - 1) * gap;
      int startX = (128 - totalWidth) / 2;
      
      for (int i = 0; i < totalBars; i++) {
        int x = startX + i * (barWidth + gap);
        if (i < activeBars) u8g2.drawBox(x, 52, barWidth, 8); 
        else u8g2.drawBox(x, 55, 1, 2);        
      }
    } else {
      u8g2.setFont(u8g2_font_8x13_tf); 
      const char* msg = bleKeyboard.isConnected() ? "Bluetooth Ready" : "Waiting to Pair...";
      int textWidth = u8g2.getStrWidth(msg);
      int centerX = (128 - textWidth) / 2;
      u8g2.setCursor(centerX, 40);
      u8g2.print(msg);
    }
  } while (u8g2.nextPage());
}

void setup() {
  WiFi.mode(WIFI_OFF);
  
  Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN);
  Wire.setClock(400000);
  pinMode(BUTTON_PIN, INPUT_PULLDOWN); 
  
  Serial.begin(115200);
  Serial.setTimeout(20);
  ConsumerControl.begin();
  USB.begin();
  
  // 1. 啟動藍牙鍵盤
  bleKeyboard.begin();
  
  // 2. 獨立建立 UART 服務與特徵值（完美避開 getServer() 找不到的問題）
  BLEServer *pServer = BLEDevice::createServer();
  BLEService *pService = pServer->createService(SERVICE_UUID);
  BLECharacteristic *pRxCharacteristic = pService->createCharacteristic(
                                        CHARACTERISTIC_UUID_RX,
                                        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
                                       );
  pRxCharacteristic->setCallbacks(new MyCallbacks());
  pService->start();
  
  // 3. 啟動廣播
  BLEAdvertising *pAdvertising = pServer->getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->start();
  
  u8g2.begin();
  u8g2.setPowerSave(0); 
  lastActivityTime = millis(); 
}

void loop() {
  unsigned long currentMillis = millis();

  int reading = digitalRead(BUTTON_PIN);
  if (reading != lastButtonState) lastDebounceTime = currentMillis;

  if ((currentMillis - lastDebounceTime) > debounceDelay) {
    if (reading != buttonState) {
      buttonState = reading;
      if (buttonState == HIGH) { 
        lastActivityTime = currentMillis; 
        if (Serial) { 
          ConsumerControl.press(CONSUMER_CONTROL_PLAY_PAUSE);
          ConsumerControl.release();
        } else if (bleKeyboard.isConnected()) {
          bleKeyboard.write(KEY_MEDIA_PLAY_PAUSE);
        }
      }
    }
  }
  lastButtonState = reading;

  // 接收 USB 傳來的畫面資料
  if (Serial.available() > 0) {
    isUsbMode = true; 
    processMediaData(Serial.readStringUntil('\n'));
  }

  bool currentConn = (currentMillis - lastDataTime <= TIMEOUT_MS);
  if (isDataConnected != currentConn) {
    isDataConnected = currentConn;
    if (!isDataConnected) {
      mediaStatus = 0; posSec = 0; durSec = 0; volume = 0;
    }
  }

  if (mediaStatus == 0 && (currentMillis - lastActivityTime > IDLE_SLEEP_MS)) {
    u8g2.setPowerSave(1); 
    esp_sleep_enable_ext0_wakeup(GPIO_NUM_11, 1);
    esp_deep_sleep_start(); 
  }

  drawUI();
  delay(30); 
}