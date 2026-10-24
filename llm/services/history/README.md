# History — 대화, 문맥, 복구와 보관

Session의 대화 저장과 모델에 전달할 문맥을 관리합니다. 재시작 후 불완전한 기록을 정리하고, 명시한 보관 정책에 따라 삭제 계획을 만드는 코드도 이곳에 있습니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | 대화·이력 서비스 패키지 설명입니다. |
| [conversation.py](conversation.py) | append-only JSONL 및 memory ConversationStore, 공유 factory와 Project별 저장 선택을 구현합니다. |
| [conversation_records.py](conversation_records.py) | 대화 이벤트의 정규 필드·Writer/Reader·사전 검증과 JSONL 기술 형식을 소유합니다. |
| [context.py](context.py) | ContextPolicy에 따른 공통 대화 선택입니다. 모델 입력 예산은 공용 CompletionPolicy가 담당합니다. |
| [turns.py](turns.py) | Run별 요청·응답·추가 지시의 조회 묶음과 시점 복제 범위를 선택합니다. 새 저장소를 만들지 않습니다. |
| [recovery.py](recovery.py) | stale Run/Step/Assistant 상태를 복구하고 대기 요청 재구성을 지원합니다. |
| [retention.py](retention.py) | 기간·용량·토큰 등의 명시된 보관 정책에 따른 계획·보호 검사·정리를 담당합니다. |

## 저장 방식

파일 저장은 JSONL 이벤트를 추가하며 스트리밍 때 전체 대화를 다시 쓰지 않습니다. memory 저장은 명시적 선택이며 프로세스 종료 시 대화·대기 요청이 소실됩니다. Project/Session/Run/Step 메타데이터는 두 모드 모두 영속됩니다.

런타임·조회·복제는 SessionManager의 같은 ConversationStore factory를 사용해야 합니다. 기존 Project의 저장 방식을 다시 추정하거나 파일 대화를 memory로 자동 가져오지 않습니다.

## 복구와 장기 문맥

대화 선택 모드는 `core/policies.py`의 `ContextMode`가 소유합니다. Project Schema,
ContextPolicy 검증·대화 선택과 Hub의 명시적 선택은 같은 선언의 문자열 값을 사용합니다.
필수 한도와 현재 입력·재개 계보의 보존 규칙은 그대로 적용합니다.

프로세스 재시작 시 실행 중이던 Run·Step은 interrupted로 정리합니다. QUEUED 요청은 복구할 수 있지만 과거 실행을 자동 replay하지 않습니다. 명시적 재개는 Run/Engine 체크포인트 계약을 따릅니다.

추가 지시는 일반 QUEUED 요청이 아닙니다. stale Run의 미반영 대상은 사유를 남기고 자동 실행하지 않습니다. 단독 Loop의 원래 요청과 지시는 한 턴이며, Graph 노드 대상 지시는 해당 실행의 체크포인트로만 복원하고 다음 일반 대화에 삽입하지 않습니다. 보관 정책은 미완료 Run의 중첩 지시 참조도 보호합니다.

문맥 제한이 없으면 별도 필터나 토큰 상한을 만들지 않습니다. 장기 기억의 저장·검색·요약 처리는 [Memory Component](../../components/memory/README.md)가 소유하고, 이 폴더는 대화와 정책 적용을 담당합니다. 보관 삭제도 명시한 정책과 보호 검사를 따릅니다.

## 대화 턴 삭제

`SessionHandle.delete_turn/adelete_turn`은 원본을 지우지 않고 일반 조회·새 문맥·복제에서 턴을 숨깁니다.
먼저 Session 런타임을 종료해야 하며 queued/streaming 작업은 없어야 합니다. 마지막 재개가 완료되면
원본의 paused 상태가 남아 있어도 삭제할 수 있습니다. 미완료 재개 체크포인트가 참조하는 턴은 보호하며,
다른 체인의 참조도 확인합니다. 삭제 표시된 필수 메시지는 재개에서도 거부하고 새 요청을 사용하도록 안내합니다.
`conversation(include_deleted=True)`와 Run/Step 조회는 원본 증거를 계속 제공합니다.
체크포인트 참조 검사는 RunRepository가 소유하고, SessionManager는 검사와 삭제 표시를 같은 트랜잭션으로 처리합니다.

[상위 안내](../README.md)


## 턴·문맥 선택의 선언과 소비

ContextPolicy의 completed_only/recent_only/has_character_limit이 선택 모드의 정규 의미를
제공한다. ConversationContextBuilder는 같은 Owner를 호출하며 현재 입력, 완료/실패 상태,
선택 문자 예산과 최신 재개 자손에 지시를 연결하는 기존 순서를 유지한다. 범위가 있는
추가 지시는 SteeringScope.ROOT와 구분해 해당 실행 Checkpoint에서만 복원한다.
복제는 사본에서 Message.detach_run과 기존 Admission/Transfer/Instruction Owner를 사용한다.
원본 Run 연결·영수증을 지우거나 예약된 지시를 자동 실행하지 않는다.

ConversationTurns는 Run별 요청/응답 묶음과 포함 시점 선택을 소유한다. 공개 함수는 같은
구현으로 연결한다. queued 입력이 섞인 조회 묶음과, 정렬한 Run 문맥에서 현재 입력을
보호하는 ContextBuilder의 선택 알고리즘은 목적과 입력 순서가 다르므로 하나로 합치지 않는다.
기존 system/developer 문맥 유지와 일반 user 역할 비교는 각각 공통 역할 Owner를 참조하며
저장 Message의 허용 역할을 확대하지 않는다. 이 정리는 저장·재개·삭제 규칙을 변경하지 않는다.


## 대화 이벤트 검증과 원본 보존

ConversationEvent가 고정 이벤트 종류·필드와 변이 검증을 소유하며, Conversation은 메시지
존재·중복·스트리밍/출력 소유권을 확인한다. 생성은 Message.from_record, Run 연결·metadata
변경은 Message.validate_changes를 통해 기존 완전한 Message 계약을 재사용한다. 공개 변경은
주입 저장소의 쓰기 훅을 부르기 전에 검증하며 기본 파일/메모리 저장소는 직렬화한 스냅샷도
효과 발생 전에 검사한다. 재열기에도 같은 검증을 적용해 잘못된 Run ID·operation을 정상으로
받아들이지 않는다. 기존 생략 operation의 append 의미와 명시적 null Run 연결은 유지한다.

ConversationJournal은 기존 JSONL 바이트 형식을, ConversationFileStamp는 증분 읽기의 파일
identity·크기·수정 시각을 소유한다. fsync 후 상태 적용·rollback 무효화·부분 끝줄 거부·명시적
prune의 원자 교체 순서는 유지하며, 토큰마다 전체 대화나 누적 본문을 복사하지 않는다.
