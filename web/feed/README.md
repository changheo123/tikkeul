# 티끌모아 티끌 에이전트 피드 (schema 2.x, 현재 2.1)

AI 에이전트·프로그램이 화면(HTML·JS)을 읽지 않고 이벤트를 받아 가는 정적 JSON입니다.
매시간 수집 뒤 다시 만들어집니다. 인증·키·요청 한도 없음, 모든 시각은 한국 시간(`+09:00`).

| 주소 | 내용 |
|---|---|
| `feed/events.json` | 진행 중 이벤트 전체(마감 가까운 순) |
| `feed/new/YYYY-MM-DD.json` | 그날 처음 등록된 이벤트(최근 30일, 지금은 종료된 것도 `status`로 표시) |
| `feed/new/index.json` | 일자 파일 목록과 건수 |
| `feed/atom.xml` | 새로 등록된 이벤트 100건(Atom) |
| `feed/schema.json` | JSON Schema(draft 2020-12) |
| `feed/auto.json` | **진행 중이고 `auto_ok=true`인 이벤트만**(작은 파일). 폼 응모에 필요한 절차·입력 항목·근거 포함 |
| `feed/active-lite.json` | 진행 중 전체의 최소 필드(`id`, `entry_type`, `entry_methods`, `entry_url`, `ends_at`, `auto_ok`, `auto_blockers`, `requirements`) |
| `feed/lite.schema.json` | 위 두 경량 파일의 JSON Schema |

경량 파일 두 개에는 점수·우선순위·순위가 **없습니다**. 순서는 `events.json`과 같은 마감 가까운 순(같으면 id)일 뿐입니다.
자세한 값이 필요하면 같은 `id`로 `feed/events.json`에서 찾으세요.

## 토큰·요청을 아끼는 사용 순서

1. `feed/auto.json`만 받아 응모한다(대부분 수 KB). `steps`, `requirements.form_fields`, `per_person_limit`, `evidence.facts[].quote`로 폼을 채우고 조건을 확인한다.
2. 사람 확인이 필요한 후보까지 보려면 `feed/active-lite.json`에서 `auto_ok`·`requirements`로 거른 뒤, 고른 id만 `feed/events.json`에서 본다.
3. 공지(`notice_url`)는 `evidence.notice.status`가 `ok`가 아니거나 근거가 더 필요할 때만 연다.

사이트 주소: <https://changheo123.github.io/tikkeul/> → 예: `https://changheo123.github.io/tikkeul/feed/events.json`

## 새 이벤트만 가져오기

1. 마지막으로 받은 날짜 이후의 `feed/new/YYYY-MM-DD.json`을 받는다(없는 날짜는 `feed/new/index.json`에서 확인).
2. 이미 가진 `id`는 건너뛴다. 같은 날 파일도 수집할 때마다 늘어나므로 **id로 중복을 거른다**.
3. 또는 `feed/events.json`을 받아 `added_at`(없으면 `added_on`)이 마지막 확인 시각 이후인 것만 고른다.

## 값의 규칙(중요)

- **null은 '확인 안 됨'이지 '없음'이 아니다.** true/false는 수집한 글에 근거가 있을 때만 쓴다.
- 분류 목록(`entry_methods`, `announce_methods`)에 근거가 없으면 `["unknown"]`.
- 2.x 안에서는 필드를 **추가만** 하고 이름·의미·열거값을 바꾸거나 지우지 않는다. 깨지는 변경은 3.0으로 올리고 경로를 따로 둔다.
- 모르는 필드·열거값이 나오면 무시하도록 만들어 두는 것을 권한다.

## 이벤트 필드

