# 도메인 저장 트랜잭션

`services/infrastructure/transactions.py`의 `TransactionManager`는 각
`WorkspaceOwnership`이 소유한다. 기존 동기 서비스의 `workspace_locked`, Facade의
소유권 범위, 비동기 `StorageIO.run()`이 같은 트랜잭션에 참여한다. 중첩 호출은
새 확정을 만들지 않는다. 잠금 획득 후 읽기 전에 미완료 저널을 복구한다.

도메인 owner가 무결성·참조·상태 전이와 안전한 삭제를 판단하고, 이 계층은 기술적 원자성을
제공한다. [영속 데이터 수명주기 계약](data-lifecycle.md)에 따라 승인·예산·효과 의미나 보존 정책을
공통 저널에서 해석하지 않는다. 아래 원자 저장 지원만으로 모든 도메인의 손상 검증이 완료된 것은 아니다.

검증 실행 시점은 [Level 1–3 검증 정책](verification-policy.md)을 따른다. 저장 원자성·원본 보존·
실제 장애 주입 계약은 매 관련 소단계에 유지한다. 공유 트랜잭션 경계 변경의 영향이 제한됨을
입증하지 못하면 전체 LLM/Hub 게이트로 승격한다.

## 적용 범위

### 실행 안전과 관찰 기록의 저장 보장

저장에는 두 완료 경계가 있다. **준비 완료**는 checksum으로 닫힌 record frame을
flush하고 소유권 잠금 안의 읽기 view에 원자적으로 공개하는 것이다. **내구성 확정**은
그 frame까지의 의존 기록을 함께 fsync한 것이다. 준비 완료를 장애 복구 보장으로 표시하지 않는다.
이 구분은 저장 구현의 명시적 API 계약이며 timeout, 개수, 용량 또는 background flush 기본값이 아니다.

- 요청 QUEUED 접수, 일반 설정/정의/데이터 변경은 기존 내구성 확정 계약을 유지한다.
- dispatch와 초기 실행 준비, 부분 출력은 명시적인 준비 저장을 사용할 수 있다.
  준비 기록은 journal의 읽기 view에만 공개한다. fsync 전 canonical 파일을 먼저 덮어쓰지 않는다.
- Run 실행 진입, 효과를 허용하는 Step/승인/예약, 사용량 예약과 효과 결과 영수증은
  의존 준비 기록까지 함께 내구성을 확정한다. 승인 응답 후에는 여전히 명시적 resume이 필요하다.
- Engine은 실제 작업을 시작하기 전에 실행 허용 이벤트의 ACK를 기다린다. 초기 채널/체크포인트
  준비 ACK는 외부 효과를 허용하지 않는다. 임의 Engine/Host 호출의 기존 진입 경계는 유지한다.
- 출력/진행 관찰 이벤트는 준비 완료 후 전달할 수 있다. OS 장애·전원 차단 시 마지막
  내구성 확정 이후의 관찰 꼬리는 유실될 수 있다. Run 종료, 실패, 취소, 중단, pause는
  최종 상태와 필요한 checkpoint를 확정한 뒤 알린다. 정상 shutdown도 남은 기록을 확정한다.
- 결과가 불확실한 효과를 자동 재실행하지 않는다. 복구는 온전한 frame만 검증하고 확정하며,
  깨진 완성 frame이나 의존성이 없는 기록을 정상 상태로 추측하지 않는다.

저장 계층은 Tool/Engine/provider 종류로 분기하지 않는다. 도메인 owner가 자신의 공개
완료 의미에 맞는 저장 API를 선택한다. 어떤 기록을 재개·권한·예산 판단에 사용한다면,
그 판단으로 외부 실행을 허용하기 전까지 해당 기록의 내구성이 확정되어야 한다.

### 요청 지연 개선의 확정 경계

요청 접수의 QUEUED 확정은 계속 독립적이다. Run 시작은 RUNNING/STREAMING/Session
활성 집합과 함께 실행 진입 증거를 확정한다. 이 증거는 모델이나 Tool을 이미 호출했다는
뜻이 아니라, 이후 Host 실행 hook·Engine 코드가 실행될 수 있어 자동 재생으로 안전성을
추측할 수 없다는 뜻이다. RUNNING 관찰자는 반드시 이 확정 이후 호출한다. 전송 권한과
deadline은 실행 직전 다시 검증한다. 살아 있는 scheduler가 dispatch 미진입을 직접 확인한
취소·검증 실패는 종료 transaction에 미진입 근거를 기록하여 기존 명시적 restart를 유지한다.
이 runtime 증거 없이 재시작한 경우에는 Step 부재로 미진입을 추측하지 않는다.

서로 다른 Engine 이벤트를 미리 소비하거나 외부 callback을 트랜잭션 안에서 실행하여
확정 횟수를 줄이지 않는다. 준비된 실행 binding과 초기 checkpoint/지시 선택은 다음 실행 허용에
함께 확정할 수 있다. Step authorization과 호출 예약도 명시적인 준비 계약 아래 합성할 수 있다.
효과 전후 영수증과 승인은 소비자가 요구하는 ACK를 유지한다. 동기화 횟수는 결과로 측정하며
고정 횟수나 시간에 맞추려고 안전 경계를 생략하지 않는다.

