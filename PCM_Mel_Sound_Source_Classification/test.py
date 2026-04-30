import socket
import struct
import threading
import time
import sys
import platform
from collections import deque

import numpy as np
import pygame
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchaudio.transforms as T

# =========================================================
# [수정] 윈도우 환경 대응을 위한 smbus2 Mocking (fcntl 에러 방지)
# =========================================================
try:
    import smbus2
    # 윈도우에서 smbus2가 설치되어 있어도 fcntl 때문에 import 에러가 날 수 있음
    if platform.system() == "Windows":
        raise ImportError("Windows detects fcntl incompatibility")
    IS_WINDOWS = False
except (ImportError, ModuleNotFoundError):
    IS_WINDOWS = True
    # 가짜(Mock) 객체 정의
    class MockSMBus:
        def __init__(self, bus=None): pass
        def write_byte(self, *args): pass
        def write_byte_data(self, *args): pass
        def close(self): pass

    # 시스템 모듈에 가짜 smbus2 등록
    from types import ModuleType
    mock_mod = ModuleType("smbus2")
    mock_mod.SMBus = MockSMBus
    sys.modules["smbus2"] = mock_mod
    import smbus2
    print("ℹ️ Windows 환경: LCD 하드웨어 기능을 시뮬레이션 모드로 전환합니다.")

# =========================================================
# 1. Config & LCD Hardware Driver
# =========================================================
CONFIG = {
    "bind_ip": "0.0.0.0",
    "db_port": 3333,
    "pcm_port": 3334,
    "sr": 16000,
    "duration": 3,
    "model_path": "sound_classification_model_v4_best.pt",
    "labels": {0: "Background", 1: "Vacuum", 2: "Footstep", 3: "Piano"},
    "device": "cpu",
    "trigger_db": 45.0,
    "conf_threshold": 0.85,
    "lcd_addr": 0x27
}

class PhysicalLCD:
    def __init__(self, addr):
        self.addr = addr
        self.enabled = False
        try:
            # 리눅스 환경에서만 실제 I2C 버스(1번)를 엽니다.
            if not IS_WINDOWS:
                self.bus = smbus2.SMBus(1)
                self.init_lcd()
                self.enabled = True
                print(f"✅ LCD Hardware Ready (0x{addr:02x})")
            else:
                self.bus = smbus2.SMBus(1) # Mock 객체 사용
                print("✅ Virtual LCD Mode Active")
        except Exception as e:
            self.enabled = False
            print(f"⚠️ LCD Hardware Not Found: {e}")

    def send_byte(self, bits, mode):
        if not self.enabled or IS_WINDOWS:
            return
        high = mode | (bits & 0xF0) | 0x08  # Backlight ON
        low = mode | ((bits << 4) & 0xF0) | 0x08
        try:
            self.bus.write_byte(self.addr, high)
            self.toggle_enable(high)
            self.bus.write_byte(self.addr, low)
            self.toggle_enable(low)
        except:
            pass

    def toggle_enable(self, bits):
        time.sleep(0.0005)
        self.bus.write_byte(self.addr, (bits | 0x04))
        time.sleep(0.0005)
        self.bus.write_byte(self.addr, (bits & ~0x04))
        time.sleep(0.0005)

    def init_lcd(self):
        if IS_WINDOWS: return
        for cmd in [0x33, 0x32, 0x06, 0x0C, 0x28, 0x01]:
            self.send_byte(cmd, False)
        time.sleep(0.01)

    def write_status(self, label, node_id):
        # 윈도우라면 콘솔에만 출력하고 실제 하드웨어 명령은 건너뜀
        if IS_WINDOWS:
            print(f"[LCD 시뮬레이션] DET: {label.upper()} | ZONE: {node_id}")
            return
            
        if not self.enabled:
            return
        self.send_byte(0x01, False)  # Clear
        l1, l2 = f"DET: {label.upper()}", f"ZONE: {node_id}"
        self.send_byte(0x80, False)  # Line 1
        for c in l1[:16]:
            self.send_byte(ord(c), True)
        self.send_byte(0xC0, False)  # Line 2
        for c in l2[:16]:
            self.send_byte(ord(c), True)

lcd_hw = PhysicalLCD(CONFIG["lcd_addr"])

# =========================================================
# 2. Global State & Model
# =========================================================
VALID_NODE_IDS = [1, 2, 3, 4]
SELECTED_NODE_ID = 1
running = True

state_lock = threading.Lock()
latest_db = {nid: 0.0 for nid in range(1, 6)}
pcm_buffers = {nid: deque(maxlen=CONFIG["sr"] * 5) for nid in range(1, 6)}

