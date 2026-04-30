import cv2 as cv
import numpy as np
import time

# 1. 이미지 로드 (파일이 같은 폴더에 있어야 함)
img = cv.imread('window.jpg')

if img is None:
    print("이미지를 찾을 수 없습니다. 경로를 확인하세요.")
else:
    h, w, _ = img.shape
    
    # ---------------------------------------------------------
    # 구현 1: OpenCV 함수 사용 (BGR -> YCrCb -> BGR)
    # ---------------------------------------------------------
    start1 = time.time()
    
    # 변환
    ycrcb_cv = cv.cvtColor(img, cv.COLOR_BGR2YCrCb)
    # Y채널 추출 (흑백 영상 확인용)
    y_channel_cv, cr_cv, cb_cv = cv.split(ycrcb_cv)
    # 복원
    result_cv = cv.cvtColor(ycrcb_cv, cv.COLOR_YCrCb2BGR)
    
    end1 = time.time()
    opencv_time = end1 - start1

    # ---------------------------------------------------------
    # 구현 2: 수식으로 직접 구현 (Manual)
    # ---------------------------------------------------------
    # 결과 저장을 위한 빈 이미지 생성 (연산 위해 float32 사용)
    manual_ycrcb = np.zeros((h, w, 3), dtype=np.float32)
    manual_result = np.zeros((h, w, 3), dtype=np.uint8)
    
    start2 = time.time()
    
    # [주의] 이중 for문은 파이썬에서 매우 느리지만, 원리 이해를 위해 사용합니다.
    for y in range(h):
        for x in range(w):
            # OpenCV는 BGR 순서임
            b, g, r = img[y, x].astype(float)
            
            # (1) BGR -> YCrCb 변환 수식 (SDTV Computer System)
            Y  = 0.257 * r + 0.504 * g + 0.098 * b + 16
            Cb = -0.148 * r - 0.291 * g + 0.439 * b + 128
            Cr = 0.439 * r - 0.368 * g - 0.071 * b + 128
            manual_ycrcb[y, x] = [Y, Cr, Cb] # 저장 순서 주의
            
            # (2) YCrCb -> BGR 복원 수식
            # 결과값이 0~255를 벗어날 수 있으므로 np.clip으로 제한해줌
            r_res = 1.164 * (Y - 16) + 1.596 * (Cr - 128)
            g_res = 1.164 * (Y - 16) - 0.813 * (Cr - 128) - 0.391 * (Cb - 128)
            b_res = 1.164 * (Y - 16) + 2.018 * (Cb - 128)
            
            manual_result[y, x] = [np.clip(b_res, 0, 255), 
                                   np.clip(g_res, 0, 255), 
                                   np.clip(r_res, 0, 255)]
            
    end2 = time.time()
    manual_time = end2 - start2

    # ---------------------------------------------------------
    # 구현 3: 시간 비교 및 결과 출력
    # ---------------------------------------------------------
    print("opencv_time : {opencv_time:.4f}")
    print("manual_time : {manual_time:.4f}")

    # 결과 디스플레이
    cv.imshow('Original', img)
    cv.imshow('Y_Channel_OpenCV', y_channel_cv)
    cv.imshow('Recovered_OpenCV', result_cv)
    cv.imshow('Recovered_Manual', manual_result)

    cv.waitKey(0)
    cv.destroyAllWindows()