# 서버 설치 절차

NCP 서버(`/opt/alphaloop`)에서 root로 실행한다. 코드는 `git pull`로 갱신되지만,
아래 파일들은 `/etc` 아래로 복사해야 적용된다 — 코드를 고쳐도 자동으로 반영되지 않는다.

## 1. 스케줄·로그 회전

```bash
cd /opt/alphaloop
mkdir -p /var/log/alphaloop /var/backups/alphaloop
chmod 750 /var/backups/alphaloop
cp ops/deploy/alphaloop.cron /etc/cron.d/alphaloop
chmod 644 /etc/cron.d/alphaloop
cp ops/deploy/alphaloop.logrotate /etc/logrotate.d/alphaloop
```

확인:

```bash
grep -vE '^\s*#|^\s*$|^[A-Z]+=' /etc/cron.d/alphaloop   # 4줄이 보여야 한다
logrotate -d /etc/logrotate.d/alphaloop >/dev/null && echo "회전 설정 OK"
```

**이 파일의 기본값은 드라이런이다** — 사이클이 집행 계획까지만 내고 주문을 내지 않는다.
다시 복사해도 실거래가 저절로 켜지지 않게 하려는 것이다.

```bash
# 실거래 켜기
sed -i 's|run_cycle.py |run_cycle.py --live |' /etc/cron.d/alphaloop
# 끄기
sed -i 's|run_cycle.py --live|run_cycle.py|' /etc/cron.d/alphaloop
# 지금 상태
grep run_cycle /etc/cron.d/alphaloop
```

완전히 멈추려면 그 줄 맨 앞에 `#`을 붙인다. `--live` 여부는 코드와 무관하므로
git·배포가 필요 없고, 고치는 즉시 다음 사이클부터 적용된다.

## 2. 대시보드 API

```bash
cp ops/deploy/alphaloop-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now alphaloop-api
systemctl status alphaloop-api --no-pager
```

Funnel은 화면 개발용 데모 포트(8787)를 향해 있을 수 있다. 운영 포트로 다시 건다:

```bash
tailscale funnel --bg --set-path / http://127.0.0.1:8000
tailscale funnel status
```

`8787`은 `dashboard/devserve.py`(가짜 데이터) 전용이다. 여기에 Funnel이 걸려 있으면
로컬에서 devserve를 띄우는 순간 가짜 데이터가 인터넷으로 나간다.

## 3. 동작 확인

```bash
cd /opt/alphaloop
./.venv/bin/python -c "
from memory.db import connect
from broker.kis_client import KISClient
print('DB:', connect().execute('select current_user').fetchone()['current_user'])
b = KISClient().fetch_balance()
print(f'계좌 예수금 {b.cash:,.0f}원 · 보유 {len(b.holdings)}종목')"

./.venv/bin/python run_daily_ingest.py --limit 20    # 배치 스모크
./.venv/bin/python run_cycle.py                      # 드라이런(주문 없음)
```
