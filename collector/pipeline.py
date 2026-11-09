# 수집 파이프라인: 출처 실행 → 무료·중복 검증 → DB 병합 → 종료·링크 처리 → 안전 점검 → 저장
# 사용: python -m collector.pipeline [--force] [--only 출처]
import argparse
import datetime as dt
import difflib
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

from lxml import html as LH

from . import forms, restrict
from .common import Fetcher, norm_date, now_kst, today_kst
from .extract import norm_url, norm_title, is_show_invite, pay_required
from .sources import events_kr, public_boards, api_sources, aggregators, experience

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = ROOT / "config" / "sources.json"

SOURCES = {
    # 원문 링크를 주는 출처를 먼저 돌려, 같은 이벤트가 목록 사이트에서 나오면 원문 쪽에 합쳐지게 한다
    "korissue": aggregators.korissue,
    "events_kr": events_kr.collect,
    "eventhouse": aggregators.eventhouse,
    "danawa": aggregators.danawa,
    "sbs": aggregators.sbs,
    "reviewnote": experience.reviewnote,
    "mrblog": experience.mrblog,
    "gangnam": experience.gangnam,
    "busan": public_boards.busan,
    "mohw": public_boards.mohw,
    "touredu": public_boards.touredu,
    "youtube": api_sources.youtube,
    "naver": api_sources.naver,
    "telegram": api_sources.telegram,
}
# 1단계에서 사람이 근거를 확인한 무료 예외(장부)
PAY_OK = {
    "jjwf-kakao-together-cheer-share-2026": "기부는 필수가 아니라고 공지에 안내됨",
    "e169": "설문 1건당 주최 측이 매칭기부하는 방식으로, 참여자 결제 없음(목록 메모 기준)",
    "e184": "'기부니'는 이벤트 이름의 말장난이며 결제 조건 없음(목록 메모 기준). 4~9월 행복취향.ZIP 3건 이상 참여자 대상",
}
NO_LINK_CHECK = re.compile(r"instagram\.com|youtube\.com|youtu\.be|blog\.naver\.com|cafe\.naver\.com|t\.me/|forms\.gle|docs\.google\.com|form\.naver\.com|naver\.me")


def load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def dump(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)  # 쓰다 실패해도 기존 파일이 깨지지 않게 교체 방식으로 저장


def seed_from_ledger(db):
    """DB가 비어 있으면 1단계 승인 장부(공연초대 제외)를 첫 데이터로 넣는다."""
    led = load(ROOT / "legacy" / "ledger-2026-10-27.json", {"events": []})
    n = 0
    for e in led["events"]:
        if e["kind"] == "공연 초대" or ("공연 초대권" in (e.get("prize") or "") and "현장 관람" in e["tags"]):
            continue
        txt = " ".join(e["needs"] + e["steps"] + [e["title"], e.get("prize") or ""])
        if re.search(r"구매|결제|입금|참가비|유료|기부", txt) and e["id"] not in PAY_OK:
            continue
        e = dict(e)
        e.update({"src": "ledger", "srcUrls": [e["url"]], "firstSeen": e.get("checked") or "2026-10-27",
                  "lastSeen": "2026-10-27", "status": "active", "freeNote": PAY_OK.get(e["id"])})
        db["events"][e["id"]] = e
        n += 1
    return n


def keys_of(e):
    ks = {norm_url(u) for u in [e.get("url"), e.get("listUrl"), *e.get("srcUrls", [])] if u}
    return {k for k in ks if k}


