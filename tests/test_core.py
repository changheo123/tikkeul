# 핵심 규칙 단위 테스트: python -m pytest tests (pytest가 없으면 python tests/test_core.py)
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.common import period, find_dates
from collector.extract import build_event, pay_required, is_show_invite, norm_url
from collector import pipeline as P


def ev(title, url, end="2099-12-31", body="댓글 남기면 커피 기프티콘 10명 추첨", **kw):
    e, why = build_event(src="t", sid=url + title, title=title, url=url, body=body, end=dt.date.fromisoformat(end) if end else None, **kw)
    assert e, why
    return e


def test_period():
    assert period("2026. 10. 05.(월) ~ 10. 31.(토)") == (dt.date(2026, 10, 5), dt.date(2026, 10, 31))
    s, e = period("2026년 12월 20일 ~ 1월 5일")
    assert (s, e) == (dt.date(2026, 12, 20), dt.date(2027, 1, 5))
    assert find_dates("발표 11/4") == [dt.date(dt.date.today().year, 11, 4)]


def test_pay_filter():
    assert pay_required("제품 구매 후 리뷰 작성 시 증정")
    assert not pay_required("구매 없이 누구나 응모 가능, 추첨 증정")
    assert not pay_required("댓글 남기면 추첨")
    assert pay_required("상품 선결제 주문 후 리뷰 확인 완료 후 페이백 진행")  # 체험단 페이백형은 유료
    assert not pay_required("3만원 이용권 제공, 방문 후 블로그 리뷰")
    e, why = build_event(src="t", sid="1", title="구매 인증 이벤트", url="https://a.kr/1", body="3만원 이상 구매 고객 추첨")
    assert e is None and "유료" in why


def test_show_excluded():
    assert is_show_invite("연극 '알테르 에고' 초대 이벤트")
    e, why = build_event(src="t", sid="2", title="문화초대이벤트 뮤지컬 관람권 증정", url="https://a.kr/2", body="추첨")
    assert e is None and why == "공연초대"


def test_norm_url():
    assert norm_url("https://www.instagram.com/p/AbC/?hl=ko&img_index=1") == norm_url("https://instagram.com/p/AbC")
    assert norm_url("https://www.youtube.com/watch?v=x1&si=zz") == norm_url("https://youtube.com/watch?v=x1")


def test_dedupe_and_board_items_stay_separate():
    db = {"events": {}}
    st = {"added": 0, "merged": 0}
    a = ev("부산시보 다른그림 찾기", "https://busan.go.kr/EVTView?srchIdx=1")
    b = ev("독자 만족도조사 참여하고 기프티콘 받자", "https://busan.go.kr/EVTView?srchIdx=2")
    P.merge(db, a, "2026-10-27", st)
    P.merge(db, b, "2026-10-27", st)
    assert st == {"added": 2, "merged": 0}  # 같은 게시판의 다른 글은 합치지 않는다
    # 같은 원문 링크(추적 파라미터만 다름)는 하나로
    c = ev("다른 제목이어도", "https://www.busan.go.kr/EVTView?srchIdx=1&utm_source=x")
    assert P.merge(db, c, "2026-10-27", st) == a["id"] and st["merged"] == 1
    # 제목이 거의 같고 마감일이 같으면 하나로(모음 사이트에서 다시 발견)
    d = ev("[부산시] 부산시보 다른그림 찾기!", "https://events.kr/event.php?id=9")
    assert P.merge(db, d, "2026-10-27", st) == a["id"]
    # 제목이 같아도 마감일이 다르면 다른 회차
    e2 = ev("부산시보 다른그림 찾기", "https://busan.go.kr/EVTView?srchIdx=3", end="2099-11-30")
    assert P.merge(db, e2, "2026-10-27", st) != a["id"]


