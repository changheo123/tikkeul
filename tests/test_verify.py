# 응모 링크 추적·검증 테스트. 대부분 모의 페이지이고, DNS rebinding·동적 페이지는 실제 소켓·브라우저로 시험한다
import http.server
import shutil
import socket
import threading
import pytest

from collector import forms, linktrace as L, verify as V

TODAY = "2026-11-09"
BASE = "<title>건강도시 식생활 챌린지</title><body>노원구보건소 건강도시 식생활 챌린지 이벤트입니다. 응모 방법 안내. 경품 스타벅스 아메리카노 10명. 이벤트 기간: 2026.11.01 ~ 2026.11.30 " + "자세한 내용을 확인해 주세요. " * 20
FORM = "https://docs.google.com/forms/d/e/ABC/viewform"
FORM_PAGE = f"<title>건강도시 식생활 챌린지 응모</title><body>노원구보건소 건강도시 식생활 챌린지 설문 " + "질문 " * 120
OFFICIAL = "https://city.example/event/1"


class R:
    def __init__(self, code=200, html="", loc=None, ctype="text/html; charset=utf-8"):
        self.status_code, self.html = code, html
        self.headers = {"content-type": ctype}
        if loc:
            self.headers["Location"] = loc

    def iter_content(self, n):
        yield self.html.encode("utf-8")

    def close(self):
        pass


class Fake:
    """주소별 응답을 정해 둔 가짜 수집기. robots 차단 주소와 요청 기록을 가진다."""

    def __init__(self, routes=None, blocked=()):
        self.routes, self.blocked, self.calls = routes or {}, set(blocked), []

    def allowed(self, url):
        return url not in self.blocked

    def get(self, url, **kw):
        self.calls.append(url)
        if url not in self.routes:
            raise RuntimeError("없는 주소 " + url)
        return self.routes[url]


def ev(**k):
    return {"id": "e1", "status": "active", "src": "eh", "title": "건강도시 식생활 챌린지 이벤트", "org": "노원구보건소", "prize": "스타벅스 아메리카노 10명",
            "end": "2026-11-30", "url": OFFICIAL, "tags": [], **k}


@pytest.fixture(autouse=True)
def _public(monkeypatch):
    monkeypatch.setattr(forms, "_ips", lambda host: ["8.8.8.8"])


def check(routes, e=None, blocked=(), renderer=None):
    f = Fake(routes, blocked)
    c = V.Checker(f, TODAY, {}, renderer, {})
    return c.verify(e or ev()), f


def test_official_notice_links_form_via_short_url():
    r, _ = check({OFFICIAL: R(html=BASE + '<a href="https://bit.ly/x">응모하기</a>'), "https://bit.ly/x": R(302, loc="https://forms.gle/y"),
                  "https://forms.gle/y": R(302, loc=FORM), FORM: R(html=FORM_PAGE)})
    assert r["verification_grade"] == "A" and r["entry_url"] == FORM and r["entry_type"] == "form" and r["verification_code"] == "official_form"
    assert r["verification_evidence_url"] == OFFICIAL and r["original_url"] == OFFICIAL


def test_wrong_redirect_is_not_a_form():
    r, _ = check({OFFICIAL: R(html=BASE + '<a href="https://bit.ly/x">응모</a>'), "https://bit.ly/x": R(302, loc="https://shop.example/ad")})
    assert r["entry_type"] != "form" and r["entry_url"] == OFFICIAL  # 광고 주소를 폼으로 등록하지 않는다


def test_unrelated_page_is_not_verified():
    r, _ = check({OFFICIAL: R(html="<title>회사 소개</title><body>우리 회사는 좋은 회사입니다. " + "소개 " * 200)})
    assert r["verification_grade"] == "B" and r["entry_url"] is None and r["verification_code"] == "identity_unconfirmed"