def find_dup(db, ev, index):
    """같은 원문 URL이거나, 제목이 거의 같고 마감일이 같으면 같은 이벤트로 본다."""
    for k in keys_of(ev):
        if k in index:
            return index[k]
    t = norm_title(ev["title"])
    if len(t) < 6:
        return None
    # 마감일이 같은 글(또는 마감일이 없는 글)끼리만 제목을 비교한다
    by_end = db["_ix"]["end"]
    pool = by_end.get(ev.get("end"), []) + by_end.get(None, []) if ev.get("end") else [x for v in by_end.values() for x in v]
    og = norm_org(ev.get("org"))
    for oid, ot, oo in pool:
        if db["events"].get(oid, {}).get("status") == "rejected":
            continue
        # 모음 사이트 제목은 '퀴즈 풀고 커피 받자'처럼 비슷한 것이 많아 주최가 다르면 다른 이벤트로 본다
        if og and oo and not (og in oo or oo in og or difflib.SequenceMatcher(None, og, oo).ratio() >= 0.6):
            continue
        if ot and ((len(ot) >= 6 and (t in ot or ot in t)) or difflib.SequenceMatcher(None, t, ot).ratio() >= 0.86):
            return oid
    return None


def norm_org(o):
    """주최명 비교용: 괄호·말줄임·공백·'(주)' 제거."""
    o = re.sub(r"\(주\)|㈜|주식회사|\.\.+|…|\([^)]*\)|\s", "", o or "")
    return o.lower()


def _index_add(ix, oid, o):
    for k in keys_of(o):
        ix["url"].setdefault(k, oid)
    ix["end"].setdefault(o.get("end"), []).append((oid, norm_title(o["title"]), norm_org(o.get("org"))))


def _index(db):
    """실행 중 한 번만 만드는 중복 판별 색인(URL·마감일별 제목). 저장 전에 지운다."""
    if "_ix" not in db:
        db["_ix"] = {"url": {}, "end": {}}
        for oid, o in db["events"].items():
            _index_add(db["_ix"], oid, o)
    return db["_ix"]


AGG_URL = re.compile(r"events\.kr/|t\.me/|eventhouse\.kr/")


def merge(db, ev, today, stats):
    ix = _index(db)
    oid = find_dup(db, ev, ix["url"])
    if oid is None:
        ev.update({"srcUrls": [u for u in {ev["url"], ev.get("listUrl")} if u], "firstSeen": today, "lastSeen": today, "status": "active"})
        db["events"][ev["id"]] = ev
        _index_add(ix, ev["id"], ev)
        stats["added"] += 1
        return ev["id"]
    o = db["events"][oid]
    for k in keys_of(ev):
        ix["url"].setdefault(k, oid)
    stats["merged"] += 1
    o["lastSeen"] = today
    o.setdefault("srcUrls", [])
    for u in ev.get("formLinks", []):  # 응모 폼 후보는 어느 출처에서 왔든 모은다(승인 장부 포함)
        if u not in o.setdefault("formLinks", []):
            o["formLinks"].append(u)
    if ev.get("rawText") and not o.get("rawText"):
        o["rawText"] = ev["rawText"]
    for u in (ev["url"], ev.get("listUrl")):
        if u and u not in o["srcUrls"]:
            o["srcUrls"].append(u)
    if o.get("cand"):
        # 자동 후보끼리는 비어 있던 값만 채운다(이미 있는 값을 덮어쓰지 않음)
        for k in ("org", "prize", "count", "start", "end", "announce"):
            if not o.get(k) and ev.get(k):
                o[k] = ev[k]
        if not o.get("steps") and ev.get("steps"):
            o["steps"] = ev["steps"]
        # 모음 사이트 링크보다 주최 원문 링크를 대표 링크로
        if AGG_URL.search(o["url"]) and not AGG_URL.search(ev["url"]):
            o["url"] = ev["url"]
            o["tags"] = [t for t in o["tags"] if t != "원문 미확인"]
    return oid


def normalize_dates(db):
    """날짜를 YYYY-MM-DD로 맞춘다. 해석 못 한 값은 원문을 남기고 비운다(종료로 처리하지 않음)."""
    fixed = 0
    for e in db["events"].values():
        for k in ("start", "end", "announce"):
            v = e.get(k)
            if v is None:
                continue
            n = norm_date(v)
            if n != v:
                e[k] = n
                if n is None:
                    e[k + "Raw"] = str(v)
                fixed += 1
    return fixed


