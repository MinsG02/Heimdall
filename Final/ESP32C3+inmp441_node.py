/*
 * ESP32-C3 TDOA Node (Streaming Mode)
 * 설정: 고정 IP, 트리거 없음(무조건 전송)
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
#include "lwip/inet.h"
#include "driver/gpio.h"
#include "esp_sntp.h"
#include "esp_netif.h"
#include "driver/i2s_std.h"

// ================= [★ 사용자 설정: 보드마다 여기만 변경하세요] =================
#define MY_NODE_ID      "NODE_4"          // <--- NODE_1, NODE_2, ... NODE_5
#define STATIC_IP_ADDR  "192.168.50.58"   // <--- .51, .52, ... .55

// 네트워크 설정 (Heimdall_Net)
#define WIFI_SSID       "Heimdall_Net"    
#define WIFI_PASS       "password1234"    
#define GATEWAY_ADDR    "192.168.50.1"    
#define NETMASK_ADDR    "255.255.255.0"
#define RPI_IP_ADDR     "192.168.50.1"    
#define UDP_PORT        3333             
// ===========================================================================

#define I2S_BCK_IO      (GPIO_NUM_2)
#define I2S_WS_IO       (GPIO_NUM_3)
#define I2S_DO_IO       (GPIO_NUM_4)

static const char *TAG = "HEIMDALL_STREAM";
static i2s_chan_handle_t rx_handle = NULL;
volatile bool is_time_synced = false; 

// --- 유틸리티 함수 ---
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

// --- Wi-Fi & SNTP ---
static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) esp_wifi_connect();
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        esp_wifi_connect();
    }
    else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "Connected! IP: " IPSTR, IP2STR(&event->ip_info.ip));
    }
}

void wifi_init_sta_static(void) {
    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_t *my_netif = esp_netif_create_default_wifi_sta();

    ESP_ERROR_CHECK(esp_netif_dhcpc_stop(my_netif));

    esp_netif_ip_info_t ip_info;
    ip_info.ip.addr = inet_addr(STATIC_IP_ADDR);
    ip_info.gw.addr = inet_addr(GATEWAY_ADDR);
    ip_info.netmask.addr = inet_addr(NETMASK_ADDR);

    ESP_ERROR_CHECK(esp_netif_set_ip_info(my_netif, &ip_info));

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);

    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL);

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID, .password = WIFI_PASS,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
            .sae_pwe_h2e = WPA3_SAE_PWE_BOTH,
        },
    };
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    esp_wifi_start();
}

void time_sync_notification_cb(struct timeval *tv) { is_time_synced = true; }

void initialize_sntp(void) {
    esp_sntp_setoperatingmode(ESP_SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, RPI_IP_ADDR);
    sntp_set_time_sync_notification_cb(time_sync_notification_cb);
    esp_sntp_init();
    setenv("TZ", "KST-9", 1);
    tzset();
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

void app_main(void) {
    nvs_flash_init();
    wifi_init_sta_static();
    vTaskDelay(pdMS_TO_TICKS(3000));

    initialize_sntp();
    while (!is_time_synced) vTaskDelay(pdMS_TO_TICKS(1000));

    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    struct sockaddr_in dest_addr;
    dest_addr.sin_addr.s_addr = inet_addr(RPI_IP_ADDR);
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port = htons(UDP_PORT);

    i2s_init_v5();

    int32_t *samples = (int32_t *)calloc(512, sizeof(int32_t));
    char tx_buffer[128];
    size_t bytes_read = 0;

    ESP_LOGI(TAG, ">>> STREAMING MODE: Sending ALL Audio Data <<<");

    while (1) {
        // 1. 읽기 (여기서 512샘플 찰 때까지 약 32ms 대기함)
        if (i2s_channel_read(rx_handle, samples, 512 * sizeof(int32_t), &bytes_read, 1000) == ESP_OK) {
            
            // 2. dB 계산
            double raw_db = calculate_raw_db(samples, bytes_read / sizeof(int32_t));
            
            // 3. 시간 및 환경 데이터 준비
            struct timeval tv_now;
            gettimeofday(&tv_now, NULL);
            int64_t time_us = (int64_t)tv_now.tv_sec * 1000000L + (int64_t)tv_now.tv_usec;
            double limit = get_legal_limit();

            // 4. 패킷 생성
            int len = snprintf(tx_buffer, sizeof(tx_buffer), "%s,%lld,%.2f,%.1f", 
                                MY_NODE_ID, time_us, raw_db, limit);
            
            // 5. [중요] 조건문 없이 무조건 전송
            sendto(sock, tx_buffer, len, 0, (struct sockaddr *)&dest_addr, sizeof(dest_addr));
            
            // (옵션) 디버깅용 로그 (너무 빠르면 주석 처리하세요)
            // ESP_LOGI(TAG, "Sending: %.2fdB", raw_db);
        }
        // 워치독 방지용 최소 딜레이
        vTaskDelay(pdMS_TO_TICKS(1)); 
    }
    free(samples);
}