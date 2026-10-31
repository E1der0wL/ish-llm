# Hub 요청 상태 전달과 스트리밍 비용 검토 (2026-10-31)

이 기록은 [작업 지침](request-latency-handoff.md)에 따른 Hub 변경과 검증이다.
기준 HEAD는 `cade23d161c088890e920214a9638b6d8004c1d0`이며, 당시 작업 트리의
미커밋 llm 최적화도 두 비교본에 동일하게 포함했다. HEAD의 llm으로 되돌리지 않았다.
사용자 workspace를 열거나 변경하지 않았고, 커밋·push는 수행하지 않았다.

## 확인한 결함과 변경

### 저장된 Run 시작 알림

`HubRuntime._run_event()`가 실제 `Run`에 없는 `project_id`를 읽어
`AttributeError`를 냈다. 이 예외는 queued event consumer에서 발생해 모델 실행은
진행되지만, Hub의 즉시 시작 알림과 이후 해당 이벤트의 화면 무효화가 중단됐다.
따라서 화면은 다음 공개 이력 조회를 기다려야 했다.

이제 이미 보유한 해당 Session 핸들의 `project.id`를 사용한다. 현재 화면의 Project를
추정하거나 별도 디스크 조회를 추가하지 않는다. 세션 전환으로 등록이 해제된 알림은
새 화면에 전달하지 않는다. Run/Step/승인/대화 저장과 실제 실행은 여전히 llm 소유다.

기존 단위 테스트의 임의 객체에는 실제 Run에 없는 필드가 들어 있어 결함을 놓쳤다.
테스트를 실제 Run 자료형으로 바꾸고, 다른 현재 Project·잠긴 snapshot·늦은 알림·
접수 callback 순서·terminal 표시를 검증한다. 저장 전 로컬 준비 표시와 저장된
접수/Run 시작을 구분하는 기존 계약은 유지한다.

### 스트리밍 블록의 너비

기존 렌더 캐시는 완성된 코드/표 블록의 줄을 재사용했지만, 변경된 응답의 전체 줄 너비를
매번 다시 계산했다. 2,000줄 코드 뒤에 40번 접미사를 붙이는 단일 스레드 진단에서
4.14초 중 약 3.77초가 `fragment_list_width` 반복 계산이었다. 이 수치는 프로파일러가
켜진 합성 CPU 진단이며, 실제 모델 응답 시간은 아니다.

기존 블록 캐시에 계산한 셀 너비를 함께 보관한다. 기존 content/revision/화면 너비/테마/
renderer 키가 그대로 무효화를 결정하며, 새 전역 캐시·스레드·튜닝값은 추가하지 않는다.
본문 레이아웃은 정규 `TranscriptLayout.INDENT`와 빈 간격의 동일한 규칙을 사용한다.
한글 셀 너비, 최종 렌더, resize, 객체/anchor, dynamic renderer 동작은 유지한다.
미완성 Markdown과 생각 본문은 계속 정규 렌더러를 사용한다. 응답을 자르거나 문자를
인위적으로 지연시키지 않으며, 완료 검증 전 초안과 검증된 답변의 경계도 유지한다.

## 유지한 경로와 범위

- 기존 SDK 초기화와 `prepare_provider=False`, custom Engine 조건을 유지했다.
- 제출 후 snapshot 재조회 제거, fresh Session 사전 확인, 전경 명령의 snapshot 취소,
  관찰 캐시/이벤트 무효화/누락 후 재조정, Run 범위 binding 캐시를 중복 구현하지 않았다.
- 실제 submit·승인·resume 판단은 관찰 캐시로 대체하지 않았다. 저장 취소 drain,
  체크포인트와 Tool 효과 기록, durable QUEUED 순서를 변경하지 않았다.
- `tool_context_chars`는 general의 양의 정수이며 기본 24,000자다. 필드 생략 시에만
  선언된 기본값을 사용하고 read는 저장하지 않는다. 명시값과 다른 필수 필드의 엄격한
  검증을 보존했다. 설정 폼 → Preferences → `create_backend()` → HubBuiltinTools의
  처리기로 전달하며, 기존 재시작 적용 시점을 유지했다.

## 검증 환경과 측정 방법

기록과 harness는 `tests/hub/reports/request-latency-20261031/`에 있다. 보고서는 자동
discovery와 배포에서 제외된다. `tests.source_tree.copy_snapshot`으로 양쪽 플러그인과
현재 검토 자료를 WSL Linux 파일시스템에 복사하고 해시를 기록했다.

