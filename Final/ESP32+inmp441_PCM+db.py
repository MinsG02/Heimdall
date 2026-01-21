/*
 * ESP32-C3 TDOA Node (Streaming Mode)
 * - 고정 IP
 * - 트리거 없음(무조건 전송)
 * - UDP 3333: dB 텍스트 (기존 유지)
 * - UDP 3334: PCM 바이너리 (추가)
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
#define STATIC_IP_ADDR  "192.168.50.58"   // <--- .51, .52, ... .55 (네가 쓰는 대로)

// 네트워크 설정 (Heimdall_Net)
#define WIFI_SSID       "Heimdall_Net"
#define WIFI_PASS       "password1234"
#define GATEWAY_ADDR    "192.168.50.1"
#define NETMASK_ADDR    "255.255.255.0"
#define RPI_IP_ADDR     "192.168.50.1"

// UDP 포트
#define UDP_PORT_DB     3333              // dB 텍스트(기존)
#define UDP_PORT_PCM    3334              // PCM 바이너리(추가)
// ===========================================================================

#define I2S_BCK_IO      (GPIO_NUM_2)
#define I2S_WS_IO       (GPIO_NUM_3)
#define I2S_DO_IO       (GPIO_NUM_4)

#define SAMPLE_RATE_HZ  16000
#define FRAME_SAMPLES   512               // 512 samples -> 약 32ms @ 16kHz

static const char *TAG = "HEIMDALL_STREAM";
static i2s_chan_handle_t rx_handle = NULL;
volatile bool is_time_synced = false;

// ================= PCM 패킷 헤더 =================
// magic: 'HPCM' (0x4850434D)  ※ 리틀엔디안 환경에서 그대로 들어감
#define PCM_MAGIC 0x4850434Du

typedef struct __attribute__((packed)) {
    uint32_t magic;       // PCM_MAGIC
    uint8_t  ver;         // 1
    uint8_t  node;        // 1~5
    uint16_t n;           // 샘플 개수 (512)
    uint32_t fs;          // 샘플레이트 (16000)
    int64_t  t0_us;       // 프레임 "첫 샘플" 시간(근사) = t_end - frame_duration
    uint32_t seq;         // 증가하는 시퀀스
    // 뒤에 int16_t pcm[n]가 붙음
} pcm_hdr_t;

// --- 유틸리티 함수 ---
static inline uint8_t node_id_to_u8(const char *id)
{
    // "NODE_4" -> 4
    // 혹시 형식이 다르면 0 반환
    if (!id) return 0;
    const char *p = strchr(id, '_');
    if (!p || *(p + 1) == '\0') return 0;
    int v = atoi(p + 1);
    if (v < 0) v = 0;
    if (v > 255) v = 255;
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

double calculate_raw_db(int32_t *samples, int count) {
    // 너가 쓰던 방식 그대로 유지 (>>14)
    double sum = 0, dc_offset = 0;
    for (int i = 0; i < count; i++) dc_offset += (double)(samples[i] >> 14);
    dc_offset /= (count > 0 ? count : 1);

    for (int i = 0; i < count; i++) {
        double val = (double)(samples[i] >> 14) - dc_offset;
        sum += val * val;
    }

    if (count > 0) {
        double rms = sqrt(sum / count);
        double db = (20.0 * log10(rms + 1e-9));
        return (db < 0) ? 0 : db;
    }
    return 0;
}

static inline int16_t sample32_to_i16(int32_t s32)
{
    // INMP441 24-bit 유효 데이터를 16-bit로 다운컨버전
    // 일반적으로 >>8 정도를 많이 사용 (오버플로우 방지용 클램프 포함)
    int32_t s = (s32 >> 8);
    if (s > 32767) s = 32767;
    if (s < -32768) s = -32768;
    return (int16_t)s;
}

// --- Wi-Fi & SNTP ---
static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    }
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        esp_wifi_connect();
    }
    else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
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
            .sae_pwe_h2e = WPA3_SAE_PWE_BOTH, // WPA3 안 쓰면 빼도 됨
        },
    };

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));

    // 스트리밍 안정성/지연 줄이기(원하면 사용):
    // ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

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
    // NVS 안정 초기화 (추천)
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

    // dB 소켓(텍스트)
    int sock_db = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    // PCM 소켓(바이너리)
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

    int32_t *samples = (int32_t *)calloc(FRAME_SAMPLES, sizeof(int32_t));
    int16_t pcm16[FRAME_SAMPLES];

    char tx_buffer[128];
    size_t bytes_read = 0;

    uint32_t seq = 0;
    const uint8_t node_u8 = node_id_to_u8(MY_NODE_ID);

    // PCM 패킷 버퍼(헤더 + PCM)
    uint8_t pcm_pkt[sizeof(pcm_hdr_t) + (FRAME_SAMPLES * sizeof(int16_t))];

    ESP_LOGI(TAG, ">>> STREAMING MODE: Sending dB(3333) + PCM(3334) <<<");

    while (1) {
        if (i2s_channel_read(rx_handle,
                             samples,
                             FRAME_SAMPLES * sizeof(int32_t),
                             &bytes_read,
                             1000) == ESP_OK)
        {
            int n_samples = (int)(bytes_read / sizeof(int32_t));
            if (n_samples <= 0) {
                vTaskDelay(pdMS_TO_TICKS(1));
                continue;
            }

            // 1) dB 계산 (기존 유지)
            double raw_db = calculate_raw_db(samples, n_samples);

            // 2) 시간 (NTP 동기된 gettimeofday)
            struct timeval tv_now;
            gettimeofday(&tv_now, NULL);
            int64_t t_end_us = (int64_t)tv_now.tv_sec * 1000000LL + (int64_t)tv_now.tv_usec;

            // 3) 프레임 첫 샘플 시각 근사
            int64_t frame_us = (int64_t)n_samples * 1000000LL / SAMPLE_RATE_HZ;
            int64_t t0_us = t_end_us - frame_us;

            // 4) 법적 기준
            double limit = get_legal_limit();

            // =========================
            // (A) 기존 dB 텍스트 패킷 전송 (3333)
            // =========================
            int len = snprintf(tx_buffer, sizeof(tx_buffer), "%s,%lld,%.2f,%.1f",
                               MY_NODE_ID, (long long)t_end_us, raw_db, limit);
            sendto(sock_db, tx_buffer, len, 0, (struct sockaddr *)&dest_db, sizeof(dest_db));

            // =========================
            // (B) PCM 바이너리 패킷 전송 (3334)
            // =========================
            for (int i = 0; i < n_samples; i++) {
                pcm16[i] = sample32_to_i16(samples[i]);
            }

            pcm_hdr_t hdr = {
                .magic = PCM_MAGIC,
                .ver   = 1,
                .node  = node_u8,              // 1~5
                .n     = (uint16_t)n_samples,  // 512
                .fs    = SAMPLE_RATE_HZ,
                .t0_us = t0_us,
                .seq   = seq++,
            };

            memcpy(pcm_pkt, &hdr, sizeof(hdr));
            memcpy(pcm_pkt + sizeof(hdr), pcm16, (size_t)n_samples * sizeof(int16_t));

            sendto(sock_pcm,
                   pcm_pkt,
                   sizeof(hdr) + (size_t)n_samples * sizeof(int16_t),
                   0,
                   (struct sockaddr *)&dest_pcm,
                   sizeof(dest_pcm));
        }

        vTaskDelay(pdMS_TO_TICKS(1)); // 워치독/스케줄링
    }

    // (여긴 도달 안 함)
    free(samples);
}