def test_same_organizer_different_event():
    html = "<title>노원구보건소 금연 캠페인</title><body>노원구보건소 금연 캠페인 참여 안내 응모 방법 " + "안내 " * 200
    r, _ = check({OFFICIAL: R(html=html)})
    assert r["verification_grade"] == "B" and r["entry_url"] is None


def test_closed_form():
    html = FORM_PAGE + " 양식에서 더 이상 응답을 받지 않습니다."
    r, _ = check({FORM: R(html=html)}, ev(url=FORM, form_url=FORM))
    assert r["verification_grade"] == "C" and r["verification_code"] == "closed" and r["entry_url"] is None


def test_closed_notice_page():
    r, _ = check({OFFICIAL: R(html=BASE + "이벤트가 종료되었습니다")})
    assert r["verification_grade"] == "C" and r["verification_code"] == "closed"


def test_sns_without_official_link_is_review_only():
    r, f = check({}, ev(url="https://www.instagram.com/p/AAA/"))
    assert r["verification_grade"] == "B" and r["entry_type"] == "instagram" and r["entry_url"] is None and r["candidate_url"] == "https://www.instagram.com/p/AAA/"
    assert f.calls == []  # 인스타그램에는 요청하지 않는다
    r, f = check({}, ev(url="https://www.youtube.com/watch?v=abc"))
    assert r["entry_type"] == "youtube" and r["verification_grade"] == "B" and f.calls == []


def test_official_notice_linking_sns_post():
    r, _ = check({OFFICIAL: R(html=BASE + '<a href="https://www.instagram.com/p/AAA/">인스타그램 참여</a>')})
    assert r["verification_grade"] == "A" and r["entry_type"] == "instagram" and r["entry_url"] == "https://www.instagram.com/p/AAA/"


def test_sns_profile_link_is_not_entry():
    r, _ = check({OFFICIAL: R(html=BASE + '<a href="https://www.instagram.com/nowon/">공식 인스타그램</a>')})
    assert r["entry_type"] == "website" and r["entry_url"] == OFFICIAL


def test_dynamic_page_uses_renderer():
    class Rend:
        count = 0

        def render(self, url):
            self.count += 1
            p = L.Page(url=url, final=url, status="ok", rendered=True)
            return L.parse(p, BASE + f'<button data-url="{FORM}">응모하기</button>')

    rend = Rend()
    r, _ = check({OFFICIAL: R(html="<title>로딩</title><body><div id=app></div>"), FORM: R(html=FORM_PAGE)}, renderer=rend)
    assert rend.count == 1 and r["verification_grade"] == "A" and r["entry_url"] == FORM


def test_login_redirect_not_bypassed():
    r, f = check({"https://forms.gle/z": R(302, loc=FORM), FORM: R(302, loc="https://accounts.google.com/v3/signin?continue=x")}, ev(url=FORM, form_url=FORM))
    assert r["verification_grade"] == "B" and r["verification_code"] == "form_login" and r["entry_url"] is None
    assert not any("accounts.google" in c for c in f.calls)  # 로그인 페이지는 요청하지 않는다


def test_forbidden_and_robots_are_recorded():
    r, _ = check({OFFICIAL: R(403)})
    assert r["verification_grade"] == "B" and r["verification_code"] == "forbidden"
    r, f = check({}, blocked=[OFFICIAL])
    assert r["verification_grade"] == "B" and r["verification_code"] == "robots" and f.calls == []


def test_not_found_is_c():
    r, _ = check({OFFICIAL: R(404)})
    assert r["verification_grade"] == "C" and r["verification_code"] == "not_found"


def test_date_conflict():
    html = BASE.replace("2026.11.01 ~ 2026.11.30", "2026.09.01 ~ 2026.09.20")
    r, _ = check({OFFICIAL: R(html=html)})
    assert r["verification_grade"] == "C" and r["verification_code"] == "date_conflict" and r["entry_url"] is None


def test_multiple_forms_not_guessed():
    other = "https://docs.google.com/forms/d/e/DEF/viewform"
    r, _ = check({OFFICIAL: R(html=BASE + f'<a href="{FORM}">바로가기</a><a href="{other}">바로가기</a>')})
    assert r["verification_grade"] == "B" and r["verification_code"] == "multiple_forms"


