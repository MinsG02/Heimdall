#include <driver/i2s.h>

#define I2S_WS 3
#define I2S_SD 4
#define I2S_SCK 2
#define I2S_PORT I2S_NUM_0

//한 번에 분석할 샘플 수 (이 값이 클수록 업데이트가 느려지고 안정적임)
#define SAMPLE_BLOCK 512 

void setup() {
  Serial.begin(115200);
  
  const i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = 44100,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = i2s_comm_format_t(I2S_COMM_FORMAT_STAND_I2S),
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 64,
    .use_apll = false
  };

  const i2s_pin_config_t pin_config = {
    .bck_io_num = I2S_SCK,
    .ws_io_num = I2S_WS,
    .data_out_num = -1,
    .data_in_num = I2S_SD
  };

  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_PORT, &pin_config);
  i2s_start(I2S_PORT);
}

void loop() {
  int32_t samples[SAMPLE_BLOCK];
  size_t bytesIn = 0;

  //데이터 읽기
  esp_err_t result = i2s_read(I2S_PORT, &samples, sizeof(samples), &bytesIn, portMAX_DELAY);

  if (result == ESP_OK) {
    int32_t max_val = -2147483648;
    int32_t min_val = 2147483647;

    //블록 내에서 최대/최소값 찾기 (Peak-to-Peak 계산용)
    for (int i = 0; i < (bytesIn / 4); i++) {
      int32_t val = samples[i] >> 14; //노이즈 제거 및 스케일 조정
      if (val > max_val) max_val = val;
      if (val < min_val) min_val = val;
    }

    int32_t amplitude = max_val - min_val; //진폭 계산

    //숫자로 출력 해주는 부분 가독성이 안좋아 주석 처리
    //Serial.println(amplitude); 

    //시리얼 플롯 막대 그래프 시각화
    int barLength = map(amplitude, 0, 5000, 0, 50); //시리얼 플롯에서 bar 크기 설정 해주는 부분
    Serial.print("Volume: ");
    for (int i = 0; i < barLength; i++) {
      Serial.print("#");
    }
    Serial.println(); 
  }
}