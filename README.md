# 🛡️ Project Heimdall

> *풀이 자라는 소리까지 듣는 엄청난 청각*

---

## 📋 Git 협업 가이드

### 1. 🌅 작업 시작 전: 메인 코드 가져오기 (Sync)

남들이 작업한 최신 코드를 내 컴퓨터로 가져오는 단계입니다.

```bash
# 메인 브랜치로 이동
git checkout main

# 원격 저장소(GitHub)의 최신 main 내용을 당겨오기
git pull origin main
```

---

### 2. 🌿 내 브랜치 만들고 이동하기

내 작업을 시작할 격리된 공간을 만드는 단계입니다.

```bash
# 처음 만들 때 (feature/이니셜 형식)
git checkout -b feature/kms

# 이미 있는 내 브랜치로 이동할 때
git checkout feature/kms
```

---

### 3. 🔄 (중요) 메인 코드 내 브랜치에 합치기

내가 작업하는 동안 main이 바뀌었을 수도 있습니다.  
내 브랜치에도 최신 내용을 반영해줘야 충돌이 안 납니다.

```bash
# 내 브랜치(feature/kms)에 있는 상태에서 입력
git merge main
```

---

### 4. 💾 코드 저장하고 올리기 (Push)

내 브랜치에서 작업한 내용을 GitHub에 올리는 단계입니다.

```bash
# 변경된 파일 장바구니에 담기
git add .

# 로컬에 저장(커밋)하기 - 메시지는 명확하게!
git commit -m "마이크 센서 데이터 수신 기능 추가"

# 내 원격 브랜치(GitHub)로 쏘기
git push origin feature/kms
```

---

### 5. 💻 자리 옮겼을 때: 내 브랜치 내용 가져오기 (Pull)

집에서 하다가 학교 와서 작업하려는데, 내 브랜치 내용이 예전 것일 때 씁니다.

```bash
# 내 브랜치로 이동
git checkout feature/kms

# GitHub에 있는 내 브랜치 내용을 내 컴퓨터로 당겨오기
git pull origin feature/kms
```

---

### 6. 🌳 분기(브랜치) 상태 눈으로 확인하기

지금 브랜치들이 어떻게 갈라지고 합쳐졌는지 지하철 노선도처럼 보여줍니다.

```bash
git log --oneline --graph --all
```

---

## ⚡ 빠른 참조

| 상황 | 명령어 |
|------|--------|
| 최신 코드 가져오기 | `git pull origin main` |
| 새 브랜치 만들기 | `git checkout -b feature/이니셜` |
| 변경사항 올리기 | `git add .` → `git commit -m "메시지"` → `git push origin feature/이니셜` |
| 메인 변경사항 반영 | `git merge main` |
