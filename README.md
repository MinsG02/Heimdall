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

1단계: 최초 브랜치 생성 (노트북)
처음 작업을 시작할 때 내 전용 작업 공간(gms)을 만드는 과정입니다.

브랜치 생성 및 이동

PowerShell

# -b 옵션은 '생성(branch)'과 '이동(checkout)'을 한 번에 합니다.
git checkout -b gms
원격 저장소(GitHub)에 내 브랜치 등록

PowerShell

# -u 옵션은 내 로컬 gms와 원격 gms를 연결(Tracking)해줍니다.
git push -u origin gms
2단계: 다른 컴퓨터로 가져오기 (데스크탑)
노트북에서 만든 gms가 데스크탑에는 아직 없습니다. 이걸 가져오는 방법입니다.

원격 정보 갱신 (가장 중요)

PowerShell

# GitHub에 무슨 일이 일어났는지(새 브랜치가 있는지) 목록을 받아옵니다.
git fetch origin
브랜치 확인 및 이동

PowerShell

# 원격에 있는 gms를 내 컴퓨터로 가져오면서 이동합니다.
git checkout gms
확인

PowerShell

# * gms 라고 되어 있는지 확인
git branch 
3단계: 작업하고 올리기 (일상 루틴)
코드를 수정하고 저장하는 반복 작업입니다.

작업 후 저장(Staging & Commit)

PowerShell

git add .
git commit -m "작업한 내용 설명"
내 브랜치에 올리기(Push)

PowerShell

# 이미 연결이 되어 있으므로 origin gms를 생략하고 그냥 git push만 해도 됨
git push origin gms
PR(Pull Request) 생성

GitHub 웹사이트에 가서 gms → main으로 PR 생성.

4단계: 메인(Main) 내용 내 브랜치(GMS)로 가져오기 (동기화)
이 부분이 질문하신 핵심입니다. 내가 gms에서 작업하는 동안, 다른 팀원이 main에 새로운 코드를 합쳤을 수 있습니다. 내 브랜치가 너무 뒤쳐지면 나중에 합칠 때 충돌(Conflict)이 납니다. 그래서 중간중간 main의 최신 내용을 내 gms로 가져와야 합니다.

순서대로 따라하세요:

일단 메인으로 이동

PowerShell

git checkout main
메인을 최신으로 업데이트 (GitHub -> 내 PC Main)

PowerShell

git pull origin main
다시 내 작업 브랜치로 이동

PowerShell

git checkout gms
메인의 내용을 내 브랜치에 합치기 (흡수)

PowerShell

# "main의 내용을 현재(gms)로 가져와서 합쳐라"
git merge main
(이때 터미널에 Vim 에디터가 열리면 :q 엔터 혹은 :wq 엔터를 쳐서 빠져나오면 됩니다. 혹은 충돌이 나면 코드를 수정하고 다시 commit 해야 합니다.)

최신화된 내 브랜치를 원격에 올리기

PowerShell

# 로컬에서 합쳤으니, 이것도 GitHub의 내 gms 브랜치에 알려줘야 함
git push origin gms
요약 치트시트 (복사해서 쓰세요)
상황 1: 다른 PC에서 내 브랜치 처음 가져올 때

git fetch origin → git checkout gms

상황 2: 작업하고 퇴근할 때

git add . → git commit -m "메시지" → git push origin gms

상황 3: 남들이 main 업데이트 했다고 할 때 (동기화)

git checkout main

git pull origin main

git checkout gms

git merge main

git push origin gms



