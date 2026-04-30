import socket
import struct
import threading
import time
import sys
import platform
import os
import re
from collections import deque

import joblib
import numpy as np
import pygame
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchaudio.transforms as T


# =========================================================
# Windows smbus2 Mock
# =========================================================
try:
    import smbus2
    if platform.system() == "Windows":
        raise ImportError("Windows detects fcntl incompatibility")
    IS_WINDOWS = False
except (ImportError, ModuleNotFoundError):
    IS_WINDOWS = True

    class MockSMBus:
        def __init__(self, bus=None): pass
        def write_byte(self, *args): pass
        def write_byte_data(self, *args): pass
        def close(self): pass

    from types import ModuleType
    mock_mod = ModuleType("smbus2")
    mock_mod.SMBus = MockSMBus
    sys.modules["smbus2"] = mock_mod
    import smbus2
    print("ℹ️ Windows 환경: LCD 하드웨어 기능을 시뮬레이션 모드로 전환합니다.")


# =========================================================
# GPIO LED Control / gpiozero
# =========================================================
try:
    from gpiozero import LED
    from gpiozero.pins.lgpio import LGPIOFactory

    PIN_FACTORY = LGPIOFactory()
    GPIOZERO_AVAILABLE = True

except Exception as e:
    GPIOZERO_AVAILABLE = False
    PIN_FACTORY = None
    print(f"⚠️ gpiozero/lgpio load failed: {e}")

    class LED:
        def __init__(self, *args, **kwargs): pass
        def on(self): pass
        def off(self): pass
        def close(self): pass


# =========================================================
# 1. Config
# =========================================================
CONFIG = {
    "bind_ip": "0.0.0.0",
    "db_port": 3333,
    "pcm_port": 3334,

    # PCM 소리 분류 설정
    "sr": 16000,
    "duration": 3,
    "sound_model_path": "sound_classification_model_v4_best.pt",
    "labels": {
        0: "Background",
        1: "Vacuum",
        2: "Footstep",
        3: "Piano"
    },

    # dB 위치 추론 모델 설정
    "location_model_path": "db_location_model_best.pt",
    "location_scaler_path": "db_location_scaler.pkl",
    "zone_ids": [1, 2, 3, 4, 5, 6],

    # dB 시계열 샘플링
    # 학습 데이터 수집 코드와 동일하게 0.1초 간격 기준
    "db_sample_interval": 0.1,
    "db_history_maxlen": 200,

    "device": "cpu",
    "trigger_db": 45.0,
    "conf_threshold": 0.85,
    "lcd_addr": 0x27,

    # LED GPIO pins, BCM 번호 기준
    "led_r_pin": 17,   # R: 소음 감지
    "led_g_pin": 27,   # G: 배경소음
    "led_b_pin": 22,   # B: 4개 노드 연결 상태

    # 노드 연결 판정 시간
    "node_timeout_sec": 3.0,
}


