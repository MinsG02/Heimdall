/*
 * ESP32-C3 / ESP-IDF v5.5.2 호환
 * 기능: TDOA 노드 (UDP 버전)
 * 특징: Trigger 후 2초간 쿨다운 (중복 전송 방지 & 버퍼 비우기)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <sys/time.h>
#include <time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_check.h"
#include "nvs_flash.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "lwip/sockets.h"
#include "lwip/sys.h"
#include "driver/gpio.h"
#include "esp_sntp.h"
#include "esp_netif.h"
#include "driver/i2s_std.h"
#include "esp_timer.h"

// ================= [★ 사용자 설정: 보드마다 바꿔주세요] =================
// 1번 보드면 NODE_1, 2번이면 NODE_2 ... NODE_5 까지 변경
#define MY_NODE_ID      "NODE_1"       
#define WIFI_SSID       "Parroy" 
#define WIFI_PASS       "88588858" 
#define RPI_IP_ADDR     "10.12.26.241" 
#define UDP_PORT        3333            

// 캘리브레이션 및 트리거 설정
#define CALIBRATION_SEC     5    
#define QUIET_ROOM_DB       25.0 
#define TRIGGER_MARGIN      5.0  
// =====================================================================

#define I2S_BCK_IO      (GPIO_NUM_2)
#define I2S_WS_IO       (GPIO_NUM_3)
#define I2S_DO_IO       (GPIO_NUM_4)

static const char *TAG = "HEIMDALL_UDP";
static i2s_chan_handle_t rx_handle = NULL;
volatile bool is_time_synced = false; 

double g_db_offset = 0.0;       
double g_trigger_threshold = 0.0; 

// --- 유틸리티 함수 ---
void get_time_string(char *buffer, size_t max_len) {
    time_t now;
    struct tm timeinfo;
    time(&now);
    localtime_r(&now, &timeinfo);
    strftime(buffer, max_len, "%Y-%m-%d %H:%M:%S", &timeinfo);
}

double get_legal_limit() {
    time_t now;
    struct tm timeinfo;
    time(&now);
    localtime_r(&now, &timeinfo);
    int hour = timeinfo.tm_hour;
    return (hour >= 6 && hour < 22) ? 39.0 : 32.0;
}

double calculate_raw_db(int32_t *samples, int count) {
    double sum = 0, dc_offset = 0;
    for (int i = 0; i < count; i++) dc_offset += (double)(samples[i] >> 14);
    dc_offset /= count;
    for (int i = 0; i < count; i++) {
        double val = (double)(samples[i] >> 14) - dc_offset;
        sum += val * val;
    }
    if (count > 0) {
        double rms = sqrt(sum / count);
        double db = (20 * log10(rms + 1e-9));
        return (db < 0) ? 0 : db;
    }
    return 0;
}

void visualize_status(double db, double limit, bool is_day) {
    int bars = (int)(db - 20); 
    if (bars < 0) bars = 0;
    if (bars > 60) bars = 60; 
    char bar_str[64];
    memset(bar_str, 0, sizeof(bar_str));
    for (int i = 0; i < bars; i++) bar_str[i] = '|';

    const char* mode = is_day ? "☀️ Day" : "🌙 Night";
    ESP_LOGW(TAG, "[%s][%s Limit:%.0f] BANG! %.1f dB | [%s]", 
             MY_NODE_ID, mode, limit, db, bar_str);
}

// --- Wi-Fi & SNTP ---
static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) esp_wifi_connect();
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) esp_wifi_connect();
    else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "Wi-Fi Connected: " IPSTR, IP2STR(&event->ip_info.ip));
    }
}

void wifi_init_sta(void) {
    esp_netif_init(); esp_event_loop_create_default(); esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT(); esp_wifi_init(&cfg);
    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL);
    wifi_config_t wifi_config = { .sta = { .ssid = WIFI_SSID, .password = WIFI_PASS, .threshold.authmode = WIFI_AUTH_WPA2_PSK }, };
    esp_wifi_set_mode(WIFI_MODE_STA); esp_wifi_set_config(WIFI_IF_STA, &wifi_config); esp_wifi_start();
}

void time_sync_notification_cb(struct timeval *tv) { is_time_synced = true; }
void initialize_sntp(void) {
    esp_sntp_setoperatingmode(ESP_SNTP_OPMODE_POLL); esp_sntp_setservername(0, RPI_IP_ADDR); 
    sntp_set_time_sync_notification_cb(time_sync_notification_cb); esp_sntp_init();
    setenv("TZ", "KST-9", 1); tzset();
}

void i2s_init_v5(void) {
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, NULL, &rx_handle));
    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED, .bclk = I2S_BCK_IO, .ws = I2S_WS_IO,
            .dout = I2S_GPIO_UNUSED, .din = I2S_DO_IO,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(rx_handle, &std_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(rx_handle));
}

void perform_calibration() {
    ESP_LOGI(TAG, "--- Auto-Zeroing [%s] (Target=%.0fdB) ---", MY_NODE_ID, QUIET_ROOM_DB);
    int32_t *samples = (int32_t *)calloc(512, sizeof(int32_t));
    size_t bytes_read = 0;
    double total_raw_db = 0;
    int count = 0;
    int64_t start_time = esp_timer_get_time();

    while ((esp_timer_get_time() - start_time) < (CALIBRATION_SEC * 1000000)) {
        if (i2s_channel_read(rx_handle, samples, 512 * sizeof(int32_t), &bytes_read, 1000) == ESP_OK) {
            double raw_db = calculate_raw_db(samples, bytes_read / sizeof(int32_t));
            if (raw_db > 10.0) { total_raw_db += raw_db; count++; }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    double measured_avg = (count > 0) ? (total_raw_db / count) : 75.0; 
    g_db_offset = QUIET_ROOM_DB - measured_avg;
    g_trigger_threshold = QUIET_ROOM_DB + TRIGGER_MARGIN;
    free(samples);
    ESP_LOGI(TAG, "Calibrated: Raw=%.2f, Offset=%.2f, Thresh=%.2f", measured_avg, g_db_offset, g_trigger_threshold);
}

void app_main(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase()); ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init_sta();
    vTaskDelay(pdMS_TO_TICKS(3000));
    initialize_sntp();
    
    ESP_LOGI(TAG, "[%s] Waiting for NTP sync...", MY_NODE_ID);
    while (!is_time_synced) vTaskDelay(pdMS_TO_TICKS(1000));
    
    char time_str[64];
    get_time_string(time_str, sizeof(time_str));
    ESP_LOGI(TAG, "Time Synced: %s", time_str);

    // ★ UDP 소켓 생성
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    struct sockaddr_in dest_addr;
    dest_addr.sin_addr.s_addr = inet_addr(RPI_IP_ADDR);
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port = htons(UDP_PORT);

    i2s_init_v5();
    perform_calibration();

    int32_t *samples = (int32_t *)calloc(512, sizeof(int32_t));
    char tx_buffer[128];
    size_t bytes_read = 0;

    ESP_LOGI(TAG, ">>> System Running (UDP). Waiting for BANG! (>%.1fdB) <<<", g_trigger_threshold);

    while (1) {
        if (i2s_channel_read(rx_handle, samples, 512 * sizeof(int32_t), &bytes_read, 1000) == ESP_OK) {
            double raw = calculate_raw_db(samples, bytes_read / sizeof(int32_t));
            double calibrated_db = raw + g_db_offset;
            if (calibrated_db < 0) calibrated_db = 0;

            // ★ 트리거 감지
            if (calibrated_db > g_trigger_threshold) {
                struct timeval tv_now;
                gettimeofday(&tv_now, NULL);
                int64_t time_us = (int64_t)tv_now.tv_sec * 1000000L + (int64_t)tv_now.tv_usec;
                double limit = get_legal_limit();
                bool is_day = (limit >= 39.0);

                int len = snprintf(tx_buffer, sizeof(tx_buffer), "%s,%lld,%.2f,%.1f", 
                                 MY_NODE_ID, time_us, calibrated_db, limit);
                
                // ★ UDP 전송 (sendto)
                sendto(sock, tx_buffer, len, 0, (struct sockaddr *)&dest_addr, sizeof(dest_addr));
                
                visualize_status(calibrated_db, limit, is_day);
                
                // ========================================================
                // ★ [핵심] 2초 쿨다운 & 버퍼 비우기 (중복 방지)
                // ========================================================
                ESP_LOGI(TAG, ">>> Cooldown: 10.0 sec (Ignoring echoes) <<<");
                vTaskDelay(pdMS_TO_TICKS(10000)); // 2초간 멈춤
                
                // I2S 버퍼 비우기
                size_t dump_bytes;
                i2s_channel_read(rx_handle, samples, 512 * sizeof(int32_t), &dump_bytes, 100);
                i2s_channel_read(rx_handle, samples, 512 * sizeof(int32_t), &dump_bytes, 100);
                i2s_channel_read(rx_handle, samples, 512 * sizeof(int32_t), &dump_bytes, 100);
                // ========================================================
            }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    free(samples);
}

ESP 코드