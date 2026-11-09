# 외부 연결과 Google Calendar

공통 연결 레지스트리와 Google Calendar 연동을 제공한다. 기본은 읽기 전용이고, [Google 일정 편집](google-calendar-editing.md)은 계정의 `write`와 OAuth 쓰기 권한으로 켠다. 실제 계정 연결은 사용자가 명시적으로 수행한다. 기본 동작은 외부 연결 없음이며, 처음 생성하는 JSONC에는 비활성 Google 계정·캘린더 예시가 들어간다. 기존 설정 파일과 개인 값을 자동으로 덮어쓰지 않는다.

## 책임과 데이터 경로

```text
Google OAuth / Calendar REST
    → integrations/google (인증, 권한, API 응답 해석)
    → CalendarProvider / ExternalEvent (캘린더가 소유한 조회 계약)
    → CalendarService (가시성, 월별 캐시, 실패·재조회 정책)
    → CalendarListing (로컬 + 외부 읽기 모델)
    → CalendarPage (월간 달력, 읽기 전용 상세)
```

`connections/`는 ID·활성 상태·공급자 생성·지원 capability 조회만 담당한다. Google wire 필드, 달력의 반복 규칙이나 UI를 해석하지 않는다. `app/composition.py`가 실제 공급자를 조합한다. Google 설정과 기본 source 예시는 `GoogleAccount`, 소스 조회 간격과 식별자는 `CalendarSource`, 연결 상태와 문구 매핑은 `ConnectionState`가 선언한다. JSONC·schema·실행 코드는 같은 선언을 사용한다.

외부 일정은 `CalendarEvent`나 로컬 저장소로 변환하지 않는다. `(connection, calendar, event ID)`로 식별하며 로컬 수정·삭제·알림·예약 명령 실행 경로에 들어가지 않는다. 외부 생성·수정·삭제는 별도의 `CalendarWriter`와 `CalendarEdits`가 담당한다. 로컬 일정과 같은 ID여도 충돌하지 않는다. 기능별 정책은 feature service에 두므로 향후 뉴스나 메일은 자신의 모델·capability·어댑터를 추가할 수 있다. 아직 사용하지 않는 패키지나 Gmail·Maps 권한은 만들지 않는다. Open-Meteo와 Hub/LLM 전달 동작은 변경하지 않는다.

## 설정

기존 JSONC에 다음 항목을 병합한다. 아래는 설정 예시이며 다른 개인 값은 유지한다.

```jsonc
{
  "connections": {
    "google": [{
      "id": "google",
      "enabled": true,
      "client_file": "google-desktop.json",
      "timeout_s": 20,
      "login_s": 180
    }]
  },
  "popups": {
    "calendar": {
      "sources": [{
        "connection": "google",
        "calendar": "primary",
        "label": "Google",
        "enabled": true,
        "refresh_s": 300
      }]
    }
  }
}
```

계정 `id`는 로컬 별칭이며 이메일이 아니다. `client_file` 상대 경로는 `config.jsonc`의 디렉터리를 기준으로 해석하고 `~`는 Linux 홈으로 확장한다. 여러 계정을 등록할 때 ID를 다르게 정하고 각각 로그인한다. 같은 계정의 다른 캘린더는 sources에 추가한다. `calendar`에는 Google Calendar 설정에서 확인한 캘린더 ID 또는 `primary`를 넣는다. CalendarList API 권한은 요청하지 않으므로 캘린더 선택 목록은 자동으로 수집하지 않는다. 동일 connection/calendar 쌍은 중복 등록할 수 없다.

`clock.timezone`으로 시간 표시를 통일하며 `null`이면 시스템 시간대를 사용한다. 종일 일정은 날짜를 유지하고 Google의 배타적 종료 날짜를 적용한다. 시간 지정 일정은 UTC 오프셋을 보존해 DST 전환도 구분한다. 로컬 일정의 저장·알림 설정은 독립적이다.

## 최초 인증

1. Google Cloud에서 Calendar API를 활성화하고 OAuth 동의 화면을 설정한다. 테스트 앱이면 사용할 계정을 테스트 사용자에 등록한다.
2. OAuth 클라이언트 유형을 **Desktop app**으로 생성하고 JSON을 내려받아 `client_file` 위치에 둔다. API key나 Gemini 모델 키를 넣는 항목이 아니다.
3. dok을 실행하는 Linux Python 환경에서 명시적으로 로그인한다. 설치본은 `~/ish/plugin/script`가 Python 검색 경로에 있어야 한다.

```bash
cd ~/ish/plugin/script
python3 -m dok.connections login --config ~/ish/dok/config.jsonc --account google
```

`python3`는 dok 의존성이 설치된 Python 3.12.14로 바꿔 실행한다. standalone 환경에서는 기존 dok 의존성 외에 `python3 -m pip install 'google-auth-oauthlib>=1,<2'`가 필요하며, ish 설치는 `PLUGIN_META.dependencies`가 선언한다.

