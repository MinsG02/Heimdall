import os
import re
import glob
import random
from collections import Counter, defaultdict

import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
import torchaudio
import torchaudio.transforms as T

from sklearn.metrics import confusion_matrix, classification_report

# =========================================================
# 1. 환경 설정
# =========================================================
CONFIG = {
    "base_path": r"C:\Users\user\Desktop\원천데이터",
    "model_save_path": "sound_classification_model_v4_best.pt",

    "sr": 16000,
    "duration": 3,              # 입력 길이(초)
    "n_mels": 64,
    "n_fft": 1024,
    "hop_length": 256,

    "batch_size": 32,
    "lr": 1e-3,
    "epochs": 40,

    "device": "cuda" if torch.cuda.is_available() else "cpu",

    "labels": ["배경소음", "진공청소기소리", "걷는소리", "피아노연주소리"],

    # split 비율
    "train_ratio": 0.7,
    "val_ratio": 0.15,
    "test_ratio": 0.15,

    # DataLoader
    "num_workers": 0,   # Windows면 0 권장
    "pin_memory": torch.cuda.is_available(),

    # augmentation
    "use_augmentation": True,
    "noise_std": 0.0025,
    "gain_min": 0.85,
    "gain_max": 1.15,

    # reproducibility
    "seed": 42,
}


# =========================================================
# 2. 공통 유틸
# =========================================================
def set_seed(seed: int):
    random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def extract_group_id(file_path: str) -> str:
    """
    파일명에서 그룹 ID 추출

    규칙:
    1) AIHub 스타일
       N-10_220830_A_3_e_12306.wav
       -> N-10_220830_A_3_e

    2) 직접 만든 배경소음
       real_bg_0004.wav
       -> real_bg_0004 (그대로 그룹)

    3) 그 외 파일
       뒤에 _숫자 또는 -숫자 가 있으면 제거
    """
    stem = os.path.splitext(os.path.basename(file_path))[0]
    parts = stem.split("_")

    # AIHub 스타일
    if stem.startswith("N-") and len(parts) >= 6 and parts[-1].isdigit():
        return "_".join(parts[:-1])

    # 직접 만든 배경소음은 그대로 그룹 사용
    if stem.startswith("real_bg_"):
        return stem

    # 기타 일반 규칙
    m = re.match(r"^(.*?)(?:[_-]\d+)$", stem)
    if m:
        return m.group(1)

    return stem


def safe_load_audio(path: str, target_sr: int):
    waveform, sr = torchaudio.load(path)

    # mono 변환
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)

    # resample
    if sr != target_sr:
        waveform = T.Resample(sr, target_sr)(waveform)

    return waveform


def pad_or_crop(waveform: torch.Tensor, target_len: int, mode: str = "center") -> torch.Tensor:
    """
    waveform shape: [1, T]
    mode:
      - random : train
      - center : eval
    """
    cur_len = waveform.shape[1]

    if cur_len == target_len:
        return waveform

    if cur_len < target_len:
        pad_amount = target_len - cur_len
        left = pad_amount // 2
        right = pad_amount - left
        return F.pad(waveform, (left, right))

    # cur_len > target_len
    if mode == "random":
        start = random.randint(0, cur_len - target_len)
    else:
        start = (cur_len - target_len) // 2

    return waveform[:, start:start + target_len]


def add_simple_augmentation(waveform: torch.Tensor) -> torch.Tensor:
    """
    너무 과하지 않은 augmentation
    """
    gain = random.uniform(CONFIG["gain_min"], CONFIG["gain_max"])
    waveform = waveform * gain

    if CONFIG["noise_std"] > 0:
        noise = torch.randn_like(waveform) * CONFIG["noise_std"]
        waveform = waveform + noise

    waveform = torch.clamp(waveform, -1.0, 1.0)
    return waveform


# =========================================================
# 3. 샘플 목록 만들기
# =========================================================
def build_sample_list(root_dir):
    samples = []

    print("\n===== 파일 스캔 시작 =====")
    for label_idx, label_name in enumerate(CONFIG["labels"]):
        folder = os.path.join(root_dir, label_name)

        if not os.path.exists(folder):
            print(f"⚠️ 폴더 없음: {folder}")
            continue

        files = sorted(glob.glob(os.path.join(folder, "*.wav")))
        print(f"📂 {label_name}: {len(files)}개 발견")

        for path in files:
            group_id = f"{label_name}__{extract_group_id(path)}"
            samples.append({
                "path": path,
                "label_idx": label_idx,
                "label_name": label_name,
                "group_id": group_id,
            })

    print(f"총 파일 수: {len(samples)}")
    return samples


