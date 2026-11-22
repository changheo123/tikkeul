# 원문 공지 읽기(collector/notices.py)와 공지 근거를 쓰는 에이전트 피드·경량 피드 테스트(네트워크 없음)
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector import agentfeed as A
from collector import linktrace, notices as N
from collector.enrich import enrich

SITE = "https://example.github.io/tikkeul/"
TODAY = "2026-11-22"


def ev(**kw):
    base = {"id": "t-0123456789", "title": "구청 만족도 설문 이벤트", "org": "구청", "kind": "설문", "tags": [], "reasons": [], "needs": [],
            "steps": [], "prize": "커피 쿠폰 100명", "count": 100, "start": "2026-11-20", "end": "2026-11-30", "endTime": None,
            "announce": None, "url": "https://www.example.go.kr/board/view?no=1", "src": "t", "status": "active",
            "firstSeen": TODAY, "cand": True, "is_restricted": False, "needs_proof": False, "verification_grade": "C",
            "entry_url": None, "candidate_url": None}
    base.update(kw)
    return enrich(base)


FORM = dict(verification_grade="A", entry_url="https://form.naver.com/response/x", entry_type="form",
            form_url="https://form.naver.com/response/x")
META = {"status": "ok", "url": "https://www.example.go.kr/board/view?no=1", "fetched_on": TODAY}


# ---- 저장할 문장 ----
def test_pick_lines_keeps_conditions_drops_menu():
    page = "\n".join(["홈", "기관소개", "열린민원", "참여 방법", "① 아래 링크 접속", "② 정답 적기", "③ 끝",
                      "경품: 커피", "당첨자 발표: 홈페이지 공지", "사이트맵", "Copyright 2026"])
    t = N.pick_lines(page)
    assert "① 아래 링크 접속" in t and "② 정답 적기" in t and "당첨자 발표: 홈페이지 공지" in t
    assert "기관소개" not in t and "사이트맵" not in t and "Copyright" not in t
    assert len(N.pick_lines("응모 " * 5000)) <= N.MAX_CHARS


# ---- 읽을 주소·다시 읽기 규칙 ----
def test_target_policy():
    assert N.target({"url": "https://www.instagram.com/p/abc/"}) == (None, "skipped_policy")
    assert N.target({"url": "https://www.reviewnote.co.kr/campaigns/1"}) == (None, "skipped_policy")
    assert N.target({"url": "https://form.naver.com/response/x"}) == (None, "skipped_policy")
    # 공지가 SNS면 출처 상세 글을 읽는다
    assert N.target({"url": "https://www.instagram.com/p/abc/", "listUrl": "https://events.kr/event.php?id=3"}) == ("https://events.kr/event.php?id=3", None)
    # verify가 확인한 주최 원문이 우선
    assert N.target({"url": "https://events.kr/event.php?id=3", "original_url": "https://a.go.kr/ev/1"})[0] == "https://a.go.kr/ev/1"
    assert N.target({"url": "https://a.go.kr/"}) == (None, "list_page")  # 첫 화면은 이 이벤트의 글이 아니다


def test_due_cache_rules():
    e = {"id": "x", "url": "https://a.go.kr/ev/1"}
    assert N.due(e, None, TODAY) is True  # 새 이벤트
    ok = {"v": N.STORE_V, "url": "https://a.go.kr/ev/1", "at": "2026-10-01", "status": "ok"}
    assert N.due(e, ok, TODAY) is False  # 이미 읽은 공지는 다시 읽지 않는다(오래돼도)
    assert N.due(dict(e, url="https://a.go.kr/ev/2"), ok, TODAY) is True  # 출처 주소가 바뀜
    assert N.due(e, ok | {"v": N.STORE_V - 1}, TODAY) is True  # 저장 형식이 바뀜
    err = {"v": N.STORE_V, "url": "https://a.go.kr/ev/1", "at": "2026-11-21", "status": "error", "tries": 1}
    assert N.due(e, err, TODAY) is False and N.due(e, err | {"at": "2026-11-19"}, TODAY) is True  # 실패는 며칠 뒤 다시
    assert N.due(e, err | {"at": "2026-11-01", "tries": N.MAX_TRIES}, TODAY) is False  # 여러 번 실패하면 그만
    assert N.due({"id": "y", "url": "https://www.instagram.com/p/a/"}, None, TODAY) is False


# ---- 실행: 상한·실패 계속·캐시·정리 ----
class FakeFetcher:
    made = []

    def __init__(self, min_interval, timeout, retries):
        self.min_interval, self.timeout, self.retries = min_interval, timeout, retries
        FakeFetcher.made.append(self)