- before: `/home/user/.cache/ish-hub-latency-before-s_vcam7y`
- after: `/home/user/.cache/ish-hub-latency-after-rn_pn1yi`
- Python: 3.12.14, `.venv-linux312`의 동일 고정 의존성
- 비교본의 `llm/` 388개 소스·자산 해시가 동일함을 확인했다.
- 실제 ish 참조 Prompt와 PTK pipe 입력, Hub 설치/Controller/BackendWorker, file store,
  실제 LiteLLM HTTP와 로컬 SSE 서버를 사용한다. `after_render`의 화면 셀을 확인한다.
- 외부 네트워크는 loopback 외 연결을 거부한다. 실제 fsync를 실행하면서 횟수·시간을
  기록하며, API 키·원문·전체 환경은 보고서에 넣지 않는다.
- 모델은 fixture의 `openai/gpt-4o-mini`, Agent는 assistant, 출력 주기는 0.1초다.
  측정 fixture에서만 title/postprocess/final_review를 명시적으로 끈 동일 설정을 사용한다.
  이 선택은 앱 기본값을 변경하지 않으며, 해당 기능의 동작 검증은 Hub 회귀에 포함된다.
- 첫 인사, 반복 인사, 파일 읽기의 두 completion, 제공된 긴 본문, 그 뒤의 긴 이력 요청을
  구분한다. 후속 Tool completion을 중복 submit으로 집계하지 않는다.

측정 중 harness의 잘못된 Handle import, 미지원 HubConfig 인자, file_read 필수 인자 누락은
각 실패 보고서에 보존했다. 별도 전체 경로 profiler는 연결 완료 전 timeout하여 비교 표본에서
제외했다. 수정 전후 성공 비교와 단일 스레드 렌더 진단을 이 실패들과 합산하지 않는다.

집중 검증은 11개 모듈, 89 passed, failures/errors/skips 0이다.

### 반복 측정 결과

성공한 실행 순서는 after-1 → before-1 → after-2 → before-2다. 버전당 새 프로세스
2개에서 각 6개 요청을 보냈고, 아래 값은 **Enter부터의 초 단위 중앙값**이다.
괄호는 두 표본의 최소–최대다. 표본 수가 작으므로 유의한 전체 지연 개선이나 상한을
입증하는 자료로 해석하지 않는다. 경계별 원본·최소·최대·조회 횟수는 `comparison.json`에 있다.

| 요청 | HTTP 도달 (수정 전) | HTTP 도달 (수정 후) | 최초 본문 화면 (전) | 최초 본문 화면 (후) |
| --- | ---: | ---: | ---: | ---: |
| 첫 인사 | 9.334 (8.892–9.777) | 9.284 (9.258–9.310) | 11.330 | 10.161 |
| 반복 인사 | 4.744 (4.716–4.773) | 4.844 (4.732–4.955) | 5.254 | 5.920 |
| 첫 파일 읽기 | 6.118 (6.093–6.143) | 6.613 (6.273–6.952) | 11.540 | 12.362 |
| 반복 파일 읽기 | 6.623 (6.183–7.062) | 7.017 (6.609–7.424) | 11.886 | 13.326 |
| 긴 본문 요청 | 7.570 (7.447–7.694) | 7.586 (7.427–7.745) | 10.224 | 8.539 |
| 긴 이력 후 요청 | 5.604 (5.501–5.708) | 5.787 (5.153–6.421) | 6.209 | 7.345 |

HTTP는 각 요청의 첫 completion이고, 파일 읽기 결과 화면은 두 번째 completion 뒤다.
네 프로세스 모두 첫 HTTP의 메시지 수/도구 수/본문 바이트는 각 시나리오에서 같았다.
인사는 3/1/1,482, 첫 파일 읽기는 9/37/23,075, 반복 파일 읽기는 11/37/23,257,
긴 본문 요청은 12/69/75,825였다. 마지막 시나리오는 긴 저장 이력이 있는 Session에서
다시 인사한 것으로, 기존 인사 분기에 따라 전송 문맥은 3/1/1,482로 축소된다.
따라서 긴 **저장 이력 조회**는 포함하지만 긴 이력을 모두 모델에 재전송하는 시험은 아니다.
새 임시 workspace 생성과 시작 준비는 별도로 전 17.009–17.191초, 후 16.817–17.872초였다.
기존 사용자 workspace를 다시 여는 비용과 같다고 볼 수 없다.