def drop_broken(db):
    """파서 오류로 제목이 깨진 글을 지운다(아직 모집 중이면 다음 수집 때 올바른 제목으로 다시 들어온다)."""
    bad = [k for k, e in db["events"].items() if e.get("src") == "rn" and re.fullmatch(r"선착순 \d+ 체험단", e.get("title") or "")]
    for k in bad:
        del db["events"][k]
    return len(bad)


def update_status(db, today, cfg):
    """한국 날짜 기준: 마감일이 지난 건만 종료. 마감일 미상은 유지하고, 한 번 종료된 건은 되살리지 않는다."""
    keep = cfg.get("keep_ended_days", 45)
    t = dt.date.fromisoformat(today)
    pruned = 0
    for oid in list(db["events"]):
        e = db["events"][oid]
        if e.get("status") in ("rejected", "removed", "excluded", "merged"):
            continue
        if e.get("status") != "ended":
            e["status"] = "ended" if e.get("end") and e["end"] < today else "active"
        # 오래 지난 종료 이벤트는 DB에서 정리(이력은 git에 남음)
        endd = e.get("end") or e.get("lastSeen")
        if e["status"] == "ended" and endd and (t - dt.date.fromisoformat(endd)).days > keep:
            del db["events"][oid]
            pruned += 1
    return pruned


def check_links(f, db, today, limit):
    """진행 중 이벤트의 원문 링크를 오래 확인 안 한 순서로 몇 개씩 확인. 404·410이 두 번 나오면 내린다."""
    act = [e for e in db["events"].values() if e.get("status") == "active" and e.get("url")]
    act.sort(key=lambda e: e.get("linkChecked") or "")
    done = {"checked": 0, "ok": 0, "dead": 0, "skip": 0, "error": 0}
    for e in act:
        if done["checked"] >= limit:
            break
        if NO_LINK_CHECK.search(e["url"]) or AGG_URL.search(e["url"]):  # SNS·폼·모음 사이트는 원문 확인 대상이 아님
            if e.get("linkChecked") != today:
                e["linkChecked"], e["linkStatus"] = today, "skip"
                done["skip"] += 1
            continue
        done["checked"] += 1
        e["linkChecked"] = today
        try:
            r = f.get(e["url"], allow_redirects=True)
            code = r.status_code
        except PermissionError:
            e["linkStatus"] = "robots"
            continue
        except Exception as ex:
            e["linkStatus"] = f"error {type(ex).__name__}"
            done["error"] += 1
            continue
        if code in (404, 410):
            e["linkFail"] = e.get("linkFail", 0) + 1
            e["linkStatus"] = f"http {code}"
            if e["linkFail"] >= 2:
                e["status"] = "removed"
                done["dead"] += 1
        else:
            e["linkFail"] = 0
            e["linkStatus"] = "ok" if code < 400 else f"http {code}"
            done["ok"] += code < 400
    return done


def audit(db, today):
    """매 실행 품질 점검. 유료·공연초대는 제외(excluded), 의심 항목은 issues에 '확인 필요' 사유를 단다.
    마감일·링크를 추측해 고치지 않고 표시만 한다."""
    t = dt.date.fromisoformat(today)
    res = {"excluded": 0, "flagged": 0, "merged": 0}
    act = [e for e in db["events"].values() if e.get("status") == "active"]
    for e in act:
        text = " ".join([e["title"], e.get("prize") or "", *e.get("steps", []), *e.get("needs", [])])
        if e.get("src") != "ledger" and e["id"] not in PAY_OK and pay_required(text):
            e["status"], e["excluded"] = "excluded", "유료 조건"
            res["excluded"] += 1
            continue
        if is_show_invite(e["title"] + " " + (e.get("prize") or "")):
            e["status"], e["excluded"] = "excluded", "공연초대"
            res["excluded"] += 1
            continue
        issues = []
        if e.get("start") and e.get("end") and e["end"] < e["start"]:
            issues.append("응모 기간")
        if e.get("end") and dt.date.fromisoformat(e["end"]) > t + dt.timedelta(days=200):
            issues.append("마감일")
        ls = e.get("linkStatus") or ""
        if ls.startswith("error") or ls == "robots" or ls.startswith("http 4") or ls.startswith("http 5"):
            issues.append("원문 링크 접속")
        if issues != e.get("issues", []):
            e["issues"] = issues
        res["flagged"] += bool(issues)
    # 제목(정규화)·마감일이 완전히 같고 주최명 앞 두 글자가 같으면 같은 이벤트(예: 전북특별자치도 / 전북시)
    seen = {}
    for e in sorted((x for x in db["events"].values() if x.get("status") == "active"), key=lambda x: (x.get("cand", True), x["id"])):
        key = (norm_title(e["title"]), e.get("end"), norm_org(e.get("org"))[:2])
        if len(key[0]) < 8 or not key[2]:
            continue
        if key in seen:
            keep = db["events"][seen[key]]
            keep.setdefault("srcUrls", [])
            for u in [e.get("url"), *e.get("srcUrls", [])]:
                if u and u not in keep["srcUrls"]:
                    keep["srcUrls"].append(u)
            e["status"], e["excluded"] = "merged", seen[key]
            res["merged"] += 1
        else:
            seen[key] = e["id"]
    return res