# =========================================================
# 2. LCD Hardware Driver
# =========================================================
class PhysicalLCD:
    def __init__(self, addr):
        self.addr = addr
        self.enabled = False
        self.lcd_lock = threading.Lock()

        try:
            if not IS_WINDOWS:
                self.bus = smbus2.SMBus(1)
                self.enabled = True
                self.init_lcd()
                print(f"✅ LCD Hardware Ready (0x{addr:02x})")
            else:
                self.bus = smbus2.SMBus(1)
                print("✅ Virtual LCD Mode Active")

        except Exception as e:
            self.enabled = False
            print(f"⚠️ LCD Hardware Not Found: {e}")

    def write_raw_byte(self, data):
        self.bus.write_byte(self.addr, data)

    def toggle_enable(self, bits):
        time.sleep(0.0005)
        self.write_raw_byte(bits | 0x04)
        time.sleep(0.0005)
        self.write_raw_byte(bits & ~0x04)
        time.sleep(0.0005)

    def send_byte(self, bits, mode):
        if not self.enabled or IS_WINDOWS:
            return

        high = mode | (bits & 0xF0) | 0x08
        low = mode | ((bits << 4) & 0xF0) | 0x08

        try:
            self.write_raw_byte(high)
            self.toggle_enable(high)
            self.write_raw_byte(low)
            self.toggle_enable(low)

            if bits in [0x01, 0x02]:
                time.sleep(0.002)

        except Exception as e:
            print(f"⚠️ LCD write error: {e}")

    def init_lcd(self):
        if IS_WINDOWS:
            return

        time.sleep(0.05)

        for cmd in [0x33, 0x32, 0x28, 0x0C, 0x06, 0x01]:
            self.send_byte(cmd, 0x00)
            time.sleep(0.005)

    def write_status(self, label, zone_id):
        if IS_WINDOWS:
            print(f"[LCD 시뮬레이션] DET: {label.upper()} | ZONE: {zone_id}")
            return

        if not self.enabled:
            return

        with self.lcd_lock:
            self.send_byte(0x01, 0x00)
            time.sleep(0.002)

            l1 = f"DET: {str(label).upper()}"
            l2 = f"ZONE: {zone_id}"

            l1 = l1[:16].ljust(16)
            l2 = l2[:16].ljust(16)

            self.send_byte(0x80, 0x00)
            for c in l1:
                self.send_byte(ord(c), 0x01)

            self.send_byte(0xC0, 0x00)
            for c in l2:
                self.send_byte(ord(c), 0x01)


lcd_hw = PhysicalLCD(CONFIG["lcd_addr"])


# =========================================================
# 3. LED Hardware Driver
# =========================================================
class LEDController:
    def __init__(self):
        self.r_pin = CONFIG["led_r_pin"]
        self.g_pin = CONFIG["led_g_pin"]
        self.b_pin = CONFIG["led_b_pin"]

        self.enabled = GPIOZERO_AVAILABLE and not IS_WINDOWS

        try:
            if self.enabled:
                self.r_led = LED(self.r_pin, pin_factory=PIN_FACTORY)
                self.g_led = LED(self.g_pin, pin_factory=PIN_FACTORY)
                self.b_led = LED(self.b_pin, pin_factory=PIN_FACTORY)

                self.all_off()
                print("✅ LED gpiozero Ready")
            else:
                self.r_led = LED()
                self.g_led = LED()
                self.b_led = LED()
                print("✅ Virtual LED Mode Active")

        except Exception as e:
            self.enabled = False
            self.r_led = LED()
            self.g_led = LED()
            self.b_led = LED()
            print(f"⚠️ LED gpiozero init failed: {e}")

    def set_led(self, led, on):
        if not self.enabled:
            return

        if on:
            led.on()
        else:
            led.off()

    def set_noise(self):
        self.set_led(self.r_led, True)
        self.set_led(self.g_led, False)

    def set_background(self):
        self.set_led(self.r_led, False)
        self.set_led(self.g_led, True)

    def set_unknown(self):
        self.set_led(self.r_led, False)
        self.set_led(self.g_led, False)

    def set_nodes_connected(self, connected):
        self.set_led(self.b_led, connected)

    def all_off(self):
        if not self.enabled:
            return

        self.r_led.off()
        self.g_led.off()
        self.b_led.off()

    def cleanup(self):
        if not self.enabled:
            return

        self.all_off()
        self.r_led.close()
        self.g_led.close()
        self.b_led.close()


led_hw = LEDController()


# =========================================================
# 4. Global State
# =========================================================
VALID_NODE_IDS = [1, 2, 3, 4]
SELECTED_NODE_ID = 1
running = True

state_lock = threading.Lock()

latest_db = {
    nid: 0.0 for nid in VALID_NODE_IDS
}

db_history = {
    nid: deque(maxlen=CONFIG["db_history_maxlen"])
    for nid in VALID_NODE_IDS
}

pcm_buffers = {
    nid: deque(maxlen=CONFIG["sr"] * 5)
    for nid in VALID_NODE_IDS
}

last_node_seen = {
    nid: 0.0 for nid in VALID_NODE_IDS
}

last_event_time = 0.0

last_result = {
    "node_id": "-",
    "zone_id": "-",
    "label": "Background",
    "conf": 1.0,
    "location_conf": 0.0,
    "probs": [1.0, 0.0, 0.0, 0.0]
}