def test_multiple_forms_with_clear_cue():
    other = "https://docs.google.com/forms/d/e/DEF/viewform"
    r, _ = check({OFFICIAL: R(html=BASE + f'<a href="{FORM}">응모하기</a><a href="{other}">자료실</a>'), FORM: R(html=FORM_PAGE)})
    assert r["verification_grade"] == "A" and r["entry_url"] == FORM


def test_form_self_identified_and_not():
    r, _ = check({FORM: R(html=FORM_PAGE)}, ev(url="https://www.instagram.com/p/A/", form_url=FORM))
    assert r["verification_grade"] == "A" and r["verification_code"] == "form_self"
    r, _ = check({FORM: R(html="<title>다른 설문</title><body>" + "무관한 설문 " * 100)}, ev(url="https://www.instagram.com/p/A/", form_url=FORM))
    assert r["verification_grade"] == "B" and r["entry_url"] is None


def test_aggregator_login_only():
    r, f = check({}, ev(url="https://www.eventhouse.kr/site/view.php?no=1"))
    assert r["verification_grade"] == "C" and r["verification_code"] == "aggregator_login" and f.calls == []


def test_aggregator_single_original_traced():
    agg = "https://events.kr/event.php?id=1"
    r, _ = check({agg: R(html='<title>x</title><body>글 <a href="https://city.example/event/1">원문</a>'), OFFICIAL: R(html=BASE)}, ev(url=agg, src="ek"))
    assert r["verification_grade"] == "A" and r["original_url"] == OFFICIAL


def test_aggregator_ambiguous_originals_not_guessed():
    agg = "https://events.kr/event.php?id=1"
    r, _ = check({agg: R(html='<title>x</title><body><a href="https://a.example/1">1</a><a href="https://b.example/2">2</a>')}, ev(url=agg, src="ek"))
    assert r["verification_grade"] == "C" and r["original_url"] is None


def test_canonical_is_candidate_only():
    html = BASE.replace("<body>", f'<link rel="canonical" href="{FORM}"><body>')
    r, _ = check({OFFICIAL: R(html=html)})
    assert r["entry_type"] == "website"  # canonical 주소만으로 폼으로 등록하지 않는다


def test_link_extraction_kinds():
    html = """<title>t</title><head><script type="application/ld+json">{"url":"https://l.example/j"}</script></head><body>
    <a href="/rel">상대</a><div data-href="https://d.example/1">d</div><button onclick="location.href='https://o.example/2'">o</button>
    <iframe src="https://i.example/3"></iframe><a href="https://www.facebook.com/sharer/sharer.php?u=x">공유</a><footer><a href="https://f.example/4">하단</a></footer>"""
    p = L.parse(L.Page(url="https://s.example/", final="https://s.example/", status="ok"), html)
    got = {k: u for u, _l, k in p.links}
    assert got["a"] == "https://s.example/rel" and got["data"] == "https://d.example/1" and got["onclick"] == "https://o.example/2" and got["iframe"] == "https://i.example/3" and got["jsonld"] == "https://l.example/j"
    assert not any("sharer" in u or "f.example" in u for u, _l, _k in p.links)


def test_entry_types():
    assert V.entry_type(FORM) == "form" and V.entry_type("https://forms.gle/a") == "form" and V.entry_type("https://form.naver.com/response/a") == "form"
    assert V.entry_type("https://www.instagram.com/p/a/") == "instagram" and V.entry_type("https://youtu.be/a") == "youtube"
    assert V.entry_type("https://blog.naver.com/a/1") == "blog" and V.entry_type("https://city.example/") == "website" and V.entry_type("") == "unknown"


def test_blog_read_through_mobile_keeps_original_url():
    url, m = "https://blog.naver.com/nowon/1", "https://m.blog.naver.com/nowon/1"
    r, f = check({m: R(html=BASE)}, ev(url=url))
    assert f.calls == [m] and r["entry_type"] == "blog" and r["entry_url"] == url