# =========================================================
# 4. 그룹 단위 분할
# =========================================================
def split_groups_stratified(samples, train_ratio=0.7, val_ratio=0.15, test_ratio=0.15, seed=42):
    """
    같은 group_id는 절대 다른 split에 가지 않게 분할
    """
    assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-8

    group_to_label = {}
    group_to_items = defaultdict(list)

    for item in samples:
        gid = item["group_id"]
        lbl = item["label_idx"]
        group_to_items[gid].append(item)

        if gid in group_to_label and group_to_label[gid] != lbl:
            raise ValueError(f"같은 group_id({gid})에 서로 다른 라벨이 섞였습니다.")
        group_to_label[gid] = lbl

    label_to_groups = defaultdict(list)
    for gid, lbl in group_to_label.items():
        label_to_groups[lbl].append(gid)

    rng = random.Random(seed)

    train_groups, val_groups, test_groups = set(), set(), set()

    for lbl, group_list in label_to_groups.items():
        rng.shuffle(group_list)
        n = len(group_list)

        if n == 1:
            print(f"⚠️ 라벨 {CONFIG['labels'][lbl]} 의 그룹 수가 1개뿐이라 train에만 배치됩니다.")
            train_groups.update(group_list)
            continue

        if n == 2:
            print(f"⚠️ 라벨 {CONFIG['labels'][lbl]} 의 그룹 수가 2개뿐이라 train/val만 분할합니다.")
            train_groups.add(group_list[0])
            val_groups.add(group_list[1])
            continue

        n_train = max(1, int(round(n * train_ratio)))
        n_val = max(1, int(round(n * val_ratio)))
        n_test = n - n_train - n_val

        if n_test < 1:
            if n_train > n_val and n_train > 1:
                n_train -= 1
            elif n_val > 1:
                n_val -= 1
            n_test = 1

        while n_train + n_val + n_test > n:
            if n_train >= n_val and n_train > 1:
                n_train -= 1
            elif n_val > 1:
                n_val -= 1
            else:
                n_test -= 1

        while n_train + n_val + n_test < n:
            n_train += 1

        train_part = group_list[:n_train]
        val_part = group_list[n_train:n_train + n_val]
        test_part = group_list[n_train + n_val:]

        train_groups.update(train_part)
        val_groups.update(val_part)
        test_groups.update(test_part)

    train_samples = [x for x in samples if x["group_id"] in train_groups]
    val_samples = [x for x in samples if x["group_id"] in val_groups]
    test_samples = [x for x in samples if x["group_id"] in test_groups]

    return train_samples, val_samples, test_samples


def print_split_stats(name, samples):
    count_by_label = Counter([x["label_name"] for x in samples])
    groups = set([x["group_id"] for x in samples])

    print(f"\n[{name}]")
    print(f"- 파일 수: {len(samples)}")
    print(f"- 그룹 수: {len(groups)}")
    for label in CONFIG["labels"]:
        print(f"  - {label}: {count_by_label.get(label, 0)}")


# =========================================================
# 5. 데이터셋
# =========================================================
class SingleMicSoundDataset(Dataset):
    def __init__(self, samples, train_mode=False):
        self.samples = []
        self.train_mode = train_mode
        self.target_len = CONFIG["sr"] * CONFIG["duration"]

        self.mel_transform = T.MelSpectrogram(
            sample_rate=CONFIG["sr"],
            n_fft=CONFIG["n_fft"],
            hop_length=CONFIG["hop_length"],
            n_mels=CONFIG["n_mels"]
        )
        self.amp_to_db = T.AmplitudeToDB()

        # 손상 파일은 미리 제외
        for item in samples:
            path = item["path"]
            try:
                info = torchaudio.info(path)
                if info.num_frames <= 0:
                    print(f"⚠️ 빈 파일 제외: {path}")
                    continue
                self.samples.append(item)
            except Exception as e:
                print(f"⚠️ 손상/로드불가 파일 제외: {path} | {e}")

        mode_name = "Train" if train_mode else "Eval"
        print(f"{mode_name} Dataset 유효 파일 수: {len(self.samples)}")

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        item = self.samples[idx]
        path = item["path"]
        label = item["label_idx"]

        waveform = safe_load_audio(path, CONFIG["sr"])

        if self.train_mode:
            waveform = pad_or_crop(waveform, self.target_len, mode="random")
            if CONFIG["use_augmentation"]:
                waveform = add_simple_augmentation(waveform)
        else:
            waveform = pad_or_crop(waveform, self.target_len, mode="center")

        mel = self.mel_transform(waveform)
        mel_db = self.amp_to_db(mel)

        return mel_db, label


# =========================================================
# 6. 모델
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


# =========================================================
# 7. 평가 함수
# =========================================================
def evaluate(model, loader, device, criterion=None):
    model.eval()

    correct, total = 0, 0
    all_trues, all_preds = [], []
    total_loss = 0.0

    if criterion is None:
        criterion = nn.CrossEntropyLoss()

    with torch.no_grad():
        for mels, labs in loader:
            mels = mels.to(device)
            labs = labs.to(device)

            outputs = model(mels)
            loss = criterion(outputs, labs)

            preds = outputs.argmax(dim=1)

            total_loss += loss.item()
            correct += (preds == labs).sum().item()
            total += labs.size(0)

            all_trues.extend(labs.cpu().tolist())
            all_preds.extend(preds.cpu().tolist())

    acc = 100.0 * correct / total if total > 0 else 0.0
    avg_loss = total_loss / max(1, len(loader))

    return avg_loss, acc, all_trues, all_preds


