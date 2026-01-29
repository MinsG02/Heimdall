#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2s_std.h"
#include "driver/uart.h"
#include "mbedtls/base64.h"

// ================= [Tenstar 핀 설정] =================
#define I2S_BCK_IO      (GPIO_NUM_6)
#define I2S_WS_IO       (GPIO_NUM_4)
#define I2S_DO_IO       (GPIO_NUM_5)
// =====================================================

#define SAMPLE_RATE     16000
#define SAMPLES_BATCH   128

static i2s_chan_handle_t rx_handle = NULL;

void i2s_init(void) {
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, NULL, &rx_handle));
    
    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = I2S_BCK_IO,
            .ws = I2S_WS_IO,
            .dout = I2S_GPIO_UNUSED,
            .din = I2S_DO_IO,
        },
    };
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(rx_handle, &std_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(rx_handle));
}

void app_main(void) {
    uart_config_t uart_config = {
        .baud_rate = 921600,
        .data_bits = UART_DATA_8_BITS,
        .parity    = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    uart_param_config(UART_NUM_0, &uart_config);
    uart_set_baudrate(UART_NUM_0, 921600);

    i2s_init();

    int32_t *raw_buff = (int32_t *)calloc(SAMPLES_BATCH * 2, sizeof(int32_t));
    int16_t *pcm_buff = (int16_t *)calloc(SAMPLES_BATCH, sizeof(int16_t));
    unsigned char *b64_out = (unsigned char *)calloc(SAMPLES_BATCH * 4, 1);
    size_t b64_len = 0;
    size_t bytes_read = 0;

    // ★ 동굴 소리 제거를 위한 필터 변수
    // static으로 선언해서 값을 계속 기억하게 함
    static float dc_offset = 0.0f;
    const float alpha = 0.05f; // 필터 강도 (0.01~0.1 사이, 작을수록 부드러움)

    vTaskDelay(pdMS_TO_TICKS(1000));

    while (1) {
        if (i2s_channel_read(rx_handle, raw_buff, SAMPLES_BATCH * 2 * sizeof(int32_t), &bytes_read, 1000) == ESP_OK) {
            
            int samples = bytes_read / 4; 
            int idx = 0;

            for (int i = 0; i < samples; i += 2) {
                // 1. 원본 데이터 가져오기 (>> 14 적당한 볼륨)
                // 만약 소리가 작으면 13, 너무 크면 15로 조절
                int32_t raw_val = raw_buff[i] >> 14; 

                // 2. ★ 이동 평균 필터 (Low Pass Filter)
                // 평균을 급격하게 바꾸지 않고 천천히 따라가게 만듭니다.
                // 이게 "동굴 소리"와 "뚝뚝 끊기는 잡음"을 없애줍니다.
                dc_offset = (dc_offset * (1.0f - alpha)) + (raw_val * alpha);
                
                // 3. 필터링된 값 빼기 (DC 제거)
                int32_t proc_val = raw_val - (int32_t)dc_offset;

                // 4. 노이즈 게이트 (아주 작은 잡음은 0으로)
                if (proc_val > -5 && proc_val < 5) proc_val = 0;

                // 클리핑 방지
                if(proc_val > 32767) proc_val = 32767;
                if(proc_val < -32768) proc_val = -32768;
                
                pcm_buff[idx++] = (int16_t)proc_val;
            }

            // Base64 인코딩 후 전송
            mbedtls_base64_encode(b64_out, SAMPLES_BATCH*4, &b64_len, (unsigned char*)pcm_buff, idx*2);
            printf("%s\n", b64_out);
            fflush(stdout);
        }
    }
}