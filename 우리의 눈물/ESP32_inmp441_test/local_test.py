import serial
import wave
import base64
import time

COM_PORT = 'COM4'       
BAUD_RATE = 921600      
SAMPLE_RATE = 16000     
RECORD_SECONDS = 10
OUTPUT_FILENAME = "tenstar_base64_clean.wav"

def record_audio():
    try:
        ser = serial.Serial(COM_PORT, BAUD_RATE)
        print(f"[{COM_PORT}] 연결됨 (Base64 모드)")
    except Exception as e:
        print(f"연결 실패: {e}")
        return

    frames = []
    # Base64로 받으면 패킷 개수가 아니라 시간으로 끊는 게 정확함
    start_time = time.time()
    
    print("--- 1초 후 녹음 시작 ---")
    time.sleep(1)
    ser.reset_input_buffer()
    print(">>> 말씀하세요! <<<")

    while (time.time() - start_time) < RECORD_SECONDS:
        if ser.in_waiting > 0:
            try:
                # 한 줄씩 읽음 (문자열이라서 readline 가능)
                line = ser.readline().strip()
                if len(line) > 0:
                    # 문자열 -> 오디오 데이터 변환
                    pcm_data = base64.b64decode(line)
                    frames.append(pcm_data)
            except:
                pass # 깨진 줄은 무시

    print("\n녹음 완료. 저장 중...")
    ser.close()

    raw_audio = b''.join(frames)
    with wave.open(OUTPUT_FILENAME, 'wb') as wf:
        wf.setnchannels(1) 
        wf.setsampwidth(2) 
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(raw_audio)

    print(f"저장 완료: {OUTPUT_FILENAME}")

if __name__ == "__main__":
    record_audio()