# =========================================================
# 8. 학습
# =========================================================
def train():
    set_seed(CONFIG["seed"])
    device = CONFIG["device"]

    samples = build_sample_list(CONFIG["base_path"])
    if len(samples) == 0:
        print("❌ 학습할 데이터가 없습니다. 경로와 폴더명을 확인해주세요.")
        return

    train_samples, val_samples, test_samples = split_groups_stratified(
        samples,
        train_ratio=CONFIG["train_ratio"],
        val_ratio=CONFIG["val_ratio"],
        test_ratio=CONFIG["test_ratio"],
        seed=CONFIG["seed"]
    )

    print_split_stats("TRAIN", train_samples)
    print_split_stats("VAL", val_samples)
    print_split_stats("TEST", test_samples)

    train_dataset = SingleMicSoundDataset(train_samples, train_mode=True)
    val_dataset = SingleMicSoundDataset(val_samples, train_mode=False)
    test_dataset = SingleMicSoundDataset(test_samples, train_mode=False)

    if len(train_dataset) == 0:
        print("❌ train 데이터가 0개입니다.")
        return
    if len(val_dataset) == 0:
        print("❌ val 데이터가 0개입니다. 그룹 수가 너무 적은지 확인하세요.")
        return

    train_loader = DataLoader(
        train_dataset,
        batch_size=CONFIG["batch_size"],
        shuffle=True,
        num_workers=CONFIG["num_workers"],
        pin_memory=CONFIG["pin_memory"]
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=CONFIG["batch_size"],
        shuffle=False,
        num_workers=CONFIG["num_workers"],
        pin_memory=CONFIG["pin_memory"]
    )
    test_loader = DataLoader(
        test_dataset,
        batch_size=CONFIG["batch_size"],
        shuffle=False,
        num_workers=CONFIG["num_workers"],
        pin_memory=CONFIG["pin_memory"]
    )

    # train 기준 class weight
    train_label_counts = Counter([x["label_idx"] for x in train_dataset.samples])
    weights = []
    for i in range(len(CONFIG["labels"])):
        weights.append(1.0 / max(1, train_label_counts.get(i, 1)))
    weights = torch.tensor(weights, dtype=torch.float32).to(device)

    model = SingleMicAudioCNN(num_classes=len(CONFIG["labels"])).to(device)
    criterion = nn.CrossEntropyLoss(weight=weights)
    optimizer = optim.Adam(model.parameters(), lr=CONFIG["lr"])

    best_val_acc = 0.0
    best_epoch = -1

    print(f"\n🔥 학습 시작: {CONFIG['labels']} ({device})")

    for epoch in range(CONFIG["epochs"]):
        model.train()
        train_loss = 0.0

        for mels, labs in train_loader:
            mels = mels.to(device)
            labs = labs.to(device)

            optimizer.zero_grad()
            outputs = model(mels)
            loss = criterion(outputs, labs)
            loss.backward()
            optimizer.step()

            train_loss += loss.item()

        val_loss, val_acc, _, _ = evaluate(model, val_loader, device)

        print(
            f"Epoch [{epoch+1}/{CONFIG['epochs']}] "
            f"Train Loss: {train_loss / max(1, len(train_loader)):.4f} | "
            f"Val Loss: {val_loss:.4f} | "
            f"Val Acc: {val_acc:.2f}%"
        )

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_epoch = epoch + 1
            torch.save(model.state_dict(), CONFIG["model_save_path"])
            print(f"⭐ Best model saved! (epoch={best_epoch}, val_acc={best_val_acc:.2f}%)")

    print(f"\n✅ 최종 학습 완료")
    print(f"최고 Val 정확도: {best_val_acc:.2f}% (epoch {best_epoch})")

    # -----------------------------------------------------
    # Best 모델 다시 로드 후 최종 평가
    # -----------------------------------------------------
    print("\n===== Best 모델 재로드 후 최종 평가 =====")
    best_model = SingleMicAudioCNN(num_classes=len(CONFIG["labels"])).to(device)
    best_model.load_state_dict(torch.load(CONFIG["model_save_path"], map_location=device))

    val_loss, val_acc, val_true, val_pred = evaluate(best_model, val_loader, device)
    print(f"\n[VAL] Loss: {val_loss:.4f} | Acc: {val_acc:.2f}%")
    print("--- VAL Classification Report ---")
    print(classification_report(val_true, val_pred, target_names=CONFIG["labels"], digits=4))
    print("--- VAL Confusion Matrix ---")
    print(confusion_matrix(val_true, val_pred))

    if len(test_dataset) > 0:
        test_loss, test_acc, test_true, test_pred = evaluate(best_model, test_loader, device)
        print(f"\n[TEST] Loss: {test_loss:.4f} | Acc: {test_acc:.2f}%")
        print("--- TEST Classification Report ---")
        print(classification_report(test_true, test_pred, target_names=CONFIG["labels"], digits=4))
        print("--- TEST Confusion Matrix ---")
        print(confusion_matrix(test_true, test_pred))
    else:
        print("\n[TEST] 데이터가 0개라 TEST 평가는 생략합니다.")


if __name__ == "__main__":
    train()