last_event_time = 0.0
last_result = {
    "node_id": "-",
    "label": "Background",
    "conf": 1.0,
    "probs": [1.0, 0.0, 0.0, 0.0]
}

class SingleMicAudioCNN(nn.Module):
    def __init__(self, num_classes):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 32, 3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(),
            nn.MaxPool2d(2),

            nn.Conv2d(32, 64, 3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.MaxPool2d(2),

            nn.Conv2d(64, 128, 3, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d((4, 4))
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128 * 4 * 4, 256),
            nn.ReLU(),
            nn.Dropout(0.5),
            nn.Linear(256, num_classes)
        )

    def forward(self, x):
        return self.classifier(self.features(x))

model = SingleMicAudioCNN(4).to(CONFIG["device"])
try:
    # [수정] weights_only=True 추가하여 Future Warning 해결
    model.load_state_dict(torch.load(CONFIG["model_path"], map_location=CONFIG["device"], weights_only=True))
    model.eval()
    print(f"✅ Model loaded: {CONFIG['model_path']}")
except Exception as e:
    print(f"⚠️ Model file missing or load failed: {e}")

mel_trans = T.MelSpectrogram(
    sample_rate=CONFIG["sr"],
    n_fft=1024,
    hop_length=256,
    n_mels=64
)
amp_db = T.AmplitudeToDB()

# =========================================================
# 3. Networking & Logic
# =========================================================
def db_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((CONFIG["bind_ip"], CONFIG["db_port"]))
    while running:
        try:
            data, _ = sock.recvfrom(1024)
            msg = data.decode().strip().split(",")
            if len(msg) >= 3:
                nid = int(msg[0].replace("NODE_", ""))
                with state_lock:
                    latest_db[nid] = float(msg[2])
        except:
            pass

def pcm_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((CONFIG["bind_ip"], CONFIG["pcm_port"]))
    hdr = struct.Struct("<I B B H I q I")
    while running:
        try:
            packet, _ = sock.recvfrom(65535)
            if len(packet) < hdr.size:
                continue
            magic, _, nid, _, _, _, _ = hdr.unpack(packet[:hdr.size])
            if magic == 0x4850434D:
                samples = np.frombuffer(packet[hdr.size:], dtype="<i2")
                with state_lock:
                    pcm_buffers[nid].extend(samples.tolist())
        except:
            pass

def prepare_waveform_from_pcm(samples_int16):
    target_len = CONFIG["sr"] * CONFIG["duration"]
    if len(samples_int16) == 0:
        waveform = np.zeros(target_len, dtype=np.float32)
    else:
        waveform = np.asarray(samples_int16, dtype=np.float32) / 32768.0
        if len(waveform) >= target_len:
            waveform = waveform[-target_len:]
        else:
            pad_len = target_len - len(waveform)
            left = pad_len // 2
            right = pad_len - left
            waveform = np.pad(waveform, (left, right), mode="constant")
    t = torch.from_numpy(waveform).unsqueeze(0).unsqueeze(0)
    return t

def run_inference(node_id):
    global last_result
    time.sleep(1.0)
    with state_lock:
        samples = list(pcm_buffers[node_id])
    t = prepare_waveform_from_pcm(samples)
    with torch.no_grad():
        mel = mel_trans(t)
        mel_db = amp_db(mel)
        out = model(mel_db)
        probs = torch.softmax(out, dim=1)[0]
        idx = probs.argmax().item()
        conf = probs[idx].item()
        final_idx = idx if conf >= CONFIG["conf_threshold"] else 0
        label_name = CONFIG["labels"][final_idx]
        
        if final_idx != 0:
            lcd_hw.write_status(label_name, node_id)

        with state_lock:
            last_result = {
                "node_id": node_id,
                "label": label_name,
                "conf": conf,
                "probs": probs.tolist()
            }

# =========================================================
# 4. Compact UI (1200x700) - 원본 유지
# =========================================================
def draw_bar(surf, x, y, w, h, val, color):
    pygame.draw.rect(surf, (40, 45, 50), (x, y, w, h), border_radius=4)
    if val > 0:
        pygame.draw.rect(surf, color, (x, y, int(w * min(1, val)), h), border_radius=4)

