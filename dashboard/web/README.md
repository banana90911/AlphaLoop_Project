# 대시보드 화면 (React + Vite + Tailwind)

아직 만들지 않았다. 백엔드 계약(`dashboard/api.py`)이 먼저고, 화면은 그 위에 붙는다 —
화면을 통째로 갈아엎어도 백엔드는 그대로다(08-dashboard 8.3).

## 시작할 때

```bash
npm create vite@latest . -- --template react-ts
npm install -D tailwindcss @tailwindcss/vite
```

## 못 박은 것

- **외부 요청 0.** 차트 라이브러리는 화면을 만들면서 고르되(recharts·ECharts·uPlot 등),
  반드시 npm 의존성으로 번들에 포함한다. 실행 중에 외부에서 불러오지 않는다.
- 값이 오르면 빨강, 내리면 파랑으로 통일.
- 한 페이지에 네 영역을 놓고 스크롤만으로 전부 본다 — 페이지 이동도 탭 전환도 없다.
- 60초마다 다시 조회하되 페이지 전체를 새로 그리지 않고 바뀐 값만 다시 그린다.
- 보유가 20종목까지 가므로 기본은 상위 5줄만 펼치고 나머지는 "더보기"로 접는다.

## 배포

화면은 Vercel, API와 DB는 서버. 화면이 보내는 조회 요청은 Tailscale Funnel을 지나
서버로 간다 — 도메인을 사지 않고도 전 구간이 암호화된다(08-dashboard 8.5).

설계 정본: `docs/08-dashboard.md`
