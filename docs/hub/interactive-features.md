# Workflow·관측·이미지·음성 연결

이 문서는 구현된 연결을 설명한다. 작업 지침은 [feature-expansion-llm-handoff.md](feature-expansion-llm-handoff.md),
Backend API 계약은 [interactive-contracts.md](../llm/interactive-contracts.md)를 참고한다.
실제 장치/Provider/터미널 검증과 모의 테스트 결과는 구분한다.

## Workflow와 실행 중 지시

Workflow 편집기는 [그래프 중심 UI](workflow-editor.md)로 노드/간선을 추가·수정·삭제한다.
그래프 옆에 선택 노드의 설정 폼을 표시하고 전체 원본은 고급 JSON에서 편집한다.
handler의 Schema가 있으면 설정 폼을 사용하고, 없으면 JSON을 유지한다. 모르는 필드나 UI metadata를
자동 삭제하지 않는다. 노드를 지워도 연결과 entry를 몰래 고치지 않으며 잘못된 초안으로 표시한다.
오류 위치 선택은 structured JSON pointer로 해당 노드 또는 중첩 본문으로 이동한다.
외부 Workflow 오류는 현재 초안의 참조 위치까지 이동하며 외부 정의를 자동 수정하지 않는다.

사용자가 선택한 Graph Engine의 선언으로 실행 없이 검증한다. 참조 revision과 미검증 범위를 표시하고,
초안 변경 뒤 돌아온 오래된 결과를 버린다. 저장은 기존 CAS를 사용한다. 편집 검증은 실행 허가가 아니며
실행 중인 Run의 Workflow snapshot이나 checkpoint를 수정하지 않는다.

실행 중 입력에서는 대기 요청/현재 실행에 전달/선택한 미시작 노드 예약을 구분한다.
Graph 예약은 Backend가 반환한 구조화된 route를 선택하고 실제 전달 시 다시 확인한다.
예약은 대상의 다음 실행 한 번에만 적용하며 실행되지 않은 대상은 기존 unapplied 사유로 종료한다.

## `/terminal`

Hub가 실행한 native 명령과 저장 출력, 선택 Session의 Run → Tool/Shell Step을 관찰한다.
명령 ID/상태/종료 코드와 출력의 문자 범위를 사용자가 지정한다. 조회·다음 페이지·근거 담기 이후
질문을 작성하고 리뷰를 요청하면 선택된 근거만 일반 Agent 요청으로 보낸다.
Data 참조는 참조로 유지하며 원문 전체를 자동 읽거나 과거 명령을 다시 실행하지 않는다.

Builtin output 목적지가 없으면 그 부분의 조회 불가 사유를 표시하고 저장된 Run/Step은 계속 선택할 수 있다.
Project 공유 저장 출력에 알 수 없는 Session/Run provenance를 만들어 붙이지 않는다.
현재 구현은 명시적 재조회이며 별도 프로세스 상태 저장소나 polling 주기를 만들지 않는다.
사용자가 ish 셸에서 직접 실행한 과거 명령을 수집하는 Host adapter는 포함하지 않는다.

## 이미지와 스킬

확장 이미지 팝업은 기존 ASCII 표시를 유지하고 ASCII/native/auto 선택을 제공한다.
native는 확인된 Kitty 직접 연결에서만 사용한다. tmux/screen 등 전달 경로가 불명확하면 ASCII로 돌아간다.
SDK/터미널 wire chunk 크기와 화면 배치는 프로토콜 값이며 Backend resource limit이 아니다.
파일/등록 artifact만 기존 이미지 읽기 경계로 열고 원격 URL은 가져오지 않는다.
paint/resize/스크롤/숨김/복귀/닫기 시 자신이 만든 Kitty image number만 정리한다.

PTK의 실제 window 위치를 읽는 좁은 adapter가 있으므로 터미널·PTK 변경 시 수동 QA가 필요하다.
실제 Kitty 화면, SSH 전달, 폰트/원격 장치 검증은 headless 테스트로 대체하지 않는다.

앱 기본 자산에 `hub-code-review`, `hub-data-analysis` 지침을 추가했다.
기존 선택 Skill/자료 조회 계약을 사용하며 모든 요청에 모든 Skill을 주입하지 않는다.
사용자가 수정한 저장 Skill을 새 자산으로 자동 덮어쓰지 않는다.

## `/voice`

마이크·스피커는 **Hub 프로세스가 실행되는 컴퓨터**에 있다. SSH 접속자의 별도 PC 장치를
자동 연결하는 기능은 없다. 일반 텍스트 모델이 오디오를 직접 처리한다고 가정하지 않는다.

```text
마이크 → 부분 인식/확정 문장 → 기존 텍스트 요청·지시 경로
                                  ↓
스피커 ← streaming TTS ← 공개된 Assistant text
```

입력과 출력은 독립적으로 로컬 설치 모델 또는 음성 서버를 선택할 수 있다. 혼합도 가능하다.
입력만/출력만 구성할 수도 있다. 기본은 비활성이며 `/voice configure`의 JSON 설정은
Hub 소유 `.hub/speech.json`에 저장한다. 파일 없음 또는 명시적 null이면 비활성이다.