def test_similar_titles_from_different_orgs_stay_separate():
    db = {"events": {}}
    st = {"added": 0, "merged": 0}
    a = ev("퀴즈 풀고 커피 받자 이벤트!!", "https://a.kr/q1", org="서울시의회")
    b = ev("퀴즈 풀고 커피 받자 이벤트!!", "https://b.kr/q2", org="오븐마루치킨")
    c = ev("퀴즈 풀고 커피 받자 이벤트!!", "https://www.eventhouse.kr/site/view.php?no=1", org="서울특별시의회")
    assert P.merge(db, a, "2026-10-27", st) != P.merge(db, b, "2026-10-27", st)
    assert P.merge(db, c, "2026-10-27", st) == a["id"]  # 같은 주최(표기만 다름)는 합친다
    assert db["events"][a["id"]]["url"] == "https://a.kr/q1"  # 원문 링크가 대표로 남는다


def test_prize_value():
    from collector.enrich import prize_value
    cases = {"GS25 5천원권 500명": 5000, "배달의민족 5만원권 20명": 50000, "모바일 상품권 5,000원": 5000,
             "상품권 1만 원 50명": 10000, "온누리 1만5천원": 15000, "CU 모바일 금액권 5천원 1,000명": 5000, "커피 쿠폰": None}
    for p, v in cases.items():
        assert prize_value({"prize": p}) == v, p


def test_aggregator_prize_tidy():
    from collector.sources.aggregators import tidy_prize, numbered
    assert tidy_prize("GS25 모바일상품권 5천원권-20명") == "GS25 모바일상품권 5천원권 20명"
    assert tidy_prize("네이버페이-00명") == "네이버페이"  # 사이트가 가린 인원은 지운다
    assert tidy_prize("상품-200명") == "상품 200명"
    assert numbered("① 팔로우 ② 댓글") == ["1. 팔로우", "2. 댓글"]


def test_audit():
    db = {"events": {}}
    st = {"added": 0, "merged": 0}
    a = ev("SNS 팔로우하고 아메리카노 챙기자 이벤트!!", "https://a.kr/1", end="2026-10-28", org="전북특별자치도")
    b = ev("SNS 팔로우하고 아메리카노 챙기자 이벤트!!", "https://b.kr/2", end="2026-10-28", org="전북시")
    c = ev("퀴즈 맞히고 치킨세트 받아가자 이벤트", "https://c.kr/3", end="2027-08-31", org="치킨")
    d = ev("댓글 이벤트", "https://d.kr/4", end="2026-11-30", org="몰")
    for x in (a, b, c, d):
        db["events"][x["id"]] = dict(x, status="active")
    db["events"][d["id"]]["steps"] = ["상품 선결제 후 리뷰 확인되면 페이백"]  # 수집 뒤에 규칙이 강화된 경우
    db["events"][a["id"]]["linkStatus"] = "error SSLError"
    r = P.audit(db, "2026-10-27")
    assert db["events"][d["id"]]["status"] == "excluded"
    assert db["events"][c["id"]]["issues"] == ["마감일"]
    assert db["events"][a["id"]]["issues"] == ["원문 링크 접속"]
    assert [db["events"][x["id"]]["status"] for x in (a, b)].count("merged") == 1
    assert r == {"excluded": 1, "flagged": 2, "merged": 1}
    P.update_status(db, "2026-10-27", {})  # 종료 처리가 제외·병합 상태를 되돌리지 않는다
    assert db["events"][d["id"]]["status"] == "excluded"


def _el(**kw):
    from collector.electronics import classify
    return classify({"title": "", "kind": "댓글", **kw})