def main():
    global SELECTED_NODE_ID, last_event_time, running
    pygame.init()
    screen = pygame.display.set_mode((1200, 700))
    pygame.display.set_caption("AI Sound Surveillance System")
    clock = pygame.time.Clock()

    f_title = pygame.font.SysFont("Arial", 22, bold=True)
    f_h1 = pygame.font.SysFont("Arial", 16, bold=True)
    f_lcd = pygame.font.SysFont("Courier New", 30, bold=True)
    f_body = pygame.font.SysFont("Arial", 14)

    threading.Thread(target=db_listener, daemon=True).start()
    threading.Thread(target=pcm_listener, daemon=True).start()

    while running:
        screen.fill((10, 12, 15))
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.KEYDOWN and event.unicode in "12345":
                SELECTED_NODE_ID = int(event.unicode)

        with state_lock:
            db_snap = dict(latest_db)
            res_snap = dict(last_result)
            max_nid = max(db_snap, key=db_snap.get) if any(db_snap.values()) else 1

        if db_snap[max_nid] >= CONFIG["trigger_db"] and (time.time() - last_event_time) > 3.0:
            last_event_time = time.time()
            threading.Thread(target=run_inference, args=(max_nid,)).start()

        # LEFT: Monitoring
        pygame.draw.rect(screen, (25, 30, 40), (20, 20, 440, 660), border_radius=15)
        screen.blit(f_title.render("SYSTEM ANALYTICS", True, (255,255,255)), (40, 40))
        for i, nid in enumerate([1, 2, 3, 4]):
            y = 100 + i * 75
            is_sel = (nid == SELECTED_NODE_ID)
            pygame.draw.rect(screen, (35, 45, 60) if is_sel else (30, 35, 45), (40, y, 400, 60), border_radius=10)
            if is_sel:
                pygame.draw.rect(screen, (0, 160, 255), (40, y, 400, 60), 2, border_radius=10)
            screen.blit(f_h1.render(f"NODE {nid}", True, (200,200,200)), (55, y+8))
            draw_bar(screen, 55, y+35, 300, 10, db_snap[nid]/80, (0, 255, 150) if db_snap[nid] < 45 else (255, 80, 80))
            screen.blit(f_body.render(f"{db_snap[nid]:.1f} dB", True, (255,255,255)), (365, y+30))

        # Probabilities
        for idx, label in CONFIG["labels"].items():
            y_p = 440 + idx * 45
            screen.blit(f_body.render(label, True, (200,200,200)), (40, y_p))
            draw_bar(screen, 140, y_p + 5, 250, 12, res_snap["probs"][idx], (0, 160, 255))
            screen.blit(f_body.render(f"{res_snap['probs'][idx]*100:.0f}%", True, (150,150,150)), (400, y_p))

        # RIGHT: LCD & Small Grid
        lcd_rect = pygame.Rect(480, 20, 700, 130)
        pygame.draw.rect(screen, (5, 20, 5), lcd_rect, border_radius=10)
        pygame.draw.rect(screen, (60, 70, 60), lcd_rect, 2, border_radius=10)
        status_c = (80, 220, 130) if res_snap["label"] != "Background" else (80, 100, 80)
        screen.blit(f_body.render("PHYSICAL LCD FEEDBACK (0x27)", True, (0, 150, 0)), (500, 35))
        screen.blit(f_lcd.render(f"> {res_snap['label'].upper()}", True, status_c), (500, 65))
        screen.blit(f_body.render(f"ZONE: {res_snap['node_id']} | CONF: {res_snap['conf']*100:.1f}%", True, (0, 100, 0)), (500, 110))

        # Grid (ㅁㅁ ㅁㅁ)
        grid_x, grid_y = 580, 180
        box_s = 180
        for i in range(4):
            nid = i + 1
            r, c = i // 2, i % 2
            bx, by = grid_x + c*(box_s+20), grid_y + 40 + r*(box_s+20)
            active = (nid == max_nid and db_snap[nid] >= CONFIG["trigger_db"])
            pygame.draw.rect(screen, (int(db_snap[nid]*2), 40, 40) if active else (25, 30, 40), (bx, by, box_s, box_s), border_radius=12)
            if active:
                pygame.draw.rect(screen, (255, 220, 0), (bx, by, box_s, box_s), 4, border_radius=12)
            screen.blit(f_h1.render(f"ZONE {nid}", True, (120, 130, 140)), (bx+15, by+15))
            val_txt = f_lcd.render(f"{db_snap[nid]:.1f}", True, (255,255,255))
            screen.blit(val_txt, (bx + box_s//2 - val_txt.get_width()//2, by + box_s//2 - 15))

        pygame.display.flip()
        clock.tick(25)

    pygame.quit()

if __name__ == "__main__":
    main()