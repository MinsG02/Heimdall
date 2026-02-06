#노트북에서 실행 가능한 파이썬 서버 코드
import socket
import struct
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import threading
from scipy import signal

# ================= [SETTINGS] =================
UDP_IP = "0.0.0.0"
UDP_PORT_PCM = 3334         # PCM Port (펌웨어와 일치해야 함)
SAMPLE_RATE = 16000
BUFFER_SIZE = 2048          # 분석 윈도우 크기
REF_NODE = "NODE_1"         # 기준 노드 (Reference)
TARGET_NODES = ["NODE_2", "NODE_3", "NODE_4", "NODE_5"] # 비교할 타겟 노드들
# ==============================================

# Node Configuration (5번 노드 추가됨 - 보라색)
NODE_CONFIG = {
    "NODE_1": {"color": "#FF5555", "label": "REF (Node 1)"},
    "NODE_2": {"color": "#55FF55", "label": "TGT (Node 2)"},
    "NODE_3": {"color": "#00CCFF", "label": "TGT (Node 3)"},
    "NODE_4": {"color": "#FFFF55", "label": "TGT (Node 4)"},
    "NODE_5": {"color": "#DDA0DD", "label": "TGT (Node 5)"}  # Plum/Purple
}

# Initialize Buffers
audio_buffers = {nid: np.zeros(BUFFER_SIZE) for nid in NODE_CONFIG.keys()}
lock = threading.Lock()

# Header Structure
HEADER_FORMAT = '<I B B H I q I'
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
PCM_MAGIC = 0x4850434D

def udp_receiver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT_PCM))
    print(f"[SYSTEM] TDOA Server Started (Port: {UDP_PORT_PCM})")
    
    while True:
        try:
            data, _ = sock.recvfrom(4096)
            if len(data) < HEADER_SIZE: continue

            header = data[:HEADER_SIZE]
            magic, ver, node_id_u8, n, fs, t0, seq = struct.unpack(HEADER_FORMAT, header)

            if magic != PCM_MAGIC: continue
            
            node_key = f"NODE_{node_id_u8}"

            if node_key in audio_buffers:
                payload = data[HEADER_SIZE:]
                new_samples = np.frombuffer(payload, dtype=np.int16)
                # Normalize (int16 -> float -1.0 to 1.0)
                new_samples_norm = new_samples / 32768.0

                with lock:
                    audio_buffers[node_key] = np.roll(audio_buffers[node_key], -len(new_samples_norm))
                    audio_buffers[node_key][-len(new_samples_norm):] = new_samples_norm

        except Exception:
            pass

t = threading.Thread(target=udp_receiver, daemon=True)
t.start()

# ================= Graph Setup =================
plt.style.use('dark_background')
fig, (ax_wave, ax_gcc) = plt.subplots(2, 1, figsize=(10, 9))
fig.canvas.manager.set_window_title('Heimdall 5-Channel TDOA System')

# 1. Waveform Graph
lines_wave = {}
for nid in NODE_CONFIG:
    cfg = NODE_CONFIG[nid]
    # 모든 노드의 파형을 그림
    line, = ax_wave.plot([], [], label=cfg["label"], color=cfg["color"], lw=1.5, alpha=0.8)
    lines_wave[nid] = line

ax_wave.set_title("1. Raw Waveforms (All 5 Nodes)", color='white', fontsize=14)
ax_wave.set_ylim(-1, 1)
ax_wave.set_xlim(0, BUFFER_SIZE)
ax_wave.legend(loc="upper right", fontsize=10)
ax_wave.grid(True, alpha=0.3)

# 2. GCC Graph (Correlation)
lines_gcc = {}
lags = signal.correlation_lags(BUFFER_SIZE, BUFFER_SIZE, mode='same')
zero_line = np.zeros_like(lags)

for nid in TARGET_NODES:
    cfg = NODE_CONFIG[nid]
    # 기준 노드를 제외한 나머지 노드들의 상관관계 그래프
    line, = ax_gcc.plot(lags, zero_line, label=f"{nid} vs REF", color=cfg["color"], lw=2)
    lines_gcc[nid] = line

ax_gcc.set_title(f"2. Time Delay Analysis (Reference: {REF_NODE})", color='white', fontsize=14)
ax_gcc.set_xlabel("Sample Lag (< 0: Early / > 0: Late)", color='white')
ax_gcc.set_ylim(-50, 50) 
ax_gcc.grid(True, alpha=0.3)
ax_gcc.legend(loc="upper right", fontsize=10)

# Text Info
info_text = ax_gcc.text(0.02, 0.95, "Initializing...", transform=ax_gcc.transAxes, 
                        color="white", fontsize=11, fontweight="bold", va='top')

def update(frame):
    with lock:
        signals = {nid: buf.copy() for nid, buf in audio_buffers.items()}
    
    # 1. Update Waveforms
    for nid, line in lines_wave.items():
        line.set_ydata(signals[nid])
        line.set_xdata(np.arange(BUFFER_SIZE))

    # 2. Calculate GCC
    sig_ref = signals[REF_NODE]
    
    # Check if reference signal is silent
    if np.max(np.abs(sig_ref)) < 0.02:
        info_text.set_text("Waiting for sound input...")
        for line in lines_gcc.values():
            line.set_ydata(zero_line)
        return list(lines_wave.values()) + list(lines_gcc.values()) + [info_text]

    status_msg = f"[Reference: {REF_NODE}]\n"
    
    for nid in TARGET_NODES:
        sig_cmp = signals[nid]
        
        # Check if target signal is silent
        if np.max(np.abs(sig_cmp)) < 0.02:
            lines_gcc[nid].set_ydata(zero_line)
            status_msg += f"{nid}: No Signal\n"
            continue

        # Cross-Correlation
        corr = signal.correlate(sig_cmp, sig_ref, mode='same')
        lines_gcc[nid].set_ydata(corr)

        # Find Peak
        peak_idx = np.argmax(corr)
        lag = lags[peak_idx]
        ms = (lag / SAMPLE_RATE) * 1000

        status_msg += f"{nid}: {lag:+d} smp ({ms:+.2f} ms)\n"

    info_text.set_text(status_msg)
    
    return list(lines_wave.values()) + list(lines_gcc.values()) + [info_text]

ani = FuncAnimation(fig, update, interval=50, blit=True)
plt.show()