FORM_NO_FETCH = re.compile(r"instagram\.com|youtube\.com|youtu\.be|blog\.naver\.com|cafe\.naver\.com|facebook\.com|//x\.com/|twitter\.com|t\.me/|eventhouse\.kr|reviewnote\.co\.kr|mrblog\.net|event\.danawa\.com")  # 체험단·다나와는 자체 사이트에서 신청


def _page_html(f, url):
    """원본 페이지에서 메뉴·스크립트를 뺀 (HTML, 글자). robots 차단·오류는 예외로 올린다."""
    d = f.doc(url)
    for bad in d.xpath("//script|//style|//nav|//header|//footer"):
        bad.drop_tree()
    return LH.tostring(d, encoding="unicode"), d.text_content()


def process_forms(db, state, today, cfg):
    """모든 이벤트의 form_url을 정한다. 저장된 글·링크를 먼저 보고, 없으면 원본 페이지를 예산 안에서 다시 읽는다."""
    res = {"direct": 0, "short_ok": 0, "pages": 0, "page_found": 0, "page_err": 0}
    f = Fetcher(min_interval=cfg.get("min_interval_sec", 1.0), timeout=10, retries=0)
    cache = state.setdefault("shortlinks", {})
    budget = {"short": cfg.get("short_checks_per_run", 150)}

    def resolver(u):
        if u not in cache and budget["short"] <= 0:
            return None, "unverified"
        if u not in cache:
            budget["short"] -= 1
        return forms.resolve_short(f, u, cache, today)

    def decide(e, active):
        text = " ".join(x or "" for x in [e.get("title"), e.get("prize"), e.get("url"), *(e.get("steps") or []), *(e.get("needs") or []),
                                          *(e.get("srcUrls") or []), e.get("rawText")])
        final, pending = forms.pick(text, e.get("formLinks") or [], resolver if active else None)
        if final:
            res["direct" if not (e.get("formLinks") and final not in e["formLinks"] and final not in text) else "short_ok"] += 1
        return final, pending

    events = list(db["events"].values())
    for e in events:
        e.setdefault("form_url", None)
        if e["form_url"]:
            continue
        e["form_url"], pending = decide(e, e.get("status") == "active")
        if pending:
            e["formPending"] = pending
        else:
            e.pop("formPending", None)

    # 저장된 글에 폼이 없는 진행 중 이벤트: 마감이 가까운 것부터 원본 페이지를 다시 읽는다(7일에 한 번)
    t = dt.date.fromisoformat(today)
    todo = [e for e in events if e.get("status") == "active" and not e["form_url"] and not e.get("formPending")
            and (not e.get("formChecked") or (t - dt.date.fromisoformat(e["formChecked"])).days >= 7)]
    todo.sort(key=lambda e: e.get("end") or "9999")
    t0, limit, cap = time.time(), cfg.get("form_pages_per_run", 300), cfg.get("form_pages_sec", 480)
    for e in todo:
        pages = [u for u in [e.get("url"), *(e.get("srcUrls") or [])]
                 if u and u.startswith("http") and not FORM_NO_FETCH.search(u) and not forms.is_form(u)
                 and (u == e.get("url") or "events.kr/event.php" in u)]
        pages = list(dict.fromkeys(pages))
        if not pages:
            continue
        if res["pages"] >= limit or time.time() - t0 > cap:
            break
        e["formChecked"] = today
        for u in pages:
            res["pages"] += 1
            try:
                page, text = _page_html(f, u)
            except Exception:
                res["page_err"] += 1
                continue
            direct, short = forms.candidates(page)
            if len(direct) + len(short) > 3:
                continue  # 폼이 여러 개인 페이지(목록·메뉴 등)는 어느 것이 이 이벤트인지 확정할 수 없다
            for x in direct + short:
                if x not in e.setdefault("formLinks", []):
                    e["formLinks"].append(x)
            flags = restrict.scan_strong(text)
            if flags:
                e["pageFlags"] = flags
        if e.get("formLinks"):
            e["form_url"], pending = decide(e, True)
            res["page_found"] += bool(e["form_url"])
            if pending:
                e["formPending"] = pending
    return res


