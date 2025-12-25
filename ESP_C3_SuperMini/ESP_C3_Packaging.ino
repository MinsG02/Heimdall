#include <WiFi.h>
#include <WiFiUdp.h>

// 1. 노드 식별자 (마이크마다 1, 2, 3, 4, 5로 변경)
#define NODE_ID 1 

// 2. 전송할 데이터 구조체 정의
struct __attribute__((packed)) AudioData {
  uint8_t id;         // 마이크 ID
  uint32_t timestamp; // 소리 발생 시점 (micros 단위)
  int32_t amplitude;  // 소리 크기
};

WiFiUDP udp;
const char* serverIP = "192.168.4.1";
const int port = 12345;

void sendData(int32_t amp) {
  AudioData packet;
  packet.id = NODE_ID;
  packet.timestamp = micros(); // 현재 마이크의 시간 기록
  packet.amplitude = amp;

  udp.beginPacket(serverIP, port);
  udp.write((uint8_t*)&packet, sizeof(packet)); // 구조체 전체를 바이너리로 전송
  udp.endPacket();
}