def test_past_year_form_is_stale_and_ended(monkeypatch):
    html = FORM_PAGE + " [참여 기간]25.11.11(화) ~ 11.30(일) [당첨자 발표]2025. 12.01"
    r, _ = check({FORM: R(html=html)}, ev(url="https://www.instagram.com/p/A/", form_url=FORM))
    assert r["verification_grade"] == "C" and r["verification_code"] == "stale_year" and r["entry_url"] is None
    f = Fake({FORM: R(html=html)})
    monkeypatch.setattr(V, "Fetcher", lambda **k: f)
    e = ev(url="https://www.instagram.com/p/A/", form_url=FORM)
    store = {}
    V.run(_db(e), store, TODAY, {}, renderer_cls=Rend0)
    assert e["status"] == "ended" and e["ended_reason"] and e["form_url"] == FORM and store["history"]["e1"][0]["field"] == "status"  # 삭제하지 않고 종료 처리


def test_title_year_older_than_event_is_stale():
    r, _ = check({FORM: R(html=FORM_PAGE)}, ev(title="2025년 건강도시 식생활 챌린지 이벤트", url=FORM, form_url=FORM))
    assert r["verification_code"] == "stale_year"


def test_current_year_not_stale():
    assert not V.stale_year(ev(), L.Page(url="x", text="이벤트 기간: 2026.11.01 ~ 2026.11.30"))


def test_list_and_home_pages_not_entry():
    for url in ("https://city.example/board/BoardList.html?x=1", "https://city.example/", "https://city.example/app/index.do", "https://city.example/main.do"):
        r, _ = check({url: R(html=BASE)}, ev(url=url))
        assert r["verification_grade"] == "B" and r["verification_code"] == "list_page" and r["entry_url"] is None, url


def test_event_landing_index_ok_for_collected_page():
    url = "https://event.sbs.example/doctorx/index.html"
    r, _ = check({url: R(html=BASE)}, ev(url=url, src="sb"))
    assert r["verification_grade"] == "A"


def test_home_page_sns_link_not_entry():
    url = "https://city.example/main.do"
    r, _ = check({url: R(html=BASE + '<a href="https://www.youtube.com/watch?v=abc">홍보 영상</a>')}, ev(url=url))
    assert r["verification_grade"] == "B" and r["entry_url"] is None


def test_future_dated_ki_event_is_ended(monkeypatch):
    e = ev(src="ki", start="2027-02-12", end="2027-02-25", url="https://www.instagram.com/p/A/")
    store = {}
    r = V.run(_db(e), store, TODAY, {"verify_pages_per_run": 0}, renderer_cls=Rend0)
    assert e["status"] == "ended" and r["future_fixed"] == 1 and e["url"].endswith("/A/")


def test_korissue_skips_far_future_dates():
    from collector.sources import aggregators as A
    import datetime as dt
    from lxml import html as H
    fut = dt.date.today() + dt.timedelta(days=100)
    page = f"""<div class='g' id='1'>[기관] 테스트 퀴즈 이벤트</div><div class='gsub'><p class='p'>ㆍ응모번호ㆍ12345-001</p><p class='p'>ㆍ경품내역ㆍ커피 10명</p>
    <p class='p'>ㆍ응모기간ㆍ{fut.month}/{fut.day}~ {fut.month}/{fut.day} ({fut.month}/{fut.day})</p><p class='p'>ㆍ응모주소ㆍ<a href='https://a.example/1'>응모주소</a></p></div>"""
    events, skipped, seen = [], {}, []
    A._kor_page(H.fromstring("<div>" + page + "</div>"), {}, events, skipped, seen)
    if fut.month >= dt.date.today().month or dt.date.today().month - fut.month <= 6:
        assert events == [] and "먼 미래" in list(skipped.values())[0]


