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
#include "driver/i2s.h"
#include "driver/gpio.h"
#include "esp_sntp.h"
#include "esp_netif.h"

// ================= [사용자 설정 구간] =================
#define MY_NODE_ID     "NODE_L"       // ★ 중요: 왼쪽은 NODE_L, 오른쪽은 NODE_R 로 변경!
#define WIFI_SSID      "Heimdall_Net" 
#define WIFI_PASS      "password1234" 
#define RPI_IP_ADDR    "192.168.50.1" 
#define UDP_PORT       3333

// [TDOA 설정]
// 소리 크기(dB)가 아니라 '진동폭(Raw Value)'을 감지합니다.
// 숫자가 클수록 둔감해집니다. (기본값: 2000 ~ 5000 추천)
#define TRIGGER_THRESHOLD  3000   
#define DEBOUNCE_MS        200    // 한 번 감지 후 0.2초간 무시 (중복 전송 방지)
// ======================================================

// 핀 설정 (ESP32-C3 SuperMini / ESP32 + INMP441)
#define I2S_BCK_IO     (GPIO_NUM_2)
#define I2S_WS_IO      (GPIO_NUM_3)
#define I2S_DO_IO      (GPIO_NUM_4)

static const char *TAG = "HEIMDALL_TDOA";
static int sock = -1;

// 와이파이 이벤트 핸들러
static void wifi_event_handler(void* arg, esp_event_base_t event_base,
                               int32_t event_id, void* event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        // ESP_LOGI(TAG, "Wi-Fi 끊김, 재연결..."); // TDOA 성능을 위해 로그 최소화
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "IP 획득: " IPSTR, IP2STR(&event->ip_info.ip));
    }
}

// 와이파이 초기화
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

// NTP 시간 동기화 (TDOA의 핵심)
void initialize_sntp(void)
{
    ESP_LOGI(TAG, "시간 동기화(SNTP) 대기 중...");
    esp_sntp_setoperatingmode(ESP_SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, RPI_IP_ADDR); 
    esp_sntp_init();
    
    time_t now = 0;
    struct tm timeinfo = { 0 };
    int retry = 0;

    // 시간이 2025년 이후로 잡힐 때까지 무한 대기
    while (timeinfo.tm_year < (2025 - 1900)) {
        vTaskDelay(2000 / portTICK_PERIOD_MS);
        time(&now);
        localtime_r(&now, &timeinfo);
        if(++retry % 5 == 0) ESP_LOGI(TAG, "동기화 시도 중... (%d)", retry);
    }
    ESP_LOGI(TAG, "시간 동기화 완료! 현재 시간: %s", asctime(&timeinfo));
}

// I2S 마이크 설정
void i2s_init(void) {
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
        .sample_rate = 16000, 
        .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 4,   // 버퍼 개수 줄임 (Latency 감소)
        .dma_buf_len = 64,    // 버퍼 길이 줄임 (빠른 인터럽트)
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
    // NVS 초기화
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
      ESP_ERROR_CHECK(nvs_flash_erase());
      ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init_sta();
    
    // IP 받을 때까지 잠시 대기
    vTaskDelay(3000 / portTICK_PERIOD_MS); 
    
    // TDOA는 시간이 생명이므로 동기화 필수
    initialize_sntp();
    
    // UDP 소켓 생성
    sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    struct sockaddr_in dest_addr;
    dest_addr.sin_addr.s_addr = inet_addr(RPI_IP_ADDR);
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port = htons(UDP_PORT);

    i2s_init();

    // 데이터 읽기용 버퍼
    int32_t *samples = malloc(256 * sizeof(int32_t));
    char tx_buffer[64];
    size_t bytes_read = 0;
    int64_t last_trigger_time = 0;

    ESP_LOGI(TAG, "=== TDOA 센서 가동 (%s) ===", MY_NODE_ID);
    ESP_LOGI(TAG, "박수를 쳐서 임계값(%d)을 넘기세요.", TRIGGER_THRESHOLD);

    while (1) {
        // I2S 데이터 읽기 (여기서 블로킹됨)
        i2s_read(I2S_NUM_0, samples, 256 * sizeof(int32_t), &bytes_read, portMAX_DELAY);
        
        int samples_count = bytes_read / sizeof(int32_t);
        bool triggered = false;
        int32_t max_val = 0;

        // 버퍼를 훑으며 피크값 찾기
        for (int i = 0; i < samples_count; i++) {
            // INMP441 데이터 정규화 및 절대값 (DC Offset 무시하고 순간 변화량 감지)
            // >> 14는 노이즈를 줄이고 숫자를 다루기 쉽게 만듦
            int32_t val = abs(samples[i] >> 14);
            
            if (val > max_val) max_val = val;

            if (val > TRIGGER_THRESHOLD) {
                triggered = true;
                break; // 하나라도 넘으면 즉시 반응
            }
        }

        // 트리거 발생 시 시간 측정 및 전송
        if (triggered) {
            struct timeval tv_now;
            gettimeofday(&tv_now, NULL); 
            int64_t current_time = (int64_t)tv_now.tv_sec * 1000000L + (int64_t)tv_now.tv_usec;

            // 디바운스: 마지막 전송 후 일정 시간이 지났는지 확인
            if (current_time - last_trigger_time > (DEBOUNCE_MS * 1000)) {
                
                // 패킷 전송: "ID,마이크로초시간,피크값"
                int len = snprintf(tx_buffer, sizeof(tx_buffer), "%s,%lld,%d", MY_NODE_ID, current_time, max_val);
                sendto(sock, tx_buffer, len, 0, (struct sockaddr *)&dest_addr, sizeof(dest_addr));
                
                ESP_LOGI(TAG, "BANG! Time: %lld us | Peak: %d", current_time, max_val);
                last_trigger_time = current_time;
            }
        }
        
        // 너무 잦은 루프 방지 (I2S DMA가 있어서 딜레이 없어도 되지만 안전상 최소값)
        // vTaskDelay(1); // TDOA에서는 딜레이를 아예 없애거나 최소화해야 함
    }
    free(samples);
}