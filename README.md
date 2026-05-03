# 📊 투찰전략 분석 시스템

## 설치 및 실행

### 1단계 - Python 설치
https://python.org 에서 Python 3.11 다운로드 후 설치
(설치 시 "Add Python to PATH" 반드시 체크)

### 2단계 - 라이브러리 설치
터미널(명령 프롬프트)에서 아래 명령어 실행:
```
pip install streamlit pandas numpy openpyxl xlrd
```

### 3단계 - 앱 실행
```
streamlit run bid_app.py
```
브라우저에서 자동으로 열립니다 (http://localhost:8501)

---

## 외부 공유 (Streamlit Cloud 무료 배포)

### 필요한 것
- GitHub 계정 (무료)
- Streamlit 계정 (무료)

### 단계
1. GitHub에서 새 저장소 생성
2. bid_app.py / requirements.txt 업로드
3. share.streamlit.io 접속 → Deploy
4. 생성된 URL을 팀원에게 공유

### 비밀번호 설정 (배포자 페이지 보호)
Streamlit Cloud → Settings → Secrets 에서:
```
ADMIN_PWD = "원하는비밀번호"
```

---

## 사용 방법

### 배포자 (낙찰이력 관리)
1. 사이드바에서 "배포자 관리" 선택
2. 비밀번호 입력
3. 낙찰이력 xlsx 파일 업로드

### 일반 사용자 (전략 조회)
1. 사이드바에서 "투찰전략 분석" 선택
2. 입찰서류함 xls 파일 업로드
3. 자동 분석 결과 확인
4. 전략표 엑셀 다운로드

---

## 분석 방법론

| 구분 | 내용 |
|---|---|
| ①패턴 | 발주처 전체 이력 — 트렌드·자기상관·가중치 최적화 |
| ②유사표본 | 유사 용역명·기초금액 ±50% 범위 낙찰이력 |
| ③트렌드 | 발주처 최근 흐름 — 최신 건에 높은 가중치 |
| 권장구간 | 3가지 값의 평균 ± 표준편차×0.5 |

