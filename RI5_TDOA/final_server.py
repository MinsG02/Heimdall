import serial
import base64
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import collections
import time

# ================= [설정] =================
COM_PORT = 'COM4'       
BAUD_RATE = 921600      
SAMPLE_RATE = 16000     
WINDOW_SIZE = 4000      # 화면에 보여줄 샘플 수
# ==========================================

try:
    ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=0.1)
    print(f"[{COM_PORT}] 연결 성공! 그래프 창이 뜹니다...")
    time.sleep(1)
    ser.reset_input_buffer()
except Exception as e:
    print(f"포트 연결 실패: {e}")
    exit()

# 데이터를 담을 그릇
data_buffer = collections.deque([0] * WINDOW_SIZE, maxlen=WINDOW_SIZE)

# 그래프 초기화
fig, ax = plt.subplots(facecolor='#2E2E2E')
ax.set_facecolor('#2E2E2E')

x_data = np.arange(WINDOW_SIZE)
# ★ 변수 충돌 방지를 위해 여기서 line을 유지합니다.
line, = ax.plot(x_data, np.zeros(WINDOW_SIZE), color='#00FF00', lw=1)

ax.set_ylim(-32768, 32767)
ax.set_xlim(0, WINDOW_SIZE)
ax.set_title("Real-time Mic Waveform (Heimdall)", color='white')
ax.grid(color='gray', linestyle='--', linewidth=0.5, alpha=0.5)
ax.tick_params(axis='x', colors='white')
ax.tick_params(axis='y', colors='white')

def update(frame):
    global data_buffer
    
    while ser.in_waiting > 0:
        try:
            # ★ [수정된 부분] 변수 이름을 line -> serial_data로 변경!
            serial_data = ser.readline().strip()
            
            if not serial_data: continue
            
            # Base64 디코딩
            decoded_bytes = base64.b64decode(serial_data)
            new_samples = np.frombuffer(decoded_bytes, dtype=np.int16)
            data_buffer.extend(new_samples)
            
        except Exception as e:
            pass 
    
    # 여기서 line은 위에서 만든 '초록색 선'을 의미함 (이제 덮어씌워지지 않음)
    line.set_ydata(data_buffer)
    return line,

# 애니메이션 실행
ani = FuncAnimation(fig, update, interval=20, blit=True, cache_frame_data=False)
plt.show()

ser.close()
메타플로잇 gui 시각화 코드