입력 버퍼는 양쪽 모두 약 0.03–0.04초에 소비됐다. 수정 후 저장된 접수 callback은
0.604–1.170초, 저장된 Run 시작 callback은 1.137–2.472초였다. 수정 전 시작 callback은
12개 요청 모두 실제 Run 자료형의 `AttributeError`로 누락됐고, 수정 후 오류는 0이다.
초기 준비 lock 대기는 모두 0.001초 미만이었다. 접수부터 첫 HTTP 전 실제 fsync는
양쪽 모두 요청당 12–15회였으며, 저장 횟수/내구성을 줄이지 않았다.

버전당 실제 HTTP는 16회로 같다. 측정 구간의 공개 관찰 조회 합계는 356 → 386회,
완료 snapshot은 186 → 168회, 취소 snapshot은 12 → 24회였다. 기존 오류 때문에 생략됐던
Run 시작 관찰이 복원되어 조회·취소가 늘어난 부분도 있다. 따라서 **전체 조회량이나 첫
본문 표시가 빨라졌다고 주장하지 않는다**. 일부 본문 표본은 더 늦었다. 빠른 Run 시작
표시를 위해 durable 상태 관찰을 생략하거나 실행 판단을 오래된 화면 캐시로 바꾸지 않았다.
UI loop 최대 지연은 전 0.370초 이하, 후 0.311초 이하로, 모든 프레임의 0.1초 보장도 아니다.

별도 렌더 측정은 profiler 없이 양쪽을 번갈아 3회 실행했다. 2,000줄 코드 뒤에 40번
접미사를 갱신하는 CPU 작업의 중앙값은 **1.1134 → 0.1834초 (약 83.5% 감소)**였다.
범위는 전 1.1115–1.1193초, 후 0.1815–0.2027초이며, 갱신당 중앙값은 약 27.8 → 4.6ms다.
이것은 해당 합성 블록 갱신의 개선이지 전체 TUI나 모델 추론 속도의 6배 개선이 아니다.
동일한 최종 행·셀 너비를 검사했고, 기존 캐시에 블록별 너비만 추가했다.

### 남은 호출 전 비용의 추가 진단

전체 회귀가 끝난 후 초기화와 첫 요청을 제외한 반복 요청을 따로 프로파일링했다.
첫 시도는 Python 3.12의 동시 profiler 충돌(`Another profiling tool is already active`)로
실패했다. 이 계측 어댑터는 StorageIO 호출을 감쌌으므로 저장 batching 표시에도 영향을
줄 수 있다. `warm-profile-invalid/` 결과는 정상 동작·저장 비용·성능 비교의 증거에서
전부 제외했다. 앱 실패로 집계하거나 제품 코드를 그 실패에 맞추어 변경하지 않았다.

후속 `warm-profile-single/`은 StorageIO를 전혀 감싸지 않고 profiler 인스턴스 하나만
반복 요청 구간에서 켰으며 정상 완료했다. 이 Python 빌드에서는 UI와 저장 thread의
호출도 관찰되므로 backend thread만의 비용으로 해석하지 않는다. 함수 호출 약 7,417만 회,
PTK redraw 약 690회, runtime component resolve 2회, Tool 등록 420회가 관찰됐다.
정규 컴포넌트 접근·경로 확인, 도구 binding, 화면 전체 layout 비용이 함께 남아 있다.
Admission과 실제 실행의 검증이 각각 필요할 수 있으므로 호출 횟수만으로 이를 불필요한
중복이라고 판단하거나 제거하지 않았다.

계측을 켠 반복 요청은 HTTP까지 47.085초로 크게 늘었다. 따라서 이 cumulative profile의
시간을 원래의 약 4.8초에 배분하거나 병렬 호출 시간을 합산하지 않는다. 원인 후보를 좁히는
보조 진단일 뿐이며, 정규 API의 권한·경로·저장 검증을 생략하는 추가 최적화는 하지 않았다.

### 정적 검사

- S1–S7 architecture gate 통과: new/stale/required outstanding/review errors 모두 0.
- 문서 검사: 198개 문서, Python 예제 170개, 상대 링크 1,277개, 오류 0.
  처음에는 handoff의 로컬 보고서 3개가 배포용 소스 snapshot에 포함되지 않아 실패했다.
  원본 보고서는 보존하고, 현재 handoff에서 배포 링크 대신 로컬 경로임을 표시했다.
