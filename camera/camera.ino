#include <WiFi.h>
#include <HTTPClient.h>
#include <time.h>
#include <esp_sleep.h>
#include "esp_camera.h"

#include "wifi-config.h"

// ESP32 Wrover
// Upload Speed: 460800
// Partition Scheme: Default 4MB with Spiffs
// ------- Wifi ----------------
// ssid, password and uploadUrl are stored in wifi-config.h (as const char*)

const long captureInterval = 60000; // 60 sec between captures
const int batchSize = 30;           // photos per batch (30 min)
const int hourStart = 6;
const int hourEnd = 22;
const unsigned long wifiConnectTimeoutMs = 15000;
const unsigned long ntpSyncTimeoutMs = 10000;

// Brightness detection – average below this threshold means "dark"
const uint8_t brightnessThreshold = 30;
const int darkSleepMorningSeconds = 3600; // 1 hour

// Battery voltage via voltage divider on GPIO 32
// Voltage divider: 100k + 100k -> ADC reads half of battery voltage
const int batteryPin = 32;
const float voltageDividerRatio = 2.0;

// PSRAM image buffer for batch uploads
struct ImageBuffer {
  uint8_t *data;
  size_t len;
  char timestamp[20]; // "2026-04-06_17-30-00"
};
ImageBuffer imageBuffer[batchSize];
int imageCount = 0;

bool wifiConnect() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi ");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < wifiConnectTimeoutMs) {
    Serial.print(".");
    delay(500);
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println(" connected");
    return true;
  }
  Serial.println(" failed");
  return false;
}

void wifiOff() {
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
}

// Deep sleep: full chip reset, RAM lost. Used for long pauses (night, darkness).
// Light sleep: RAM preserved, ~0.8 mA. Used between captures to keep buffered images.
void deepSleep(unsigned long seconds) {
  Serial.printf("Deep sleeping %lu seconds\n", seconds);
  Serial.flush();
  esp_deep_sleep(seconds * 1000000ULL);
}

void lightSleep(unsigned long ms) {
  Serial.printf("Light sleeping %lu ms\n", ms);
  Serial.flush();
  esp_sleep_enable_timer_wakeup(ms * 1000ULL);
  esp_light_sleep_start();
  // Discard first frame after wake-up — camera DMA needs one cycle to sync
  camera_fb_t *discard = esp_camera_fb_get();
  if (discard) esp_camera_fb_return(discard);
}

bool isNightTime(struct tm *t) {
  return t->tm_hour < hourStart || t->tm_hour >= hourEnd;
}

int secondsUntilStart(struct tm *t) {
  if (t->tm_hour >= hourEnd) {
    return (24 - t->tm_hour + hourStart) * 3600 - t->tm_min * 60 - t->tm_sec;
  }
  return (hourStart - t->tm_hour) * 3600 - t->tm_min * 60 - t->tm_sec;
}

void deepSleepUntilStart() {
  struct tm t;
  if (!getLocalTime(&t)) {
    deepSleep(3600);
    return;
  }
  deepSleep(secondsUntilStart(&t));
}

uint8_t averageBrightness(camera_fb_t *fb) {
  uint32_t sum = 0;
  for (size_t i = 0; i < fb->len; i++) {
    sum += fb->buf[i];
  }
  return sum / fb->len;
}

bool isDark(camera_fb_t *fb) {
  uint8_t brightness = averageBrightness(fb);
  Serial.printf("Brightness: %d (threshold: %d)\n", brightness, brightnessThreshold);
  return brightness < brightnessThreshold;
}

float readBatteryVoltage() {
  int raw = analogRead(batteryPin);
  float voltage = (raw / 4095.0) * 3.3 * voltageDividerRatio;
  return voltage;
}

void uploadImage(uint8_t *buf, size_t len, float batteryV, const char *timestamp) {
  HTTPClient http;
  http.setTimeout(10000);
  if (http.begin(uploadUrl)) {
    http.addHeader("Content-Type", "image/jpeg");
    http.addHeader("X-Battery-Voltage", String(batteryV, 2));
    if (timestamp) http.addHeader("X-Capture-Time", timestamp);
    int httpResponseCode = http.POST(buf, len);
    if (httpResponseCode > 0) {
      Serial.printf("Response: %d\n", httpResponseCode);
    } else {
      Serial.printf("Failed. HTTP error: %d (%s)\n", httpResponseCode, http.errorToString(httpResponseCode).c_str());
    }
    http.end();
  } else {
    Serial.println("Unable to connect to server");
  }
}