Record batch는 owner가 명시한 best-effort 파생 append를 같은 내구성 frame에 담을 수 있다.
원본을 먼저 반영하고 파생 append를 반영하며, 파생 대상의 장애는 원본 확정을 실패시키지 않는다.
저널의 경로·checksum 검증 자체는 여전히 fail-closed다. 파생 자료는 실행 권한·승인·receipt나
성공 판단의 원본으로 사용할 수 없다. Activity가 이 경계를 사용하며 새로운 비동기 queue,
임의 flush 간격이나 용량 설정은 만들지 않는다. 같은 frame의 fsync가 파생 이벤트의 근거도
보존하므로 별도의 Activity fsync를 요청마다 기다리지 않는다. checkpoint에서 파생 파일도
best-effort로 동기화하며 복구 append는 이미 반영된 동일 bytes를 중복 추가하지 않는다.
정상 저장에서는 관찰 전에 반영되며, 대상 손상·I/O 실패 시 인덱스가 불완전할 수 있다는
기존 best-effort 계약은 유지한다. 일반 undo transaction/독립 Activity 쓰기는 기존 경로다.

이 변경은 Project/Session/Run/Step·승인·재개 checkpoint 형식을 바꾸지 않는다. 내부 record
manifest에는 파생 append 목록이 명시된다. 이전 프로세스를 정상 종료한 뒤 코드를 교체하며,
미완료 옛 저널을 자동 변환하지 않는다.

| 서비스 작업 | 함께 확정하는 원본 |
| --- | --- |
| 요청 제출·취소 | 선택된 Conversation의 메시지 상태·메타데이터; 큐 투입은 확정 후 |
| 요청 dispatch | 모든 수신자의 PENDING Run·빈 출력 원본/manifest, 입력 COMMITTED·완전한 Run map·동일 history snapshot, 각 Assistant QUEUED; 준비 view로 공개하고 Run 진입 전에 함께 fsync |
| Run 시작 | 해당 Run RUNNING·실행 진입 허용 증거, Assistant STREAMING, Session.active_run_ids/status |
| Run 완료·실패·중단·일시정지 | 미완료 Step 상태, Assistant 상태, Run 상태·사용량 확정, Session 활성 집합에서 해당 Run 제거 |
| 출력 델타 묶음 | outputs.jsonl 추가·필수 manifest 갱신·대화 JSONL 추가; 준비 view로 공개하고 다음 강한 경계에서 함께 fsync |
| 최종 출력·Step 완료 | 출력 저널과 Run 결과/대화 또는 Step 결과 |
| Step 생성·시작·수정·종료 | 이벤트 하나에 대응하는 Step 기록 |
| 체크포인트 초기화 | 체크포인트 원본과 Run.metadata.checkpoints/checkpoint_revisions 연결 |
| 체크포인트 갱신 | 노드·Loop 회차·영수증 레코드, 파일 리비전 manifest와 Run의 manifest 리비전 |
| 승인 응답·취소·갱신 | 해당 요청의 응답/제어 레코드 |
| 명시적 재개 | 승인 응답 영수증과 새 QUEUED 요청; 원본 Run은 유지 |
| 자동 승인 정책 | 해당 정책 평가의 응답 전체. PAUSED 확정 후 별도 저장 단위 |
| Tool 원장 | 예약·완료·효과 없음 영수증, Session 원장의 파일/리비전 manifest와 소유 Step의 operation_receipt 근거 |
| 외부 결과 확인 | CAS 원장 반영과 원래 Step의 근거 갱신 |
| Project 생성 | project.json, Session/Component 디렉토리 초기화; 기본 Project 선택은 Application 소유 |
| Project 설정·Component 선택 | 설정·선택, initialized_components 이력과 신규 Component 저장소/사용량 manifest 초기화 |
| Component 모델 호출 | 호출 전 예약과 호출 후 종료 각각의 영수증·파일 리비전 manifest |
| Component JSON 기록 | 생성·수정·명시 삭제의 원본과 records.manifest.json; Data 패키지 정의 포함 |
| Skill 표준 디렉터리 | SKILL.md·자료 파일·디렉터리와 _state의 필수 목록·개별 운영 기록; 정의 JSON 복제 없음 |
| RAG Job | 신규 입력·lease·Job·목록, 상태 갱신의 Job·목록, 승인된 삭제의 Job 트리·목록 |
| Python Tool 패키지 | 생성·수정·명시 삭제의 소스·선언된 requirements·packages.manifest.json |
| Component 영구 제거 | 안전성 검증 후 Project 선택/설정/저장소 이력 제거와 Component 디렉토리 격리 |
| Session 생성·수정·삭제·복원 | Session 레코드 및 디렉토리 수명; 생성 시에만 빈 외부 작업 원장 manifest 확정 |
| Project/Session 복제 | 목적지 메타데이터·Component 자료·선택 저장소의 대화. Run 기록은 복사하지 않음 |
| Project/Session 영구 삭제 | 트리 격리와 호출자가 보유한 객체 상태; 대화 캐시 해제는 확정 후 |
| 백업 복원 | 저널 내부의 준비 사본을 검증한 뒤 목적지 공개 |
| 보관 적용 | 대화 prune, 승인된 Run/Session 제거, 정리 영수증/의도 해제 |
| stale 상태 복구 | 출력 대조·메시지/Step/Run/Session 정리를 한 저장 단위로 처리 |

단일 파일 변경도 같은 계층을 통과한다. 일반적인 조회는 저널 파일을 생성하지 않는다.
기존 삭제/보관 의도 저널의 복구 API는 유지한다. 새 트랜잭션의 확정 전 실패는
부분 변경을 남기지 않고 되돌리므로 같은 의도를 다시 승인 없이 실행하지 않는다.
Run이 시작되기 전 저장 실패로 입력이 QUEUED로 돌아간 경우 Engine은 아직 실행되지 않았다.
이미 시작한 stale Run은 계속 INTERRUPTED로 정리하며 효과를 자동 재생하지 않는다.