브라우저가 자동으로 열리지 않는 WSL 환경에서는 명령이 출력한 인증 URL을 같은 PC의 브라우저에서 연다. 콜백은 `127.0.0.1`의 임시 포트이며 Windows 브라우저에서 WSL localhost로 연결될 수 있어야 한다. 로그인 대기 시간은 `login_s`로 조절한다. OAuth state·PKCE는 Google의 OAuth 라이브러리가 처리한다. 프롬프트나 팝업을 여는 것만으로 로그인 브라우저가 열리지 않는다.

4. 계정과 source의 `enabled`를 켜고 dok 설정을 재로드한다. Alt+C로 캘린더를 열고 필요하면 F5로 조회한다. 이미 활성 상태로 설정한 경우 로그인 뒤 F5만 눌러도 재시도한다.

기본 요청 scope는 `calendar.events.readonly` 하나다. 계정에 `write: true`를 지정하면 `calendar.events`로 바뀌며 새 권한으로 다시 로그인해야 한다. Gmail 읽기/전송, Maps API 사용 권한은 요청하지 않는다. Google의 [권한 안내](https://developers.google.com/workspace/calendar/api/auth), [Desktop OAuth 안내](https://developers.google.com/identity/protocols/oauth2/native-app)를 참고한다.

## 사용과 갱신

- 월간 달력의 `↗` 표시는 외부 일정이다. Enter로 상세를 보고 E로 해당 날짜의 일정 목록을 선택한다. 편집 가능하면 최신 버전의 편집 폼으로 진입한다. N은 새 일정이며 쓰기 가능한 외부 캘린더가 있으면 저장할 위치를 먼저 고른다.
- F5는 설정된 source를 재조회한다. 상단 연결 상태·새로 고침 버튼은 두지 않는다. 조회 중에는 중복 요청을 만들지 않는다.
- 성공한 결과는 기본 300초 동안 재사용한다. 달력이 열린 동안 만료되면 갱신하고, 닫힌 동안 월별 화면 조회는 중지한다. dok 알림·명령어를 등록한 일정은 별도 실행 조회가 유지된다. 닫을 때 진행 중인 비동기 요청을 취소하며 이미 시작한 토큰 갱신 스레드는 완료까지 소유한다.
- 실패 후에는 주기적으로 재시도하지 않는다. 다시 열거나 F5를 누르면 재시도한다. 일반 조회 실패는 마지막 성공 데이터를 `캐시된 정보`로 표시하지만 인증·접근 권한 오류는 해당 캐시를 비운다.
- 월이나 연결 설정이 바뀌면 현재 조회를 교체하고 이전 결과가 새 화면에 게시되지 않도록 한다. 캐시는 메모리에 선택한 월 하나만 유지하며 재시작 후에는 새로 조회한다.
- 모든 페이지를 성공적으로 읽은 뒤 결과를 통째로 교체한다. `singleEvents=true`로 Google이 계산한 반복 회차·예외를 사용하며 취소·삭제된 일정은 다음 성공 조회에서 사라진다. **양방향 동기화나 syncToken 증분 동기화는 아니다.**

월 단위 `timeMin`/`timeMax` 조회와 페이지 처리의 계약은 [Events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list)를 따른다. 401은 토큰을 한 번 갱신한 뒤 다시 조회하고, 계속 실패하면 재로그인을 표시한다. 할당량 오류는 접근 거부와 구분한다. 화면 조회는 재열기/F5로 재시도하고, 실행용 조회는 source.refresh_s 간격을 지킨다. [Google 오류 안내](https://developers.google.com/workspace/calendar/api/guides/errors)

## 저장과 연결 해제

토큰은 JSONC 밖의 `<config directory>/connections/google/<account ID SHA-256>/credentials.json`에 저장한다. 새 디렉터리는 `0700`, 파일은 `0600`이며 파일 잠금과 원자적 교체를 사용한다. 계정 별칭의 경로 구분자는 파일 경로로 해석하지 않는다. OAuth 클라이언트 파일을 바꾸면 다른 클라이언트 토큰을 자동 재사용하지 않는다.

```bash
python3 -m dok.connections status --config ~/ish/dok/config.jsonc --account google
python3 -m dok.connections logout --config ~/ish/dok/config.jsonc --account google
```

`status`는 로컬 인증 파일 유무만 확인하며 네트워크 검증을 하지 않는다. `logout`은 로컬 토큰만 지우고 Google 계정의 앱 권한은 철회하지 않는다. 실행 중 화면의 캐시는 F5로 갱신하거나 source/계정을 끄고 재로드해 비운다. Google 계정 자체의 접근 허용을 철회하려면 Google 계정의 연결된 앱 관리에서 해제한다.

UI 시안은 샘플 일정으로 생성했으며 실제 계정 인증·Google 요청·WSL 개인 설치본 적용은 하지 않았다. UI 확정 후 dok 테스트 424건이 통과했고 선택 Hub·llm 연동 6건은 독립 환경에서 건너뛰었다. 캘린더/Google 테스트는 모두 실행했다. [검증 기록](validation.md)을 참고한다.

일정 실행 설정은 [calendar-actions.md](calendar-actions.md)를 참고한다. 이번 변경은 UI 확정 후 dok 446건 통과, 선택 Hub/llm 6건 건너뜀으로 검증했다. 최신 결과는 [검증 기록](validation.md)을 따른다.