- 계약 inventory: 파일 698, stale/violation/incomplete/unreadable/issues 0.
  기존 미검토 파일 68개와 미분류 후보 4,755개는 그대로다. 전체 계약 감사 완료를
  주장하지 않으며, 다른 작업의 검토를 일괄 승인하지 않았다.
- 감사 인덱스의 변경 행은 `hub/backend/runtime.py`와
  `hub/widget/transcript/message_cache.py`뿐이다. 기존 검토 분류와 소유권을 보존했다.
- `git diff --check` 통과.

집중 테스트와 전체 Hub 회귀는 위 after 소스로 실행한다. 이후 정적 검사 snapshot은
`/home/user/.cache/ish-hub-latency-audit-0xqjytnc`이며, 그 사이 `hub/`, `tests/hub/`,
`llm/`의 소스·자산 해시는 동일하다. 추가 변경은 검토 증거와 문서다.
정적 검사 및 문서 재검사의 실제 결과는 `gates/`에 보존했다.

### 전체 Hub 회귀

Linux Python 3.12.14에서 전체 **905 passed**. failures/errors/skips/expected failures/
unexpected successes/missing/unexpected/duplicates 모두 0이며 exit 0이다.
시간은 2,004.783초였다. 동일 환경의 Xvfb와 xclip으로 네이티브 X11 클립보드 검사도
실행했다. 제출, 스트리밍, 활성 Run 중 예약, 승인, 명시적 중단/재개, 동시 Session,
이력 복제/삭제, 설정, RAG, 화면 cache/resize 등 전체 Hub discovery를 실행했다.
LLM 전체 회귀는 반복 실행하지 않았다.

실제 명령은 아래와 같으며 cwd는 위 after snapshot이다.

```sh
/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python \
  -m tests.llm.run_regression --suite hub \
  --output /home/user/.cache/ish-hub-latency-validation-37jrnzbt/hub.json
```

원본 report/log/실행 인자는 작업 보고서 디렉터리의 `full/`에 복사했다. `scope.json`으로
시작 시점의 llm 388개를 포함한 보호 대상 804개 파일의 원본 byte hash, 테스트한
Hub·Hub 테스트 463개 파일의 원본 byte hash가 현재와 같음을 확인했다. 관련 없는
검토 자료 변경은 0이며 Git HEAD도 동일하다.

## 실제 ish에서 확인할 절차

현재 작업 파일은 `D:\WorkSpace\ish\hub`다. 이미 실행 중인 worker는 소스 파일을 고치는
것만으로 바뀌지 않는다. ish의 플러그인이 이 checkout의 Hub를 로드하는지 확인한 다음
ish를 종료·다시 시작한다. `.ishrc.py`에서 불러온 `hub_plugin.__file__` 경로로 로드 위치를
확인할 수 있다. 다른 설치본을 가리킨다면 그 설치본은 이번 테스트 소스가 아니다.

실제 대화 데이터와 분리한 테스트 workspace를 지정하고 다음을 확인한다.

1. 시작 진행 표시 완료 후 인사 → 같은 Session에서 반복 인사. Enter 직후 입력 소비,
   저장 후 접수 표시, Run 시작 후 준비/실행 표시를 구분한다.
2. 허용된 임시 파일을 읽고 결과를 질문한다. 기존 Agent 모델·API 설정을 사용한다.
3. 긴 코드/Markdown 응답의 스트리밍, 자동 스크롤, 과거 위치로 스크롤, resize를 확인한다.
4. 임시 파일 생성 승인을 요청해 승인 전 파일이 없고 승인 후 한 번 생성되는지 확인한다.
5. 출력 중 중단, 명시적 재개, 실행 중 다음 요청, Session 전환을 확인한다.
6. general의 Tool 문맥 상한을 저장·재시작·재열기해 적용값을 확인한다.

이 작업의 자동 ish 검증은 참조 호스트와 가상 터미널 입력/출력에 한정된다.
사용자의 실 터미널, 사내 SSH endpoint의 추론 시간과 인증은 검증하지 않았다.
Hub 전체 회귀는 이 소단계의 사용 준비 검증이며, LLM 전체/원격 CI/배포 Level 3 완료를 뜻하지 않는다.