## 저장 및 복구 방식

저장 작업의 종류에 따라 두 가지 저널을 사용한다. 디렉터리 복사·삭제·복원과 임의 파일
수정은 아래 undo 경로를 유지한다. Run의 기록 저장은 `StorageIO.run_records()`로 명시적으로
선택하는 record batch를 사용한다. `StorageIO.prepare_records()`는 같은 형식으로 준비만
공개하고 다음 `run_records()`가 그 의존 기록까지 확정한다. 두 경로 모두 같은 workspace 잠금, 중첩 트랜잭션,
객체 rollback, 취소 drain, 저장 이후 관찰 계약에 참여한다. 예전 형식 호환이나 자동 migration이 아니다.

### 기록 묶음 확정

요청 QUEUED 저장, dispatch, Run 시작·실행 진입, 실행 binding, steering 경계, Engine 이벤트와
출력 델타 저장은 JSON·JSONL 변경을 하나의 파일에 모은다. Memory의 호출 전 context 관찰 저장도
owner가 `@workspace_locked(records=True)`로 이 경로를 명시한다. Facade의 같은 StorageIO 실행기를
사용하며, 일반 Component 작업을 일괄 전환하지 않는다. 원본 파일은 준비 중에는 수정하지 않는다.
`read_json`, `open_record`, `record_stat`, `record_exists`, `record_children`, `record_directory`는
잠금 안에서 준비된 변경을 포함하여 읽는다. 기존 JSONL은 복사하지 않고 기존 prefix와 새 byte
range를 연결해 읽는다. 대화 projection은 성공적으로 확정된 동일 view에 한해 실제 파일 stamp로
다시 연결하므로 매번 전체 대화를 재생하지 않는다.

1. `records-pending-<id>`에 변경 bytes와 closed manifest·checksum·format footer를 준비한다. 임시 준비 파일은 확정 근거가 아니므로 fsync하지 않는다.
2. `records-<first-sequence>-<id>` 저널 끝에 sequence·길이·header checksum과 준비된 묶음을 하나의 frame으로 추가하고 flush한다. 검증한 준비 view는 다음 저장/조회에서 사용할 수 있다. 강한 경계는 이 prefix를 파일 fsync 한 번으로 확정한다. 아직 공개하지 않은 새 저널 이름이면 디렉터리도 fsync한다.
3. 전체 manifest/경로/범위/checksum을 검증하고 **fsync한 prefix만** 원본에 반영한다. 교체는 atomic rename,
   JSONL은 증분 쓰기다. 이 단계에서는 target fsync 대신 영속 저널이 복구를 보장한다.
4. 준비 API는 읽기 view 공개 후, 강한 API는 fsync와 파일 반영 뒤 완료를 전달한다. 같은 OS
   소유권 아래 후속 준비의 byte ranges를 연결하며 target 파일·디렉터리의 동기화 대상은 합친다.
   읽기만 하는 작업은 추가 동기화를 요구하지 않는다. 실행 허용은 강한 ACK만 사용한다.
5. 일반 undo 작업의 첫 변경 직전 또는 마지막 OS 소유권 해제 직전에 target을 한 번씩 동기화한다.
   이후 최고 sequence의 `record-checkpoint`를 checksum과 함께 원자 교체·fsync한 뒤 해당 저널을 지운다. 다음 기록은 새 저널 파일을 사용하며 이미 정리된 파일에 추가하지 않는다.
   Run 종료는 마지막 기록 묶음을 확정한 뒤 이 저장 checkpoint를 수행하고 종료 알림을 보낸다.
   종료 직전과 직후에 같은 파일을 두 번 동기화하지 않고, 완료한 Run의 저널을 Session 종료까지 누적하지 않는다.

`record-checkpoint`는 저장 계층의 반영 완료 지점이며 Run/RAG의 재개 checkpoint와 별개다.
삭제했던 저널이 전원 장애로 다시 보여도 완료 지점 이하이면 재생하지 않는다. 서로 다른 commit을
개별 삭제한 상태만으로 완료를 판정하지 않는다. sequence 누락·중복은 fail-closed한다.
OS 잠금을 새로 얻거나 파일 반영에 실패하면 메모리의 반영 증명을 버린다. 다음 읽기 전에는
남은 전체 저널을 검증하고, 파일별 최종 교체와 append 범위를 합쳐 복구·동기화한다. 옛 append를
먼저 truncate하여 최신 기록을 잃지 않는다. 도메인 동작이나 외부 효과는 재생하지 않는다.

저널을 보유한 동안 파일별 fsync가 늦어지는 것이며 QUEUED/승인/receipt의 내구성을 늦추는 것이 아니다.
잠금 안의 서비스 조회는 준비된 최신 view를 읽고, 일반 파일 변경·백업은 준비 기록을 먼저 확정한다.
잠금을 거치지 않는 raw 파일 조회는 진행 중 최신 view를 보장하지 않는다. 파일만 별도로 복사하거나 소유권을 우회하여
저널을 삭제하는 것은 지원하지 않는다. 긴 Run에서는 다음 checkpoint까지 저널 디스크 공간이 필요하다.
임의의 시간·개수 기반 flush 설정, 별도 background worker, 전역 완화 옵션은 추가하지 않는다.

