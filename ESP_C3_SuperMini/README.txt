## 🛠 초기 설정 이슈 및 해결 (Initial Setup)

문제 상황 (Issue)
- ESP32 보드 패키지 3.0.X 버전 사용 시, ESP32-C3의 하드웨어 리소스 제약으로 인한 구동/설치 불가 문제 발생.

해결 방법 (Solution)
- 보드 패키지 버전을 2.0.17로 다운그레이드하여 안정화.

최종 설정값 (Configuration)
- IDE: Arduino IDE
- Board Package : esp32 by Espressif Systems (v2.0.17)
- Target Board : ESP32C3 Dev Module