def test_electronics():
    assert _el(prize="애플워치 SE3(1명)&네이버페이 3만원권(12명)") == (["watch"], ["애플워치 SE3"])
    assert _el(prize="에어팟 프로 3 외 굿즈")[0] == ["audio"]
    assert _el(prize="맥북 에어 1명, 아이패드 미니 2명, PS5 3명")[0] == ["pc", "tablet", "console"]  # 여러 종류 중복 분류
    assert _el(prize="LG 그램 노트북")[0] == ["pc"]
    assert _el(prize="갤럭시 S26 울트라 1명")[0] == ["phone"]
    assert _el(prize="갤럭시 워치9 1명, 갤럭시 버즈4 프로 2명")[0] == ["audio", "watch"]
    assert _el(prize="로봇청소기 1명, 빔프로젝터 2명, 고프로 액션캠")[0] == ["other"]
    assert _el(prize="에어팟 2명 160명") == (["audio"], ["에어팟"])  # 인원을 세대로 읽지 않는다
    assert _el(prize="에어팟 Pro 3 1명")[1] == ["에어팟 Pro 3"]
    assert _el(prize="에어팟 5세대 2명")[1] == ["에어팟 5세대"]
    assert _el(prize="템플스테이 1박 2일 체험권")[0] == []
    # 모델이 없으면 만들지 않는다
    assert _el(prize="노트북 1명") == (["pc"], [])
    # 액세서리·이용권·시청 서비스·다른 뜻은 제외
    for p in ["아이폰 케이스 10명", "맥북 파우치", "노트북 가방 5명", "TV 시청권", "프로그램 100그램 세트", "모니터링 요원 기념품", "스타벅스 아메리카노"]:
        assert _el(prize=p)[0] == [], p
    # 체험단(리뷰 조건)은 추첨 경품이 아니다
    assert _el(kind="체험단", prize="에어팟 프로 3")[0] == []
    # 제목은 경품 칸이 불명확하고 기기 바로 뒤에 증정 표현이 있을 때만
    assert _el(title="맥북 받자 이벤트!!", prize="상품 1명")[0] == ["pc"]
    assert _el(title="갤럭시 GALAX 지포스 RTX 타자게임 이벤트!")[0] == []
    assert _el(title="갤럭시 Z 플립 출시 기념 퀴즈 풀고 커피쿠폰 받자", prize="커피 쿠폰 10명")[0] == []
    # 본문은 경품 안내 줄만
    assert _el(rawText="최우수(3명): 에어팟 프로 3 외 굿즈\n노트북으로 접속하세요")[0] == ["audio"]


def test_restriction_is_boolean_with_note():
    db = {"events": {"a": {"id": "a", "title": "양산시민과 함께하는 퀴즈", "tags": []}, "b": {"id": "b", "title": "부산시민만 참여 가능", "tags": []}}}
    P.classify_restrictions(db)
    a, b = db["events"]["a"], db["events"]["b"]
    assert a["is_restricted"] is False and a["restriction_reason"] is None and "확인 필요" in a["restriction_note"]  # 불확실은 확정하지 않음
    assert b["is_restricted"] is True and b["restriction_reason"]


def test_status_expiry():
    db = {"events": {}}
    st = {"added": 0, "merged": 0}
    old = ev("지난 이벤트", "https://a.kr/old", end="2026-10-20")
    noend = ev("2026년 9월 퀴즈 이벤트", "https://a.kr/m", end=None)
    live = ev("진행 이벤트", "https://a.kr/live", end="2026-11-30")
    for x in (old, noend, live):
        P.merge(db, x, "2026-10-27", st)
    P.update_status(db, "2026-10-27", {})
    s = {e["title"]: e["status"] for e in db["events"].values()}
    # 마감일 미상은 제목에 지난 달이 있어도 임의로 종료하지 않는다
    assert s == {"지난 이벤트": "ended", "2026년 9월 퀴즈 이벤트": "active", "진행 이벤트": "active"}


