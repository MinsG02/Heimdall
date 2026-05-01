
import os
from pathlib import Path

import torch
import torchaudio


# =========================================================
# 설정
# =========================================================
CONFIG = {
    # 현재 폴더 안의 mp3 파일들을 자동으로 찾음
    "input_dir": ".",

    # 잘린 wav 저장 위치
    "output_dir": "dataset/Background",

    # PCM 모델 입력 길이에 맞춰 3초
    "segment_sec": 3.0,

    # 네 실시간 코드와 맞추기 위해 16000Hz
    "target_sr": 16000,

    # 마지막 조각이 이보다 짧으면 버림
    "min_last_sec": 2.5,

    # 파일 이름 앞부분
    "prefix": "bg",
}


# =========================================================
# 오디오 처리 함수
# =========================================================
def convert_to_mono(waveform):
    """
    stereo 또는 여러 채널이면 mono로 변환.
    shape: (channels, samples)
    """
    if waveform.shape[0] == 1:
        return waveform

    return waveform.mean(dim=0, keepdim=True)


def resample_if_needed(waveform, original_sr, target_sr):
    """
    샘플레이트가 다르면 16000Hz로 변환.
    """
    if original_sr == target_sr:
        return waveform

    resampler = torchaudio.transforms.Resample(
        orig_freq=original_sr,
        new_freq=target_sr
    )

    return resampler(waveform)


def safe_filename(name):
    """
    파일 이름에 문제될 수 있는 문자 정리.
    한글은 그대로 둠.
    """
    bad_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    for ch in bad_chars:
        name = name.replace(ch, "_")
    return name


def cut_one_audio_file(input_path, output_dir):
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n처리 중: {input_path.name}")

    # mp3/wav 등 로드
    waveform, sr = torchaudio.load(str(input_path))

    # mono 변환
    waveform = convert_to_mono(waveform)

    # 16000Hz 변환
    waveform = resample_if_needed(
        waveform,
        original_sr=sr,
        target_sr=CONFIG["target_sr"]
    )

    target_sr = CONFIG["target_sr"]
    segment_samples = int(CONFIG["segment_sec"] * target_sr)
    min_last_samples = int(CONFIG["min_last_sec"] * target_sr)

    total_samples = waveform.shape[1]
    total_sec = total_samples / target_sr

    print(f"전체 길이: {total_sec:.1f}초")
    print(f"저장 단위: {CONFIG['segment_sec']}초")

    saved_count = 0
    start = 0

    while start < total_samples:
        end = start + segment_samples
        chunk = waveform[:, start:end]

        # 마지막 조각이 너무 짧으면 버림
        if chunk.shape[1] < min_last_samples:
            break

        # 마지막 조각이 3초보다 조금 짧으면 0으로 채움
        if chunk.shape[1] < segment_samples:
            pad_len = segment_samples - chunk.shape[1]
            chunk = torch.nn.functional.pad(chunk, (0, pad_len))

        saved_count += 1

        base_name = safe_filename(input_path.stem)
        output_name = f"{CONFIG['prefix']}_{base_name}_{saved_count:06d}.wav"
        output_path = output_dir / output_name

        torchaudio.save(
            str(output_path),
            chunk,
            target_sr
        )

        start += segment_samples

    print(f"저장 완료: {saved_count}개")

    return saved_count


# =========================================================
# main
# =========================================================
def main():
    input_dir = Path(CONFIG["input_dir"])
    output_dir = Path(CONFIG["output_dir"])

    audio_files = []

    # 현재 폴더에서 mp3 파일 찾기
    for file in input_dir.iterdir():
        if file.suffix.lower() in [".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"]:
            audio_files.append(file)

    if len(audio_files) == 0:
        print("오디오 파일을 찾지 못했어.")
        print("현재 폴더에 mp3 파일이 있는지 확인해줘.")
        return

    print("==============================================")
    print("배경소음 3초 단위 자르기 시작")
    print("==============================================")
    print(f"입력 폴더: {input_dir.resolve()}")
    print(f"출력 폴더: {output_dir.resolve()}")
    print(f"찾은 파일 수: {len(audio_files)}개")
    print("==============================================")

    for f in audio_files:
        print(f"- {f.name}")

    total_saved = 0

    for file_path in audio_files:
        try:
            count = cut_one_audio_file(file_path, output_dir)
            total_saved += count
        except Exception as e:
            print(f"처리 실패: {file_path.name}")
            print(f"에러 내용: {e}")

    print("\n==============================================")
    print("전체 완료")
    print(f"총 저장된 wav 개수: {total_saved}개")
    print(f"저장 위치: {output_dir}")
    print("==============================================")


if __name__ == "__main__":
    main()