publication 전 실패는 원본과 메모리 projection을 보존/복원한다. publication 후 실패는 commit 여부가
호출자에게 불확실한 오류이며 되돌리거나 성공 알림을 보내지 않는다. 다음 소유권 진입에서 확정된
묶음 전체를 먼저 복구한다. 복구 중 다시 종료되어도 동일 bytes를 적용할 수 있다. 부분 append는
저널이 증명하는 prefix/최종 길이 범위에서만 복구하며 일반 손상 JSONL의 끝을 임의로 잘라내지 않는다.
완성된 frame의 header·manifest·payload checksum 오류와 sequence 누락은 fail-closed한다.
끝에서 쓰다가 중단된 header/payload만 미확정 꼬리로 처리한다. 전체 완성 frame의 경로·범위·checksum을
먼저 검사한 뒤에만 그 꼬리를 잘라낸다. 완성 frame의 손상을 미완료 기록으로 간주하지 않는다.
fsync 실패 후 온전한 frame이 남은 경우 확정 여부는 호출자에게 불확실하다. 다음 소유권 진입은
검증한 저널 자체를 먼저 fsync하고 복구하므로 읽기·효과보다 내구성 확정이 앞선다. 미확정 pending 파일은 버린다.

### 준비 재사용과 확정 병합

처음 추가 지시 채널을 여는 `OPEN`은 Engine이 준비한 초기 checkpoint와 첫
선택 `boundary`를 함께 보낼 수 있다. 서비스는 채널 등록·예약 지시 활성화·실제
pending 지시 선택·선택 checkpoint를 한 트랜잭션으로 준비한다. Engine은 준비 ACK 이후에만
그 선택을 사용한다. 첫 선택은 processor 준비 전에 확정되며 그 후 도착한 지시는
다음 선택 경계에서 처리한다. 지시가 없다는 추측으로 선택 기록을 생략하지 않는다.
중첩 Engine도 같은 checkpoint envelope와 Tool 사용량 기록을 유지한다. 재개 때는
저장된 선택을 재사용하며 이미 선택한 boundary를 다시 선택하지 않는다.
따라서 기본 Loop의 첫 호출 전 동기화에서 OPEN과 첫 SELECT를 따로 확정할 필요가 없다.

성공한 검증 결과는 **검증한 입력 스냅샷과 결과에 영향을 주는 의존성이 동일함을 확인할 수 있는 범위**에서
재사용한다. 변경 이력이 없다는 사실, 객체 ID, 파일명, mtime만으로 동일성을 추정하지 않는다.
현재 준비 작업이 이미 읽고 검증한 자료는 수정 없이 공유할 수 있다. 참조 대상·설정·제약·검증 구현이
달라지거나 동일성을 확인할 수 없으면 다시 검증한다. 외부 파일 변경을 추적할 수 없는 owner는 다음
준비 작업에서 원본 무결성을 다시 확인한다. 실패·취소·rollback은 새로운 성공 증거가 아니다.
이 재사용은 권한이나 실행 완료를 뜻하지 않는다. 실제 Tool 인자와 제약, 현재 Host 거부, 남은 예산,
승인/재개 binding과 operation receipt는 실행 경계에서 계속 확인한다. 영속 `validated` 표시나
숨은 TTL·용량 설정을 만들지 않는다. 검증과 재사용 범위는 해당 데이터 owner가 소유한다.

Skill capability 준비는 전체 목록을 검증해 얻은 자료로 참조 검사를 수행한다. 각 Skill의 참조를
확인할 때 같은 전체 목록을 다시 읽지 않는다. 외부 Tool/Data 참조의 현재 revision 확인과 실행 중
resource 조회의 expected_revision 검사는 유지한다. 다음 준비 요청은 저장 원본을 다시 읽는다.

Run 접수 검사, 실행 전략 선택, context 준비는 각각 동기 `reuse_schema_checks()` 범위에서 동일
스키마의 성공한 문법 검사만 재사용한다. Tool registry가 새로 만들어져도 그 범위 안의 동일 문법
증거는 공유할 수 있다. 실제 값·Tool 인자·선택 binding·Host 권한과 의미 검증은 생략하지 않으며,
Run·Session 간 runtime 객체나 승인 결정을 공유하지 않는다. 일반 범위의 임시 문법 증거는 종료 시 버린다.
EngineRegistry는 등록된 구현체마다 마지막 성공한 Agent binding의 정확한 JSON 스키마 문법 증거만
보유한다. 다음 binding에서도 schema hook과 설정값/의미 검증은 실행하고, 그 호출에서 실제 사용한
증거로 교체한다. 변경된 스키마는 다시 검사하며, 실패한 검사나 과거 변형은 누적하지 않는다.
이 내부 준비 자료는 설정·정의·권한의 source of truth가 아니며 용량/TTL 정책을 추가하지 않는다.

재개 시 실행 binding 확인과 추가 지시 접수 상태 초기화는 하나의 record 단위로 준비하고
Engine의 외부 실행 전에 확정한다. 준비 계약 미선언 경로는 Engine 진입 전에 강하게 확정한다.
동일한 binding을 재검증했을 뿐인 경우 별도 재저장은 하지 않는다. 반면 승인 정보 상속과
체크포인트 복사는 분리한다. 초기 복사가 실패해도 상속된 승인 근거로 명시적 재개가 가능해야 하기 때문이다.
QUEUED 접수는 독립적으로 확정한다. dispatch는 준비 ACK로 공개하고 Run 시작의 강한 ACK가
전체 수신자 연결까지 확정한다. 실행 진입 허용은 Run 시작에 포함하며 실제 호출 직전 권한·기한 재검증은 유지한다. Engine event는
아래의 명시적인 결합 계약을 사용한 경우에만 하나의 ACK로 확정한다.

