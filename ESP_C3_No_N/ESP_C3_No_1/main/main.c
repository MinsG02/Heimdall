/*
 * ESP32-C3 TDOA Node (dB Only - Golden Filter Edition)
 * - 고정 IP / UDP 스트리밍
 * - ★ 황금 코드 오디오 필터 적용 (동굴 소리 제거, 노이즈 게이트)
 * - UDP 3333: dB 텍스트 전송 (PCM 바이너리 전송 제거됨)
 * - 라즈베리파이 연결 상태 상세 로그 추가
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

// ================= [★ 사용자 설정] =================
#define MY_NODE_ID      "NODE_5"          // 1~5번 노드에 맞춰 변경
#define STATIC_IP_ADDR  "10.42.0.55"      // 노드별로 51, 52, 53, 54, 55 부여

// 네트워크 설정
#define WIFI_SSID       "ice_gwc"
#define WIFI_PASS       "00000000"
#define GATEWAY_ADDR    "10.42.0.1"       // 라파 핫스팟 IP
#define NETMASK_ADDR    "255.255.255.0"
#define RPI_IP_ADDR     "10.42.0.1"       // 데이터 보낼 라파 목적지 IP
// ====================================================

// UDP 포트
#define UDP_PORT_DB     3333              // dB 텍스트

// ================= [Tenstar 핀 설정] =================
#define I2S_BCK_IO      (GPIO_NUM_6)
#define I2S_WS_IO       (GPIO_NUM_4)
#define I2S_DO_IO       (GPIO_NUM_5)
// =====================================================

#define SAMPLE_RATE_HZ  16000
#define FRAME_SAMPLES   512               // 512 samples -> 약 32ms

static const char *TAG = "HEIMDALL_GOLDEN";
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

double calculate_db_from_pcm(int16_t *pcm_samples, int count) {
    double sum = 0;
    for (int i = 0; i < count; i++) {
        double val = (double)pcm_samples[i];
        sum += val * val;
    }

    if (count > 0) {
        double rms = sqrt(sum / count);
        double db = (20.0 * log10(rms + 1e-9));
        return (db < 0) ? 0 : db;
    }
    return 0;
}

// --- Wi-Fi & SNTP 이벤트 핸들러 (로그 강화) ---
static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        ESP_LOGI(TAG, ">>> 라즈베리파이 AP(%s)에 연결을 시도합니다...", WIFI_SSID);
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, ">>> 라즈베리파이 AP 연결 끊김! 재연결을 시도합니다...");
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "===================================================");
        ESP_LOGI(TAG, "  [성공] 라즈베리파이에 성공적으로 연결되었습니다!");
        ESP_LOGI(TAG, "  - 노드 IP 주소: " IPSTR, IP2STR(&event->ip_info.ip));
        ESP_LOGI(TAG, "  - 라즈베리파이(GW) IP: %s", GATEWAY_ADDR);
        ESP_LOGI(TAG, "===================================================");
    }
}

void wifi_init_sta_static(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_t *my_netif = esp_netif_create_default_wifi_sta();
    ESP_ERROR_CHECK(esp_netif_dhcpc_stop(my_netif));

    esp_netif_ip_info_t ip_info;
    ip_info.ip.addr = inet_addr(STATIC_IP_ADDR);
    ip_info.gw.addr = inet_addr(GATEWAY_ADDR);
    ip_info.netmask.addr = inet_addr(NETMASK_ADDR);
    ESP_ERROR_CHECK(esp_netif_set_ip_info(my_netif, &ip_info));

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
            .sae_pwe_h2e = WPA3_SAE_PWE_BOTH,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
}

void time_sync_notification_cb(struct timeval *tv) { 
    (void)tv; 
    is_time_synced = true; 
    ESP_LOGI(TAG, "[성공] 라즈베리파이와 시간 동기화(SNTP) 완료!");
}

void initialize_sntp(void) {
    ESP_LOGI(TAG, "라즈베리파이(%s)와 시간 동기화를 시도합니다...", RPI_IP_ADDR);
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
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = I2S_BCK_IO,
            .ws   = I2S_WS_IO,
            .dout = I2S_GPIO_UNUSED,
            .din  = I2S_DO_IO,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

    ESP_ERROR_CHECK(i2s_channel_init_std_mode(rx_handle, &std_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(rx_handle));
}

void app_main(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    } else {
        ESP_ERROR_CHECK(ret);
    }

    // Wi-Fi 연결 시작
    wifi_init_sta_static();
    vTaskDelay(pdMS_TO_TICKS(3000));

    // 시간 동기화 시작
    initialize_sntp();
    while (!is_time_synced) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }

    // 소켓 생성
    int sock_db = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    struct sockaddr_in dest_db = {0};
    dest_db.sin_addr.s_addr = inet_addr(RPI_IP_ADDR);
    dest_db.sin_family = AF_INET;
    dest_db.sin_port = htons(UDP_PORT_DB);

    i2s_init_v5();

    int32_t *raw_samples = (int32_t *)calloc(FRAME_SAMPLES, sizeof(int32_t));
    int16_t *pcm16 = (int16_t *)calloc(FRAME_SAMPLES, sizeof(int16_t));

    char tx_buffer[128];
    size_t bytes_read = 0;

    static float dc_offset = 0.0f;
    const float alpha = 0.05f;

    ESP_LOGI(TAG, "===================================================");
    ESP_LOGI(TAG, ">>> HEIMDALL GOLDEN NODE STARTED (dB ONLY) <<<");
    ESP_LOGI(TAG, ">>> 라즈베리파이로 데이터 전송을 시작합니다! UDP: %d <<<", UDP_PORT_DB);
    ESP_LOGI(TAG, "===================================================");

    while (1) {
        if (i2s_channel_read(rx_handle, raw_samples, FRAME_SAMPLES * sizeof(int32_t), &bytes_read, 1000) == ESP_OK) {
            
            int n_samples = (int)(bytes_read / sizeof(int32_t));
            if (n_samples <= 0) { vTaskDelay(1); continue; }

            for (int i = 0; i < n_samples; i++) {
                int32_t raw_val = raw_samples[i] >> 14; 
                dc_offset = (dc_offset * (1.0f - alpha)) + (raw_val * alpha);
                int32_t proc_val = raw_val - (int32_t)dc_offset;

                if (proc_val > -5 && proc_val < 5) proc_val = 0;
                if (proc_val > 32767) proc_val = 32767;
                if (proc_val < -32768) proc_val = -32768;

                pcm16[i] = (int16_t)proc_val;
            }

            double raw_db = calculate_db_from_pcm(pcm16, n_samples);

            struct timeval tv_now;
            gettimeofday(&tv_now, NULL);
            int64_t t_end_us = (int64_t)tv_now.tv_sec * 1000000LL + (int64_t)tv_now.tv_usec;
            
            double limit = get_legal_limit();

            int len = snprintf(tx_buffer, sizeof(tx_buffer), "%s,%lld,%.2f,%.1f",
                               MY_NODE_ID, (long long)t_end_us, raw_db, limit);
            sendto(sock_db, tx_buffer, len, 0, (struct sockaddr *)&dest_db, sizeof(dest_db));
        }
        vTaskDelay(pdMS_TO_TICKS(1)); 
    }
    
    free(raw_samples);
    free(pcm16);
}