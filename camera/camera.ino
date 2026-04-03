#include <WiFi.h>
#include <HTTPClient.h>
#include "esp_camera.h"

#include "wifi-config.h"

// ESP32 Wrover
// Upload Speed: 460800
// Partition Scheme: Default 4MB with Spiffs
// ------- Wifi ----------------
// ssid, password and uploadUrl are stored in wifi-config.h (as const char*)

const long interval = 60000; // 60 sec

void setup() {
  Serial.begin(115200);
  while (!Serial)
    ;
  Serial.println("Garden camera");

  // Init Wifi + and get time
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi ");
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print(".");
    delay(500);
  }
  Serial.println(" connected");
  delay(1000);

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = 4;
  config.pin_d1 = 5;
  config.pin_d2 = 18;
  config.pin_d3 = 19;
  config.pin_d4 = 36;
  config.pin_d5 = 39;
  config.pin_d6 = 34;
  config.pin_d7 = 35;
  config.pin_xclk = 21;
  config.pin_pclk = 22;
  config.pin_vsync = 25;
  config.pin_href = 23;
  config.pin_sscb_sda = 26;
  config.pin_sscb_scl = 27;
  config.pin_pwdn = -1;
  config.pin_reset = -1;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG; 
  config.frame_size = FRAMESIZE_UXGA;
  config.jpeg_quality = 10;
  config.fb_count = 2;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x", err);
    return;
  }

  Serial.println("Set-up done");
}

void loop() {
  static unsigned long lastRun = -interval;
  unsigned long now = millis();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi lost, reconnecting...");
    WiFi.reconnect();
    while (WiFi.status() != WL_CONNECTED) {
      Serial.print(".");
      delay(500);
    }
    Serial.println(" reconnected");
  }

  if (now - lastRun >= interval) {
    lastRun = now;
    takePhoto();
  }
}

void takePhoto() {
  Serial.println("Taking photo...");
  camera_fb_t * fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("Camera capture failed");
    return;
  }

  HTTPClient http;
  Serial.print("Connecting to server...");
  if (http.begin(uploadUrl)) {
    http.addHeader("Content-Type", "image/jpeg");
    int httpResponseCode = http.POST(fb->buf, fb->len);

    if (httpResponseCode > 0) {
      Serial.printf(" Done! Response: %d\n", httpResponseCode);
    } else {
      Serial.printf(" Failed. Error: %s\n", http.errorToString(httpResponseCode).c_str());
    }
    http.end();
  } else {
    Serial.println(" Unable to connect to server");
  }
  esp_camera_fb_return(fb);
}