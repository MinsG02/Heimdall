/*
 * ESP32-C3 TDOA Node (Golden Filter Edition)
 * - 고정 IP / UDP 스트리밍
 * - ★ 황금 코드 오디오 필터 적용 (동굴 소리 제거, 노이즈 게이트)
 * - UDP 3333: dB 텍스트
 * - UDP 3334: PCM 바이너리 (깨끗한 음질)
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
#define MY_NODE_ID      "NODE_3"          // 1~5번 노드에 맞춰 변경
#define STATIC_IP_ADDR  "10.42.0.53"      // 노드별로 51, 52, 53, 54, 55 부여

// 네트워크 설정
#define WIFI_SSID       "Heimdall_JEW"
#define WIFI_PASS       "00000000"
#define GATEWAY_ADDR    "10.42.0.1"       // 라파 핫스팟 IP
#define NETMASK_ADDR    "255.255.255.0"
#define RPI_IP_ADDR     "10.42.0.1"       // 데이터 보낼 라파 목적지 IP
// ====================================================

// UDP 포트
#define UDP_PORT_DB     3333              // dB 텍스트
#define UDP_PORT_PCM    3334              // PCM 바이너리
// ===========================================================================

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

// ================= PCM 패킷 헤더 =================
#define PCM_MAGIC 0x4850434Du

typedef struct __attribute__((packed)) {
    uint32_t magic;       // PCM_MAGIC
    uint8_t  ver;         // 1
    uint8_t  node;        // 1~5
    uint16_t n;           // 샘플 개수 (512)
    uint32_t fs;          // 샘플레이트 (16000)
    int64_t  t0_us;       // 프레임 시작 시간
    uint32_t seq;         // 시퀀스 번호
} pcm_hdr_t;

// --- 유틸리티 함수 ---
static inline uint8_t node_id_to_u8(const char *id) {
    if (!id) return 0;
    const char *p = strchr(id, '_');
    if (!p || *(p + 1) == '\0') return 0;
    int v = atoi(p + 1);
    if (v < 0) {
    v = 0;
}
if (v > 255) {
    v = 255;
}
    return (uint8_t)v;
}

double get_legal_limit() {
    time_t now;
    struct tm timeinfo;
    time(&now);
    localtime_r(&now, &timeinfo);
    int hour = timeinfo.tm_hour;
    return (hour >= 6 && hour < 22) ? 39.0 : 32.0;
}

// [수정됨] 이미 필터링된 PCM 데이터로 dB 계산 (더 정확함)
double calculate_db_from_pcm(int16_t *pcm_samples, int count) {
    double sum = 0;
    for (int i = 0; i < count; i++) {
        double val = (double)pcm_samples[i];
        sum += val * val;
    }

    if (count > 0) {
        double rms = sqrt(sum / count);
        // +1e-9는 log(0) 방지
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

    wifi_init_sta_static();
    vTaskDelay(pdMS_TO_TICKS(3000));

    initialize_sntp();
    while (!is_time_synced) vTaskDelay(pdMS_TO_TICKS(1000));

    // 소켓 생성
    int sock_db = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    int sock_pcm = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);

    struct sockaddr_in dest_db = {0};
    dest_db.sin_addr.s_addr = inet_addr(RPI_IP_ADDR);
    dest_db.sin_family = AF_INET;
    dest_db.sin_port = htons(UDP_PORT_DB);

    struct sockaddr_in dest_pcm = {0};
    dest_pcm.sin_addr.s_addr = inet_addr(RPI_IP_ADDR);
    dest_pcm.sin_family = AF_INET;
    dest_pcm.sin_port = htons(UDP_PORT_PCM);

    i2s_init_v5();

    int32_t *raw_samples = (int32_t *)calloc(FRAME_SAMPLES, sizeof(int32_t));
    int16_t *pcm16 = (int16_t *)calloc(FRAME_SAMPLES, sizeof(int16_t));

    char tx_buffer[128];
    size_t bytes_read = 0;
    uint32_t seq = 0;
    const uint8_t node_u8 = node_id_to_u8(MY_NODE_ID);
    
    // PCM 패킷 버퍼
    uint8_t pcm_pkt[sizeof(pcm_hdr_t) + (FRAME_SAMPLES * sizeof(int16_t))];

    // ★ [황금 코드 이식 1] 필터용 정적 변수 (값을 기억해야 함)
    static float dc_offset = 0.0f;
    const float alpha = 0.05f; // 필터 강도

    ESP_LOGI(TAG, ">>> HEIMDALL GOLDEN NODE STARTED <<<");

    while (1) {
        if (i2s_channel_read(rx_handle, raw_samples, FRAME_SAMPLES * sizeof(int32_t), &bytes_read, 1000) == ESP_OK) {
            
            int n_samples = (int)(bytes_read / sizeof(int32_t));
            if (n_samples <= 0) { vTaskDelay(1); continue; }

            // ★ [황금 코드 이식 2] 오디오 프로세싱 파이프라인
            for (int i = 0; i < n_samples; i++) {
                // 1. 볼륨 조정 (>> 14)
                int32_t raw_val = raw_samples[i] >> 14; 

                // 2. 이동 평균 필터 (동굴 소리 제거)
                dc_offset = (dc_offset * (1.0f - alpha)) + (raw_val * alpha);
                
                // 3. DC 제거
                int32_t proc_val = raw_val - (int32_t)dc_offset;

                // 4. 노이즈 게이트 (화이트 노이즈 제거)
                if (proc_val > -5 && proc_val < 5) proc_val = 0;

                // 5. 클리핑 방지 및 저장
                if (proc_val > 32767) proc_val = 32767;
                if (proc_val < -32768) proc_val = -32768;

                pcm16[i] = (int16_t)proc_val;
            }

            // ==========================================
            //  이제 pcm16 에는 "깨끗한 오디오"가 들어있음
            // ==========================================

            // 1) dB 계산 (깨끗해진 PCM 데이터로 계산)
            double raw_db = calculate_db_from_pcm(pcm16, n_samples);

            // 2) 시간 정보
            struct timeval tv_now;
            gettimeofday(&tv_now, NULL);
            int64_t t_end_us = (int64_t)tv_now.tv_sec * 1000000LL + (int64_t)tv_now.tv_usec;
            int64_t frame_us = (int64_t)n_samples * 1000000LL / SAMPLE_RATE_HZ;
            int64_t t0_us = t_end_us - frame_us;

            double limit = get_legal_limit();

            // 3) dB 패킷 전송 (UDP 3333)
            int len = snprintf(tx_buffer, sizeof(tx_buffer), "%s,%lld,%.2f,%.1f",
                               MY_NODE_ID, (long long)t_end_us, raw_db, limit);
            sendto(sock_db, tx_buffer, len, 0, (struct sockaddr *)&dest_db, sizeof(dest_db));

            // 4) PCM 패킷 전송 (UDP 3334)
            pcm_hdr_t hdr = {
                .magic = PCM_MAGIC,
                .ver   = 1,
                .node  = node_u8,
                .n     = (uint16_t)n_samples,
                .fs    = SAMPLE_RATE_HZ,
                .t0_us = t0_us,
                .seq   = seq++,
            };

            memcpy(pcm_pkt, &hdr, sizeof(hdr));
            memcpy(pcm_pkt + sizeof(hdr), pcm16, (size_t)n_samples * sizeof(int16_t));

            sendto(sock_pcm, pcm_pkt, sizeof(hdr) + (size_t)n_samples * sizeof(int16_t), 
                   0, (struct sockaddr *)&dest_pcm, sizeof(dest_pcm));
        }
        vTaskDelay(pdMS_TO_TICKS(1)); 
    }
    
    free(raw_samples);
    free(pcm16);
}

//개선 코드와 PCM+dB+파형정보 시각화 버전
