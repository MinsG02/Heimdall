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
#define MY_NODE_ID     "NODE_1"       // 보드 ID
#define WIFI_SSID      "Parroy" // 핫스팟 이름
#define WIFI_PASS      "88588858" // 비밀번호
#define RPI_IP_ADDR    "10.12.26.241" // 라즈베리파이 IP
#define UDP_PORT       3333
#define TRIGGER_DB     0.0           // 감지 임계값 (로그 보면서 조절 필요)
// ======================================================

// 핀 설정 (ESP32-C3 SuperMini + INMP441)
#define I2S_BCK_IO     (GPIO_NUM_4)
#define I2S_WS_IO      (GPIO_NUM_5)
#define I2S_DO_IO      (GPIO_NUM_6)

static const char *TAG = "HEIMDALL";
static int sock = -1;

// 와이파이 이벤트 핸들러
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

// NTP 시간 동기화 (무한 대기 안전장치 포함)
void initialize_sntp(void)
{
    ESP_LOGI(TAG, "시간 동기화(SNTP) 시작...");
    esp_sntp_setoperatingmode(ESP_SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, RPI_IP_ADDR); 
    esp_sntp_init();
    
    // [핵심] 시간이 제대로 잡힐 때까지 여기서 대기
    time_t now = 0;
    struct tm timeinfo = { 0 };
    int retry = 0;

    while (timeinfo.tm_year < (2025 - 1900)) { // 2025년 이전이면 시간 안 맞음
        ESP_LOGI(TAG, "시간 동기화 대기 중... (%d)", ++retry);
        vTaskDelay(2000 / portTICK_PERIOD_MS);
        time(&now);
        localtime_r(&now, &timeinfo);
    }
    ESP_LOGI(TAG, "동기화 완료! 현재 시간: %s", asctime(&timeinfo));
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
    // NVS 초기화
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
      ESP_ERROR_CHECK(nvs_flash_erase());
      ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init_sta();
    vTaskDelay(3000 / portTICK_PERIOD_MS); 
    
    // 시간 동기화
    initialize_sntp();
    
    // UDP 소켓 생성
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

    while (1) {
        // 1. 마이크 데이터 읽기
        size_t bytes_read_val = 0;
        i2s_read(I2S_NUM_0, samples, 512 * sizeof(int32_t), &bytes_read_val, portMAX_DELAY);
        bytes_read = bytes_read_val;

        // 2. dB 계산 (DC 오프셋 제거 포함)
        double sum = 0;
        double dc_offset = 0;
        int samples_count = bytes_read / sizeof(int32_t);
        
        // 평균값(DC Bias) 구하기
        for (int i = 0; i < samples_count; i++) {
             dc_offset += (double)(samples[i] >> 8);
        }
        dc_offset /= samples_count;

        // 편차 제곱의 합 (분산)
        for (int i = 0; i < samples_count; i++) {
            double val = (double)(samples[i] >> 8) - dc_offset; // 평균 빼기
            sum += val * val;
        }
        
        if (samples_count > 0) {
            double rms = sqrt(sum / samples_count);
            double db = 20 * log10(rms); 

            // 3. 임계값 넘으면 전송
            if (db > TRIGGER_DB) {
                struct timeval tv_now;
                gettimeofday(&tv_now, NULL); // 동기화된 시간 가져오기
                int64_t time_us = (int64_t)tv_now.tv_sec * 1000000L + (int64_t)tv_now.tv_usec;

                int len = snprintf(tx_buffer, sizeof(tx_buffer), "%s,%lld,%.2f", MY_NODE_ID, time_us, db);
                
                // 전송 및 에러 체크
                int err = sendto(sock, tx_buffer, len, 0, (struct sockaddr *)&dest_addr, sizeof(dest_addr));
                if (err < 0) {
                    ESP_LOGE(TAG, "전송 실패 (Errno: %d)", errno);
                } else {
                    ESP_LOGI(TAG, "충격 감지! %.1fdB (전송 완료)", db);
                }
                
                vTaskDelay(100 / portTICK_PERIOD_MS); 
            }
        }
    }
    free(samples);
}
