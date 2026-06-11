"""시간대 정규화 (0-B 게이트, 11-2.7)."""
from core.timeutils import now_utc, session_label, to_kst


def test_now_utc_is_aware():
    assert now_utc().tzinfo is not None


def test_kst_offset_is_9h():
    assert to_kst(now_utc()).utcoffset().total_seconds() == 9 * 3600


def test_session_label_boundary():
    # KST 09시(=00:00Z) → morning, KST 14시(=05:00Z) → afternoon
    assert session_label("2026-06-11T00:00:00+00:00") == "morning"
    assert session_label("2026-06-11T05:00:00+00:00") == "afternoon"
    # 정오 경계: KST 12:00(=03:00Z)부터 afternoon
    assert session_label("2026-06-11T02:59:00+00:00") == "morning"
    assert session_label("2026-06-11T03:00:00+00:00") == "afternoon"
