# 키가 있어야 켜지는 공식 API 출처(유튜브·네이버)와 공개 채널 미리보기(텔레그램)
# 키·채널이 없으면 '연동 필요'로 기록만 하고 요청하지 않는다
import html as htmllib
import os
import re
import datetime as dt

from ..common import text_of, now_kst, find_dates
from ..extract import build_event, EVENT_WORDS

STRONG = re.compile(r"경품|추첨|증정|기프티콘|당첨")


class NotConfigured(Exception):
    pass


# ---- 유튜브 Data API v3 (무료 할당량 하루 10,000단위, 검색 1회 100단위) ----
def youtube(f, state, cfg):
    key = os.environ.get("YT_API_KEY")
    if not key:
        raise NotConfigured("YT_API_KEY 없음")
    after = (now_kst() - dt.timedelta(days=cfg.get("days", 14))).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = []
    # 전자기기 검색어는 할당량을 아끼려 회차마다 2개씩 돌아가며 쓴다(검색 1회 100단위)
    extra = cfg.get("elec_queries", [])
    st = state.setdefault("youtube", {"elecIdx": 0})
    picked = [extra[(st["elecIdx"] + i) % len(extra)] for i in range(min(2, len(extra)))] if extra else []
    st["elecIdx"] = st["elecIdx"] + 2
    for q in cfg.get("queries", []) + picked:
        r = f.get("https://www.googleapis.com/youtube/v3/search", check_robots=False, params={
            "part": "snippet", "q": q, "type": "video", "regionCode": "KR", "relevanceLanguage": "ko",
            "publishedAfter": after, "maxResults": 25, "order": "date"}, headers={"X-Goog-Api-Key": key})
        r.raise_for_status()
        ids += [it["id"]["videoId"] for it in r.json().get("items", [])]
    ids = list(dict.fromkeys(ids))
    events, skipped = [], {}
    for i in range(0, len(ids), 50):  # 영상 설명은 videos.list로(50개당 1단위)
        r = f.get("https://www.googleapis.com/youtube/v3/videos", check_robots=False,
                  params={"part": "snippet", "id": ",".join(ids[i:i + 50])}, headers={"X-Goog-Api-Key": key})
        r.raise_for_status()
        for it in r.json().get("items", []):
            sn = it["snippet"]
            desc = sn.get("description", "")
            if not (STRONG.search(desc) and re.search(r"댓글|구독|좋아요|응모", desc) and find_dates(desc)):
                skipped[it["id"]] = "설명란에 경품·참여·날짜가 함께 없음"
                continue
            ev, why = build_event(src="yt", sid=it["id"], title=sn["title"], url=f"https://www.youtube.com/watch?v={it['id']}",
                                  org=sn.get("channelTitle"), body=desc, extra_tags=["유튜브 참여"], plat_hint="yt")
            (events.append(ev) if ev else skipped.__setitem__(it["id"], why))
    return {"events": events, "seen": [e["id"] for e in events], "listed": len(ids), "skipped": skipped}


# ---- 네이버 검색 API(블로그·카페, 무료 하루 25,000회) ----
def naver(f, state, cfg):
    cid, sec = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and sec):
        raise NotConfigured("NAVER_CLIENT_ID/SECRET 없음")
    hdr = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec}
    events, skipped, listed = [], {}, 0
    # webkr(웹문서) 검색은 인스타그램·X·유튜브 게시물 원문 링크를 찾는 검색엔진 경로로 쓴다
    for kind in cfg.get("types", ["blog", "cafearticle", "webkr"]):
        qs = cfg.get("sns_queries", []) if kind == "webkr" else cfg.get("queries", [])
        for q in qs:
            r = f.get(f"https://openapi.naver.com/v1/search/{kind}.json", check_robots=False, headers=hdr,
                      params={"query": q, "display": 50, "sort": "date"})
            r.raise_for_status()
            for it in r.json().get("items", []):
                listed += 1
                title = htmllib.unescape(re.sub(r"<[^>]+>", "", it["title"]))
                desc = htmllib.unescape(re.sub(r"<[^>]+>", "", it.get("description", "")))
                # 검색 결과 요약만 쓰므로 경품 단어와 마감 날짜가 요약에 함께 있어야 후보로 둔다
                if not (STRONG.search(title + desc) and find_dates(desc)):
                    skipped[it["link"]] = "요약에 경품·날짜 없음"
                    continue
                link = it["link"]
                plat = ("insta" if "instagram.com" in link else "x" if re.search(r"//(?:www\.)?(?:x|twitter)\.com/", link)
                        else "yt" if "youtube.com" in link else "naver" if kind != "webkr" else "web")
                ev, why = build_event(src="nv", sid=link, title=title, url=link,
                                      org=it.get("bloggername") or it.get("cafename"), body=desc, plat_hint=plat)
                if ev:
                    ev["tags"].append("원문 미확인")
                    events.append(ev)
                else:
                    skipped[it["link"]] = why
    return {"events": events, "seen": [e["id"] for e in events], "listed": listed, "skipped": skipped}


# ---- 텔레그램 공개 채널 웹 미리보기(t.me/s/채널) ----
def telegram(f, state, cfg):
    chans = cfg.get("channels", [])
    if not chans:
        raise NotConfigured("수집할 공개 채널 목록이 비어 있음")
    events, skipped, listed = [], {}, 0
    for ch in chans:
        d = f.doc(f"https://t.me/s/{ch}")
        for msg in d.xpath("//div[contains(@class,'tgme_widget_message ')]"):
            listed += 1
            post = msg.get("data-post")
            body = text_of((msg.xpath(".//div[contains(@class,'tgme_widget_message_text')]") or [None])[0])
            if not (body and STRONG.search(body) and find_dates(body)):
                continue
            links = [h for h in msg.xpath(".//div[contains(@class,'tgme_widget_message_text')]//a/@href") if h.startswith("http") and "t.me/" not in h]
            first = body.splitlines()[0][:80]
            ev, why = build_event(src="tg", sid=post, title=first, url=links[0] if links else f"https://t.me/{post}",
                                  org=None, body=body, plat_hint="tg", list_url=f"https://t.me/{post}")
            if ev:
                if not links:
                    ev["tags"].append("원문 미확인")
                events.append(ev)
            else:
                skipped[post] = why
    return {"events": events, "seen": [e["id"] for e in events], "listed": listed, "skipped": skipped}