# ---- SSRF 방어 ----
@pytest.mark.parametrize("bad", ["http://127.0.0.1/", "http://169.254.169.254/latest/", "http://10.0.0.1/", "http://[::1]/", "http://100.64.0.1/", "http://192.168.0.1/"])
def test_private_targets_blocked(monkeypatch, bad):
    monkeypatch.setattr(forms, "_ips", lambda host: [host.strip("[]")])
    f = Fake()
    p = L.fetch_page(f, bad)
    assert p.status == "blocked" and f.calls == []


@pytest.mark.parametrize("bad", ["file:///etc/passwd", "ftp://x.example/a", "https://user:pw@x.example/", "https://x.example:8443/"])
def test_bad_schemes_blocked(bad):
    f = Fake()
    assert L.fetch_page(f, bad).status == "blocked" and f.calls == []


def test_redirect_to_internal_blocked(monkeypatch):
    monkeypatch.setattr(forms, "_ips", lambda host: ["169.254.169.254"] if host == "meta.example" else ["8.8.8.8"])
    r, f = check({OFFICIAL: R(302, loc="http://meta.example/latest")})
    assert r["verification_grade"] == "C" and r["verification_code"] == "blocked" and f.calls == [OFFICIAL]


def test_redirect_loop_limited():
    f = Fake({OFFICIAL: R(302, loc=OFFICIAL)})
    p = L.fetch_page(f, OFFICIAL)
    assert p.status == "too_many" and len(f.calls) == L.MAX_REDIRECTS + 1


@pytest.fixture
def local_server():
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = b"<title>t</title><body>ok</body>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], hits
    srv.shutdown()


def test_dns_rebinding_blocked_with_real_socket(monkeypatch, local_server):
    """사전 검사는 공개 IP로 통과했는데 연결 순간 내부 IP로 바뀌는 경우: 실제 소켓으로 시험."""
    import requests
    port, hits = local_server
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: real("127.0.0.1", *a, **k) if host == "rebind.example" else real(host, *a, **k))
    with L.guarded():
        with pytest.raises(requests.exceptions.ConnectionError) as x:
            requests.get(f"http://rebind.example:{port}/secret", timeout=3)
    assert "공개되지 않은 주소" in str(x.value) and hits == []
    assert requests.get(f"http://127.0.0.1:{port}/open", timeout=3).status_code == 200  # 검사 밖의 연결(로컬 테스트 서버)은 영향 없음


@pytest.mark.skipif(not (shutil.which("google-chrome") or shutil.which("chromium") or __import__("os").path.exists("C:/Program Files/Google/Chrome/Application/chrome.exe")), reason="Chrome 없음")
def test_real_browser_finds_js_button_without_clicking():
    """실제 Chrome으로 JavaScript가 만든 응모 버튼을 찾되 누르지는 않는다."""
    hits = []
    page = f"""<title>이벤트</title><body><div id=a></div><script>
      const b=document.createElement('a');b.href='{FORM}';b.textContent='응모하기';b.setAttribute('onclick','fetch("/clicked")');document.getElementById('a').appendChild(b);</script>""".encode()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    rend = L.Renderer(Fake(), limit=1, cap_sec=60, url_ok=lambda u: "127.0.0.1" in u)
    try:
        p = rend.render(f"http://127.0.0.1:{srv.server_address[1]}/ev")
    finally:
        rend.close()
        srv.shutdown()
    if rend.unavailable:
        pytest.skip("브라우저를 시작하지 못함")
    assert p is not None and p.rendered and any(u == FORM for u, _l, _k in p.links)
    assert "/clicked" not in hits and rend.render("http://127.0.0.1/none") is None  # 예산 초과


# ---- 파이프라인 통합 ----
class Rend0:
    count = 0

    def __init__(self, *a, **k):
        pass

    def render(self, url):
        return None

    def close(self):
        pass


def _db(*events):
    return {"events": {e["id"]: e for e in events}}