# =========================================================
# 5. PCM Sound Classification Model
# =========================================================
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


sound_model = SingleMicAudioCNN(4).to(CONFIG["device"])
sound_model_loaded = False

try:
    sound_model.load_state_dict(
        torch.load(
            CONFIG["sound_model_path"],
            map_location=CONFIG["device"],
            weights_only=True
        )
    )
    sound_model.eval()
    sound_model_loaded = True
    print(f"✅ Sound model loaded: {CONFIG['sound_model_path']}")
except TypeError:
    try:
        sound_model.load_state_dict(
            torch.load(
                CONFIG["sound_model_path"],
                map_location=CONFIG["device"]
            )
        )
        sound_model.eval()
        sound_model_loaded = True
        print(f"✅ Sound model loaded: {CONFIG['sound_model_path']}")
    except Exception as e:
        print(f"⚠️ Sound model file missing or load failed: {e}")
except Exception as e:
    print(f"⚠️ Sound model file missing or load failed: {e}")


mel_trans = T.MelSpectrogram(
    sample_rate=CONFIG["sr"],
    n_fft=1024,
    hop_length=256,
    n_mels=64
)

amp_db = T.AmplitudeToDB()


# =========================================================
# 6. dB Location Classification Model
# =========================================================
class DBLocationMLP(nn.Module):
    def __init__(self, input_dim, num_classes):
        super().__init__()

        self.net = nn.Sequential(
            nn.Linear(input_dim, 256),
            nn.BatchNorm1d(256),
            nn.ReLU(),
            nn.Dropout(0.3),

            nn.Linear(256, 128),
            nn.BatchNorm1d(128),
            nn.ReLU(),
            nn.Dropout(0.3),

            nn.Linear(128, 64),
            nn.ReLU(),

            nn.Linear(64, num_classes)
        )

    def forward(self, x):
        return self.net(x)


location_model = None
location_model_loaded = False
location_scaler = None
location_feature_cols = None
location_zone_ids = CONFIG["zone_ids"]


def load_location_model():
    global location_model
    global location_model_loaded
    global location_scaler
    global location_feature_cols
    global location_zone_ids

    try:
        if not os.path.exists(CONFIG["location_model_path"]):
            raise FileNotFoundError(CONFIG["location_model_path"])

        if not os.path.exists(CONFIG["location_scaler_path"]):
            raise FileNotFoundError(CONFIG["location_scaler_path"])

        scaler_bundle = joblib.load(CONFIG["location_scaler_path"])

        location_scaler = scaler_bundle["scaler"]
        location_feature_cols = scaler_bundle["feature_cols"]
        location_zone_ids = scaler_bundle.get("zone_ids", CONFIG["zone_ids"])

        try:
            ckpt = torch.load(
                CONFIG["location_model_path"],
                map_location=CONFIG["device"],
                weights_only=False
            )
        except TypeError:
            ckpt = torch.load(
                CONFIG["location_model_path"],
                map_location=CONFIG["device"]
            )

        input_dim = ckpt["input_dim"]
        num_classes = ckpt["num_classes"]

        location_model = DBLocationMLP(
            input_dim=input_dim,
            num_classes=num_classes
        ).to(CONFIG["device"])

        location_model.load_state_dict(ckpt["model_state_dict"])
        location_model.eval()

        location_model_loaded = True

        print(f"✅ Location model loaded: {CONFIG['location_model_path']}")
        print(f"✅ Location scaler loaded: {CONFIG['location_scaler_path']}")
        print(f"✅ Location zones: {location_zone_ids}")

    except Exception as e:
        location_model_loaded = False
        print(f"⚠️ Location model/scaler load failed: {e}")


load_location_model()


# =========================================================
# 7. Networking
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
                db_val = float(msg[2])

                if nid in VALID_NODE_IDS:
                    with state_lock:
                        latest_db[nid] = db_val
                        last_node_seen[nid] = time.time()

        except Exception:
            pass