def test_status_kst_boundaries_and_no_reactivation():
    today = "2026-11-09"
    db = {"events": {k: {"id": k, "title": k, "end": v, "status": "active", "tags": [], "reasons": []} for k, v in
                     {"어제": "2026-11-08", "오늘": "2026-11-09", "미래": "2026-11-30", "미상": None}.items()}}
    db["events"]["종료유지"] = {"id": "종료유지", "title": "x", "end": "2026-12-31", "status": "ended", "tags": [], "reasons": []}
    db["events"]["미상종료"] = {"id": "미상종료", "title": "y", "end": None, "status": "ended", "lastSeen": today, "tags": [], "reasons": []}
    P.update_status(db, today, {})
    assert {k: e["status"] for k, e in db["events"].items()} == {
        "어제": "ended", "오늘": "active", "미래": "active", "미상": "active", "종료유지": "ended", "미상종료": "ended"}
    # 종료된 이벤트가 다시 수집돼도 되살아나지 않는다
    st = {"added": 0, "merged": 0}
    again = ev("x 다시 수집", "https://a.kr/again")
    db["events"]["종료유지"]["url"] = "https://a.kr/again"
    db["events"]["종료유지"]["srcUrls"] = ["https://a.kr/again"]
    assert P.merge(db, again, today, st) == "종료유지"
    P.update_status(db, today, {})
    assert db["events"]["종료유지"]["status"] == "ended"


def test_normalize_dates():
    from collector.common import norm_date
    assert norm_date("2026.11.20") == "2026-11-20"
    assert norm_date("2026/1/5") == "2026-01-05"
    assert norm_date("2026년 11월 20일") == "2026-11-20"
    assert norm_date("26. 10. 29.") == "2026-10-29"
    assert norm_date("상시") is None and norm_date("11월 말") is None
    db = {"events": {"a": {"id": "a", "title": "t", "end": "상시", "status": "active", "tags": [], "reasons": []}}}
    P.normalize_dates(db)
    P.update_status(db, "2026-11-09", {})
    assert db["events"]["a"]["end"] is None and db["events"]["a"]["endRaw"] == "상시" and db["events"]["a"]["status"] == "active"


def test_form_extraction():
    from collector import forms as F
    d, s = F.candidates('참여: form.naver.com/response/AbC-1, <a href="https://docs.google.com/forms/d/e/1FAIpQ/viewform?usp=sf&amp;x=1">폼</a>')
    assert d == ["https://form.naver.com/response/AbC-1", "https://docs.google.com/forms/d/e/1FAIpQ/viewform"]
    d, s = F.candidates("설문 moaform.com/q/Xy12). 또는 https://abc.smore.im/form/QwEr1!")
    assert d == ["https://moaform.com/q/Xy12", "https://abc.smore.im/form/QwEr1"]
    d, s = F.candidates("https://forms.gle/AAA https://forms.gle/AAA naver.me/5AuSucrk.")
    assert d == [] and s == ["https://forms.gle/AAA", "https://naver.me/5AuSucrk"]
    # 편집 화면 주소, 다른 사이트에 끼어든 글자는 폼이 아니다
    assert F.candidates("https://docs.google.com/forms/d/1U9U/edit")[0] == []
    assert F.candidates("https://example.com/naver.me/abc notforms.gle/abc") == ([], [])
    assert F.candidates("?go=https%3A%2F%2Fforms.gle%2FBBB")[1] == ["https://forms.gle/BBB"]
    assert F.pick("링크 없음", []) == (None, [])
    # 긴 주소 폼이 있으면 단축 링크보다 우선
    assert F.pick("naver.me/a form.naver.com/response/b", [])[0] == "https://form.naver.com/response/b"


def test_short_link_verification():
    from collector import forms as F

    class R:
        def __init__(self, code, loc=None):
            self.status_code, self.headers = code, {"Location": loc} if loc else {}

    class Fake:
        routes = {"https://naver.me/form": R(302, "https://form.naver.com/response/ok"),
                  "https://naver.me/blog": R(302, "https://blog.naver.com/x/1"),
                  "https://forms.gle/g": R(302, "https://docs.google.com/forms/d/e/ID/viewform?usp=send_form")}

        def get(self, url, **kw):
            if url == "https://naver.me/down":
                raise RuntimeError("timeout")
            return self.routes[url]

    c, f, t = {}, Fake(), "2026-11-09"
    assert F.resolve_short(f, "https://naver.me/form", c, t) == ("https://form.naver.com/response/ok", "form")
    assert F.resolve_short(f, "https://forms.gle/g", c, t) == ("https://docs.google.com/forms/d/e/ID/viewform", "form")
    assert F.resolve_short(f, "https://naver.me/blog", c, t) == (None, "not_form")
    assert F.resolve_short(f, "https://naver.me/down", c, t) == (None, "unverified")  # 확인 못 하면 폼으로 확정하지 않는다
    assert F.pick("naver.me/blog", [], lambda u: F.resolve_short(f, u, c, t)) == (None, [])
    assert F.pick("naver.me/down", [], lambda u: F.resolve_short(f, u, c, t)) == (None, ["https://naver.me/down"])