완전한 요청 이력 검증은 같은 조회에서 같은 User 객체와 전체 Run 집합으로 이미 검증한 recipient
group을 recipient마다 재해석하지 않는다. 모든 Run의 원본 User/Assistant 연결, binding과 dispatch
증거는 계속 검증한다. 이 조회가 끝나면 증거를 버리므로 이후 변경·손상·삭제는 다시 검사한다.
`RequestHandle.wait()`는 영속 조회 전에 변경 알림을 확보하여 조회와 대기 사이 완료를 놓치지 않는다.
대기 직전의 중복 전체 조회만 제거하며, 알림은 영속 결과를 다시 읽으라는 신호일 뿐 성공 근거가 아니다.
대기 취소/timeout은 실행을 취소하지 않고, shutdown은 미완료 요청을 성공으로 바꾸지 않는다.

### 관련 기록과 하나의 완료 경계

요청의 읽기 전용 사전검사와 런타임의 현재 Project/Session 조회는 같은 `StorageIO` 소유권
범위에서 연속 수행할 수 있다. QUEUED 저장 직전에는 선택 Agent·현재 정책·등록·큐 제한을
다시 검사한다. 이전 사전검사 성공은 접수 권한이 아니며, 저장 확정 경계도 합치지 않는다.
Run context를 구성하는 한 동기 호출에서는 동일한 admitted policy snapshot을 한 번 해석해
전달한다. 현재 Project 권한/사용량 검사나 승인 영수증을 이 사본으로 대체하지 않는다.
취소 경계를 사이에 둔 Engine factory, capability resolver와 context hook 호출은 임의로 묶지 않는다.

#### 준비와 실행 허용을 결합하는 명시적 계약

준비는 호출할 자료와 기록 내용을 만드는 구간이다. 실행 허용은 현재 권한·취소·기한·예산을
확인하고 그 실행을 증명하는 기록까지 fsync하는 구간이다. 준비만으로 실행 권한이 생기지 않는다.
이름이 prepare인 함수나 임의 metadata를 근거로 무효과를 추정하지 않는다.

- `prepares_execution=True`를 선언한 Engine은 실행 시작부터 첫 강한 이벤트 ACK까지
  데이터 준비와 준비 이벤트만 수행한다. 외부 hook 앞에서는 `execution_boundary()`를 먼저
  기다린다. 선언이 없는 Engine은 기존의 실행 전 강한 binding 확정을 유지한다.
- root Agent invocation은 이 계약 아래 binding을 준비하고, 선택 Engine 진입이나 MCP 연결이
  강한 경계를 요구하면 먼저 확정한다. Run 진입 허용은 그대로 강하게 저장하므로 capability
  resolver 및 구성 hook의 기존 실행 경계를 임의로 옮기지 않는다.
- Adapter의 `pure_preparation=True`는 설명·검증·binding·retry 계산이 데이터 처리라는
  구현 선언이다. 외부 호출 또는 도메인 쓰기를 포함하지 않는다. 미선언 Adapter는 강한 경계를
  거쳐 진입하며 기존 기능을 유지한다. 사용자 설정이나 저장 metadata로 이 선언을 만들지 않는다.
- `step(..., prepare=True)`는 action이 첫 실행 허용 이벤트까지 무효과 준비만 한다는 명시적인
  계약이다. 준비한 첫 Completion 예약은 STEP_STARTED에 첨부할 수 있다. 서비스가 Step 시작과
  예산 검사·예약을 한 transaction에서 확정한 뒤에만 SDK를 호출한다. 공개 Step/Completion
  관찰은 둘 다 이 확정 뒤 전달한다. 임의 action의 기본 step 계약은 바꾸지 않는다.
- token counter·입력 policy 등 외부 hook이 먼저 필요하면 실행 경계를 요청하여 Step을 먼저
  확정한다. 재시도는 각 실제 호출의 예약을 유지하고 승인·Tool 효과 receipt와 결합하지 않는다.

준비 단계에서 취소되면 외부 호출하지 않는다. 준비만 한 뒤 아직 시작하지 않은 Step을 실행된
것처럼 만들지 않는다. 준비 중 일반 오류는 기존 Step 실패 진단 경로로 기록한다. 기존 timeout은
준비 시간을 포함하며 새 phase를 이유로 재시작하지 않는다. 별도로 필요한 Step 시작 ACK의
저장 대기는 기존처럼 action 기한에 포함하지 않고, 호출 예약 ACK는 계속 포함한다. 현재 권한·예산은 준비 cache에서
재사용하지 않는다. 준비와 승인 사이 변경은 기존 binding/CAS/취소 검증으로 거부한다.

서비스가 다음 Engine 이벤트를 미리 실행해서 묶음을 찾지 않는다. 선언한 준비를 소유한 Engine이
이미 준비한 기록만 합성한다. 새로운 Run 상태·별도 저널·배경 확정·숨은 tuning 값은 만들지 않는다.
동기화 4회는 단순 Loop 측정 경로의 목표이며 모든 Engine/확장 기능에 강제하는 계약이 아니다.

관련 기록은 **하나의 공개 완료 응답 또는 다음 실행 허용을 함께 증명하는 기록**이다.
같은 Run에 속하거나 가까운 시간에 생성됐다는 이유만으로 묶지 않는다. 도메인 owner가
전부 준비한 유한한 변경 집합을 명시하며, 저장 계층은 원자적 확정과 복구만 담당한다.

- Session runtime 접속 시 stale 상태 정리, Session 상태와 시작 기록은 접속 완료 전에 함께 확정한다.
  재시작 복구는 저장 상태만 정리하며 Engine/Tool을 재실행하지 않는다.