def classify_restrictions(db):
    """전체 이벤트의 참여 제한을 다시 판별한다(규칙이 바뀌어도 매 실행 반영)."""
    res = {"restricted": 0, "review": 0}
    for e in db["events"].values():
        e["is_restricted"], e["restriction_reason"] = restrict.classify(e)
        res["restricted"] += e["is_restricted"] is True
        res["review"] += e["is_restricted"] is None
    return res


def apply_overrides(db):
    """관리자 수정(data/overrides.json): approve·reject·edit. GitHub 저장소 권한이 있는 사람만 고칠 수 있다."""
    ov = load(DATA / "overrides.json", {})
    for oid in ov.get("approve", []):
        if oid in db["events"]:
            e = db["events"][oid]
            e["cand"] = False
            if e.get("grade") == "C":
                e["grade"] = "A"
            e["checked"] = e.get("checked") or today_kst().isoformat()
    for oid in ov.get("reject", []):
        if oid in db["events"]:
            db["events"][oid]["status"] = "rejected"
    for oid, fields in ov.get("edit", {}).items():
        if oid in db["events"]:
            db["events"][oid].update(fields)


def due(state, name, interval_min, now):
    last = state.get("lastRun", {}).get(name)
    if not last:
        return True
    return (now - dt.datetime.fromisoformat(last)).total_seconds() >= interval_min * 60 - 120


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="주기와 관계없이 모든 출처 실행")
    ap.add_argument("--only", help="이 출처만 실행")
    ap.add_argument("--no-links", action="store_true")
    a = ap.parse_args(argv)

    cfg = load(CONFIG, {})
    db = load(DATA / "db.json", {"events": {}})
    state = load(DATA / "state.json", {})
    runs = load(DATA / "runs.json", [])
    now = now_kst()
    today = now.date().isoformat()
    f = Fetcher(min_interval=cfg.get("min_interval_sec", 1.0))
    t0 = time.time()

    seeded = seed_from_ledger(db) if not db["events"] else 0
    prev_active = sum(1 for e in db["events"].values() if e.get("status") == "active")
    run = {"at": now.isoformat(timespec="seconds"), "seeded": seeded, "sources": {}}
    stats = {"added": 0, "merged": 0, "rejected": 0}

    for name, fn in SOURCES.items():
        sc = cfg.get("sources", {}).get(name, {})
        if a.only and name != a.only:
            continue
        if not sc.get("enabled", True):
            run["sources"][name] = {"status": "off"}
            continue
        if not a.force and not a.only and not due(state, name, sc.get("interval_min", 60), now):
            run["sources"][name] = {"status": "wait"}
            continue
        s0, req0 = time.time(), f.count
        try:
            r = fn(f, state, sc)
            added0, merged0 = stats["added"], stats["merged"]
            for ev in r["events"]:
                # 수집기가 놓친 경우까지 한 번 더: 공연초대·유료 조건은 저장하지 않는다
                if is_show_invite(ev["title"]) or pay_required(ev["title"] + " " + (ev.get("prize") or "")):
                    stats["rejected"] += 1
                    continue
                oid = merge(db, ev, today, stats)
                if oid != ev["id"]:
                    state.setdefault("alias", {})[ev["id"]] = oid  # 병합된 글은 대표 이벤트 ID로 기억
            for sid in r.get("seen", []):
                sid = state.get("alias", {}).get(sid, sid)
                if sid in db["events"]:
                    db["events"][sid]["lastSeen"] = today
            run["sources"][name] = {"status": "ok", "listed": r.get("listed", 0), "new": stats["added"] - added0,
                                    "merged": stats["merged"] - merged0, "skipped": len(r.get("skipped", {})),
                                    "requests": f.count - req0, "sec": round(time.time() - s0, 1)}
            state.setdefault("lastRun", {})[name] = now.isoformat(timespec="seconds")
            state.setdefault("lastOk", {})[name] = now.isoformat(timespec="seconds")
            state.setdefault("lastSkipped", {})[name] = dict(list(r.get("skipped", {}).items())[:30])
        except api_sources.NotConfigured as e:
            run["sources"][name] = {"status": "unconfigured", "reason": str(e)}
            state.setdefault("lastRun", {})[name] = now.isoformat(timespec="seconds")
        except Exception as e:
            # 출처 하나가 실패해도 다른 출처와 기존 데이터는 그대로 둔다(다음 실행에서 재시도)
            run["sources"][name] = {"status": "error", "error": f"{type(e).__name__}: {e}"[:300],
                                    "trace": traceback.format_exc()[-600:]}
            state.setdefault("fails", {})[name] = state.get("fails", {}).get(name, 0) + 1
        else:
            state.setdefault("fails", {})[name] = 0

    db.pop("_ix", None)  # 색인은 수집 중에만 쓴다(아래에서 항목을 지울 수 있음)
    broken = drop_broken(db)
    dates = normalize_dates(db)
    pruned = update_status(db, today, cfg)  # 폼 재조회 예산을 끝난 이벤트에 쓰지 않도록 먼저 종료 처리
    fcfg = {**cfg, "form_pages_per_run": 0, "short_checks_per_run": 0} if a.no_links else cfg  # --no-links면 네트워크 조회 없음
    form = process_forms(db, state, today, fcfg)
    restricted = classify_restrictions(db)
    apply_overrides(db)  # 관리자 수정이 자동 판별보다 우선
    pruned += update_status(db, today, cfg)
    links = {} if a.no_links else check_links(f, db, today, cfg.get("link_checks_per_run", 25))
    quality = audit(db, today)
    active = sum(1 for e in db["events"].values() if e.get("status") == "active")
    run.update({"added": stats["added"], "merged": stats["merged"], "rejected": stats["rejected"], "pruned": pruned,
                "links": links, "quality": quality, "form": form, "restricted": restricted, "dates": dates, "broken": broken, "active": active, "total": len(db["events"]), "requests": f.count,
                "sec": round(time.time() - t0, 1)})

    # 안전장치: 진행 중 건수가 이전의 절반 아래로 급감하면 저장하지 않고 실패로 끝낸다(기존 사이트 유지)
    if prev_active >= 20 and active < prev_active * 0.5:
        run["aborted"] = f"진행 중 {prev_active}→{active}건 급감, 저장 안 함"
        runs.append(run)
        dump(DATA / "runs.json", runs[-300:])
        print(json.dumps(run, ensure_ascii=False))
        return 2

    db.pop("_ix", None)
    db["meta"] = {"updated": now.isoformat(timespec="seconds")}
    dump(DATA / "db.json", db)
    dump(DATA / "state.json", state)
    runs.append(run)
    dump(DATA / "runs.json", runs[-300:])
    print(json.dumps({k: v for k, v in run.items() if k != "sources"}, ensure_ascii=False))
    for k, v in run["sources"].items():
        print(f"  {k}: {json.dumps({x: y for x, y in v.items() if x != 'trace'}, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
