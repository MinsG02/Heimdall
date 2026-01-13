import pygame
import socket
import sys
import math
from datetime import datetime  # ★ 시간 확인용 모듈 추가

# ================= [사용자 설정] =================
UDP_IP = "0.0.0.0"
UDP_PORT = 3333

# 마이크 간 거리 (cm)
MIC_DISTANCE_CM = 200
SPEED_OF_SOUND = 34300  # cm/s

# 화면 설정
SCREEN_WIDTH = 1024
SCREEN_HEIGHT = 600
# =================================================

# 색상 정의
BLACK = (10, 10, 30)
WHITE = (255, 255, 255)
GREEN = (50, 255, 50)
RED = (255, 50, 50)
BLUE = (50, 150, 255)    # 야간
GRAY = (100, 100, 100)
YELLOW = (255, 255, 0)   # 주간

def get_current_mode_info():
    """현재 시스템 시간(라즈베리파이 시계)을 보고 모드와 색상을 반환"""
    now = datetime.now()
    hour = now.hour
    
    # 06:00 ~ 22:00 주간
    if 6 <= hour < 22:
        return "DAY Mode (Limit: 39dB)", YELLOW
    else:
        return "NIGHT Mode (Limit: 32dB)", BLUE

def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Heimdall 2-Channel TDOA Radar")
    clock = pygame.time.Clock()

    # 폰트 설정
    font_large = pygame.font.SysFont("arial", 50, bold=True)
    font_medium = pygame.font.SysFont("arial", 30)
    font_small = pygame.font.SysFont("arial", 20)

    # UDP 소켓 설정
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    sock.setblocking(False)

    # 데이터 저장소
    node_data = {
        "NODE_LEFT": {"time": 0, "db": 0, "limit": 0, "updated": 0},
        "NODE_RIGHT": {"time": 0, "db": 0, "limit": 0, "updated": 0}
    }

    # 시각화 변수
    impact_x = SCREEN_WIDTH // 2
    impact_timer = 0
    last_event_time = 0
    
    # 디스플레이용 변수
    current_dist_diff = 0.0

    running = True
    while running:
        current_tick = pygame.time.get_ticks()

        # 1. 이벤트 처리
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False

        # 2. ★ [수정됨] 실시간 모드 업데이트 (데이터 수신과 무관하게 항상 실행)
        mode_str, mode_color = get_current_mode_info()

        # 3. 데이터 수신 (UDP)
        try:
            while True: 
                data, addr = sock.recvfrom(1024)
                message = data.decode('utf-8')
                parts = message.split(',')
                
                if len(parts) >= 4:
                    node_id = parts[0]
                    timestamp_us = int(parts[1])
                    db = float(parts[2])
                    limit = float(parts[3])

                    if node_id in node_data:
                        node_data[node_id] = {
                            "time": timestamp_us,
                            "db": db,
                            "limit": limit,
                            "updated": current_tick
                        }
                        
        except BlockingIOError:
            pass

        # 4. TDOA 분석
        t_left = node_data["NODE_LEFT"]["updated"]
        t_right = node_data["NODE_RIGHT"]["updated"]
        
        # 최근 500ms 안에 양쪽 다 데이터를 보냈으면 위치 계산
        if (current_tick - t_left < 500) and (current_tick - t_right < 500):
            ts_left = node_data["NODE_LEFT"]["time"]
            ts_right = node_data["NODE_RIGHT"]["time"]
            
            diff_us = ts_left - ts_right 
            diff_ms = diff_us / 1000.0
            dist_diff_cm = (diff_us / 1000000.0) * SPEED_OF_SOUND
            
            if abs(diff_ms) < 5000:
                current_dist_diff = dist_diff_cm
                
                # 위치 매핑
                center_x = SCREEN_WIDTH // 2
                scale_factor = (SCREEN_WIDTH - 200) / (MIC_DISTANCE_CM * 2)
                calc_x = center_x + (dist_diff_cm * scale_factor)
                
                # 화면 갱신용 변수 설정
                impact_x = max(100, min(SCREEN_WIDTH - 100, calc_x))
                impact_timer = 255
                last_event_time = current_tick
                
                # 처리 완료 (중복 방지)
                node_data["NODE_LEFT"]["updated"] = 0
                node_data["NODE_RIGHT"]["updated"] = 0

        # 5. 화면 그리기
        screen.fill(BLACK)
        
        # 기준선
        pygame.draw.line(screen, GRAY, (SCREEN_WIDTH//2, 100), (SCREEN_WIDTH//2, SCREEN_HEIGHT-100), 1)
        pygame.draw.line(screen, WHITE, (50, SCREEN_HEIGHT//2), (SCREEN_WIDTH-50, SCREEN_HEIGHT//2), 2)

        # 노드 아이콘
        pygame.draw.circle(screen, GREEN, (100, SCREEN_HEIGHT//2), 20)
        lbl_l = font_medium.render("LEFT", True, GREEN)
        screen.blit(lbl_l, (70, SCREEN_HEIGHT//2 + 30))
        
        pygame.draw.circle(screen, GREEN, (SCREEN_WIDTH-100, SCREEN_HEIGHT//2), 20)
        lbl_r = font_medium.render("RIGHT", True, GREEN)
        screen.blit(lbl_r, (SCREEN_WIDTH-140, SCREEN_HEIGHT//2 + 30))

        # 충격 애니메이션
        if impact_timer > 0:
            impact_timer -= 5 
            if impact_timer < 0: impact_timer = 0
            
            # 타겟 마커
            pygame.draw.line(screen, RED, (impact_x, 150), (impact_x, SCREEN_HEIGHT-150), 2)
            
            radius = (255 - impact_timer) // 2
            pygame.draw.circle(screen, RED, (int(impact_x), SCREEN_HEIGHT//2), radius, 2)
            pygame.draw.circle(screen, RED, (int(impact_x), SCREEN_HEIGHT//2), 10)
            
            # 정보 텍스트
            dist_text = f"{abs(current_dist_diff):.0f}cm"
            pos_label = "CENTER"
            if current_dist_diff < -10: pos_label = "LEFT Side"
            elif current_dist_diff > 10: pos_label = "RIGHT Side"
            
            info_surf = font_medium.render(f"{pos_label} ({dist_text})", True, WHITE)
            screen.blit(info_surf, (int(impact_x) - info_surf.get_width()//2, SCREEN_HEIGHT//2 - 60))
        
        # ★ [수정됨] 실시간 모드 상태 표시 (시간에 따라 자동 변경됨)
        mode_surf = font_small.render(mode_str, True, mode_color)
        screen.blit(mode_surf, (20, 20))
        
        # 시계 표시 (우측 상단)
        now_str = datetime.now().strftime("%H:%M:%S")
        clock_surf = font_small.render(f"System Time: {now_str}", True, GRAY)
        screen.blit(clock_surf, (SCREEN_WIDTH - clock_surf.get_width() - 20, 20))

        # 하단 상태바
        status_msg = "Waiting for noise..."
        if pygame.time.get_ticks() - last_event_time < 3000:
             status_msg = "Target Detected!"
             
        stat_surf = font_small.render(status_msg, True, GRAY)
        screen.blit(stat_surf, (SCREEN_WIDTH//2 - stat_surf.get_width()//2, SCREEN_HEIGHT - 40))

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()