선택적 의존성은 운영자가 설치한다. 예를 들어 Linux Python 3.12.14 환경에서
`python -m pip install -e '.[hub-audio,speech-local]'` 또는 `'.[hub-audio,speech-server]'`를
사용할 수 있다. 시스템 PortAudio 장치 지원과 모델 파일/음성 서버는 별도로 준비해야 한다.
이 명령은 사용 예제이며 Hub가 자동 실행하지 않는다.

- 로컬 예제: [speech.local.example.json](../../examples/hub/speech.local.example.json).
  faster-whisper 모델 디렉터리, Piper 모델 및 대응 설정 파일이 이미 설치되어 있어야 한다.
- 서버 예제: [speech.server.example.json](../../examples/hub/speech.server.example.json).
  URL·모델·voice·인증을 실제 음성 서버에 맞춘다. Gemma의 chat-completions endpoint만으로는 충분하지 않다.
- 예제 수치는 앱에서 선택한 예시다. 설정을 생략하면 이 수치를 적용하지 않는다.
  `device: null`도 사용자가 명시한 SDK 기본 장치 선택이며 key 생략과 다르다.

`capture/playback`의 device, block_frames, queue_size를 모두 명시한다.
입력에는 submit=confirm 또는 automatic을, 출력에는 read_responses를 명시한다.
읽기가 활성화되면 text_chunk_chars가 필요하다. 이는 텍스트 분할 규모이며 문장 경계를 보장하지 않는다.

`/voice start`는 녹음을 시작하고 `/voice finish`는 현재 발화를 확정한다.
speech_threshold와 silence_frames를 둘 다 명시하면 음성/무음 기준으로 연속 발화를 구분한다.
미설정 시 임의 VAD 기준을 만들지 않으며 사용자가 finish한다. `/voice cancel`은 현재 녹음을 버린다.
`/voice output`은 명시적으로 활성화된 읽기를 연결하고 `/voice quiet`는 재생만 중단한다.
창 숨김·세션 변경·종료는 해당 장치를 해제하고 늦은 결과를 폐기한다.

부분 인식은 알림/초안이며 Run으로 보내지 않는다. 최종 순서·종료 검증을 통과한 문장만 입력창에 넣는다.
confirm은 사용자가 보내야 하고 automatic도 기존 승인 창/설정/팝업을 우회하지 않는다.
음성의 “승인”을 자동 승인으로 해석하지 않는다. 기존 interaction의 선택지를 사용자가 확인한다.
실행 중 전달 방식이나 Graph 대상도 기존 명시적 선택을 유지한다.

확정 발화 ID를 request_key로 전달하고 ACK 유실 후에는 Backend의 접수 결과를 조회한다.
UI가 살아 있는 동안 같은 미확인 초안의 키를 유지한다. 조회 실패를 미접수로 취급하지 않는다.
프로세스 재시작 후 키를 가진 호출자는 file 저장의 접수 영수증을 조회할 수 있다.
Hub는 종료 전 미접수 음성 초안을 별도 outbox에 저장하거나 재시작 후 자동 재전송하지 않는다.
새로 녹음한 발화는 새 요청이며 사용자가 다른 내용으로 수정한 초안도 별도 요청이다.

말하기 시작하면 재생만 멈춘다. Run/Tool을 자동 취소하거나 효과를 반복하지 않는다.
취소/중단/실패 응답의 늦은 audio를 버린다. `policy.final_review`가 켜졌다면 draft 후보를 읽지 않고
공개 text를 기다린다. 꺼져 있으면 음성 때문에 Reviewer/검토 대기를 추가하지 않는다.

버퍼 overflow는 오류로 드러내고 오디오 일부를 몰래 버린 뒤 성공한 발화로 제출하지 않는다.
재생은 bounded queue로 SDK에 backpressure를 건다. raw 오디오는 보관하지 않는다.
음성 SDK의 usage 미제공은 unknown/None이며 0으로 추정하지 않는다. 기존 Run의 모델 사용량과
음성 SDK 진단 usage는 별도이며 음성 비용이 Run token 합계에 포함됐다고 표시하지 않는다.
Provider 진단에는 첫 chunk/전체 시간, Hub에는 첫 장치 write 및 관찰된 draft→공개 대기 시간이 남는다.
이 시간은 실제 스피커 소리/네트워크 왕복의 실측을 대체하지 않는다.

로컬 Whisper partial은 명시된 frame 간격마다 누적 발화를 재인식한다. 모델의 native token streaming이나
무제한 녹음 지원이 아니다. 로컬 발화 최대 frames를 반드시 정한다. 동기 native 추론은 즉시 선점하지
못하므로 취소 후 정리 완료까지 해당 kernel 종료를 기다릴 수 있다.

현재 단계는 STT → 텍스트 Run → TTS다. 네이티브 audio-to-audio 모델, 응답 중 provider-side steering,
문장별 완료 검토, SSH 장치 bridge, 자동 reconnect/backoff는 추가하지 않았다.
실제 모델/서버/장치/Kitty 수동 QA는 별도 수행해야 한다.
