#include <stdio.h>
#include <string.h>
#include <math.h>
#include <sys/time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "lwip/sockets.h"
#include "lwip/sys.h"
#include "driver/i2s.h"      // I2S (Legacy)
#include "driver/gpio.h"     // <--- [핵심 수정] 이 헤더가 없어서 에러가 났었습니다!
#include "esp_sntp.h"        // <--- [수정] sntp.h 대신 esp_sntp.h 사용 (v5.x 표준)
#include "esp_netif.h"

// ================= [사용자 설정 구간] =================
#define MY_NODE_ID     "NODE_1"       // <--- 보드마다 여기를 바꿔주세요! (NODE_2, NODE_3...)
#define WIFI_SSID      "Heimdall_Net" // 라즈베리파이 AP 이름
#define WIFI_PASS      "password1234" // 라즈베리파이 AP 비밀번호
#define RPI_IP_ADDR    "192.168.50.1" // 라즈베리파이(ipTIME 동글)의 고정 IP
#define UDP_PORT       3333           // 통신 포트
#define TRIGGER_DB     60.0           // 소리 감지 임계값 (환경에 따라 50~80 조절 필요)

// 핀 설정 (ESP32-C3 SuperMini + INMP441)
#define I2S_BCK_IO     (GPIO_NUM_4)
#define I2S_WS_IO      (GPIO_NUM_5)
#define I2S_DO_IO      (GPIO_NUM_6)
// ======================================================

static const char *TAG = "HEIMDALL";
static int sock = -1;

// 1. 와이파이 이벤트 핸들러
static void wifi_event_handler(void* arg, esp_event_base_t event_base,
                               int32_t event_id, void* event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGI(TAG, "Wi-Fi 끊김, 재연결 시도...");
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "IP 획득: " IPSTR, IP2STR(&event->ip_info.ip));
    }
}

// 2. 와이파이 초기화
void wifi_init_sta(void)
{
    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);

    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL);

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
            .sae_pwe_h2e = WPA3_SAE_PWE_BOTH,
        },
    };
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    esp_wifi_start();
}

// 3. NTP 시간 동기화 (v5.x 최신 API 적용)
void initialize_sntp(void)
{
    ESP_LOGI(TAG, "시간 동기화(SNTP) 시작...");
    
    // [수정] esp_sntp_... 함수 사용 (이전 sntp_... 는 Deprecated 됨)
    esp_sntp_setoperatingmode(ESP_SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, RPI_IP_ADDR); 
    esp_sntp_init();
    
    // 시간이 잡힐 때까지 잠시 대기 (최대 10초)
    int retry = 0;
    while (esp_sntp_get_sync_status() == SNTP_SYNC_STATUS_RESET && ++retry < 10) {
        ESP_LOGI(TAG, "시간 동기화 대기 중... (%d/10)", retry);
        vTaskDelay(1000 / portTICK_PERIOD_MS);
    }
}

// 4. I2S 마이크 설정
void i2s_init(void) {
    // ESP-IDF v5.x에서도 레거시 드라이버 사용 가능 (경고는 무시 가능)
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
        .sample_rate = 16000, 
        .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 4,
        .dma_buf_len = 512,
        .use_apll = false
    };
    i2s_pin_config_t pin_config = {
        .bck_io_num = I2S_BCK_IO,
        .ws_io_num = I2S_WS_IO,
        .data_out_num = -1,
        .data_in_num = I2S_DO_IO
    };
    i2s_driver_install(I2S_NUM_0, &i2s_config, 0, NULL);
    i2s_set_pin(I2S_NUM_0, &pin_config);
}

void app_main(void)
{
    // [SETUP]
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
      ESP_ERROR_CHECK(nvs_flash_erase());
      ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init_sta();
    vTaskDelay(3000 / portTICK_PERIOD_MS); 
    
    initialize_sntp();
    
    // UDP 소켓
    sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    struct sockaddr_in dest_addr;
    dest_addr.sin_addr.s_addr = inet_addr(RPI_IP_ADDR);
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port = htons(UDP_PORT);

    i2s_init();

    int32_t *samples = malloc(512 * sizeof(int32_t));
    char tx_buffer[64];
    size_t bytes_read = 0;

    ESP_LOGI(TAG, "=== 감시 시작 (%s) ===", MY_NODE_ID);

    // [LOOP]
    while (1) {
        // 1. 마이크 읽기
        size_t bytes_read_val = 0;
        i2s_read(I2S_NUM_0, samples, 512 * sizeof(int32_t), &bytes_read_val, portMAX_DELAY);
        bytes_read = bytes_read_val;

        // 2. dB 계산
        double sum = 0;
        int samples_count = bytes_read / sizeof(int32_t);
        for (int i = 0; i < samples_count; i++) {
            double val = (double)(samples[i] >> 8); 
            sum += val * val;
        }
        
        if (samples_count > 0) {
            double rms = sqrt(sum / samples_count);
            double db = 20 * log10(rms); 

            // 3. 전송
            if (db > TRIGGER_DB) {
                struct timeval tv_now;
                gettimeofday(&tv_now, NULL);
                int64_t time_us = (int64_t)tv_now.tv_sec * 1000000L + (int64_t)tv_now.tv_usec;

                int len = snprintf(tx_buffer, sizeof(tx_buffer), "%s,%lld,%.2f", MY_NODE_ID, time_us, db);
                
                sendto(sock, tx_buffer, len, 0, (struct sockaddr *)&dest_addr, sizeof(dest_addr));
                
                ESP_LOGI(TAG, "💥 쾅! %.1fdB", db);
                vTaskDelay(100 / portTICK_PERIOD_MS); 
            }
        }
    }
    free(samples);
}