# History — 대화, 문맥, 복구와 보관

Session의 대화 저장과 모델에 전달할 문맥을 관리합니다. 재시작 후 불완전한 기록을 정리하고, 명시한 보관 정책에 따라 삭제 계획을 만드는 코드도 이곳에 있습니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | 대화·이력 서비스 패키지 설명입니다. |
| [conversation.py](conversation.py) | append-only JSONL 및 memory ConversationStore, 공유 factory와 Project별 저장 선택을 구현합니다. |
| [context.py](context.py) | ContextPolicy와 CompletionPolicy에 따른 대화 선택·토큰 예산 적용입니다. |
| [turns.py](turns.py) | Run별 요청·응답·추가 지시의 조회 묶음과 시점 복제 범위를 선택합니다. 새 저장소를 만들지 않습니다. |
| [recovery.py](recovery.py) | stale Run/Step/Assistant 상태를 복구하고 대기 요청 재구성을 지원합니다. |
| [retention.py](retention.py) | 기간·용량·토큰 등의 명시된 보관 정책에 따른 계획·보호 검사·정리를 담당합니다. |

## 저장 방식

파일 저장은 JSONL 이벤트를 추가하며 스트리밍 때 전체 대화를 다시 쓰지 않습니다. memory 저장은 명시적 선택이며 프로세스 종료 시 대화·대기 요청이 소실됩니다. Project/Session/Run/Step 메타데이터는 두 모드 모두 영속됩니다.

런타임·조회·복제는 SessionManager의 같은 ConversationStore factory를 사용해야 합니다. 기존 Project의 저장 방식을 다시 추정하거나 파일 대화를 memory로 자동 가져오지 않습니다.

## 복구와 장기 문맥

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
