# 티끌모아 티끌 에이전트 피드 (schema 2.x)

AI 에이전트·프로그램이 화면(HTML·JS)을 읽지 않고 이벤트를 받아 가는 정적 JSON입니다.
매시간 수집 뒤 다시 만들어집니다. 인증·키·요청 한도 없음, 모든 시각은 한국 시간(`+09:00`).

| 주소 | 내용 |
|---|---|
| `feed/events.json` | 진행 중 이벤트 전체(마감 가까운 순) |
| `feed/new/YYYY-MM-DD.json` | 그날 처음 등록된 이벤트(최근 30일, 지금은 종료된 것도 `status`로 표시) |
| `feed/new/index.json` | 일자 파일 목록과 건수 |
| `feed/atom.xml` | 새로 등록된 이벤트 100건(Atom) |
| `feed/schema.json` | JSON Schema(draft 2020-12) |

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

- `true`: 검증된(A) 응모 주소(`entry_url`)가 네이버폼·구글폼 등 폼 자체이고, 절차가 확인됐고, 로그인·SNS 행동·인증샷·방문·후기·자격 제한·자동화 금지 문구가 **확인되지 않음**
- `false`: 위 중 하나가 확인됨 → `auto_blockers`에 이유
  (`terms_forbid_automation`, `login_required`, `sns_action_required`, `proof_or_photo_required`, `visit_required`, `review_required`, `eligibility_restricted`)
- `null`: 판단 근거 부족 → `auto_blockers`에 부족한 것(`entry_url_not_verified`, `not_a_form`, `steps_unknown`)

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

## 기존 피드와의 관계

`data/feed.json`(schema 1.0)은 그대로 유지됩니다. 새로 연동한다면 이 `feed/`를 쓰세요.
수집 출처와 판별 규칙은 저장소 `collector/agentfeed.py`, `collector/conditions.py`에 있습니다.