| 필드 | 형식 | 설명 |
|---|---|---|
| `id` | string | 고유·불변 ID. 자동 수집분은 `출처코드-SHA1앞10자`(출처 글 키 기준), 사람 확인분은 장부 ID. 처음 등록 때 정해지고 원문·응모 주소가 바뀌어도 그대로 |
| `merged_ids` | string[] | 같은 이벤트로 합쳐진 다른 출처의 ID |
| `status` | `active`·`ended` | |
| `added_on` | date | 이 사이트에 처음 등록된 날짜 |
| `added_at` | date-time·null | 처음 등록된 시각. 시각 기록을 시작하기 전(2026-11-22 이전) 등록분은 null |
| `title`, `organizer`, `kind` | string | 제목, 주최, 사이트 분류(댓글·퀴즈·설문·체험단 등) |
| `entry_methods` | enum[] | 응모 방식(아래 표). 복수 가능 |
| `auto_ok` | bool·null | 아래 '자동 응모 판단' |
| `auto_blockers` | enum[] | auto_ok=false면 막는 조건, null이면 부족한 근거 |
| `entry_url` | url·null | 실제 응모 주소(폼·게시물). 검증 등급 A·B일 때만 |
| `entry` | object | `url`, `type`, `grade`(A 확인·B 근거만·C 미확보), `verified`, `form_kind`, `reason` |
| `notice_url` | url | 이벤트 공지(원문) 주소. 항상 있음 |
| `source_url` | url·null | 이벤트를 찾은 모음 사이트 글(원문과 다를 때) |
| `prize` | object | `name`, `items[]`, `categories[]`, `value_krw`(경품명에 적힌 최대 금액), `winners`(총 인원), `raw` |
| `starts_at` | date-time·null | 시작. 시각을 모르면 00:00:00 |
| `ends_at` | date-time·null | 마감. 시각을 모르면 23:59:59, 이때 `ends_at_time_known=false` |
| `announce_at` | date-time·null | 당첨 발표일. 시각을 모르면 00:00:00 |
| `announce_methods` | enum[] | `dm`·`comment`·`notice`·`sms`(문자·알림톡·카카오톡)·`email`·`unknown` |
| `selection` | string·null | 추첨·선착순·선정 등 |
| `limits` | object | 아래 '응모 제한' |
| `steps` | object[] | 참여 절차: `order`, `text`(원문), `actions[]`, `platform`(문장으로 판별될 때만) |
| `conditions` | object | 참여 조건(로그인·회원가입·팔로우·댓글·퀴즈·인증·개인정보 항목·자격 제한 등). `data/feed.schema.json`의 conditions와 같은 키 |
| `requirements` | object | (2.1) 응모 요구 조건 요약 — 아래 표 |
| `evidence` | object | (2.1) 원문 공지 읽기 결과(`notice`)와 공지에서 채운 값의 근거(`facts`) — 아래 '원문 공지 근거' |

### requirements (2.1)

`true`=필요함이 확인됨, `false`=필요 없음이 확인됨, `null`=확인 안 됨.

| 필드 | 설명 |
|---|---|
| `login` | 로그인·회원가입 필요 |
| `sns_account` | SNS 계정 필요 |
| `proof_or_photo` | 인증샷·캡처·사진 제출 필요 |
| `purchase` | 구매·결제 필요(공지 문장 근거). `false`는 무료로 확인된 이벤트만 |
| `personal_info` | 요구 개인정보 항목(`name`·`phone`·`address`·`email`·`birth`·`sns_id`) |
| `form_fields` | 공지에 적힌 입력 항목(같은 키). 공지를 읽지 못했으면 `null` |
| `visit`, `review` | 방문·후기 작성 필요 |
| `eligibility_restricted` | 참여 자격 제한(지역·대상 등) |

### 원문 공지 근거 `evidence` (2.1)

수집 때 각 이벤트의 공지(원문) 페이지를 **한 번** 읽어 응모 조건 문장만 저장하고, 빌드 때 거기서 값을 뽑습니다.

- `evidence.notice.status`: `ok`(읽었고 이 이벤트의 글로 확인됨), `mismatch`(다른 글로 보여 쓰지 않음),
  `skipped_policy`(인스타그램·유튜브·X·페이스북·네이버 카페·텔레그램, 로그인 전용 모음·체험단 사이트, 폼 — 약관·로그인 때문에 읽지 않음),
  `pending`(아직 차례가 안 됨), `robots`·`forbidden`·`login`·`not_found`·`error`·`empty`(스크립트 전용)·`list_page`·`blocked`·`non_html`·`too_many`, `no_url`
- `evidence.notice.url`, `fetched_on`: 읽은 주소(공지가 읽을 수 없는 곳이면 출처 상세 글)와 날짜
- `evidence.facts[]`: 공지에서 **채운** 값만. `{field, value, source: "notice", quote}` — `quote`는 근거 문장(짧게)
  - `field`: `login_required`, `proof_required`, `personal_info`, `form_fields`, `purchase_required`, `steps`, `announce_methods`,
    `terms_forbid_automation`, `limits.per_person_limit`, `limits.per_day_limit`, `limits.duplicate_entry_allowed`, `limits.duplicate_win_allowed`

규칙:
- 공지는 **수집한 글에 값이 없을 때(null·`unknown`)만** 채우고, 수집한 글의 true/false를 뒤집지 않습니다.
- 공지 절차(`steps[].source = "notice"`)는 수집한 글에 절차가 없을 때만 씁니다.
- 페이지가 이 이벤트의 것이라는 근거(제목 일부·경품·마감일)가 없으면 그 페이지의 문장은 쓰지 않습니다.
- 게시판 댓글 위젯('댓글작성 로그인이 필요합니다'), 푸터('이메일무단수집거부') 같은 사이트 화면 문구는 근거로 쓰지 않습니다.
- 공지는 새 이벤트이거나 공지 주소가 바뀐 이벤트만 읽습니다(실패는 며칠 뒤 최대 3회 재시도).

### entry_methods