- Engine은 초기 checkpoint와 지시 수신 채널 개방을 같은 이벤트에 선언할 수 있다.
  서비스는 둘을 함께 준비한 뒤 채널 ACK를 돌려준다. Step/외부 hook의 실행 경계에서 먼저 fsync한다.
  기존 Step 완료와 checkpoint receipt는 강한 저장 완료를 유지한다.
  초기 checkpoint를 단독으로 전달하는 Engine 역시 유효하다.
- Run 종료의 Step 정리, Assistant 최종 상태, Run 최종 상태, Session 실행 목록은 한 종료 응답에 속한다.
  이 기록을 redo journal로 확정한 뒤 `StorageIO.checkpoint()`로 최종 파일을 한 번씩 동기화하고
  저널을 회수한다. 종료 알림은 이 과정 뒤에 보낸다. 기존 Run 종료의 저널 정리 경계는 유지한다.
  조회는 이미 저널로 확정·반영된 최종 상태를 읽을 수 있다. 저널 회수는 실행의 새 성공 근거가 아니다.
- 저널의 fsync와 전체 파일 반영 뒤에만 강한 성공 응답과 다음 실행을 허용한다. 준비/부분 출력
  관찰은 원자 읽기 view 이후 발행하며 장애 복구를 보증하는 응답과 구별한다.
  마지막 workspace 소유권 해제나 일반 파일 변경 경계에서는 기존 target checkpoint를 유지한다.

서로 다른 완료 경계는 그대로 분리한다. 승인 대기와 승인 응답, Tool 효과 전 authorization/operation
reservation과 효과 후 receipt, 외부 구현체 호출 전후를 묶지 않는다. 추가 지시 SELECT는 사용 전
ACK가 필요하다. 첫 선택만 OPEN과 함께 준비할 수 있으며, 외부 실행을 허용하기 전에 강한 경계가
그 준비까지 확정한다. 이후 선택은 별도의 강한 경계다.
저장기가 다음 Engine 이벤트를 미리 실행하여 묶음을 만들거나, 모델·Tool·사용자 응답을
기다리는 동안 트랜잭션을 열어 두지 않는다. Activity는 best-effort 파생 인덱스이므로 필수 기록의
성공 조건에 넣지 않는다. Record batch에서는 검증된 파생 append bytes를 같은 frame에 넣고
핵심 기록 다음에 반영한다. 일반 undo 저장에서는 기존 after-commit fsync를 유지한다.

명시적인 Session runtime 시작과 Run 종료의 checkpoint에서는 다음 빈 저널 파일 이름을
미리 만들고 디렉터리를 동기화할 수 있다. Run 종료에서는 watermark 확정에 필요한 기존
디렉터리 fsync와 함께 준비한다. 실제 요청·승인·Tool 기록은 이 빈 파일에 첫 frame을 쓴 뒤
반드시 파일 fsync를 완료해야 한다. 파일 이름의 준비는 요청 접수나 실행 완료를 뜻하지 않는다.
마지막 소유권 해제에서는 사용하지 않은 빈 파일도 정리한다. 준비 중 I/O 실패는 확정 여부를
추측하지 않고 저널 검증·복구를 거치며, 빈 파일만 남은 재시작에서도 외부 효과를 실행하지 않는다.
Activity의 빈 인덱스 이름도 runtime 시작 시 best-effort로 준비할 수 있다. 가짜 Activity는 만들지
않고 실제 이벤트 bytes는 journal frame 또는 독립 append의 fsync로 보존한다. 도메인 저장
형식·version은 유지하며, 내부 record manifest 변경은 앞 절의 배포 경계를 따른다.

준비가 실패하면 준비 관찰을 보내지 않는다. 강한 확정이 실패하면 다음 실행을 허용하지 않으며,
확정 시도 후 I/O 오류를 성공이나 rollback으로 단정하지 않는다.
재접속 시 검증·동기화된 저널로 파일을 복구한다. 취소는 이미 접수한 저장 작업을 drain하며 완료
증거 없이 Engine을 진행시키지 않는다. 단일 파일의 flush만으로 핵심 기록의 fsync를 대체하지 않는다.
동기화 횟수는 이 경계의 결과이며 설정이나 고정 목표가 아니다. 최초 접속, 재개, 중첩 실행,
명시적인 처리기·백업·retention 사용 여부에 따라 횟수가 달라질 수 있다.

### 코드 교체와 내부 저널 형식

Project/Session/Run/Step, 승인과 체크포인트의 저장 schema/version은 바뀌지 않는다. 내부 redo 저널에는
frame 경계가 추가되었다. 이전 코드로 연 workspace를 정상 종료하여 저널 checkpoint·정리를 마친 뒤
새 코드를 사용한다. 미완료된 이전 비프레임 저널이 남아 있으면 새 코드는 변환하거나 무시하지 않고
거부한다. 그 경우 이전 코드로 먼저 복구·정상 종료해야 한다. 자동 migration이나 이중 reader는 없다.

record batch에는 raw 복사·삭제·truncate를 혼합하지 않는다. 해당 storage primitive는 즉시 거부하며
작업을 여러 commit으로 몰래 나누거나 undo로 자동 전환하지 않는다. 이런 작업의 owner는 기존
`StorageIO.run()`을 사용한다. record batch는 Engine/Tool을 실행하거나 재실행하지 않는다.
승인·효과 전 저널의 저장 완료와 새 Run으로 명시적 재개하는 계약은 그대로다. 사용자 설정, 숨은 대기 시간,
flush 생략 옵션, 처리 개수 상한은 추가하지 않는다. 도메인 JSON·schema/storage version도 바꾸지 않는다.