def _rs(**kw):
    from collector.restrict import classify
    return classify({"title": "", "tags": [], **kw})


def test_restriction():
    assert _rs(title="부산시민만 참여 가능")[0] is True
    assert _rs(title="부산 여행 댓글 이벤트, 전국 누구나 참여")[0] is False
    assert _rs(needs=["매장 방문 필수"])[0] is True
    assert _rs(title="매장 방문 없이 온라인 참여")[0] is False
    assert _rs(who="통영시민 대상")[0] is True
    assert _rs(who="제주도민 한정")[0] is True
    assert _rs(needs=["관내 주민 한정"])[0] is True
    assert _rs(needs=["지역 거주자만 참여"])[0] is True
    assert _rs(needs=["주민등록상 거주자"])[0] is True
    assert _rs(needs=["실물 영수증 제출 필수"])[0] is True
    assert _rs(needs=["방문 인증 필수"])[0] is True
    assert _rs(needs=["현장 구매 필수"])[0] is True
    assert _rs(steps=["행사장 방문", "인증샷 업로드"])[0] is True
    # 부정 표현·시설 이름·거주 무관은 제한이 아니다
    assert _rs(who="제주어에 관심 있는 누구나라고 공지; 제주 거주 필수라고 안내하지 않음")[0] is False
    assert _rs(title="부산시민공원 사진 공모 퀴즈")[0] is False
    assert _rs(needs=["부산시민이 아니어도 참여 가능"])[0] is False
    assert _rs(needs=["홈페이지 방문 후 댓글"])[0] is False
    # 불확실하면 확정하지 않고 검토 대상(None)
    assert _rs(title="양산시민과 함께하는 퀴즈")[0] is None
    assert _rs(title="현장 이벤트 안내")[0] is None
    assert _rs(needs=["부산시민 한정 경품", "전국 누구나 댓글 참여"])[0] is None
    assert _rs(who="공지는 '시민'이라는 표현을 쓰나 인천시민 한정인지는 미확인")[0] is None
    assert _rs(title="전북사랑도민증 소문내고 특산품 받자 이벤트")[0] is False
    assert _rs(steps=["시장 방문", "SNS 소개글 게시"])[0] is True
    assert _rs(steps=["누리집 방문", "설문 제출"])[0] is False
    assert _rs(src="rn", title="[경북/경산시] 미순할머니순대족발 체험단")[0] is True
    assert _rs(src="rn", title="[재택] 에어팟 프로 3 체험단")[0] is False
    # 수집기 표시
    assert _rs(tags=["방문 필요"])[0] is True and _rs(tags=["지역 한정"])[0] is True


def test_safety_abort(tmp_path, monkeypatch):
    # 진행 중 건수가 절반 아래로 줄면 db.json을 쓰지 않고 2를 돌려준다
    monkeypatch.setattr(P, "DATA", tmp_path)
    monkeypatch.setattr(P, "CONFIG", tmp_path / "none.json")
    monkeypatch.setattr(P, "SOURCES", {})
    events = {f"x{i}": {"id": f"x{i}", "title": f"t{i}", "url": f"https://a.kr/{i}", "status": "active",
                        "end": "2026-10-01" if i < 30 else "2099-01-01", "tags": [], "reasons": []} for i in range(40)}
    P.dump(tmp_path / "db.json", {"events": events})
    before = (tmp_path / "db.json").read_text(encoding="utf-8")
    assert P.main(["--force", "--no-links"]) == 2
    assert (tmp_path / "db.json").read_text(encoding="utf-8") == before


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
