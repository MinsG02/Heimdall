import socket
import time

# ================= [사용자 설정 구간] =================
UDP_IP = "0.0.0.0"      # 모든 인터페이스 수신
UDP_PORT = 3333         # ESP32와 맞춰야 함
EVENT_WINDOW = 0.2      # (초) 첫 신호 후, 나머지 신호를 기다려줄 시간
# ======================================================

def start_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    
    print(f"\nHeimdall TDOA 서버 시작 (Port {UDP_PORT})")
    print(f"수신 대기 중... (Ctrl+C로 종료)\n")

    event_buffer = []      # 데이터를 모을 바구니
    collecting = False     # 수집 중인지 확인하는 깃발
    sock.settimeout(None)  # 평소엔 무한 대기

    while True:
        try:
            # 1. 데이터 수신
            data, addr = sock.recvfrom(1024)
            message = data.decode('utf-8')
            
            # 2. 데이터 해독 (NODE_1,시간,dB)
            parts = message.split(',')
            node_id = parts[0]
            timestamp = int(parts[1])
            db_val = float(parts[2])

            # 3. 바구니에 담기
            event_buffer.append({'node': node_id, 'time': timestamp, 'db': db_val})

            # 4. 첫 데이터라면? -> 타이머 작동 시작!
            if not collecting:
                collecting = True
                sock.settimeout(EVENT_WINDOW) # 0.2초만 기다린다!

        except socket.timeout:
            # 5. 시간 초과 -> 수집 끝! 계산 시작
            if event_buffer:
                calc_tdoa(event_buffer)
            
            # 초기화 (다음 소리 대기)
            event_buffer = []
            collecting = False
            sock.settimeout(None)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"에러 발생: {e}")

def calc_tdoa(buffer):
    if len(buffer) < 2:
        print(f"단일 신호 감지: {buffer[0]['node']} (비교 대상 없음)")
        return

    # 시간순으로 정렬 (먼저 도착한 놈이 1등)
    sorted_data = sorted(buffer, key=lambda x: x['time'])
    first = sorted_data[0] # 기준점

    print(f"\n충격 감지! 기준: {first['node']} ({first['time']} us)")
    print("-" * 40)
    print(f"{'노드 ID':<10} | {'시간차 (ms)':<15} | {'크기 (dB)':<10}")
    print("-" * 40)

    for item in sorted_data:
        diff = (item['time'] - first['time']) / 1000.0 # us -> ms 변환
        print(f"{item['node']:<10} | +{diff:.3f} ms        | {item['db']:.1f}")
    print("-" * 40 + "\n")

if __name__ == "__main__":
    start_server()