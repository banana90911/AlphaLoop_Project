# 스키마 변경 (Alembic)

아직 초기화하지 않았다. 지금은 `memory/schema.sql`을 `CREATE TABLE IF NOT EXISTS`로
다시 적용하는 방식이라, 표를 **더하는** 변경만 감당된다. 컬럼 타입 변경이나 삭제가
필요해지는 시점이 Alembic을 들일 때다(10-ops 10.12).

## 시작할 때

```bash
pip install alembic
alembic init memory/migrations
```

## 규칙

- **스키마 변경은 Alembic으로만 한다.** 순번이 붙은 변경 파일이 쌓이고, 어느 버전까지
  적용됐는지를 DB가 스스로 기록한다.
- **변경 관리에만 쓴다.** 조회·적재 쿼리는 지금처럼 직접 쓴 SQL 그대로다 — ORM을 두지
  않는다.
- 모든 변경은 되돌리는 쪽(backward)도 함께 쓴다. 롤백은 직전 git tag 복귀 +
  마이그레이션 backward다.
- 실행 **전에** 자동 백업(`ops/backup.py`), 실행 **후에** 정합성 체크(참조 무결성·필수
  컬럼 채움)를 돌린다. 스키마 변경이 포함된 배포는 이 둘을 통과한 뒤에만 진행한다.

설계 정본: `docs/10-operations.md` 10.12
