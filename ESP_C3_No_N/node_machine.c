/*
 * ESP32-C3 TDOA Node (dB Only - Lite Version)
 * - 고정 IP / UDP 스트리밍 (dB 데이터만 전송) / SNTP
 * - PCM 오디오 전송 제거 -> 네트워크 부하 최소화 & 핑 최적화
 * - L/R 채널 합산 & 오디오 필터는 dB 정확도를 위해 유지
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
#define MY_NODE_ID      "NODE_4"          
#define STATIC_IP_ADDR  "192.168.199.54"  
#define NETMASK_ADDR    "255.255.255.0"
#define GATEWAY_ADDR    "192.168.199.1"   

#define WIFI_SSID       "U+zone_2.4Ghz"       
#define WIFI_PASS       "kp2011722@"       

#define RPI_IP_ADDR     "192.168.199.37" 

// UDP 포트 (dB 전송용만 남김)
#define UDP_PORT_DB     3333              
// =================================================

// ================= [Tenstar 핀 설정] =================
#define I2S_BCK_IO      (GPIO_NUM_6)
#define I2S_WS_IO       (GPIO_NUM_4)
#define I2S_DO_IO       (GPIO_NUM_5)
// =====================================================

#define SAMPLE_RATE_HZ  16000
#define FRAME_SAMPLES   512               

static const char *TAG = "HEIMDALL_LITE";
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

// --- Wi-Fi & SNTP ---
static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "Connected! IP: " IPSTR, IP2STR(&event->ip_info.ip));
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
    
    // ★ 핑 안 튀게 하는 핵심 설정
    esp_wifi_set_ps(WIFI_PS_NONE);
}

void time_sync_notification_cb(struct timeval *tv) { (void)tv; is_time_synced = true; }

void initialize_sntp(void) {
    esp_sntp_setoperatingmode(ESP_SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, RPI_IP_ADDR);
    sntp_set_time_sync_notification_cb(time_sync_notification_cb);
    esp_sntp_init();
    setenv("TZ", "KST-9", 1);
    tzset();
}

// [I2S 초기화] STEREO 모드로 설정하여 L/R 데이터를 모두 수신할 준비
void i2s_init_universal(void) {
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, NULL, &rx_handle));

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = I2S_BCK_IO,
            .ws   = I2S_WS_IO,
            .dout = I2S_GPIO_UNUSED,
            .din  = I2S_DO_IO,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;

    ESP_ERROR_CHECK(i2s_channel_init_std_mode(rx_handle, &std_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(rx_handle));
}

void app_main(void) {
    // 1. NVS 초기화
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    } else {
        ESP_ERROR_CHECK(ret);
    }

    // 2. Wi-Fi 및 SNTP 연결
    wifi_init_sta_static();
    vTaskDelay(pdMS_TO_TICKS(3000));
    initialize_sntp();

    // 3. 소켓 생성 (dB 전송용 1개만 생성)
    int sock_db = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    struct sockaddr_in dest_db = {0};
    dest_db.sin_addr.s_addr = inet_addr(RPI_IP_ADDR);
    dest_db.sin_family = AF_INET;
    dest_db.sin_port = htons(UDP_PORT_DB);

    // 4. I2S 초기화 (Stereo)
    i2s_init_universal();

    // 5. 버퍼 할당
    // PCM 전송은 안 해도 dB 계산을 위해 임시 저장소(pcm16)는 필요함
    int32_t *raw_samples = (int32_t *)calloc(FRAME_SAMPLES * 2, sizeof(int32_t));
    int16_t *pcm16 = (int16_t *)calloc(FRAME_SAMPLES, sizeof(int16_t));
    
    char tx_buffer[128];
    size_t bytes_read = 0;

    // 필터 상태 변수
    static float dc_offset = 0.0f;
    const float alpha = 0.05f; 

    ESP_LOGI(TAG, ">>> HEIMDALL LITE (dB ONLY) STARTED <<<");

    while (1) {
        // [읽기] 스테레오 데이터(L+R)를 모두 읽어옴
        if (i2s_channel_read(rx_handle, raw_samples, FRAME_SAMPLES * 2 * sizeof(int32_t), &bytes_read, 1000) == ESP_OK) {
            
            int total_samples = (int)(bytes_read / sizeof(int32_t));
            if (total_samples <= 0) { vTaskDelay(1); continue; }

            int pcm_idx = 0;

            // [L/R 합산 & 필터링]
            // 소리를 보내진 않지만, 정확한 dB 계산을 위해 필터링 과정은 필수입니다.
            for (int i = 0; i < total_samples; i += 2) {
                if(pcm_idx >= FRAME_SAMPLES) break;

                // L + R 합산 (배선 이슈 해결)
                int32_t combined_val = (raw_samples[i] >> 14) + (raw_samples[i+1] >> 14);

                // 1. LPF (DC Offset 제거)
                dc_offset = (dc_offset * (1.0f - alpha)) + (combined_val * alpha);
                int32_t proc_val = combined_val - (int32_t)dc_offset;

                // 2. 노이즈 게이트
                if (proc_val > -5 && proc_val < 5) proc_val = 0;

                // 3. 하드 리미터
                if (proc_val > 32767) proc_val = 32767;
                if (proc_val < -32768) proc_val = -32768;

                pcm16[pcm_idx++] = (int16_t)proc_val;
            }

            int valid_count = pcm_idx;
            if (valid_count == 0) continue;

            // --- 데이터 전송 (dB Only) ---
            
            // dB 계산
            double raw_db = calculate_db_from_pcm(pcm16, valid_count);

            // 타임스탬프 계산
            struct timeval tv_now;
            gettimeofday(&tv_now, NULL);
            int64_t t_end_us = (int64_t)tv_now.tv_sec * 1000000LL + (int64_t)tv_now.tv_usec;
            
            double limit = get_legal_limit();

            // UDP 전송 (가벼운 텍스트 패킷)
            int len = snprintf(tx_buffer, sizeof(tx_buffer), "%s,%lld,%.2f,%.1f",
                               MY_NODE_ID, (long long)t_end_us, raw_db, limit);
                               
            sendto(sock_db, tx_buffer, len, 0, (struct sockaddr *)&dest_db, sizeof(dest_db));

            // PCM 전송 로직은 모두 제거됨
        }
        // 과도한 CPU 점유 방지
        vTaskDelay(pdMS_TO_TICKS(1)); 
    }
    
    free(raw_samples);
    free(pcm16);
}