void freeBuffer() {
  for (int i = 0; i < imageCount; i++) {
    free(imageBuffer[i].data);
    imageBuffer[i].data = NULL;
  }
  imageCount = 0;
}

void uploadBuffered() {
  if (imageCount == 0) return;

  Serial.printf("Uploading %d buffered images...\n", imageCount);
  if (!wifiConnect()) {
    Serial.println("WiFi failed, keeping images in buffer");
    return;
  }

  float batteryV = readBatteryVoltage();
  Serial.printf("Battery: %.2fV\n", batteryV);
  for (int i = 0; i < imageCount; i++) {
    Serial.printf("  Uploading image %d/%d (%d bytes, %s)... ", i + 1, imageCount, imageBuffer[i].len, imageBuffer[i].timestamp);
    uploadImage(imageBuffer[i].data, imageBuffer[i].len, batteryV, imageBuffer[i].timestamp);
  }
  freeBuffer();
  wifiOff();
}

void handleDark() {
  Serial.println("Too dark, skipping capture.");
  uploadBuffered();
  struct tm t;
  if (getLocalTime(&t) && t.tm_hour < 12) {
    deepSleep(darkSleepMorningSeconds);
  } else {
    deepSleepUntilStart();
  }
}

bool captureToBuffer() {
  Serial.printf("Capturing image %d/%d... ", imageCount + 1, batchSize);
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("Camera capture failed");
    return false;
  }
  if (isDark(fb)) {
    esp_camera_fb_return(fb);
    handleDark();
    // handleDark always deep sleeps, never returns
  }
  imageBuffer[imageCount].data = (uint8_t *)ps_malloc(fb->len);
  if (!imageBuffer[imageCount].data) {
    Serial.println("PSRAM allocation failed, flushing buffer");
    esp_camera_fb_return(fb);
    uploadBuffered();
    return false;
  }
  memcpy(imageBuffer[imageCount].data, fb->buf, fb->len);
  imageBuffer[imageCount].len = fb->len;
  struct tm t;
  if (getLocalTime(&t)) {
    snprintf(imageBuffer[imageCount].timestamp, sizeof(imageBuffer[imageCount].timestamp),
             "%04d-%02d-%02d_%02d-%02d-%02d", t.tm_year + 1900, t.tm_mon + 1, t.tm_mday,
             t.tm_hour, t.tm_min, t.tm_sec);
  }
  imageCount++;
  Serial.printf("%d bytes stored\n", fb->len);
  esp_camera_fb_return(fb);
  return true;
}

void syncTime() {
  configTzTime("CET-1CEST,M3.5.0,M10.5.0/3", "pool.ntp.org");
  Serial.print("Syncing time");
  struct tm t;
  unsigned long ntpStart = millis();
  while (!getLocalTime(&t) && millis() - ntpStart < ntpSyncTimeoutMs) {
    Serial.print(".");
    delay(500);
  }
  if (!getLocalTime(&t)) {
    Serial.println(" NTP sync failed, retrying in 60s");
    wifiOff();
    deepSleep(60);
  }
  Serial.printf(" %02d:%02d:%02d\n", t.tm_hour, t.tm_min, t.tm_sec);

  if (isNightTime(&t)) {
    Serial.printf("Night time (%02d:%02d)\n", t.tm_hour, t.tm_min);
    wifiOff();
    deepSleepUntilStart();
  }
}

void initCamera() {
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
  config.frame_size = FRAMESIZE_XGA;
  config.jpeg_quality = 10;
  config.fb_count = 1;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x\n", err);
    wifiOff();
    deepSleep(60);
  }
}

void setup() {
  Serial.begin(115200);
  while (!Serial)
    ;
  Serial.println("Garden camera");

  initCamera();
  if (!wifiConnect()) {
    deepSleep(60);
  }
  syncTime();

  // First photo: buffer and upload immediately
  captureToBuffer();
  uploadBuffered();

  Serial.println("Setup done, WiFi off, entering capture loop");
}

void loop() {
  lightSleep(captureInterval);

  struct tm t;
  if (getLocalTime(&t) && isNightTime(&t)) {
    Serial.printf("Night time (%02d:%02d)\n", t.tm_hour, t.tm_min);
    uploadBuffered();
    deepSleepUntilStart();
  }

  captureToBuffer();

  if (imageCount >= batchSize - 1) {
    uploadBuffered();
  }
}
