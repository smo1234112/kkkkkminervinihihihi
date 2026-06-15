# 미너비니 전략실 대시보드

스탁이지 전략실처럼 — 매매일자·시간, 종목, 매수가, 손절가, 현재 수익률을 토스(TDS) 스타일 UI로 보여주는 개인 대시보드.

## 매일 루틴 (미국장 마감 후 = 한국시간 아침)

```
python generate_data.py
```

- 첫 실행: 지난 10거래일을 소급해서 매매내역(보유/편출)을 자동 생성
- 이후: 그날의 신규 매수 신호 기록 + 보유종목 손절(-8%)·50일선 이탈 판정
- 결과는 `data.json`에 저장되고, `state.json`이 포지션 장부 역할

## 보는 방법

로컬: `index.html` 더블클릭이 아니라 **간이 서버로** 여세요 (fetch 보안 제한 때문):
```
python -m http.server 8000
```
→ 브라우저에서 http://localhost:8000

## 🤖 완전 자동화 (아무것도 안 눌러도 됨) — 권장

구조: GitHub Actions가 매일 아침(한국 06:30) 자동 실행 → 신호 계산 → 매매장부(state.json·data.json)
누적 커밋 → **Vercel이 그 커밋을 감지해 자동 재배포** → 사이트 갱신

> ⚠️ **가장 흔한 고장 지점:** Vercel을 "폴더 드래그"로 한 번만 올리면 GitHub와 끊겨 있어서,
> Actions가 매일 계산해도 사이트는 영원히 안 바뀐다(첫 배포 시점에 얼어붙음).
> 반드시 **Vercel을 GitHub 저장소에 git 연결(Import Git Repository)** 해야 매 커밋마다 자동 갱신된다.

최초 1회만 세팅:

1. https://github.com/new 에서 새 저장소 생성 (이름 예: minervini-dashboard, **Private** 가능)
2. 이 폴더의 파일 전체를 저장소에 업로드 (또는 git push)
   - `.github/workflows/daily.yml` 폴더 구조가 유지되어야 함
3. 저장소 → Settings → Actions → General → Workflow permissions →
   **"Read and write permissions"** 선택 후 Save (봇이 data.json을 커밋할 수 있게)
4. https://vercel.com/new → **"Import Git Repository"** → 방금 만든 저장소 선택 → Deploy
   - ※ 폴더 드래그가 아니라 반드시 이 **Git 연결** 방식으로 해야 자동 재배포됨
5. 끝. 이후 매 거래일 아침 자동으로: 신호 계산 → 기록 누적 커밋 → Vercel 재배포 → 화면 갱신

수동으로 즉시 돌리고 싶을 때: GitHub 저장소 → Actions 탭 → daily-minervini-update → "Run workflow"

### (선택) 60일 자동멈춤 방지 — 진짜 손 안 대려면

GitHub은 "사람 활동"이 60일간 없으면 스케줄 워크플로를 자동으로 끈다. 봇 커밋은
기본 토큰(`GITHUB_TOKEN`)으로 하면 이 타이머를 리셋하지 못하므로, 약 2달 뒤 멈출 수 있다.
이를 막으려면 PAT(개인 액세스 토큰)를 시크릿으로 등록한다:

1. https://github.com/settings/tokens → "Fine-grained token" 생성 →
   해당 저장소에 **Contents: Read and write** 권한 부여
2. 저장소 → Settings → Secrets and variables → Actions → New repository secret →
   이름 `PAT`, 값에 토큰 붙여넣기

워크플로는 `PAT`가 있으면 그걸로 push(→ 사람 활동으로 인식 → 타이머 리셋), 없으면 기본 토큰으로 동작한다.

## 로컬 수동 실행 (자동화 안 쓸 경우)

매일 `python generate_data.py` 실행 후 Vercel 재배포 (`vercel --prod`) 또는
https://vercel.com/new 에 폴더 드래그앤드롭.

## 실제 매매와 동기화

- 실제로 산 종목/가격이 신호와 다르면 `state.json`의 positions에서 entry 값을 수정하세요
- 신호는 안 샀는데 기록된 경우: positions에서 해당 티커 삭제
- 수동 매도한 경우: positions에서 삭제 (또는 closed에 직접 기록)

## 전략 규칙 (A 미너비니 — 3년 백테스트 CAGR +37%, MDD -24.7%)

매수: 미너비니 8조건 + 직전 50일 고점 돌파 종가 + 거래량 50일평균 1.5배 (최대 10종목)
매도: 종가 기준 매수가 -8% 손절 또는 50일선 종가 이탈

※ 본 도구는 참고용이며 투자 손익의 책임은 본인에게 있습니다.
