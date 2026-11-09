# 원문 텍스트에서 이벤트 정보를 규칙으로 뽑는다(AI 미사용: 원문에 없는 값은 만들지 않고 None으로 둔다)
import re
import hashlib
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from .common import find_dates, period, iso, clean, today_kst
from . import forms

# 이벤트로 볼 신호(제목·본문에 하나 이상)
EVENT_WORDS = re.compile(r"이벤트|경품|추첨|증정|기프티콘|상품권|당첨|사은품|선물|쿠폰|체험단|EVENT|event")

# 유료 조건: 하나라도 있으면 무료 이벤트가 아니므로 제외(부정 표현은 먼저 지운다)
# 체험단의 '선결제 후 페이백'·'구매평'도 먼저 돈을 내야 하므로 유료로 본다
PAY = re.compile(r"구매|구입|결제|입금|참가비|참가 ?비용|유료|가입비|주문 ?고객|주문 ?시|충전 ?고객|금액 이상|원 이상 ?(?:구매|결제|이용)|구독료|멤버십 가입|페이백|환급형|구매평|실구매")
PAY_NEG = re.compile(r"(구매|구입|결제)\s*(?:없이|와 ?무관|와 ?관계 ?없이|여부와 ?(?:관계|상관) ?없이|하지 ?않아도|불필요|필요 ?없)|무료 ?(?:참여|응모)|참가비 ?(?:없음|무료)|유료 ?(?:아님|아닙니다)")
# 공연초대는 사이트 정책상 싣지 않는다
SHOW = re.compile(r"(공연|연극|뮤지컬|콘서트|오페라|발레|리사이틀).{0,12}(초대|관람권|티켓)|초대 ?이벤트.{0,10}(공연|연극|뮤지컬)|문화초대이벤트")
# 이미 끝난 글(당첨자 발표 공지 등)
ENDED = re.compile(r"당첨자 ?(?:발표|안내|공지)|결과 ?발표|이벤트 ?(?:종료|마감) ?안내|수상자 ?발표")

KINDS = [  # (종류, 패턴) 앞에서부터 우선
    ("라디오", r"라디오|사연 ?(?:신청|보내|모집)|선곡|청취자"),
    ("체험단", r"체험단|서포터즈|리뷰어|테스터|기자단 ?모집"),
    ("설문", r"설문|만족도|조사 ?이벤트|의견 ?조사"),
    ("투표", r"투표|국민 ?심사"),
    ("퀴즈", r"퀴즈|정답|맞히|맞추|OX|초성|틀린 ?그림|다른 ?그림|숨은 ?그림|낱말"),
    ("댓글", r"댓글|기대평|응원 ?(?:메시지|한마디)|한 ?줄|소감|구독 ?이벤트|팔로우 ?이벤트|좋아요"),
]

REASONS = [  # 조건이 많아 기본 화면에서 숨기는 사유(장부의 X 등급과 같은 기준)
    ("공모전", r"공모전|공모 ?분야|시상금|작품 ?(?:접수|제출|공모)|기획서|(?:영상|UCC|디자인|수기|사진|아이디어) ?공모"),
    ("공유·게시형", r"소문내기|공유 ?이벤트|개인 ?SNS에 ?(?:게시|업로드|공유)|리그램|해시태그.{0,10}(?:게시|업로드)"),
    ("신규 회원가입 필요", r"신규 ?(?:회원 ?)?가입"),
    ("계좌·카드 개설 필요", r"계좌 ?개설|카드 ?발급|신규 ?개설"),
    # 기부가 참여 조건인지 글만으로는 알 수 없어 기본 화면에서 숨기고 원문 확인을 맡긴다(매칭기부는 제외)
    ("기부 관련 확인 필요", r"(?<!매칭)기부(?:하고|하기|금|해 ?주|참여)"),
]

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


def tidy(s):
    """이모지·장식 기호를 지우고 공백 정리."""
    return clean(EMOJI.sub("", s or "")).strip(" -·:|") or None