def _db(n, host_of=lambda i: f"h{i % 3}.go.kr"):
    return {"events": {f"t-{i}": {"id": f"t-{i}", "status": "active", "url": f"https://{host_of(i)}/ev/{i}", "firstSeen": TODAY}
                       for i in range(n)}}


def test_run_budget_failures_and_cache():
    calls, threads, lock = [], {}, threading.Lock()

    def reader(f, e, url, today):
        with lock:
            calls.append(url)
            threads.setdefault(url.split("/")[2], set()).add(id(f))
        if e["id"] == "t-1":
            raise RuntimeError("boom")  # 한 건의 실패가 나머지를 멈추지 않는다
        return {"v": N.STORE_V, "url": url, "at": today, "status": "ok", "text": "참여 방법"}

    FakeFetcher.made = []
    db = _db(10)
    store = {"events": {"gone": {"url": "x", "status": "ok"}}}
    cfg = {"notice_pages_per_run": 6, "notice_sec": 60, "notice_workers": 2, "notice_min_interval_sec": 2.5}
    r = N.run(db, store, TODAY, cfg, fetcher_cls=FakeFetcher, reader=reader)
    assert r["fetched"] == 6 and len(calls) == 6 and r["skipped_budget"] == 4 and r["pruned"] == 1
    assert r["failed"] == 1 and store["events"]["t-1"]["status"] == "error" and store["events"]["t-1"]["tries"] == 1
    assert "gone" not in store["events"]
    # 호스트마다 Fetcher 하나, 같은 호스트에는 간격(min_interval)을 둔다
    assert all(len(v) == 1 for v in threads.values()) and all(f.min_interval == 2.5 and f.retries == 0 for f in FakeFetcher.made)
    # 다음 실행: 이미 읽은 것은 다시 읽지 않고, 남은 것만
    calls.clear()
    r = N.run(db, store, TODAY, cfg, fetcher_cls=FakeFetcher, reader=reader)
    assert r["fetched"] == 4 and all(u.split("/")[-1] not in ("0", "2") for u in calls)
    calls.clear()
    assert N.run(db, store, TODAY, cfg, fetcher_cls=FakeFetcher, reader=reader)["fetched"] == 0 and not calls


def test_run_time_cap_and_no_links():
    db = _db(5)
    store = {"events": {}}
    r = N.run(db, store, TODAY, {"notice_sec": -1}, fetcher_cls=FakeFetcher, reader=lambda *a: 1 / 0)
    assert r["fetched"] == 0 and r["skipped_budget"] == 5 and store["events"] == {}
    r = N.run(db, store, TODAY, {"notice_pages_per_run": 0}, fetcher_cls=FakeFetcher, reader=lambda *a: 1 / 0)
    assert r["fetched"] == 0 and r["due"] == 5


def test_read_one_identity_and_redirect(monkeypatch):
    e = ev()
    good = "기관 소개 " * 20 + "\n구청 만족도 설문 이벤트\n참여 방법\n1. 설문 참여 후 제출하기\n당첨자는 문자로 개별 안내\n"

    def fake(page_text, final=None, status="ok"):
        return lambda f, url: linktrace.Page(url=url, final=final or url, status=status, text=page_text)

    monkeypatch.setattr(linktrace, "fetch_page", fake(good))
    r = N.read_one(None, e, e["url"], TODAY)
    assert r["status"] == "ok" and "설문 참여 후 제출하기" in r["text"] and "기관 소개" not in r["text"]
    monkeypatch.setattr(linktrace, "fetch_page", fake("전혀 다른 행사 안내 페이지입니다 " * 10))
    assert N.read_one(None, e, e["url"], TODAY)["status"] == "mismatch"  # 이 이벤트의 글이라는 근거 없음 → 쓰지 않음
    monkeypatch.setattr(linktrace, "fetch_page", fake(good, final="https://www.instagram.com/p/x/"))
    assert N.read_one(None, e, e["url"], TODAY)["status"] == "skipped_policy"
    monkeypatch.setattr(linktrace, "fetch_page", fake("", status="robots"))
    assert N.read_one(None, e, e["url"], TODAY)["status"] == "robots"
    monkeypatch.setattr(linktrace, "fetch_page", fake("짧음"))
    assert N.read_one(None, e, e["url"], TODAY)["status"] == "empty"


