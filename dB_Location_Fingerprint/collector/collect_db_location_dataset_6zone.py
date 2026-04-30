import socket
import threading
import time
import csv
import os
from collections import deque
from datetime import datetime


# =========================================================
# 1. Config
# =========================================================
CONFIG = {
    # UDP 수신 설정
    "bind_ip": "0.0.0.0",
    "db_port": 3333,

    # 사용하는 노드 번호
    # 실제 dB를 보내는 장치 번호
    "node_ids": [1, 2, 3, 4],

    # 수집할 위치 Zone 번호
    # 배치:
    # 1 2 3
    # 4 5 6
    "zone_ids": [1, 2, 3, 4, 5, 6],

    # 한 샘플이 몇 초짜리 dB 패턴인지
    "sample_duration": 5.0,

    # 몇 초마다 dB를 기록할지
    # 0.1초 = 초당 10개 기록
    "sample_interval": 0.1,

    # 각 Zone마다 몇 개 샘플을 저장할지
    "samples_per_zone": 100,

    # 샘플 하나 저장 후 다음 샘플 저장까지 쉬는 시간
    "gap_between_samples": 0.5,

    # 저장할 CSV 파일명
    "csv_path": "location_db_dataset.csv",
}


# =========================================================
# 2. Global State
# =========================================================
running = True
state_lock = threading.Lock()

latest_db = {
    nid: 0.0 for nid in CONFIG["node_ids"]
}

db_history = {
    nid: deque(
        maxlen=int(CONFIG["sample_duration"] / CONFIG["sample_interval"]) + 20
    )
    for nid in CONFIG["node_ids"]
}


# =========================================================
# 3. UDP dB Listener
# =========================================================
def db_listener():
    """
    노드에서 UDP로 보내는 dB 값을 계속 받는 함수.
    예상 UDP 형식:
    NODE_1, ..., 52.3

    기존 코드 기준:
    msg[0] = NODE_1
    msg[2] = dB 값
    """
    global running

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((CONFIG["bind_ip"], CONFIG["db_port"]))

    print("✅ dB UDP listener started")
    print(f"   bind_ip = {CONFIG['bind_ip']}")
    print(f"   port    = {CONFIG['db_port']}")

    while running:
        try:
            data, _ = sock.recvfrom(1024)
            text = data.decode().strip()
            msg = text.split(",")

            if len(msg) >= 3:
                nid_text = msg[0].replace("NODE_", "")
                nid = int(nid_text)
                db_val = float(msg[2])

                if nid in latest_db:
                    with state_lock:
                        latest_db[nid] = db_val

        except Exception as e:
            if running:
                print(f"⚠️ dB receive error: {e}")

    sock.close()


# =========================================================
# 4. dB Sampler
# =========================================================
def db_sampler():
    """
    latest_db 값을 일정한 시간 간격으로 db_history에 저장.
    UDP 패킷이 불규칙하게 와도 CSV 데이터는 일정 간격 시계열로 저장하기 위함.
    """
    global running

    while running:
        with state_lock:
            for nid in CONFIG["node_ids"]:
                db_history[nid].append(latest_db[nid])

        time.sleep(CONFIG["sample_interval"])


# =========================================================
# 5. CSV Utility
# =========================================================
def init_csv():
    csv_path = CONFIG["csv_path"]
    expected_len = int(CONFIG["sample_duration"] / CONFIG["sample_interval"])

    if os.path.exists(csv_path):
        print(f"✅ Existing CSV found: {csv_path}")
        return

    header = [
        "sample_id",
        "label_zone",
        "created_at",
        "sample_duration",
        "sample_interval",
    ]

    for nid in CONFIG["node_ids"]:
        for t in range(expected_len):
            header.append(f"node{nid}_t{t}")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)

    print(f"✅ New CSV created: {csv_path}")