def db_sampler():
    """
    dB 위치 모델 추론용 시계열 저장.
    학습 데이터 수집 코드와 동일하게 일정 간격으로 latest_db를 저장한다.
    """
    while running:
        with state_lock:
            for nid in VALID_NODE_IDS:
                db_history[nid].append(latest_db[nid])

        time.sleep(CONFIG["db_sample_interval"])


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

            if magic == 0x4850434D and nid in VALID_NODE_IDS:
                samples = np.frombuffer(packet[hdr.size:], dtype="<i2")

                with state_lock:
                    pcm_buffers[nid].extend(samples.tolist())
                    last_node_seen[nid] = time.time()

        except Exception:
            pass


# =========================================================
# 8. Logic
# =========================================================
def are_all_nodes_connected():
    now = time.time()

    with state_lock:
        for nid in VALID_NODE_IDS:
            if now - last_node_seen[nid] > CONFIG["node_timeout_sec"]:
                return False

    return True


def update_led_status(label_name=None):
    all_connected = are_all_nodes_connected()
    led_hw.set_nodes_connected(all_connected)

    if label_name is None:
        with state_lock:
            label_name = last_result["label"]

    if label_name == "Background":
        led_hw.set_background()
    else:
        led_hw.set_noise()


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


def get_required_db_len_from_feature_cols(feature_cols):
    max_t = 0

    for col in feature_cols:
        m = re.search(r"_t(\d+)$", col)
        if m:
            max_t = max(max_t, int(m.group(1)))

    return max_t + 1


def get_fixed_length_db(seq, target_len):
    arr = list(seq)

    if len(arr) >= target_len:
        arr = arr[-target_len:]
    else:
        pad_len = target_len - len(arr)
        arr = [0.0] * pad_len + arr

    return arr


def build_location_feature_vector():
    """
    학습 때 저장된 feature_cols 순서와 똑같은 순서로
    node1_t0, node1_t1 ... node4_t49 형태의 벡터를 만든다.
    """
    if location_feature_cols is None:
        return None

    target_len = get_required_db_len_from_feature_cols(location_feature_cols)

    with state_lock:
        history_snapshot = {
            nid: get_fixed_length_db(db_history[nid], target_len)
            for nid in VALID_NODE_IDS
        }

    feature_values = []

    for col in location_feature_cols:
        m = re.match(r"node(\d+)_t(\d+)$", col)

        if not m:
            feature_values.append(0.0)
            continue

        nid = int(m.group(1))
        tidx = int(m.group(2))

        if nid in history_snapshot and tidx < len(history_snapshot[nid]):
            feature_values.append(history_snapshot[nid][tidx])
        else:
            feature_values.append(0.0)

    x = np.array(feature_values, dtype=np.float32).reshape(1, -1)
    return x


def run_location_inference():
    """
    dB 위치 모델로 Zone 1~6 중 하나를 예측한다.
    실패하면 "-" 반환.
    """
    if not location_model_loaded:
        return "-", 0.0

    x = build_location_feature_vector()

    if x is None:
        return "-", 0.0

    x_scaled = location_scaler.transform(x)
    x_tensor = torch.tensor(x_scaled, dtype=torch.float32).to(CONFIG["device"])

    with torch.no_grad():
        out = location_model(x_tensor)
        probs = torch.softmax(out, dim=1)[0]
        pred_idx = probs.argmax().item()
        conf = probs[pred_idx].item()

    if pred_idx < len(location_zone_ids):
        zone_id = location_zone_ids[pred_idx]
    else:
        zone_id = pred_idx + 1

    return zone_id, conf


def run_sound_inference(node_id):
    """
    PCM 모델로 소리 종류를 예측한다.
    """
    if not sound_model_loaded:
        return "Background", 0.0, [1.0, 0.0, 0.0, 0.0]

    with state_lock:
        samples = list(pcm_buffers[node_id])

    t = prepare_waveform_from_pcm(samples)

    with torch.no_grad():
        mel = mel_trans(t)
        mel_db = amp_db(mel)
        out = sound_model(mel_db)
        probs = torch.softmax(out, dim=1)[0]

        idx = probs.argmax().item()
        conf = probs[idx].item()

        final_idx = idx if conf >= CONFIG["conf_threshold"] else 0
        label_name = CONFIG["labels"][final_idx]

    return label_name, conf, probs.tolist()