def fix_url(u):
    """본문 자동 링크에 붙은 닫는 괄호·문장부호를 뗀다."""
    if not u:
        return u
    u = u.strip()
    while u and u[-1] in ")].,'\"" and not (u[-1] == ")" and "(" in u):
        u = u[:-1]
    return u

TAGS = [
    ("팔로우 필요", r"팔로우|구독|이웃 ?추가|채널 ?추가|친구 ?추가"),
    ("인스타 참여", r"인스타|instagram"),
    ("유튜브 참여", r"유튜브|youtube|youtu\.be"),
    ("캡처 인증", r"캡처|캡쳐|인증샷|스크린샷"),
    ("로그인 필요", r"로그인 ?(?:후|해야|필요)"),
    ("아이디어 제출", r"아이디어 ?(?:제출|제안|공모)|제안 ?이벤트"),
]


def classify(text):
    t = text or ""
    kind = next((k for k, p in KINDS if re.search(p, t, re.I)), "기타")
    reasons = [r for r, p in REASONS if re.search(p, t)]
    tags = [g for g, p in TAGS if re.search(p, t, re.I)]
    return kind, reasons, tags


def pay_required(text):
    t = PAY_NEG.sub(" ", text or "")
    hits = PAY.findall(t)
    return hits


def is_show_invite(text):
    return bool(SHOW.search(text or ""))


_PRIZE_LINE = re.compile(r"(?:경품|상품|선물|혜택|시상|증정|당첨 ?(?:인원|혜택))\s*[:：]?\s*(.+)")
_PRIZE_WORDS = re.compile(r"상품권|기프티콘|쿠폰|커피|아메리카노|스타벅스|투썸|메가|컴포즈|네이버페이|포인트|금액권|배달의민족|배민|치킨|피자|케이크|에어팟|갤럭시|아이패드|[0-9,]+\s*(?:만\s*)?원권?|모바일")


def find_prize(text):
    """'경품: …' 줄이나 경품 단어가 든 짧은 줄을 원문 그대로 가져온다."""
    lines = [clean(x) for x in (text or "").splitlines() if clean(x)]
    for i, ln in enumerate(lines):
        m = _PRIZE_LINE.match(ln)
        if m and _PRIZE_WORDS.search(m.group(1)) and len(m.group(1)) <= 90:
            return m.group(1).strip(" -·:")
        if re.fullmatch(r"(?:이벤트 ?)?(?:경품|상품|선물|혜택|당첨 ?혜택)\s*[:：]?", ln) and i + 1 < len(lines):
            nxt = " · ".join(x.strip(" -·") for x in lines[i + 1:i + 4] if _PRIZE_WORDS.search(x) and len(x) <= 70)
            if nxt:
                return nxt[:120]
    for ln in lines:
        if _PRIZE_WORDS.search(ln) and re.search(r"\d+\s*명|추첨|증정|드려요|드립니다", ln) and len(ln) <= 80:
            return ln.strip(" -·")
    return None


def find_count(prize_text):
    if not prize_text:
        return None
    nums = [int(x.replace(",", "")) for x in re.findall(r"([\d,]+)\s*명", prize_text)]
    return sum(nums) if nums else None


def find_announce(text, ref_year=None):
    for ln in (text or "").splitlines():
        if re.search(r"당첨자? ?(?:발표|안내|공지)|발표 ?일|결과 ?발표", ln):
            d = find_dates(ln, ref_year)
            if d:
                return d[-1]
    return None