| 값 | 뜻 |
|---|---|
| `instagram_comment` / `_follow` / `_like` / `_share` / `_dm` | 인스타그램 댓글(친구 태그 포함)·팔로우·좋아요·공유(스토리·리그램)·DM |
| `x_repost` / `x_follow` / `x_reply` | X 재게시·팔로우·답글 |
| `youtube_comment` / `_subscribe` / `_watch` | 유튜브 댓글·구독·시청 |
| `youtube` | 유튜브 참여인데 절차를 모름 |
| `blog_comment` / `blog_follow` / `blog_post` | 네이버 블로그·카페 댓글·이웃추가·글(후기) 작성 |
| `naver_form` / `google_form` / `other_form` | 네이버폼 / 구글폼 / 기타 폼(모아폼·스모어 등) |
| `web_form` | 폼 종류를 모르는 웹 신청·퀴즈·설문 |
| `other` | 절차는 있지만 위에 해당하지 않음 |
| `unknown` | 근거 없음 |

SNS 행동은 절차 문장에 플랫폼이 적혀 있거나 이벤트의 SNS가 하나뿐일 때만 플랫폼별 값으로 나눕니다.

### 자동 응모 판단 `auto_ok`

- `true`: 검증된(A) 응모 주소(`entry_url`)가 네이버폼·구글폼 등 폼 자체이고, 절차가 확인됐고(수집한 글 또는 공지의 '참여 방법'),
  로그인·SNS 행동·인증샷·방문·후기·자격 제한·구매·자동화 금지 문구가 **확인되지 않음**
- `false`: 위 중 하나가 확인됨(수집한 글 또는 공지 문장) → `auto_blockers`에 이유
  (`terms_forbid_automation`, `login_required`, `sns_action_required`, `proof_or_photo_required`, `visit_required`, `review_required`, `eligibility_restricted`, `purchase_required`)
  공지 문장이 근거면 `evidence.facts`에 같은 항목과 `quote`가 있습니다.
- `null`: 판단 근거 부족 → `auto_blockers`에 부족한 것(`entry_url_not_verified`, `not_a_form`, `steps_unknown`, `notice_not_read`)
  `notice_not_read`는 공지를 읽지 못해(정책상 읽지 않는 곳 포함) 근거를 더할 수 없었다는 뜻입니다.

`auto_ok=true`는 기술적으로 사람 계정 없이 응모가 끝난다는 뜻일 뿐, **주최 측 약관의 허락이 아닙니다.**
응모 전 반드시 `notice_url`의 약관을 확인하고, 사람 대신 제출할 때는 본인 동의와 1인 응모 제한을 지키세요.

### 응모 제한 `limits`

| 필드 | 설명 |
|---|---|
| `per_person_limit` | 1인(계정)당 응모 횟수. '1인/계정당 N회 참여·응모·신청'이면 N, '중복 응모 불가'면 1 |
| `per_day_limit` | 하루 응모 횟수('하루 N회 응모' 등) |
| `duplicate_entry_allowed`, `duplicate_win_allowed` | 중복 응모·중복 당첨 허용 여부 |
| `sns_account.required` | SNS 계정 필요 여부 |
| `sns_account.platforms` | `insta`·`yt`·`naver`·`x`·`tg` |
| `sns_account.public_account_required` | 공개 계정 필수(비공개 계정 제외) |
| `sns_account.min_followers` | 최소 팔로워 수 |
| `sns_account.event_only_account_excluded` | 이벤트용·부계정 제외 |

## 예시

```json
{
  "id": "eh-40eb0adcce",
  "status": "active",
  "added_on": "2026-11-13",
  "added_at": null,
  "title": "한화자산운용 영상 퀴즈 풀고 상품권 받자 이벤트!!",
  "entry_methods": ["youtube_subscribe", "youtube_comment", "naver_form"],
  "auto_ok": false,
  "auto_blockers": ["sns_action_required"],
  "entry_url": "https://form.naver.com/response/DNiQqRcGjrB",
  "notice_url": "https://www.youtube.com/watch?v=GSsPT72db0M",
  "prize": {"name": "교보문고 3만원권", "value_krw": 30000, "winners": 42},
  "starts_at": "2026-11-11T00:00:00+09:00",
  "ends_at": "2026-11-22T23:59:59+09:00",
  "ends_at_time_known": false,
  "announce_at": "2026-11-27T00:00:00+09:00",
  "announce_methods": ["unknown"],
  "limits": {"per_person_limit": null, "sns_account": {"required": true, "platforms": ["yt"]}}
}
```
(일부 필드 생략)

## 변경 기록

- 2.1: `requirements`, `evidence`, `steps[].source` 필드, `auto_blockers`의 `purchase_required`·`notice_not_read`,
  `feed/auto.json`·`feed/active-lite.json`·`feed/lite.schema.json` 추가(기존 필드·값의 뜻은 그대로)

## 기존 피드와의 관계

`data/feed.json`(schema 1.0)은 그대로 유지됩니다. 새로 연동한다면 이 `feed/`를 쓰세요.
수집 출처와 판별 규칙은 저장소 `collector/agentfeed.py`, `collector/conditions.py`, `collector/notices.py`(원문 공지)에 있습니다.
