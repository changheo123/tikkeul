# 참여할 때 인증 자료(캡처·영수증 등)를 내야 하는 이벤트 판별. is_restricted와 별개이며 목록에서 빼지 않는다
import re

from .restrict import clauses

CAPTURE = re.compile(r"캡처|캡쳐|스크린\s*샷|화면\s*저장")
PROOF_ITEM = re.compile(
    r"인증\s*(?:샷|사진|이미지|화면)|영수증|구매\s*(?:내역|인증|확인서)|증빙|증명\s*(?:서|자료)|참여\s*인증|완료\s*(?:화면|인증)"
    r"|(?:구독|팔로우|채널\s*추가|친구\s*추가|이웃\s*추가|좋아요|설치|가입|댓글|방문|구매)\s*(?:완료\s*)?(?:후\s*)?인증")
# 있으면 좋은 것, 없어도 되는 것
OPTIONAL = re.compile(r"선택|가산|우대|확률|없이|불필요|필요\s*없|않아도|마세요|생략|참고|예시")
# 응모가 아니라 당첨 뒤 절차(신분증·제세공과금 서류 등)
AFTER_WIN = re.compile(r"당첨\s*(?:자|시|후|되)|수령|제세공과금|신분증|배송지")
# 본문(유의사항이 섞인 글)에서는 제출을 뜻하는 말이 함께 있어야 조건으로 본다
ACTION = re.compile(r"제출|첨부|업로드|올려|올리|보내|폼|필수|DM|인증")
# 신원 확인용 인증은 증빙이 아니다
IDENTITY = re.compile(r"(?:본인|휴대폰|핸드폰|문자|이메일|메일|회원|성인|실명|공동|SMS|간편)\s*인증")
TITLE_PROOF = re.compile(r"인증\s*(?:하고|해|하면|샷)")
SHOT_STEP = re.compile(r"촬영|사진")
HEADER = re.compile(r"^(?:응모형태|참여 방법)")


def _hit(c):
    if OPTIONAL.search(c) or AFTER_WIN.search(c):
        return None
    m = CAPTURE.search(c) or PROOF_ITEM.search(c)
    return re.sub(r"\s+", " ", c).strip()[:60] if m else None


def classify(e):
    """(needs_proof, 근거 문장). 참여 절차·조건에 실제로 요구될 때만 True."""
    required = [*(e.get("steps") or []), *(e.get("needs") or [])]
    for line in required:
        for c in clauses(line):
            if (h := _hit(c)):
                return True, h
            # 참여 절차 안의 '폼 인증', '걸음수 인증' 같은 줄임 표현(신원 확인은 제외)
            if "인증" in IDENTITY.sub("", c) and not OPTIONAL.search(c) and not AFTER_WIN.search(c):
                return True, re.sub(r"\s+", " ", c).strip()[:60]
    # 제목이 인증을 요구하고 절차에 촬영·사진이 있으면 증빙 사진 제출로 본다
    if TITLE_PROOF.search(e.get("title") or "") and any(SHOT_STEP.search(x) for x in required):
        return True, "제목·절차: " + next(x for x in required if SHOT_STEP.search(x))[:50]
    if e.get("src") == "ledger" and "캡처 인증" in (e.get("tags") or []):
        return True, "장부 확인: 캡처 인증 필요"
    body = [x for x in (e.get("rawText") or "").splitlines() if x.strip() and not HEADER.match(x.strip())]
    for line in body:
        for c in clauses(line):
            if (h := _hit(c)) and ACTION.search(c):
                return True, h
    if not required and not body:
        # 참여 절차를 모를 때만 제목을 본다('팔로우 인증해 케이크' 같은 요약 제목)
        h = _hit(e.get("title") or "")
        if h:
            return True, h
    return False, None