def test_migration_keeps_url_and_form(monkeypatch):
    f = Fake({OFFICIAL: R(html=BASE)})
    monkeypatch.setattr(V, "Fetcher", lambda **k: f)
    e = ev(form_url="https://docs.google.com/forms/d/e/OLD/viewform", checked="2026-10-01")
    old = {k: e[k] for k in ("url", "form_url", "title", "checked")}
    f.routes[e["form_url"]] = R(403)
    store = {}
    r = V.run(_db(e), store, TODAY, {"verify_pages_per_run": 10}, renderer_cls=Rend0)
    assert r["checked"] == 1 and {k: e[k] for k in old} == old  # 기존 값 유지
    assert e["verification_grade"] == "A" and e["entry_type"] == "website" and e["verified_at"] == TODAY and e["last_checked_at"] == TODAY


def test_cache_ttl_and_ended_not_rechecked(monkeypatch):
    f = Fake({OFFICIAL: R(html=BASE)})
    monkeypatch.setattr(V, "Fetcher", lambda **k: f)
    e, done = ev(), ev(id="e2", status="ended", url="https://city.example/old")
    db, store = _db(e, done), {}
    V.run(db, store, TODAY, {}, renderer_cls=Rend0)
    n = len(f.calls)
    r = V.run(db, store, TODAY, {}, renderer_cls=Rend0)
    assert len(f.calls) == n and r["checked"] == 0 and "e2" not in store["events"]  # 주기 안에는 다시 요청하지 않고 종료 이벤트는 건너뜀
    r = V.run(db, store, "2026-11-16", {}, renderer_cls=Rend0)
    assert r["checked"] == 1  # A등급은 7일 뒤 재검증


def test_failed_url_cached(monkeypatch):
    f = Fake({OFFICIAL: R(404)})
    c = V.Checker(f, TODAY, {}, None, {})
    c.page(OFFICIAL)
    c.page(OFFICIAL)
    assert f.calls == [OFFICIAL]


def test_budget_limit_and_exception_isolated(monkeypatch):
    routes = {f"https://c.example/{i}": R(html=BASE) for i in range(5)}
    f = Fake(routes)
    monkeypatch.setattr(V, "Fetcher", lambda **k: f)
    evs = [ev(id=f"e{i}", url=f"https://c.example/{i}", end=f"2026-11-{20 + i}") for i in range(5)]
    real = V.Checker.verify
    monkeypatch.setattr(V.Checker, "verify", lambda self, e: (_ for _ in ()).throw(ValueError("x")) if e["id"] == "e0" else real(self, e))
    store = {}
    r = V.run(_db(*evs), store, TODAY, {"verify_pages_per_run": 2}, renderer_cls=Rend0)
    assert r["failed"] == 1 and store["events"]["e0"]["verification_code"] == "exception:ValueError"
    assert len(f.calls) == 2 and "e4" not in store["events"]  # 요청 상한에서 멈춘다


def test_change_history_records_old_url(monkeypatch):
    other = "https://docs.google.com/forms/d/e/NEW/viewform"
    f = Fake({OFFICIAL: R(html=BASE)})
    monkeypatch.setattr(V, "Fetcher", lambda **k: f)
    e = ev()
    db, store = _db(e), {}
    V.run(db, store, TODAY, {}, renderer_cls=Rend0)
    f.routes[OFFICIAL] = R(html=BASE + f'<a href="{other}">응모하기</a>')
    f.routes[other] = R(html=FORM_PAGE)
    V.run(db, store, "2026-11-16", {}, renderer_cls=Rend0)
    h = store["history"]["e1"]
    assert e["entry_url"] == other and any(x["field"] == "entry_url" and x["old"] == OFFICIAL and x["new"] == other and x["reason"] for x in h)


def test_no_secret_or_personal_fields_in_record():
    r, _ = check({OFFICIAL: R(html=BASE)})
    assert set(r) == {"entry_type", "entry_url", "original_url", "candidate_url", "verification_grade", "verification_reason", "verification_code", "verification_evidence_url", "verification_status", "last_checked_at"}