def append_csv(row):
    with open(CONFIG["csv_path"], "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def get_fixed_length_db(seq, target_len):
    """
    dB 시계열 길이를 target_len으로 맞춤.
    부족하면 앞쪽을 0.0으로 패딩.
    길면 가장 최근 target_len개만 사용.
    """
    arr = list(seq)

    if len(arr) >= target_len:
        arr = arr[-target_len:]
    else:
        pad_len = target_len - len(arr)
        arr = [0.0] * pad_len + arr

    return arr


# =========================================================
# 6. Save One Sample
# =========================================================
def save_one_sample(label_zone, sample_index):
    expected_len = int(CONFIG["sample_duration"] / CONFIG["sample_interval"])

    with state_lock:
        snapshot = {
            nid: get_fixed_length_db(db_history[nid], expected_len)
            for nid in CONFIG["node_ids"]
        }

    sample_id = f"zone{label_zone}_{sample_index:05d}"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row = [
        sample_id,
        label_zone,
        created_at,
        CONFIG["sample_duration"],
        CONFIG["sample_interval"],
    ]

    for nid in CONFIG["node_ids"]:
        row.extend(snapshot[nid])

    append_csv(row)

    avg_db = {
        nid: sum(snapshot[nid]) / len(snapshot[nid])
        for nid in CONFIG["node_ids"]
    }

    avg_text = " | ".join(
        [f"N{nid}={avg_db[nid]:.1f} dB" for nid in CONFIG["node_ids"]]
    )

    print(
        f"✅ Saved {sample_id} | "
        f"label=Zone {label_zone} | "
        f"{avg_text}"
    )


# =========================================================
# 7. Collection Mode
# =========================================================
def wait_until_history_ready():
    """
    수집 시작 직후에는 history가 비어 있을 수 있으므로,
    sample_duration만큼 dB가 쌓일 때까지 잠깐 기다림.
    """
    expected_len = int(CONFIG["sample_duration"] / CONFIG["sample_interval"])

    while running:
        with state_lock:
            ready = all(
                len(db_history[nid]) >= expected_len
                for nid in CONFIG["node_ids"]
            )

        if ready:
            break

        print("⏳ dB history filling...")
        time.sleep(1.0)


def collect_zone(label_zone):
    print()
    print("=" * 60)
    print(f"📍 Zone {label_zone} 데이터 수집 시작")
    print()
    print("Zone 배치:")
    print("1  2  3")
    print("4  5  6")
    print()
    print(f"스피커/소리 발생 장치를 Zone {label_zone} 위치에 둬.")
    print(f"한 샘플 길이: {CONFIG['sample_duration']}초")
    print(f"샘플 개수: {CONFIG['samples_per_zone']}개")
    print("3초 뒤부터 수집 시작")
    print("=" * 60)
    print()

    time.sleep(3)

    wait_until_history_ready()

    for i in range(1, CONFIG["samples_per_zone"] + 1):
        if not running:
            break

        save_one_sample(label_zone, i)
        time.sleep(CONFIG["gap_between_samples"])

    print()
    print(f"✅ Zone {label_zone} 수집 완료")
    print()


# =========================================================
# 8. Monitor
# =========================================================
def print_live_db():
    """
    현재 들어오는 dB 값 확인용.
    수집이 제대로 되는지 보기 좋게 1초마다 출력.
    """
    global running

    while running:
        with state_lock:
            values = {
                nid: latest_db[nid]
                for nid in CONFIG["node_ids"]
            }

        live_text = " | ".join(
            [f"N{nid}={values[nid]:.1f} dB" for nid in CONFIG["node_ids"]]
        )

        print(f"[LIVE] {live_text}")

        time.sleep(1.0)


# =========================================================
# 9. Main
# =========================================================
def main():
    global running

    init_csv()

    threading.Thread(target=db_listener, daemon=True).start()
    threading.Thread(target=db_sampler, daemon=True).start()
    threading.Thread(target=print_live_db, daemon=True).start()

    print()
    print("==============================================")
    print("dB 기반 Zone 위치 분류 데이터 수집기")
    print("==============================================")
    print("Zone 배치:")
    print("1  2  3")
    print("4  5  6")
    print()
    print("1 입력: Zone 1 라벨로 수집")
    print("2 입력: Zone 2 라벨로 수집")
    print("3 입력: Zone 3 라벨로 수집")
    print("4 입력: Zone 4 라벨로 수집")
    print("5 입력: Zone 5 라벨로 수집")
    print("6 입력: Zone 6 라벨로 수집")
    print("q 입력: 종료")
    print("==============================================")
    print()

    valid_zone_cmds = [str(zid) for zid in CONFIG["zone_ids"]]

    while True:
        cmd = input("수집할 Zone 번호 입력 > ").strip().lower()

        if cmd == "q":
            running = False
            print("종료합니다.")
            time.sleep(0.5)
            break

        if cmd in valid_zone_cmds:
            collect_zone(int(cmd))
        else:
            print("⚠️ 1, 2, 3, 4, 5, 6, q 중 하나를 입력해줘.")


if __name__ == "__main__":
    main()