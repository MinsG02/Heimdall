idf_component_register(SRCS "main.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp_wifi nvs_flash driver esp_event lwip esp_netif esp_timer)
