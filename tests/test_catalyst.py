"""촉매 분석가 — 프롬프트 빌드·부분실패 (agents/catalyst). 실호출은 단위테스트 제외."""
from agents.catalyst import NewsBundle, _build_user, analyze


def test_build_user_format():
    bundles = [
        NewsBundle("005930", "삼성전자", ["영업이익 상회", "외국인 순매수"]),
        NewsBundle("000660", "SK하이닉스", ["HBM 증설"]),
    ]
    txt = _build_user(bundles)
    assert "[005930 삼성전자] 영업이익 상회 / 외국인 순매수" in txt
    assert "[000660 SK하이닉스] HBM 증설" in txt


def test_analyze_empty_returns_empty():
    assert analyze([]) == []


def test_analyze_skips_no_headline():
    # 헤드라인 없는 종목만 있으면 호출 없이 빈 리스트(call_json 미호출)
    assert analyze([NewsBundle("A", "에이", [])]) == []