def find_steps(text, limit=5):
    """'참여 방법' 아래 번호 붙은 줄이나 단계 문장을 원문 그대로 최대 5개."""
    lines = [clean(x) for x in (text or "").splitlines() if clean(x)]
    for i, ln in enumerate(lines):
        if re.search(r"참여 ?방법|응모 ?방법|참여 ?방식|How to", ln):
            out = []
            for x in lines[i + 1:i + 12]:
                x = tidy(x) or ""
                if not x or re.fullmatch(r"https?://\S+", x):
                    continue
                if re.search(r"(?:이벤트|공모전|참여)? ?일정$|인증된 기업|^※|기간\s*[:：]|^경품|^발표|^유의", x):
                    if out:
                        break
                    continue
                if re.match(r"^(?:\d+[.)]|STEP ?\d|[①-⑩]|[-·•▶✔✅☑])", x) or (out and len(out) < limit and len(x) < 90 and not re.search(r"기간|경품|발표|유의", x)):
                    out.append(re.sub(r"^(?:\d+[.)]|STEP ?\d[:.]?|[①-⑩]|[-·•▶✔✅☑])\s*", "", x))
                elif out:
                    break
                if len(out) >= limit:
                    break
            if out:
                return [s[:140] for s in out if s]
    return []


def norm_url(u):
    """중복 판별용 URL: 추적용 파라미터와 조각을 지우고 호스트를 소문자로."""
    if not u:
        return None
    p = urlparse(u.strip())
    q = [(k, v) for k, v in parse_qsl(p.query) if not re.match(r"utm_|hl$|img_index|igsh|fbclid|si$|nPage|vlist|b_list|keyField|orderby|cg_code", k)]
    host = p.netloc.lower().removeprefix("www.").removeprefix("m.")
    path = p.path.rstrip("/")
    if "youtube.com" in host and path == "/watch":
        q = [(k, v) for k, v in q if k == "v"]
    return urlunparse(("https", host, path, "", urlencode(sorted(q)), ""))


def norm_title(t):
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|<[^>]*>|「|」|『|』", " ", t or "")
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", t).lower()
    return t


def make_id(prefix, key):
    return f"{prefix}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:10]}"


def build_event(*, src, sid, title, url, org=None, body="", start=None, end=None, announce=None,
                prize=None, kind_hint=None, extra_reasons=(), extra_tags=(), list_url=None, plat_hint=None, links=()):
    """수집기 하나가 찾은 글을 사이트 장부 형식으로 바꾼다. 무료가 아니면 (None, 사유)."""
    title = clean(title)
    full = f"{title}\n{body or ''}"
    if is_show_invite(full):
        return None, "공연초대"
    hits = pay_required(full)
    if hits:
        return None, "유료 조건: " + ",".join(sorted(set(hits)))
    if not EVENT_WORDS.search(full):
        return None, "이벤트 신호 없음"
    kind, reasons, tags = classify(full)
    if kind_hint:
        kind = kind_hint
    reasons = list(dict.fromkeys(list(reasons) + list(extra_reasons)))
    tags = list(dict.fromkeys(list(tags) + list(extra_tags)))
    ref = today_kst().year
    if not (start and end):
        s, e = period(body, ref) if body else (None, None)
        start, end = start or s, end or e
    announce = announce or find_announce(body, end.year if end else ref)
    prize = tidy(prize or find_prize(body))
    if prize:
        prize = re.sub(r"^(?:내역|내용)\s*[:：]\s*", "", prize)
    url = fix_url(url)
    title = tidy(title) or title
    ev = {
        "id": make_id(src, sid), "title": title, "org": org or None, "kind": kind,
        "grade": "X" if reasons else "C", "tags": tags, "reasons": reasons,
        "prize": prize, "count": find_count(prize), "mode": "추첨" if re.search(r"추첨", full) else None,
        "start": iso(start), "end": iso(end), "endTime": None, "announce": iso(announce),
        "min": None, "max": None, "who": None, "needs": [], "steps": find_steps(body),
        "place": None, "url": url, "checked": None, "cand": True,
        "src": src, "listUrl": list_url, "platHint": plat_hint,
    }
    # 응모 폼 후보(단축 링크 검증은 파이프라인에서)와 참여 제한 판별용 본문
    direct, short = forms.candidates(f"{full}\n{url}", links)
    ev["formLinks"] = direct + short
    ev["rawText"] = (body or "")[:1500]
    if not ev["steps"] and prize is None and end is None:
        return None, "기간·경품·참여 방법 모두 확인 안 됨"
    return ev, None