# ---- 공지 문장 → 사실 ----
def test_facts_login_and_widget_noise():
    assert N.facts("홈페이지 로그인 후 투표에 참여해 주세요")[0]["login_required"] is True
    assert N.facts("숏폼 10개 시청(로그인 필수)")[0]["login_required"] is True
    assert N.facts("비회원도 참여 가능합니다")[0]["login_required"] is False
    # 게시판 댓글 위젯·화면 안내 문구는 이벤트 조건이 아니다
    assert "login_required" not in N.facts("참여하기 댓글작성 로그인이 필요합니다.")[0]
    assert "login_required" not in N.facts("로그인이 필요 없으신 경우 닫기 버튼을 눌러 주세요")[0]
    assert N.facts("")[0] == {} and N.facts(None) == ({}, [])


def test_facts_fields_proof_purchase():
    f, ev_ = N.facts("개인정보 수집 항목 : 성명, 휴대전화번호, 주소\n이메일무단수집거부")
    assert f["form_fields"] == ["name", "phone", "address"]  # 푸터의 '이메일무단수집거부'는 항목이 아니다
    assert next(x for x in ev_ if x["field"] == "form_fields")["quote"].startswith("개인정보 수집 항목")
    assert "form_fields" not in N.facts("상세주소입력")[0]  # 화면 이름표
    assert N.facts("구글폼에 인증샷 첨부하고 소개 작성하면 응모 완료")[0]["proof_required"] is True
    assert N.facts("이용권 구매 완료 시 자동 응모됩니다")[0]["purchase_required"] is True
    assert "purchase_required" not in N.facts("경품은 구매하실 수 없습니다")[0]


def test_notice_steps_and_announce():
    t = "참여 방법\n① 공식 계정 팔로우하기\n② 게시물에 댓글 남기기\n공유 링크 만들기\n※ 당첨자 발표 12/1\n경품 안내"
    assert N.notice_steps(t) == ["공식 계정 팔로우하기", "게시물에 댓글 남기기"]
    assert N.notice_steps("참여방법\nSandisk OX 퀴즈이벤트!\nㆍ신청인원") == []  # 제목·표 머리는 절차가 아니다
    assert N.announce("발표방법: 개별 DM발송") == (["dm"], "발표방법: 개별 DM발송")
    assert N.announce("다른 게시물에 댓글을 남기면 당첨 확률 UP") == ([], None)
    assert N.announce("발표 위치는 정하지 않았습니다") == ([], None)


# ---- 에이전트 피드에 반영 ----
def test_item_fills_only_nulls_with_evidence():
    r = A.item(ev(), None, {}, ("홈페이지 로그인 후 응모 가능합니다\n당첨자는 문자로 개별 연락", META))
    assert r["conditions"]["login_required"] is True and r["requirements"]["login"] is True
    assert r["auto_ok"] is False and "login_required" in r["auto_blockers"]
    assert r["announce_methods"] == ["sms"]
    fields = {f["field"]: f for f in r["evidence"]["facts"]}
    assert fields["login_required"]["source"] == "notice" and "로그인 후 응모" in fields["login_required"]["quote"]
    assert r["evidence"]["notice"]["status"] == "ok"
    # 수집한 글에 값이 있으면 공지가 뒤집지 않는다
    r = A.item(ev(needs=["로그인 필요"]), None, {}, ("비회원도 참여 가능", META))
    assert r["conditions"]["login_required"] is True and not r["evidence"]["facts"]
    # 수집한 글의 발표 방법이 있으면 그대로
    assert A.item(ev(needs=["당첨자는 DM으로 안내"]), None, {}, ("당첨자는 문자로 개별 연락", META))["announce_methods"] == ["dm"]


def test_item_notice_steps_make_auto_ok_true():
    text = "참여 방법\n① 설문 참여하기\n② 폼 제출하기"
    r = A.item(ev(**FORM), None, {}, (text, META))
    assert r["conditions"]["steps_known"] is True and r["steps"][0]["source"] == "notice"
    assert r["auto_ok"] is True and r["auto_blockers"] == []
    assert any(f["field"] == "steps" for f in r["evidence"]["facts"])
    # 같은 이벤트라도 공지에 구매 조건이 있으면 false
    r = A.item(ev(**FORM), None, {}, (text + "\n제품 구매 후 응모 가능", META))
    assert r["auto_ok"] is False and r["auto_blockers"] == ["purchase_required"] and r["requirements"]["purchase"] is True
    # 공지에 자동화 금지 문구
    r = A.item(ev(**FORM), None, {}, (text + "\n매크로 사용 시 당첨 취소", META))
    assert r["auto_ok"] is False and "terms_forbid_automation" in r["auto_blockers"]
    assert any(f["field"] == "terms_forbid_automation" for f in r["evidence"]["facts"])


