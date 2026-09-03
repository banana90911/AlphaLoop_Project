# 대시보드 화면 (React + Vite + Tailwind + ECharts)

`dashboard/api.py`가 내주는 JSON 위에 올라가는 읽기 전용 화면이다. 설계 정본은
[`docs/08-dashboard.md`](../../docs/08-dashboard.md).

## 띄우기

데모 데이터로 화면만 보려면 터미널 두 개를 쓴다.

```bash
# ① 1인용 PostgreSQL + 한 해치 가짜 기록 + 조회 API (비밀번호는 dev)
python -m dashboard.devserve          # --reset 을 붙이면 데이터를 다시 만든다

# ② 화면
cd dashboard/web && npm install && npm run dev   # http://localhost:5173
```

`dashboard/devserve.py`는 **개발 전용**이다. 실제 `.env`의 DB도, 매매 코어도 건드리지
않는다 — 임시 서버를 따로 띄우고 거기에만 쓴다.

실제 DB를 보려면 devserve 없이 API만 직접 띄운다.

```bash
uvicorn dashboard.api:app --port 8787
```

`vite.config.ts`가 `/api`를 `http://127.0.0.1:8787`로 넘긴다. 다른 곳을 보려면
`VITE_DEV_API`로 바꾼다.

## 구조

| 파일 | 맡은 것 |
| --- | --- |
| `src/api.ts` | 조회 API 계약의 타입 사본. 쓰기 요청은 로그인·로그아웃뿐 |
| `src/usePolling.ts` | 60초 재조회. 첫 조회만 로딩 상태를 낸다 |
| `src/format.ts` | 금액·비율·시각 표기(KST 고정) |
| `src/components/AccountPanel.tsx` | ① 나의 정보 |
| `src/components/EquityChart.tsx` | ② 수익 그래프 (ECharts) |
| `src/components/TradeReport.tsx` | ③ 거래 리포트 + 근거 펼침 |
| `src/components/AlertPanel.tsx` | ④ 오류·정지 |

## 못 박은 것

- **외부 요청 0.** ECharts는 npm 의존성으로 번들에 들어간다. 글꼴도 CDN에서 받지 않고
  기기에 있는 것만 쓴다.
- **차트 렌더러는 SVG.** 캔버스는 기기 화소 배율에 맞춰 미리 크게 그려 두고 줄이는
  방식이라 배율이 어긋나면 뭉개진다. SVG는 래스터화 단계가 없어 브라우저가 매번 화면
  해상도로 직접 그린다 — 배율을 맞출 필요 자체가 없다.
- **값이 오르면 빨강, 내리면 파랑.** 매수·매도 점은 이것과 다른 축이라 초록·빨강을 쓰고,
  입출금은 회색이다. 두 팔레트를 섞지 않는다.
- 한 페이지에 네 영역. 페이지 이동도 탭 전환도 없다.
- 60초마다 다시 조회하되 페이지를 새로 그리지 않는다. 확대해 둔 차트 위치도 유지된다.
- 보유는 상위 5줄만 펼치고 나머지는 "더보기"로 접는다.
- 읽기 전용이다. 정지 해제도 현금흐름 라벨 붙이기도 여기서 못 한다 —
  **무엇을 해야 하는지만 알려준다.**

## 배포

화면은 Vercel, API와 DB는 NCP 서버. 둘을 잇는 길은 Tailscale Funnel(08-dashboard 8.5,
10-operations 10.12).

```
브라우저 ──https──▶ Vercel(화면) ──https──▶ Tailscale Funnel ──▶ NCP 서버
```

**`vercel.json`의 `CHANGE-ME.ts.net`을 실제 Funnel 호스트명으로 바꾸는 것이 배포의
전부다.** Vercel이 `/api/*`를 그 주소로 넘겨 주므로 브라우저에게는 화면과 API가 같은
주소로 보인다 — 교차 출처 차단에 걸리지 않고, 출입증 쿠키도 1차 쿠키가 되어 그냥 실려
간다. 그래서 화면 쪽 환경변수가 하나도 필요 없다(`VITE_API_BASE`는 비워 두면 같은 출처).

```bash
npm run build       # 산출물은 dist/
```

서버 `.env`에는 로그인 설정만 있으면 된다.

```
DASHBOARD_PASSWORD_HASH=scrypt$...   # python -c "from dashboard import auth; print(auth.make_hash('...'))"
DASHBOARD_TOKEN_SECRET=<32바이트 이상>
```

`DASHBOARD_ALLOWED_ORIGINS`는 **비워 둔다.** 화면에서 Funnel 주소를 직접 부르는 방식으로
바꿀 때만 채우고, 그때는 쿠키가 `SameSite=none`이 되면서 전 구간 HTTPS가 강제된다.

`uvicorn`은 `127.0.0.1`에만 바인딩하고 **워커는 하나로 띄운다** — 로그인 잠금 횟수를
프로세스 메모리에서 세기 때문에 워커가 여럿이면 임계가 그만큼 느슨해진다(8.6).

```bash
uvicorn dashboard.api:app --host 127.0.0.1 --port 8787 --workers 1
```