### 일반 파일·트리 변경의 undo 저널

```text
workspace/projects/
  .ish.lock
  .transactions/
    <transaction-id>/
      00000000.json       # 버전·체크섬을 포함한 undo 명령
      00000000.data       # 교체 전 파일 inode의 hard link (해당 작업에만)
      00000001.removed/   # 삭제를 위해 격리한 원본 트리
      stage-.../         # 큰 복원 작업의 비공개 준비 사본
      COMMITTED          # 모든 대상 fsync 이후 확정 마커
      undone-00000000    # 중단된 rollback을 이어가기 위한 진행 표시
    gc-<transaction-id>/  # 확정 또는 rollback 완료 후 물리 정리 대기
```

쓰기 전 undo 정보를 fsync하고 기존 파일을 잠금 안에서 수정한다. 이 때문에
동일 트랜잭션 안에서는 직전 저장값을 다시 읽을 수 있다. 확정 마커가 없으면 역순으로
되돌리고, 마커가 있으면 결과를 보존하고 저널만 정리한다. 복구 자체가 중단되어도
완료한 undo 표시를 확인하여 계속 진행한다. 경로 이탈·심볼릭 링크·손상된 저널은
읽기를 차단하며 임의로 데이터를 추측하거나 저널을 지우지 않는다.

같은 저장 단위 안의 반복 append/replace는 같은 원본에 대한 undo를 재사용한다.
작업 종류가 달라지거나 삭제·재생성되는 경우에는 새 undo가 필요하다. 중간 파일을 후속
undo의 원본으로 사용할 때는 먼저 해당 파일과 아직 확정되지 않은 rename의 디렉터리를
동기화한다. 교체 전 hard link와 undo entry는
같은 저널 디렉터리 동기화로 보호하며 원본 변경 전에 모두 내구성을 확보한다.

`atomic_bytes`/`append_bytes`가 활성 트랜잭션에 참여하면 파일의 최종 내용과 교체된 디렉터리는
확정 직전에 모아서 동기화한다. 새 트리에 Component가 직접 복사한 파일도 포함하며 기존
트리를 전체 순회하지 않는다. 최종 동기화에서는 같은 파일·디렉터리를 한 번씩 처리한다. 이는 동기화 생략이나
백그라운드 확정이 아니다. `COMMITTED` 기록, callback, 비동기 저장 반환, 다음 Engine/Tool 실행은
필요한 동기화가 모두 성공한 뒤에만 가능하다. 트랜잭션 밖의 공통 저장 호출은 즉시 동기화한다.

새 디렉터리도 먼저 CREATE undo를 확정한 뒤 생성하고, directory entry의 동기화는 위
최종 트리 확정에 합친다. 확정 전 새 디렉터리가 유실되는 것은 해당 CREATE의 rollback과
같은 결과이며, 완료 반환 전에는 부모 디렉터리까지 동기화된다. 기존 파일의 원본이나
삭제 격리 rename처럼 후속 복구가 의존하는 자료는 이 규칙으로 지연시키지 않는다.

영속화된 `gc-*` rename 뒤의 물리 정리에는 추가 디렉터리 동기화를 요구하지 않는다.
전원 장애로 정리 잔여물이 다시 보이면 GC 이름으로만 복구되며 확정 데이터를 rollback하지 않는다.
저널 루트 생성의 동기화는 소유 Manager에서 성공 여부를 기억한다. 저장 형식·버전·도메인 저장
단위·승인/재개 정책·사용자 설정은 바뀌지 않는다.