def run_inference(node_id):
    """
    1. 가장 dB가 큰 노드의 PCM으로 소리 종류 분류
    2. 4개 노드의 dB 시계열로 Zone 1~6 위치 추론
    """
    global last_result

    time.sleep(1.0)

    label_name, sound_conf, sound_probs = run_sound_inference(node_id)
    zone_id, location_conf = run_location_inference()

    lcd_hw.write_status(label_name, zone_id)
    update_led_status(label_name)

    with state_lock:
        last_result = {
            "node_id": node_id,
            "zone_id": zone_id,
            "label": label_name,
            "conf": sound_conf,
            "location_conf": location_conf,
            "probs": sound_probs
        }


# =========================================================
# 9. Compact UI
# =========================================================
def draw_bar(surf, x, y, w, h, val, color):
    pygame.draw.rect(surf, (40, 45, 50), (x, y, w, h), border_radius=4)

    if val > 0:
        pygame.draw.rect(
            surf,
            color,
            (x, y, int(w * min(1, val)), h),
            border_radius=4
        )


def main():
    global SELECTED_NODE_ID
    global last_event_time
    global running

    pygame.init()
    screen = pygame.display.set_mode((1200, 700))
    pygame.display.set_caption("AI Sound Surveillance System")
    clock = pygame.time.Clock()

    f_title = pygame.font.SysFont("Arial", 22, bold=True)
    f_h1 = pygame.font.SysFont("Arial", 16, bold=True)
    f_lcd = pygame.font.SysFont("Courier New", 30, bold=True)
    f_body = pygame.font.SysFont("Arial", 14)
    f_grid = pygame.font.SysFont("Arial", 28, bold=True)

    threading.Thread(target=db_listener, daemon=True).start()
    threading.Thread(target=db_sampler, daemon=True).start()
    threading.Thread(target=pcm_listener, daemon=True).start()

    while running:
        screen.fill((10, 12, 15))

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            if event.type == pygame.KEYDOWN and event.unicode in "1234":
                SELECTED_NODE_ID = int(event.unicode)

        with state_lock:
            db_snap = dict(latest_db)
            res_snap = dict(last_result)

            if any(db_snap.values()):
                max_nid = max(db_snap, key=db_snap.get)
            else:
                max_nid = 1

        # B LED는 추론 여부와 상관없이 항상 연결 상태 반영
        update_led_status()

        # dB 트리거 발생 시:
        # 가장 큰 dB를 가진 노드의 PCM으로 소리 분류
        # 동시에 dB 위치 모델로 Zone 1~6 추론
        if (
            db_snap[max_nid] >= CONFIG["trigger_db"]
            and (time.time() - last_event_time) > 3.0
        ):
            last_event_time = time.time()
            threading.Thread(
                target=run_inference,
                args=(max_nid,),
                daemon=True
            ).start()

        # =================================================
        # LEFT: Monitoring
        # =================================================
        pygame.draw.rect(
            screen,
            (25, 30, 40),
            (20, 20, 440, 660),
            border_radius=15
        )

        screen.blit(
            f_title.render("SYSTEM ANALYTICS", True, (255, 255, 255)),
            (40, 40)
        )

        for i, nid in enumerate(VALID_NODE_IDS):
            y = 100 + i * 75
            is_sel = (nid == SELECTED_NODE_ID)

            pygame.draw.rect(
                screen,
                (35, 45, 60) if is_sel else (30, 35, 45),
                (40, y, 400, 60),
                border_radius=10
            )

            if is_sel:
                pygame.draw.rect(
                    screen,
                    (0, 160, 255),
                    (40, y, 400, 60),
                    2,
                    border_radius=10
                )

            screen.blit(
                f_h1.render(f"NODE {nid}", True, (200, 200, 200)),
                (55, y + 8)
            )

            draw_bar(
                screen,
                55,
                y + 35,
                300,
                10,
                db_snap[nid] / 80,
                (0, 255, 150) if db_snap[nid] < CONFIG["trigger_db"] else (255, 80, 80)
            )

            screen.blit(
                f_body.render(f"{db_snap[nid]:.1f} dB", True, (255, 255, 255)),
                (365, y + 30)
            )

        # Probabilities
        for idx, label in CONFIG["labels"].items():
            y_p = 440 + idx * 45

            screen.blit(
                f_body.render(label, True, (200, 200, 200)),
                (40, y_p)
            )

            draw_bar(
                screen,
                140,
                y_p + 5,
                250,
                12,
                res_snap["probs"][idx],
                (0, 160, 255)
            )

            screen.blit(
                f_body.render(f"{res_snap['probs'][idx] * 100:.0f}%", True, (150, 150, 150)),
                (400, y_p)
            )

        # =================================================
        # RIGHT: LCD
        # =================================================
        lcd_rect = pygame.Rect(480, 20, 700, 130)

        pygame.draw.rect(screen, (5, 20, 5), lcd_rect, border_radius=10)
        pygame.draw.rect(screen, (60, 70, 60), lcd_rect, 2, border_radius=10)

        status_c = (
            (80, 220, 130)
            if res_snap["label"] != "Background"
            else (80, 100, 80)
        )

        screen.blit(
            f_body.render("PHYSICAL LCD FEEDBACK (0x27)", True, (0, 150, 0)),
            (500, 35)
        )

        screen.blit(
            f_lcd.render(f"> {res_snap['label'].upper()}", True, status_c),
            (500, 65)
        )

        screen.blit(
            f_body.render(
                f"ZONE: {res_snap['zone_id']} | "
                f"SOUND CONF: {res_snap['conf'] * 100:.1f}% | "
                f"LOC CONF: {res_snap['location_conf'] * 100:.1f}%",
                True,
                (0, 100, 0)
            ),
            (500, 110)
        )

        # =================================================
        # RIGHT: Zone Grid 2 x 3
        # 배치:
        # 1 2 3
        # 4 5 6
        # =================================================
        grid_x = 520
        grid_y = 190

        box_w = 190
        box_h = 150
        gap_x = 25
        gap_y = 25

        screen.blit(
            f_title.render("LOCATION GRID", True, (255, 255, 255)),
            (grid_x, grid_y - 40)
        )

        screen.blit(
            f_body.render("Zone layout: 1 2 3 / 4 5 6", True, (150, 150, 150)),
            (grid_x, grid_y - 15)
        )

        active_zone = res_snap["zone_id"]

        for i, zone_id in enumerate(CONFIG["zone_ids"]):
            r = i // 3
            c = i % 3

            bx = grid_x + c * (box_w + gap_x)
            by = grid_y + r * (box_h + gap_y)

            is_active = (
                active_zone == zone_id
                and res_snap["label"] != "Background"
            )

            base_color = (25, 30, 40)
            active_color = (90, 45, 35)

            pygame.draw.rect(
                screen,
                active_color if is_active else base_color,
                (bx, by, box_w, box_h),
                border_radius=12
            )

            if is_active:
                pygame.draw.rect(
                    screen,
                    (255, 220, 0),
                    (bx, by, box_w, box_h),
                    4,
                    border_radius=12
                )

            screen.blit(
                f_h1.render(f"ZONE {zone_id}", True, (120, 130, 140)),
                (bx + 15, by + 15)
            )

            zone_txt = f_grid.render(str(zone_id), True, (255, 255, 255))
            screen.blit(
                zone_txt,
                (
                    bx + box_w // 2 - zone_txt.get_width() // 2,
                    by + box_h // 2 - zone_txt.get_height() // 2
                )
            )

            if is_active:
                conf_txt = f_body.render(
                    f"LOC {res_snap['location_conf'] * 100:.1f}%",
                    True,
                    (255, 230, 120)
                )

                screen.blit(
                    conf_txt,
                    (
                        bx + box_w // 2 - conf_txt.get_width() // 2,
                        by + box_h - 35
                    )
                )

        pygame.display.flip()
        clock.tick(25)

    led_hw.cleanup()
    pygame.quit()


if __name__ == "__main__":
    main()