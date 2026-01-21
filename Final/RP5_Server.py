import pygame
import socket
import sys
import math
from datetime import datetime
import time

# ================= [사용자 설정] =================
UDP_IP = "0.0.0.0"
UDP_PORT = 3333

# 화면 설정
SCREEN_WIDTH = 1024
SCREEN_HEIGHT = 600

# TDOA 민감도
SENSITIVITY_X = 15.0  
SENSITIVITY_Y = 15.0

# 유효 데이터 판정 시간 (ms)
GROUPING_WINDOW_MS = 200 
# =================================================

# 색상 정의
BLACK = (10, 10, 20)
WHITE = (220, 220, 220)
GREEN = (0, 255, 100)       
RED = (255, 50, 50)         
GRAY = (60, 60, 60)
DARK_GREEN = (0, 50, 0)
YELLOW = (255, 200, 0)
CYAN = (0, 200, 255)

def format_timestamp(us_timestamp):
    """마이크로초 타임스탬프를 읽기 쉬운 시:분:초.밀리초 로 변환"""
    if us_timestamp == 0: return "--:--:--"
    seconds = us_timestamp / 1000000
    dt_obj = datetime.fromtimestamp(seconds)
    return dt_obj.strftime("%H:%M:%S")

def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Heimdall 5-Node TDOA Radar (Center Node Added)")
    clock = pygame.time.Clock()

    # 폰트 설정
    font_large = pygame.font.SysFont("arial", 36, bold=True)
    font_medium = pygame.font.SysFont("arial", 24, bold=True)
    font_small = pygame.font.SysFont("arial", 16)

    # UDP 소켓
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    sock.setblocking(False)

    # [수정 1] 데이터 저장소에 NODE_5 추가
    nodes = {
        "NODE_1": {"time": 0, "val": 0.0, "recv_tick": -99999},
        "NODE_2": {"time": 0, "val": 0.0, "recv_tick": -99999},
        "NODE_3": {"time": 0, "val": 0.0, "recv_tick": -99999},
        "NODE_4": {"time": 0, "val": 0.0, "recv_tick": -99999},
        "NODE_5": {"time": 0, "val": 0.0, "recv_tick": -99999}  # 중앙 노드
    }

    # 좌표 변수
    impact_pos = None  
    impact_timer = 0   
    last_group_time = 0 
    ESP_LOG_MSG = "Waiting for data..."

    print(f"✅ 서버 시작됨! (포트: {UDP_PORT})")
    
    running = True
    while running:
        current_tick = pygame.time.get_ticks()

        # 1. 이벤트 처리
        for event in pygame.event.get():
            if event.type == pygame.QUIT: running = False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE: running = False

        # 2. UDP 데이터 수신
        try:
            while True:
                data, addr = sock.recvfrom(1024)
                msg = data.decode('utf-8').strip()
                # print(f"[RECV] {msg}") # 디버깅용 주석 처리

                parts = msg.split(',')
                # Format: NODE_ID, timestamp_us, dB, limit
                if len(parts) >= 3:
                    nid = parts[0]
                    t_us = int(parts[1])
                    db_val = float(parts[2])

                    if nid in nodes:
                        nodes[nid] = {
                            "time": t_us,
                            "val": db_val,
                            "recv_tick": current_tick 
                        }
                        ESP_LOG_MSG = f"Last: {nid} ({db_val:.1f}dB)"
                        
        except BlockingIOError:
            pass 
        except Exception as e:
            print(f"Error: {e}")

        # 3. TDOA 계산 (위치 추적)
        # 참고: 현재 위치 계산 로직은 4개 코너(1,2,3,4) 노드만 사용하여 좌표를 계산합니다.
        # 5번 노드는 화면 표시용 및 데이터 모니터링 용도로 사용됩니다.
        active_nodes = []
        timestamps = []
        
        # 활성 노드 확인
        for nid, info in nodes.items():
            if (current_tick - info['recv_tick']) < GROUPING_WINDOW_MS:
                active_nodes.append(nid)
                timestamps.append(info['time'])

        # 위치 계산 (기존 1~4번 노드 기준 로직 유지)
        nodes_for_calc = ["NODE_1", "NODE_2", "NODE_3", "NODE_4"]
        active_count_calc = sum(1 for n in nodes_for_calc if n in active_nodes)

        if active_count_calc >= 3 and (current_tick - last_group_time > 300):
            last_group_time = current_tick
            
            # 없는 노드는 1~4번의 평균값으로 대체
            t_vals = [nodes[n]["time"] for n in nodes_for_calc]
            valid_ts = [t for t in t_vals if t > 0]
            if not valid_ts: valid_ts = [0]
            avg_t = sum(valid_ts) / len(valid_ts)
            
            t1 = t_vals[0] if "NODE_1" in active_nodes else avg_t
            t2 = t_vals[1] if "NODE_2" in active_nodes else avg_t
            t3 = t_vals[2] if "NODE_3" in active_nodes else avg_t
            t4 = t_vals[3] if "NODE_4" in active_nodes else avg_t

            diff_x = (t1 + t3) - (t2 + t4)
            diff_y = (t1 + t2) - (t3 + t4)

            center_x = SCREEN_WIDTH // 2
            center_y = SCREEN_HEIGHT // 2
            
            calc_x = center_x + (diff_x / SENSITIVITY_X)
            calc_y = center_y + (diff_y / SENSITIVITY_Y)

            calc_x = max(50, min(SCREEN_WIDTH - 50, calc_x))
            calc_y = max(50, min(SCREEN_HEIGHT - 50, calc_y))

            impact_pos = (int(calc_x), int(calc_y))
            impact_timer = 255

        # 4. 화면 그리기
        screen.fill(BLACK)

        # 십자선
        pygame.draw.line(screen, GRAY, (SCREEN_WIDTH//2, 0), (SCREEN_WIDTH//2, SCREEN_HEIGHT), 1)
        pygame.draw.line(screen, GRAY, (0, SCREEN_HEIGHT//2), (SCREEN_WIDTH, SCREEN_HEIGHT//2), 1)

        # [수정 2] 노드 위치 매핑에 NODE_5(중앙) 추가
        pos_map = {
            "NODE_1": (80, 80),                             # 좌상
            "NODE_2": (SCREEN_WIDTH-80, 80),                # 우상
            "NODE_3": (80, SCREEN_HEIGHT-80),               # 좌하
            "NODE_4": (SCREEN_WIDTH-80, SCREEN_HEIGHT-80),  # 우하
            "NODE_5": (SCREEN_WIDTH//2, SCREEN_HEIGHT//2)   # 중앙 (NEW)
        }

        for nid, pos in pos_map.items():
            # 3초간 활성 상태 유지
            is_active = (current_tick - nodes[nid]['recv_tick'] < 3000)
            
            color = GREEN if is_active else GRAY
            val_color = YELLOW if is_active else GRAY
            
            # 1. 노드 원
            pygame.draw.circle(screen, color, pos, 25, 0 if is_active else 2) 
            
            # 2. 노드 이름 (원 안에)
            lbl = font_small.render(nid[-1], True, BLACK if is_active else GRAY) # 숫자만 표시
            screen.blit(lbl, (pos[0]-5, pos[1]-10))

            # 3. dB 값 표시 (원 아래)
            db_val = nodes[nid]['val']
            db_str = f"{db_val:.1f} dB" if is_active else "-- dB"
            db_surf = font_medium.render(db_str, True, val_color)
            screen.blit(db_surf, (pos[0] - db_surf.get_width()//2, pos[1] + 30))

            # 4. NTP 시간 표시 (더 아래)
            node_time_str = format_timestamp(nodes[nid]['time'])
            time_surf = font_small.render(f"T: {node_time_str}", True, CYAN if is_active else GRAY)
            screen.blit(time_surf, (pos[0] - time_surf.get_width()//2, pos[1] + 55))


        # 타격 효과
        if impact_timer > 0 and impact_pos:
            impact_timer -= 5
            if impact_timer < 0: impact_timer = 0
            ix, iy = impact_pos
            
            # 십자 타겟 & 원
            pygame.draw.line(screen, RED, (ix-20, iy), (ix+20, iy), 2)
            pygame.draw.line(screen, RED, (ix, iy-20), (ix, iy+20), 2)
            radius = (255 - impact_timer) // 1.5
            s = pygame.Surface((radius*2, radius*2), pygame.SRCALPHA)
            pygame.draw.circle(s, (255, 50, 50, impact_timer), (radius, radius), radius, 5)
            screen.blit(s, (ix - radius, iy - radius))

            # 좌표 & dB 정보
            info_txt = font_small.render(f"DETECTED!", True, YELLOW)
            screen.blit(info_txt, (ix + 25, iy - 10))

        # === [상단 상태바] ===
        curr_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        title_surf = font_large.render(f"HEIMDALL TDOA SERVER", True, WHITE)
        time_surf = font_medium.render(f"System Time: {curr_time}", True, GREEN)
        
        screen.blit(title_surf, (20, 20))
        screen.blit(time_surf, (SCREEN_WIDTH - time_surf.get_width() - 20, 25))
        
        # 마지막 로그
        log_surf = font_small.render(f"Last Log: {ESP_LOG_MSG}", True, GRAY)
        screen.blit(log_surf, (20, 70))

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()