def test_notice_steps_never_flip_listing_values():
    # 인스타 이벤트(절차 미상 → SNS 계정 필요=true)는 공지 절차에 SNS 행동이 없어도 false/null로 바뀌지 않는다
    e = ev(url="https://www.instagram.com/p/a/", listUrl="https://gonggongevent.blogspot.com/2026/11/a.html")
    before = A.item(e, None, {})
    after = A.item(e, None, {}, ("참여 방법\n① 구글폼 설문 참여하기", META))
    assert before["conditions"]["sns_account_required"] is True and after["conditions"]["sns_account_required"] is True
    assert before["auto_ok"] is False and after["auto_ok"] is False


def test_item_without_evidence_stays_null():
    # 공지를 못 읽었으면 추측하지 않고 null + 부족 사유
    r = A.item(ev(**FORM), None, {}, (None, {"status": "skipped_policy", "url": None, "fetched_on": None}))
    assert r["auto_ok"] is None and "steps_unknown" in r["auto_blockers"] and "notice_not_read" in r["auto_blockers"]
    assert r["requirements"]["login"] is None and r["requirements"]["purchase"] is None
    # 공지를 쓰지 않는 호출(기존 방식)은 예전 결과 그대로
    r = A.item(ev(**FORM), None, {})
    assert r["auto_ok"] is None and r["auto_blockers"] == ["steps_unknown"] and r["evidence"]["notice"]["status"] == "not_used"


def test_text_for_status():
    e = ev()
    assert N.text_for({"events": {}}, e) == (None, {"status": "pending", "url": e["url"], "fetched_on": None})
    store = {"events": {e["id"]: {"v": N.STORE_V, "url": e["url"], "at": TODAY, "status": "ok", "text": "참여 방법"}}}
    assert N.text_for(store, e)[0] == "참여 방법"
    store["events"][e["id"]]["url"] = "https://old.example/1"  # 출처가 바뀐 뒤 아직 다시 못 읽음 → 옛 글은 쓰지 않음
    assert N.text_for(store, e)[0] is None
    assert N.text_for({}, ev(url="https://www.instagram.com/p/a/"))[1]["status"] == "skipped_policy"


# ---- 경량 피드 ----
def _build():
    events = [ev(id="t-auto", **FORM), ev(id="t-sns", steps=["팔로우", "댓글"], url="https://www.instagram.com/p/a/"),
              ev(id="t-old", status="ended", end="2026-11-01", **FORM)]
    store = {"events": {"t-auto": {"v": N.STORE_V, "url": events[0]["url"], "at": TODAY, "status": "ok",
                                   "text": "참여 방법\n① 설문 참여하기\n② 폼 제출하기\n개인정보 수집 항목: 성명, 연락처"}}}
    return A.build(events, {}, {}, SITE, TODAY, "2026-11-22T11:00:00+09:00", store)


def test_auto_and_lite_feeds():
    out = _build()
    auto, lite = out["feed/auto.json"], out["feed/active-lite.json"]
    assert [r["id"] for r in auto["events"]] == ["t-auto"] and auto["count"] == 1  # 진행 중 + auto_ok=true만
    a = auto["events"][0]
    assert a["auto_ok"] is True and a["steps"] == ["설문 참여하기", "폼 제출하기"] and a["requirements"]["form_fields"] == ["name", "phone"]
    assert {r["id"] for r in lite["events"]} == {"t-auto", "t-sns"} and lite["count"] == 2
    assert set(lite["events"][0]) == {"id", "entry_type", "entry_methods", "entry_url", "ends_at", "auto_ok", "auto_blockers", "requirements"}
    # 점수·순위 필드는 없다
    for r in auto["events"] + lite["events"]:
        assert not any(k in r for k in ("score", "priority", "rank"))
    assert lite["schema"] == "tikkeul.agent-feed.lite" and lite["schema_url"] == SITE + "feed/lite.schema.json"
    assert out["feed/events.json"]["count"] == 2  # 기존 피드는 그대로


def test_schemas_validate():
    try:
        import jsonschema
    except ImportError:
        return
    out = _build()
    jsonschema.validate(out["feed/events.json"], A.SCHEMA)
    jsonschema.validate(out["feed/auto.json"], A.LITE_SCHEMA)
    jsonschema.validate(out["feed/active-lite.json"], A.LITE_SCHEMA)
