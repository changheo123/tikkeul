# 지역 거주·현장 방문이 참여 조건인 이벤트 판별.
# 결과 is_restricted: True(제한 확정), False(제한 표현 없음), None(표현은 있으나 판단 불확실 → 검토 대상)
import re

NOT_PLACE = r"(?!전국|국내|대한민국|모든|일반|많은|여러|우리|참여|인근|지역|해당|각|본|전체|온라인)"
PLACE = NOT_PLACE + r"[가-힣]{2,7}?"
# 시민공원·구민회관 같은 시설 이름은 사람을 가리키는 말이 아니다
NOT_FACILITY = r"(?!증|공원|회관|체전|대학|의날|의\s*날|청|센터|단체|기자|참여단|안전|축제|강좌|아파트|복지|문화|운동장|교육|대상\s*수상)"
PEOPLE = r"(?:시민|도민|구민|군민)"

REGION_STRONG = [
    re.compile(PLACE + PEOPLE + NOT_FACILITY + r"\s*(?:만|분들?만|에\s*한[해함정]|한정|대상|으로\s*제한|누구나)"),
    re.compile(r"관내\s*(?:주민|거주|거주자|시민|사업장|학교|학생)"),
    re.compile(r"(?:해당\s*)?지역\s*(?:거주자|주민)\s*(?:만|대상|한정)"),
    re.compile(r"주민등록(?:상)?.{0,12}(?:거주|주소)"),
    re.compile(PLACE + r"\s*거주(?:자|민)?\s*(?:만|한정|대상|필수)"),
    re.compile(r"거주자만\s*(?:참여|응모|신청)"),
]
REGION_WEAK = re.compile(PLACE + PEOPLE + NOT_FACILITY + r"(?!\s*여러분)")

OFFLINE_STRONG = [
    re.compile(r"(?:현장|매장|오프라인|행사장|부스|지점|팝업(?:\s*스토어)?)\s*(?:방문|참여|참석)\s*(?:이\s*)?(?:필수|필요|해야|시에만)"),
    re.compile(r"(?<!홈페이지 )(?<!사이트 )(?<!블로그 )(?<!채널 )(?<!온라인 )방문\s*인증\s*필수"),
    re.compile(r"실물\s*영수증"),
    re.compile(r"현장\s*구매\s*필수"),
]
OFFLINE_STEP = re.compile(r"(?:현장|매장|행사장|부스|시장|가게|장소|축제장|팝업(?:\s*스토어)?)\S{0,3}\s*(?:방문|참석)")
OFFLINE_WEAK = re.compile(r"(?:매장|현장|오프라인|행사장|팝업\s*스토어)\s*(?:방문|참여|참석|이벤트)|방문\s*인증|방청|스탬프\s*투어|현장\s*검증")

NEGATE = re.compile(r"제한\s*(?:이\s*)?없|무관|상관\s*없|관계\s*없|없이|없어도|않아도|않음|않습니다|않아요|아니어도|불필요|필요\s*없|비대면|타\s*지역|다른\s*지역")
UNSURE = re.compile(r"미확인|미기재|확인되지|알 수 없|불명확")  # 조건이 불명확하다고 적힌 문장은 확정하지 않는다
NATIONWIDE = re.compile(r"전국\s*(?:누구나|민|어디서나)|대한민국\s*국민\s*누구나|누구나\s*참여\s*가능|거주지\s*무관|지역\s*제한\s*없")
ONLINE_ONLY = re.compile(r"온라인(?:으로)?\s*(?:만\s*)?(?:참여|응모|진행|신청)")


def clauses(text):
    return [x.strip() for x in re.split(r"[\n;!?·]|\.(?=\s|$)|,", text or "") if x.strip()]


def _snip(s):
    return re.sub(r"\s+", " ", s).strip()[:60]


def scan_strong(text):
    """원본 페이지 전체 글처럼 메뉴가 섞인 글에서는 명확한 제한 표현만 찾는다. [(종류, 문장)]"""
    out = []
    for c in clauses(text):
        if len(c) > 120 or NEGATE.search(c):
            continue
        if any(r.search(c) for r in REGION_STRONG):
            out.append(("지역", _snip(c)))
        elif any(r.search(c) for r in OFFLINE_STRONG):
            out.append(("현장", _snip(c)))
    return out[:5]


def classify(e):
    """이벤트 하나를 판별해 (is_restricted, restriction_reason)을 돌려준다. 사유에는 근거 문장을 그대로 싣는다."""
    tags = e.get("tags") or []
    steps = e.get("steps") or []
    who = e.get("who") or ""
    main_text = "\n".join([e.get("title") or "", who, *(e.get("needs") or []), *steps, e.get("rawText") or ""])
    nationwide = bool(NATIONWIDE.search(main_text))

    if "지역 한정" in tags:
        return True, "지역 거주 제한: 참여 대상이 특정 지역으로 한정됨"
    if "방문 필요" in tags or "현장 관람" in tags:
        return True, "현장 방문 필요: 방문형 참여"
    if e.get("src") == "mb" and not re.search(r"배송|재택|기자단", (e.get("title") or "") + (e.get("prize") or "")):
        return True, "현장 방문 필요: 매장 방문·포장 체험단"

    if e.get("src") == "rn" and re.match(r"\[[^\]/]+/[^\]]+\]", e.get("title") or ""):  # [경북/경산시] 형식은 지역 매장
        return True, "현장 방문 필요: 지역 매장 방문·포장 체험단"

    weak = None
    for c in clauses(main_text):
        if len(c) > 200:
            continue
        negated = bool(NEGATE.search(c))
        if any(r.search(c) for r in REGION_STRONG) and not negated:
            if nationwide or UNSURE.search(c):
                weak = weak or f"참여 지역 확인 필요: {_snip(c)}"
                continue
            return True, f"지역 거주 제한: {_snip(c)}"
        if any(r.search(c) for r in OFFLINE_STRONG) and not negated and not ONLINE_ONLY.search(c):
            return True, f"현장 방문 필요: {_snip(c)}"
    for s in steps:
        if OFFLINE_STEP.search(s) and not NEGATE.search(s) and not re.search(r"온라인|또는", s):
            return True, f"현장 방문 필요: {_snip(s)}"
    for kind, sn in e.get("pageFlags") or []:
        if not nationwide:
            return True, ("지역 거주 제한: " if kind == "지역" else "현장 방문 필요: ") + sn + " (원문 페이지)"
    if weak:
        return None, weak
    for c in clauses(main_text):
        if len(c) > 200 or NEGATE.search(c) or nationwide or ONLINE_ONLY.search(c):
            continue
        if REGION_WEAK.search(c):
            return None, f"참여 지역 확인 필요: {_snip(c)}"
        if OFFLINE_WEAK.search(c):
            return None, f"현장 참여 여부 확인 필요: {_snip(c)}"
    return False, None
