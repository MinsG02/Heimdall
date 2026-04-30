import socket
import struct
import numpy as np
from scipy.optimize import least_squares
import time

# ================= [사용자 설정 구간] =================
UDP_IP = "0.0.0.0"
UDP_PORT = 3333
SOUND_SPEED = 343.0  # 소리 속도 (m/s)

# ★★★ 중요: 각 노드의 실제 설치 위치 (x, y) 좌표 (단위: 미터) ★★★
# 중앙을 (0,0)으로 잡았을 때의 예시입니다. 줄자로 잰 실제 거리를 입력하세요.
NODE_COORDS = {
    "NODE_1": [ 0.0,  0.0],  # 중앙 (Center)
    "NODE_2": [-1.5,  1.5],  # 왼쪽 위
    "NODE_3": [ 1.5,  1.5],  # 오른쪽 위
    "NODE_4": [-1.5, -1.5],  # 왼쪽 아래
    "NODE_5": [ 1.5, -1.5],  # 오른쪽 아래
}
# ======================================================

def solve_position(events):
    # 1. 가장 먼저 소리를 들은 노드를 찾음 (기준점)
    sorted_events = sorted(events, key=lambda x: x['time'])
    ref_event = sorted_events[0]
    ref_node_id = ref_event['node']
    
    if ref_node_id not in NODE_COORDS:
        print(f"Error: {ref_node_id}의 좌표 정보가 없습니다.")
        return

    ref_coords = np.array(NODE_COORDS[ref_node_id])
    t0 = ref_event['time']

    # 2. TDOA 계산을 위한 데이터 준비
    # 다른 노드들이 기준 노드보다 얼마나 늦게 들었는지(거리 차이) 계산
    input_data = []
    
    print(f"\n--- 연산 시작 (기준: {ref_node_id}) ---")
    for event in sorted_events[1:]:
        node_id = event['node']
        if node_id not in NODE_COORDS: continue

        # 시간 차이 (microsecond -> second)
        time_diff = (event['time'] - t0) / 1000000.0
        
        # 거리 차이 (Distance Difference) = 시간차 * 소리속도
        dist_diff = time_diff * SOUND_SPEED
        
        node_coords = np.array(NODE_COORDS[node_id])
        input_data.append((node_coords, dist_diff))
        
        print(f"-> {node_id}: {time_diff*1000:.2f}ms 늦음 (거리차: {dist_diff:.2f}m)")

    if len(input_data) < 2:
        print("데이터 부족: 최소 3개 이상의 노드가 필요합니다.")
        return

    # 3. 비선형 최소자승법(Least Squares)으로 좌표 추정
    # "모든 노드와의 거리 차이를 만족하는 (x, y)는 어디인가?"를 푸는 함수
    def equations(guess_pos, data, ref_pos):
        x, y = guess_pos
        residuals = []
        for (sensor_pos, d_diff) in data:
            # 추정 위치에서 기준 센서까지 거리
            dist_to_ref = np.sqrt((x - ref_pos[0])**2 + (y - ref_pos[1])**2)
            # 추정 위치에서 타겟 센서까지 거리
            dist_to_sensor = np.sqrt((x - sensor_pos[0])**2 + (y - sensor_pos[1])**2)
            
            # 이론상 거리차 vs 실제 거리차 비교
            # (타겟거리 - 기준거리) - 실제측정거리차 = 0 이어야 함
            err = (dist_to_sensor - dist_to_ref) - d_diff
            residuals.append(err)
        return residuals

    # 초기 추측 위치는 (0,0)으로 시작
    initial_guess = [0.0, 0.0]
    
    # 방정식 풀기
    result = least_squares(equations, initial_guess, args=(input_data, ref_coords))
    
    est_x, est_y = result.x
    print("-" * 30)
    print(f"🎯 추정 위치: X={est_x:.2f}m, Y={est_y:.2f}m")
    print("-" * 30 + "\n")
    return est_x, est_y

def start_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    print(f"🚀 위치 추적 서버 시작 (Port {UDP_PORT})")
    print("5개의 노드 데이터를 기다립니다...\n")

    event_buffer = []
    sock.settimeout(None)

    while True:
        try:
            data, addr = sock.recvfrom(1024)
            msg = data.decode('utf-8').split(',')
            
            node_id = msg[0]
            timestamp = int(msg[1])
            db_val = float(msg[2])

            # 버퍼에 추가
            event_buffer.append({'node': node_id, 'time': timestamp, 'db': db_val})

            # 첫 데이터가 들어오면 0.2초만 더 기다리고 바로 계산
            if len(event_buffer) == 1:
                sock.settimeout(0.2)

        except socket.timeout:
            # 0.2초 지남 -> 모인 데이터로 계산 시작!
            if len(event_buffer) >= 3: # 최소 3개는 있어야 계산 가능
                solve_position(event_buffer)
            else:
                print(f"데이터 부족 (수신된 노드: {len(event_buffer)}개) - 무시됨")
            
            event_buffer = []
            sock.settimeout(None)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    start_server()