JSONL은 이전 길이만 기록하고 입증된 미확정 쓰기를 되돌릴 때 truncate한다.
H2-a에서 ConversationStore의 근거 없는 끝줄 수리 경로를 제거했다. 끝 newline이 없는
원본은 조회·append를 거부하고 그대로 보존하며, 재시작해도 자동 절단하지 않는다.
[H2-a 검증](lifecycle-verification.md#h2-a-도메인-필수-기록사용량refinement)을 따른다.
H2-d는 출력 저널에도 같은 원본 보존 경계를 적용하고 필수 manifest로 전체 바이트를 검증한다.
모든 JSONL owner의 감사 완료를 뜻하지 않는다.
대화 이벤트는 ConversationEvent와 Message의 기존 타입/필드 계약을 쓰기 전과 재생 시
같이 검증한다. 잘못된 생성·delta·상태·Run 연결·metadata는 원본 JSONL이나 메모리 상태를
변경하지 않는다. 부분 metadata update도 사본에서 먼저 검증하며, 손상된 기존 기록을
기본값으로 복원하거나 자동 절단하지 않는다. JSONL 형식과 명시적 null Run 연결,
기존 생략 operation의 append 의미는 유지한다.
스트리밍 중 전체 대화를 복사하거나 재작성하지 않는다.
명시적 보관 prune의 원자 교체는 기존 파일 hard link로 보호한다. 삭제는 트리를
저널로 rename한 뒤 확정하고 물리 삭제는 재시도 가능한 GC로 수행한다. GC 실패는
이미 완료된 삭제를 취소하지 않는다. 캐시와 도메인 객체는 rollback 때 복원/무효화한다.
운영 로그와 대화 캐시 해제는 commit 이후 실행하며 로그 실패로 저장을 되돌리지 않는다.

여기서 삭제용 rename은 지속적인 손상 격리가 아니다. 확정된 삭제의 GC는 이미 검증·결정한
작업을 완료하며 새 대상을 선정하지 않는다. 손상 원본 보존용 격리는 별도 owner 계약과
안전성 증명이 필요하다. 격리·삭제의 근거가 부족하면 원위치 보존과 사용 차단을 유지한다.

저널과 확정 마커 때문에 fsync 비용은 증가한다. 기존 `policies.output`의
`batch_size`, `max_delay`, `max_chars`로 델타 묶음을 조절할 수 있다. 승인·Tool·Step·
체크포인트 이벤트의 저장 완료 경계는 묶음 설정으로 생략하지 않는다.

## 도메인·외부 효과 경계

Engine은 기존처럼 이벤트만 내보낸다. TransactionManager가 LLM/Tool을 실행하거나
Project/Session/Run/Step의 상태 전이 규칙을 결정하지 않는다. 서비스가 묶을 범위를
정하고 공통 저장 함수가 참여한다. 다른 workspace를 중첩해서 확정하는 기능은 없다.

Tool 실행 전의 원장 예약과 실행 후의 결과 저장은 별개 트랜잭션이다. 외부 효과와
로컬 파일을 하나의 원자 작업으로 만들 수는 없다. 결과/Step 저장이 실패하면 원장은
started로 남아 불확실한 효과를 보호한다. 명시적 외부 확인 후에만 재사용한다.
None 반환도 정상 완료 결과다. `STEP_COMPLETED`에 첨부된 checkpoint receipt는 Step 최종 상태와
같은 트랜잭션으로 저장하며 둘 모두 확정된 뒤 알린다. 별도의 CHECKPOINT 이벤트는 해당 이벤트의
저장 단위를 갖는다. 미래 이벤트를 기다리며 트랜잭션을 열어 두지 않는다.
[완료·receipt 계약](shared-contracts.md)을 함께 따른다.

자동 승인 정책은 PAUSED 확정 뒤 별도로 실행한다. 정책 응답 저장 실패는 응답 묶음을
되돌리고 PAUSED는 유지한다. 정책 응답을 저장하는 것만으로 재개하지 않는다.
UI 응답/취소/갱신의 INTERACTION_CHANGED 알림 예약도 after_commit에 참여한다.
확정 실패로 rollback하면 알림을 보내지 않고, 저장 transaction 문맥을 UI에 넘기지 않는다.
알림 전달은 관찰 기능이며 실제 상태는 승인 조회 API와 Run 기록이 원본이다.

RAG는 계속 자체 세대·DB 구조를 소유한다. 모델 준비는 잠금 밖에서 진행하고 공개
단계만 참여한다. 새 세대의 corpus/catalog, 활성 포인터와 RAG owner의 필수 corpus.manifest.json을
같은 transaction으로 확정하며 정리 작업의 파일 연동을 보호한다.
JobStorage는 준비/배치 원본과 필수 목록을 함께 확정한다. 공개·Job 완료의 commit 후
별도 transaction으로 준비 자료를 정리하며 정리 실패는 그 자료와 목록 전체를 되돌린다.
Memory의 Session 요약과 압축 상태는 각 owner의 존재 목록과 함께 확정한다. Tool 요약의
원본 파일·참조 생성과 명시 삭제도 동일 transaction에 참여하며 참조 소실을 삭제 허가로
해석하지 않는다. Memory 병합도 공통 JSON 쓰기에 참여하므로 서비스 호출 실패는 변경 전 기록으로
돌아간다. 프로젝트 밖 백업 준비·공개는 기존 검증된 준비 디렉토리의 원자 공개를
유지한다. workspace와 외부 백업 경로를 묶는 분산 트랜잭션은 제공하지 않는다.

## 확장 저장소와 사용 조건

기본 파일 저장의 보장은 같은 로컬 Linux 파일시스템의 atomic rename/hard link,
fsync 및 협력하는 workspace 잠금을 전제로 한다. 파일을 직접 열어 읽는 외부 도구는
저장 중간 상태를 볼 수 있으므로 UI는 Facade를 사용한다. 저수준 Repository를 직접
호출할 때는 `with projects.ownership.scope():`로 논리 작업 전체를 감싼다.

확장 파일 저장소는 `atomic_json`, `atomic_bytes`, `append_bytes`, `make_directory`, `unlink_file`,
`remove_named_tree` 또는 현재 Transaction의 참여 메서드를 사용한다. 새 디렉토리의
사용자 초기화는 선언한 루트 아래에서 해야 한다. 기존 디렉토리를 수정하는 확장은
공통 파일 함수를 사용해야 한다. 직접 `Path.write_text`, 외부 DB/네트워크 호출은
자동으로 원자화되지 않는다. 사용자 저장소를 기본 구현으로 교체하지 않는다.

`current_transaction().on_rollback(key, callback)`은 확장 메모리 투영을 복원하고
`after_commit(callback)`은 소유 저장 단위의 성공적인 공개 후 관찰을 연결한다. 강한 단위에서는
fsync 이후이며 준비 단위에서는 flush/view 공개 이후다. callback 자체를 다음 외부 실행의
허가로 사용하지 않으며, 이 훅 자체는 외부 저장소의 crash recovery를 보장하지 않는다.
기본 MemoryConversation은 메시지 단위 rollback을
지원하지만 프로세스가 종료되면 사라지는 기존 수명은 바뀌지 않는다.

commit 마커 rename 직후 fsync가 실패한 경우 확정 결과가 불명확한 오류가 전달될 수 있다.
다음 잠금 진입의 복구와 저장된 요청/Run을 조회한 뒤 판단한다. 같은 작업을 무조건
다시 제출하지 않는다. 전원 차단·손상된 디스크·네트워크 파일시스템의 보장까지
프로세스 강제 종료 테스트로 입증한 것은 아니다.
