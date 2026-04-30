idf_component_register(SRCS "main.c"
                    INCLUDE_DIRS "."
                    REQUIRES esp_wifi esp_event nvs_flash driver esp_netif lwip esp_timer mbedtls) 
                    # 맨 뒤에 mbedtls 추가