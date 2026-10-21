# 영속 데이터 수명주기 보강 검증

현재 및 후속 작업의 검증 범위·실행 시점·커밋과 Push는
[Level 1–3 검증 정책](verification-policy.md)을 따른다. 아래 과거 실행의 명령·결과·실패와
당시 완료 조건은 역사적 증거로 보존하며, 소단계마다 전체 회귀를 반복하는 지침으로 적용하지 않는다.

[수명주기 계약](data-lifecycle.md)의 H1부터 순서대로 검증한다. 구현한 범위와 남은
감사 항목을 구분하며, 원감사 실패 기록은 [P1 검증](p1-verification.md)에 보존한다.
시작점은 `refactor/llm-clean`의 `df6b50813eba11776282a3feed8a1abb26d49a7a`와
기존 P1-c Provider 변경이다. main·이전 backend 브랜치·Draft PR #1은 변경하지 않는다.

## H2-v 원본 입력의 생성·저장 검증 — Level 2 로컬 완료

`69e775a7378f8d384af58812a2dbc53c74986b2d`에서 이어서 RAG 입력의 남은 필수 필드와
Job 연결을 보강했다. 기존 전체 감사와 관련 소비자 목록을 재사용하고 변경 범위만 조사했다.
변경 전 집중 재현은 2개 중 failure 1/error 1(0.218초), 백업 재현 추가 후 3개 중
failure 2/error 1(0.290초), 각각 Skip 0/exit 1이었다. `metadata=False`가 빈 객체로
수락됐고, metadata를 제거한 저장 입력도 일치하는 digest/manifest가 있으면 백업됐다.
실행에서는 모델 효과 전에 KeyError가 났지만 이미 claim/실패 상태를 기록했다. 이 결과를
모델 중복 효과로 보고하지 않는다. 원래 입력 거부·실제 효과 0·원본 보존 Assertion을 유지했다.

- RAGComponent.INPUT_FIELDS/validate_input가 identifier/title/content/metadata의 필수 존재,
  정확한 값 종류·유효 ID·비어 있지 않은 본문/제목·유한 JSON을 검증한다. description은
  선택 필드이며 부재/None/문자열을 허용한다. 저장 Reader는 기본값을 주입하지 않는다.
- 생성용 validate_documents는 identifier와 metadata의 부재/None에만 기존 생성 초기값을
  적용한 뒤 같은 owner를 호출한다. 단일 enqueue도 이 경로를 사용하고 False/0/잘못된 빈
  컬렉션을 기본값으로 바꾸지 않는다. ID 미지정 단일 Job의 document_id=job_id 계약을 유지한다.
- JobStorage는 단일/배치 입력의 정확한 envelope·문서 수·순서/ID와 digest를 검증한다.
  create, claim, 재개 계획, 준비 문서 영수증, backup/clone이 같은 Reader/Writer 검증을 사용한다.
  claim은 이 검증 후에만 attempt를 변경한다. 누락 파일은 기존 missing 진단을 유지한다.
- 새 8개 회귀는 기존 세 실패, 저장 필드/타입/ID matrix, 배치 순서·수, 생성 세 경로의
  잘못된 타입, Writer 쓰기 전 거부, 정상 None 초기값·nullable description을 검사한다.
  모든 거부에서 원본과 실제 모델 파일 효과를 비교한다. FIFO 시험의 Component test double에는
  새 owner callback만 연결했고 기존 장애/바이트 보존 Assertion은 변경하지 않았다.

영향 범위는 RAG 직접 배치 생성·단일/배치 enqueue·Job claim/재개·준비 결과·backup/clone이다.
기존 H2-s 영향도 목록에 T/U/V 모듈을 추가했다. 직접 Repository/Component/Engine/Application/Hub
소비자, 승인·예산·효과·Checkpoint·삭제·트랜잭션, 실제 프로세스 중단/장애 주입 검증을 포함한다.
공통 저장 트랜잭션·실행 권한·예산 정책·효과 재시도 규칙은 변경하지 않았다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 집중 5 modules | 76/76 | 76 | 0/0/0 | 40.967초 | 0 |
| Level 2 LLM, 48 modules | 641/641 | 641 | 0/0/0 | 257.151초 | 0 |
| Level 2 Hub, 6 modules | 33/33 | 33 | 0/0/0 | 85.526초 | 0 |

expected failure/unexpected success/누락/미발견 실행/중복은 각각 0이며 발견 가능한 전체 목록은
LLM 1953/Hub 540개다. 정적 검사 new/stale/S1 0, 기존 debt 531,
exit 0이다. R3 Mutation은 실제 두 번째 효과 Assertion으로 누락을 검출했다(True, exit 0).
전체 LLM/Hub 회귀는 **이번 소단계에서 수행하지 않았다**. 위 범위 제한과 관련 통합/핵심 안전성
통과를 근거로 로컬 완료하며, 남은 관련 H2 계약과 묶는 다음 Level 3에서 전체 실행 후 Push한다.
이전 U 전체 결과를 변경된 제품의 통과로 재사용하지 않는다. H2 전체·원격 CI 완료는 아니다.

전후 Python 676개 hash `041cf4d3117e78e8eb83a575851f615f1a0f5b7bcc9f40ab5cbe65f82dd4aab6`,
전체 입력 835개/환경 hash `885a0fb1626eafd5caa394e61d9bc312cbe821e5c2de0ba2b94b8e8d1ecd62c8`는 각각 동일했다.
Python 3.12.14·136 packages·host `d833790959a76ac457fdeea3f21a6bea1eecfe12`, UID 1000과
기존 bwrap/Xvfb/xclip·5개 환경 변수는 그대로다. 성공 후 문서를 먼저 갱신했다.
문서 검사는 131개 문서·178개 Python fence·953개 상대 링크·34개 anchor에서 오류 0, exit 0이다.
실제 argv/exit/요약·원래 실패 로그는 `/workspace/llm-clean-resume/h2-v/`에 보존한다.

```bash
python /workspace/llm-clean-resume/h2-q/run-focused.py llm /workspace/llm-clean-resume/h2-v/baseline-2 tests.llm.test_rag_input_integrity
python /workspace/llm-clean-resume/h2-q/run-focused.py llm /workspace/llm-clean-resume/h2-v/focused-1 tests.llm.test_rag_input_integrity tests.llm.test_rag_job_integrity tests.llm.test_rag_prepared_integrity tests.llm.test_checkpoint_lifecycle tests.llm.test_rag_optimization
python /workspace/llm-clean-resume/h2-v/run-level2.py llm /workspace/llm-clean-resume/h2-v/final
python /workspace/llm-clean-resume/h2-v/run-level2.py hub /workspace/llm-clean-resume/h2-v/final
python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/h2-v/static-1.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_docs --output /workspace/llm-clean-resume/h2-v/docs.json
```

남은 RAG 배치와 원문 연결·관측 의미 및 나머지 owner 계약은 별도 후속 범위다.
현재 raw graph의 원문 인용 검사는 실제 재사용 때 수행한다. 저장 시점 설정과 원문에 대한
추가 증거 없이 현재 설정으로 과거 receipt의 의미를 추정하지 않는다.

## H2-u 배치 영수증 key 연결 — H2-r/s/t/u 묶음 Level 3

H2-t 로컬 커밋 `76110ec0c96664f99a1f3e2f7b916f282aa10b48`에서 이어서 실제 중복 효과 결함을
수정했다. 미완료 Job의 완료 embedding_0 영수증을 embedding_99로 바꾸고 목록 key를 맞추면
retry에서 첫 모델 호출이 반복됐다. 원래 실제 효과 Assertion은 `first` 한 줄을 요구했지만
`first/first/second` 세 줄을 관측했다(1 failure, error·Skip 0, 1.395초, exit 1).
T 전체 runner 실행은 끝까지 보존했고 이 실패 때문에 묶음 완료·Push를 차단했다.

- JobStorage.BATCH_FIELDS가 현재 배치 envelope의 필수 key/signature/value를 선언한다.
  Reader는 저장 key와 실제 checkpoint 경로가 같아야 영수증으로 인정한다. key 소실·변조·파일명
  불일치는 일치하는 manifest hash만으로 정상 완료 기록이 되지 않는다.
- JobStorage.write_batch가 같은 형식을 만들고 기존 write_checkpoint 원자 저장을 사용한다.
  RAGJobs의 정규 Writer를 이 owner로 이관했다. 준비 문서와 embedding/graph/document_N payload의
  기존 소유 검증, signature·configuration·상태·취소·포기·CAS·사용량 보호는 그대로 유지한다.
  필수 key가 없는 이전 영수증을 자동 변환·재생성하지 않는다. 원본을 보존하고 사용을 거부한다.
- 새 4개 회귀는 원래 실제 중복 효과 재현, 모든 생성된 배치 key의 소실/타입/불일치 matrix,
  Writer의 사전 거부 및 정상 retry를 확인한다. 거부 후 원본·모델 효과를 비교하며 재현 시험은
  RAG 트리 전체 hash와 backup 미생성도 검사한다. 정상 retry에서도 이미 완료된 모델 효과는 한 번이다.

변경 영향은 모든 RAG 배치 envelope와 Writer/Reader·claim/재개·backup/clone이다. 실제 중복 효과가
발견됐고 필수 저장 증거가 변경됐으므로 집중 검증 후 현재 제품 소스로 전체 LLM/Hub와 R3를 새로
실행했다. T의 전체 통과는 변경 전 증거로 보존했으며 U 통과로 재사용하지 않았다. 불필요한 반복이
아니라 안전성 결함 수정으로 기존 통합 검증 근거가 무효화된 경우다. 기존 승인·예산·Tool 효과·
Checkpoint·삭제·실제 중단/재개와 직접 Repository/Component/Engine/Application/Hub 소비자를 포함한다.

| 현재 브랜치 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 집중, 5 modules | 68/68 | 68 | 0/0/0 | 42.052초 | 0 |
| 전체 LLM | 1945/1945 | 1945 | 0/0/0 | 669.665초 | 0 |
| 전체 Hub | 540/540 | 540 | 0/0/0 | 706.452초 | 0 |

expected failure/unexpected success/미실행/미발견 실행/중복은 각각 0이다. 원래 실패 Assertion은
새 모듈에 포함해 실행했으며 제외·Skip·완화하지 않았다. Job/Checkpoint의 기존 실제 프로세스
중단·쓰기 실패·복구 시험도 새 envelope로 통과했다. 이 결과로 U의 중복 효과 차단을 해소하고
H2-r/s/t/u 묶음 Level 3을 완료한다. H2 전체의 완료를 뜻하지 않는다.
정적 감사 new/stale/S1 0, 기존 debt 531, exit 0이다. R3 Mutation은 실제 두 번째 파일 효과
Assertion으로 호출 한도 누락을 검출했다(True, detector exit 0). 문서는 이 검증 후 커밋보다 먼저 갱신했다.
문서 검사는 131개 문서·178개 Python fence·953개 상대 링크·34개 anchor에서 오류 0, exit 0이다.

별도 Git 메타데이터 없는 복사본의 초안 68 pass/43.630초/exit 0 및 정적 통과는 현재 결과로
승계하지 않았다. 실제 작업 브랜치에 적용한 뒤 위 집중/전체를 새로 수행했다.
전후 Python 675개 hash는 `2bb25acfb6fe87b0927a35c0ce6ac413b317f5c4bd8f4509ddec0e410cd4f1e2`,
전체 입력 834개/환경 hash는 `cdc91f45555030c336fe382a176368c1c196cd6255070eb7a8fdba3158faf8af`로 각각 동일하다. Python 3.12.14·UID 1000·
136 packages·host `d833790959a76ac457fdeea3f21a6bea1eecfe12`와 기존 bwrap/Xvfb/xclip·
5개 환경 변수를 유지했다. 초기 외부 probe의 구문 오류도 별도 로그로 보존하고 실제 실패와 구분했다.

전체 argv/요약/로그·원래 실패는 `/workspace/llm-clean-resume/h2-u/`에 있다.

```bash
python /workspace/llm-clean-resume/h2-u/renamed-batch-probe.py
python /workspace/llm-clean-resume/h2-q/run-focused.py llm /workspace/llm-clean-resume/h2-u/focused-1 tests.llm.test_rag_batch_identity tests.llm.test_rag_checkpoint_integrity tests.llm.test_checkpoint_lifecycle tests.llm.test_rag_optimization tests.llm.test_rag_prepared_integrity
python /workspace/llm-clean-resume/h2-m/run-full.py llm /workspace/llm-clean-resume/h2-u/final
python /workspace/llm-clean-resume/h2-m/run-full.py hub /workspace/llm-clean-resume/h2-u/final
python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/h2-u/static-1.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_docs --output /workspace/llm-clean-resume/h2-u/docs.json
```

원격 CI 통과는 실제 결과를 확인하기 전까지 선언하지 않는다. 원격 main·refactor/llm-backend·
Draft PR #1은 변경하지 않으며 clean 브랜치에만 이 묶음을 Push한다. H2의 나머지 원문/입력 연결,
관측 의미와 owner 계약을 이어서 확인하며 H2 완료 전 H3–H5·P1·A–F에는 진입하지 않는다.

## H2-t 모델 호출 전후 원본 증거 — 전체 실행과 후속 안전성 차단

H2-s 로컬 커밋 `70dc3b8adadb2f1f9551d4a2b0dd4faca6df2698`에서 이어서 검색의 읽기 경계를 보강했다.
이전 고정 소스의 외부 재현은 rerank 중 catalog 소실 뒤에도 신뢰한 검색 결과를 반환하는
1 failure, error·Skip 0, 1.088초, exit 1이었다. 원래 Assertion을 `test_rag_query_evidence`에 편입했다.

- RAGComponent.source_version은 현재 identity/세대·게시 corpus/catalog revision과 두 필수 원본의
  device/inode/size/mtime/ctime을 묶는다. 필수 파일의 regular/link 상태와 vector/graph 색인 존재도
  확인한다. 새 저장 형식이나 모델 호출·정책은 추가하지 않는다.
- RAGData는 같은 ownership 범위에서 검증된 snapshot과 이 증거를 얻고, query 모델 호출 후 검색·
  rerank 후 결과 반환·관계 결합 전에 현재 증거를 재검사한다. 변경·소실은 명시 거부하고 원본을
  복원하거나 모델을 재실행하지 않는다. 기존 generation/configuration CAS와 결과 의미는 유지한다.
- 새 5개 회귀는 원래 catalog 소실, query 호출 중 소실, 같은 크기의 원문 변조와 mtime 복원,
  graph/vector 색인 소실, 게시 proof 변경을 실제 모델 파일 효과·원본 hash와 함께 검사한다.
  기존 정상 조회 및 코퍼스 1회 파싱 Assertion도 유지한다. source stat은 협력하는 workspace
  잠금 아래의 불변 snapshot 증거이며 임의 외부 writer와의 분산 트랜잭션 보장은 아니다.

영향 범위는 RAGData 검색/모델 대기/재정렬/관계 결합과 Component 원본 owner다. H2-r/s와 묶는
전체 게이트이므로 관련 집중 검증 후 전체 LLM/Hub를 새로 실행했다. 승인·예산·효과·Checkpoint·
삭제·트랜잭션의 실제 중단/외부 효과 Assertion, 직접 Repository/Component/Engine/Application/Hub
소비자를 포함하고 부분 Level 2를 중복 실행하지 않았다.

| 현재 브랜치 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 집중, 5 modules | 77/77 | 77 | 0/0/0 | 26.159초 | 0 |
| 전체 LLM | 1941/1941 | 1941 | 0/0/0 | 670.193초 | 0 |
| 전체 Hub | 540/540 | 540 | 0/0/0 | 707.146초 | 0 |
| 별도 U 필수 안전성 재현 | 1/1 | 0 | 1/0/0 | 1.395초 | 1 |

runner들의 expected failure/unexpected success/미실행/미발견 실행/중복은 각각 0이다. 다만 아래 U
안전성 실패가 별도로 발견됐으므로 **묶음 게이트는 미완료이고 Push하지 않는다**. 전체 runner의
개별 통과를 이 실패까지 해결한 완료로 표현하거나 실패를 합계에서 제외해 게이트를 통과시키지 않는다.
기존 계약으로 수정·재검증한 후에만 후속 묶음 Push와 상위 단계 진행을 허용한다.

정적 감사 new/stale/S1 0, 기존 debt 531, exit 0이다. R3 Mutation은 실제 두 번째 파일 효과
Assertion으로 호출 한도 누락을 검출했다(True, detector exit 0). 문서는 이 검증 후 로컬 커밋보다
먼저 갱신했다. 문서 검사는 131 documents·178 Python fences·953 relative links·34 anchors,
오류 0, exit 0이다. 별도 Git 메타데이터 없는 복사본의 초안 77 pass/26.957초/exit 0과 정적 통과는
현재 결과로 승계하지 않았으며 위 실행은 현재 브랜치에서 수행했다.
전후 Python 674개 hash는 `4c260867c6cf014708d2756539a09c38e4743776003532505d3010a69a39be6f`,
전체 입력 833개/환경 hash는 `e02d83ef6eb279bde04d1bd6750302b633f546929d3201eeaa6cce3e0fa0c22b`로 각각 동일하다. Python 3.12.14·UID 1000·
136 packages·host `d833790959a76ac457fdeea3f21a6bea1eecfe12`와 기존 bwrap/Xvfb/xclip·
5개 환경 변수를 유지했다.

전체 argv/요약/로그·원래 실패는 `/workspace/llm-clean-resume/h2-t/`에 있다.

```bash
python /workspace/llm-clean-resume/h2-q/run-focused.py llm /workspace/llm-clean-resume/h2-t/focused-1 tests.llm.test_rag_query_evidence tests.llm.test_optimized_queries tests.llm.test_rag_optimization tests.llm.test_rag_components tests.llm.test_unified_rag
python /workspace/llm-clean-resume/h2-m/run-full.py llm /workspace/llm-clean-resume/h2-t/final
python /workspace/llm-clean-resume/h2-m/run-full.py hub /workspace/llm-clean-resume/h2-t/final
python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/h2-t/static-1.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_docs --output /workspace/llm-clean-resume/h2-t/docs.json
```

U 재현은 미완료 Job의 완료 embedding_0 영수증을 embedding_99로 바꾸고 목록 key를 맞춘 뒤
retry하는 경우다. 실제 효과 파일이 `first` 한 줄에서 `first/first/second` 세 줄로 늘었다.
`/workspace/llm-clean-resume/h2-u/renamed-batch-probe.log`와 위 고정 소스에 증거를 보존했다.
처음 외부 probe 스크립트의 괄호 오류는 renamed-batch-probe-syntax.log에 별도로 보존했고 실제
실패 재현과 혼합하지 않는다. 저장 key와 파일명 연결을 owner가 검증하도록 수정하고 원래 실제
중복 효과 Assertion을 유지한 후 현재 브랜치 전체 회귀를 새로 수행한다. H2 전체·원격 CI 완료가
아니며 H3–H5·P1·A–F에는 진입하지 않는다. 원격 CI 결과도 아직 확인하지 못했다.

## H2-s 그래프 배치 필수 증거 — Level 2

H2-r 로컬 커밋 `bb67fb5bc8b5d50eca26cc896e31d52ce198d184`에서 이어서 저장 그래프 배치를 보강했다.
이전 고정 소스의 외부 재현은 빈 graph payload와 일치하는 manifest hash를 backup이 수락하는
1 failure, error·Skip 0, 0.349초, exit 1이었다. 원래 Assertion을 `test_rag_graph_checkpoint`에 편입했다.

- extraction의 `validate_graph_checkpoint`가 저장된 정규 graph의 정확한 root/entity/relation 필드,
  canonical 이름/ID·중복·엔티티 참조·필수 metadata와 finite JSON을 검증한다. 저장 영수증에서
  빠진 metadata를 공급자 응답용 기본값으로 복원하지 않는다. 모델 응답 정규화는 기존대로 유지한다.
- RAGComponent/JobStorage가 graph key·단건/배치 문서 위치·signature 형식과 payload owner 검증을
  Reader/Writer·claim/재개·backup/clone에서 공유한다. 준비 재사용도 같은 검증 후 실제 chunk의
  원문 인용을 검사한다. 실제 재개 시 signature/configuration·인용 검사는 제거하지 않았다.
- 새 4개 회귀는 원래 빈 payload, 필수 metadata 소실, 필드·타입·canonical ID 손상 matrix 및 정상
  retry를 검사한다. 매 거부 뒤 원본 바이트와 embedding/extraction의 실제 파일 효과를 비교하고
  정상 retry도 두 모델 효과를 반복하지 않는다. 저장 형식·트랜잭션·승인/예산/효과 정책은 그대로다.

이 범위는 저장된 raw graph의 현재 필드 의미다. backup 때 live 설정을 이용해 과거 signature를
재계산하거나, 실제 원문 대신 만든 인용 문맥으로 검증하지 않는다. 그래프의 전체 원문/signature
연결, 임의 key 및 나머지 owner 계약까지 완료했다는 뜻은 아니다.

영향 범위는 extraction 재사용→Component→JobStorage의 직접 소비자다. H2-r의 호출 관계와 공통
안전성 목록에 새 graph 모듈만 추가했다. LLM 45개·Hub 6개 모듈 전체에서 승인·usage deletion·
operation/checkpoint/lifecycle 무결성, 실제 중단·저장 실패·재개 및 직접 소비자 통합을 유지했다.

| 현재 브랜치 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 집중, 5 modules | 52/52 | 52 | 0/0/0 | 25.536초 | 0 |
| Level 2 LLM | 624/624 | 624 | 0/0/0 | 248.414초 | 0 |
| Level 2 Hub | 33/33 | 33 | 0/0/0 | 85.619초 | 0 |

expected failure/unexpected success/미실행/미발견 실행/중복은 각각 0이다. 전체 발견 목록은
LLM 1936개·Hub 540개이며 위 실행은 **선택 모듈의 부분 검증**이다.
정적 감사 new/stale/S1 0, 기존 debt 531, exit 0이다. R3 Mutation은 실제 두 번째 파일 효과
Assertion으로 호출 한도 누락을 검출했다(True, detector exit 0). 문서는 이 검증 후 커밋보다 먼저 갱신했다.
문서 검사는 131 documents·178 Python fences·953 relative links·34 anchors, 오류 0, exit 0이다.

별도 Git 메타데이터 없는 복사본의 최초 초안은 52개 중 51 pass/1 error, 26.225초, exit 1이었다.
새 테스트가 공개 document view에 없는 raw graph를 조회했다. 실제 공개 graph search로 관계를
확인하도록 고쳤고 모델 효과/관계 존재 Assertion은 유지했다. 후속 4개는 3.387초, exit 0이다.
이 초안 결과는 현재 검증으로 승계하지 않고 실제 브랜치에서 위 검증을 새로 수행했다.
전후 Python 673개 hash는 `b151b2af2bffa71095373dd3ea59c2aea825f4d1253daf50b800f91ca1561104`,
전체 입력 832개/환경 hash는 `ed07a3f72490addd7fc44e29c2e664a6dc54402e5e89517c4b523a3099f323aa`로 각각 동일하다. Python 3.12.14·UID 1000·
136 packages·host `d833790959a76ac457fdeea3f21a6bea1eecfe12`와 기존 bwrap/Xvfb/xclip·
5개 환경 변수를 유지했다.

전체 argv/요약/로그·원본 실패는 `/workspace/llm-clean-resume/h2-s/`에 보존했다.

```bash
python /workspace/llm-clean-resume/h2-q/run-focused.py llm /workspace/llm-clean-resume/h2-s/focused-1 tests.llm.test_rag_graph_checkpoint tests.llm.test_rag_checkpoint_integrity tests.llm.test_rag_extraction_repair tests.llm.test_rag_optimization tests.llm.test_unified_rag
python /workspace/llm-clean-resume/h2-s/run-level2.py llm /workspace/llm-clean-resume/h2-s/final
python /workspace/llm-clean-resume/h2-s/run-level2.py hub /workspace/llm-clean-resume/h2-s/final
python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/h2-s/static-1.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_docs --output /workspace/llm-clean-resume/h2-s/docs.json
```

전체 LLM/Hub는 이 소단계에서 **실행하지 않았다**. 변경이 그래프 영수증 owner와 직접 소비자에
한정되고 관련 통합·핵심 안전성 검증을 통과했으므로 Level 2 로컬 완료로 보존한다. 다음 읽기
경계와 H2-r/s를 묶은 Level 3에서 전체 검증 후 Push한다. 과거 전체 결과를 현재 통과로 승계하지
않는다. H2 전체·원격 CI 완료가 아니며 H3–H5·P1·A–F에는 아직 진입하지 않는다.
다음 읽기 경계에서는 rerank 중 catalog가 소실돼도 결과를 반환하는 1 failure, error·Skip 0,
1.088초, exit 1을 재현했다. `/workspace/llm-clean-resume/h2-t/query-evidence-probe.log`와 위
고정 소스에 증거를 남겼다. 이 suite 밖의 실패를 부분 검증 통과로 표현하지 않는다.

## H2-r 임베딩 배치 영수증 — Level 2

H2-p/q 묶음 Push `e474f38974e485f40f028570f8119a5d874c06e1`에서 이어서 임베딩 영수증 owner를
보강했다. 이전 고정 소스의 외부 재현은 빈 embedding payload와 갱신된 manifest hash를 backup이
수락하는 1 failure, error·Skip 0, 0.390초, exit 1이었다. 원래 Assertion을
`test_rag_embedding_checkpoint`에 편입했다.

- ingestion의 `validate_chunk_checkpoint`가 현재 fingerprint/text_hash/vector의 정확한 필드,
  hash 형식, signature 연결과 finite/nonzero 벡터를 검증한다. 생성/재사용도 같은 owner를 사용한다.
- RAGComponent가 단건/배치 Job의 embedding key와 문서 위치를 확인하고 JobStorage의 Reader/Writer가
  이 owner에 위임한다. claim/재개·backup/clone도 같은 저장 Reader를 사용한다. 실제 재개 때의
  원문 hash·configuration·차원·중복 chunk 검증은 유지한다. graph와 임의 key의 의미는 이 단계의
  완료 범위에 포함하지 않는다. 저장 형식·트랜잭션 경계·승인/예산/효과 정책은 바꾸지 않았다.
- 새 3개 회귀는 원래 빈 payload 재현, 필수 필드/타입/vector/signature 손상 matrix와 정상 retry를
  확인한다. 매 거부 뒤 원본 바이트와 실제 모델 효과 파일을 비교하며 정상 retry에서도 추가 효과는
  없다. FIFO/lease 시험의 test double에는 실제 owner callback을 추가했고 기존 Assertion은 유지한다.

영향 범위는 prepare_vectors→RAGComponent→RAGJobs/JobStorage와 저장·재개·백업/복제 소비자다.
H2-p의 호출 관계/공통 안전성 목록에 Q/R 모듈만 추가했다. LLM 44개·Hub 6개 모듈 전체를 선택하고
승인·usage deletion·operation/checkpoint/lifecycle 무결성, 기존 실제 중단·쓰기 장애와 직접
Repository/Component/Engine/Application/Hub 통합을 포함했다.

| 현재 브랜치 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 집중, 4 modules | 47/47 | 47 | 0/0/0 | 27.563초 | 0 |
| 최초 Level 2 LLM | 620/620 | 618 | 0/2/0 | 249.647초 | 1 |
| Fixture 수정 후 집중 | 23/23 | 23 | 0/0/0 | 7.888초 | 0 |
| 최종 Level 2 LLM | 620/620 | 620 | 0/0/0 | 241.588초 | 0 |
| 최초 Level 2 Hub, 결과 재사용 | 33/33 | 33 | 0/0/0 | 84.313초 | 0 |

expected failure/unexpected success/미실행/미발견 실행/중복은 각각 0이다. 전체 발견 목록은
LLM 1932개·Hub 540개이며 위 결과는 **선택 모듈의 부분 검증**이다.
최초 LLM 오류 2개는 lifecycle Fixture가 임시 signature와 vector만 저장해서 새 owner에 거부된
경우다. 취소 시험은 그 오류 때문에 진입 이벤트 전에 끝나 timeout으로 보고됐다. 제품의 검증을
완화하지 않고 Fixture에 필수 fingerprint/원문 hash와 공유 revision_token으로 연결한 signature를
갖췄다. 원래 취소·포기·삭제·영수증 보존 Assertion은 유지했고 23개 집중 및 LLM Level 2 전체를
새로 실행했다. 최초 실패는 제거하거나 최종 성공에 합산하지 않았다.

Hub 33개 통과는 조건부 재사용했다. 최종 소스와 최초 소스의 차이는
tests/llm/test_checkpoint_lifecycle.py 하나뿐이고 제품·Hub 테스트/Fixture·의존성/환경은 동일하다.
Hub 재발견 540개/선택 33개의 전체 ID와 중복 0이 이전과 같고 해당 LLM 테스트를 import하지 않는
것도 확인했다(final-2/change-evidence.json, hub-reuse.json). 별도 실행을 하나의 전체 실행으로
표현하지 않는다. 정적 감사와 R3의 입력·의존 경로도 바뀌지 않았으며 각 실행 소스는 최초
final/inputs-before.json이다.

정적 감사 new/stale/S1 0, 기존 debt 531, exit 0이다. R3 Mutation은 실제 두 번째 파일 효과
Assertion으로 호출 한도 누락을 검출했다(True, detector exit 0). 문서는 검증 후 커밋보다 먼저 갱신했다.
문서 검사는 131 documents·178 Python fences·953 relative links·34 anchors, 오류 0, exit 0이다.

별도 Git 메타데이터 없는 소스 복사본의 초안 47 pass/29.763초/exit 0 및 정적 통과는 현재 검증으로
승계하지 않았다. 실제 브랜치에 patch를 적용한 뒤 위 검증을 새로 실행했다. 집중 후 테스트
module 설명만 현재 계약에 맞췄고 Level 2 동안 제품·테스트·Fixture·문서를 고정했다.
전후 Python 672개 hash는 `59ffc14e01efc84489aee82bb3add3b5e6317e1f90a0a7372354335416966b1f`,
전체 입력 831개/환경 hash는 `33d710bd21fb81eb8e1d60095b5282d52a9c2b9af6b3e0b0706cc8e1467d658c`로 각각 동일하다. 최초 실패 실행의
Python/input hash도 각각 `20b16383c39b3e60041d8a792252f8f02b6d0ab4cb11eed4bed9788d056e4e0c`와
`7da4c8e100a9000732d3076774e3e228283d1763830053838e954ae59c41c284`로 전후 동일했다.
Python 3.12.14·UID 1000·136 packages·host `d833790959a76ac457fdeea3f21a6bea1eecfe12`와
기존 bwrap/Xvfb/xclip·5개 환경 변수를 유지했다.

명령·전체 argv/요약/로그·원본 실패는 `/workspace/llm-clean-resume/h2-r/`에 있다.

```bash
python /workspace/llm-clean-resume/h2-q/run-focused.py llm /workspace/llm-clean-resume/h2-r/focused-1 tests.llm.test_rag_embedding_checkpoint tests.llm.test_rag_job_integrity tests.llm.test_rag_checkpoint_integrity tests.llm.test_rag_optimization
python /workspace/llm-clean-resume/h2-r/run-level2.py llm /workspace/llm-clean-resume/h2-r/final
python /workspace/llm-clean-resume/h2-q/run-focused.py llm /workspace/llm-clean-resume/h2-r/focused-2 tests.llm.test_checkpoint_lifecycle tests.llm.test_rag_embedding_checkpoint
python /workspace/llm-clean-resume/h2-r/run-level2.py llm /workspace/llm-clean-resume/h2-r/final-2
python /workspace/llm-clean-resume/h2-r/run-level2.py hub /workspace/llm-clean-resume/h2-r/final
python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/h2-r/static-1.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_docs --output /workspace/llm-clean-resume/h2-r/docs.json
```

전체 LLM/Hub는 이 소단계에서 **실행하지 않았다**. 변경이 임베딩 영수증 owner와 직접 소비자에
한정되고 관련 통합·핵심 안전성 검증을 통과했으므로 Level 2 로컬 완료로 보존한다. 다음 관련
소단계와 묶은 Level 3에서 전체 회귀 후 Push한다. H2-p/q 전체 결과를 변경 후 전체 통과로
승계하지 않는다. H2 전체·원격 CI 완료가 아니며 H3–H5·P1·A–F에는 아직 진입하지 않는다.
다음 graph owner의 외부 재현은 빈 graph payload의 backup 수락 1 failure, error·Skip 0,
0.349초, exit 1이다. `/workspace/llm-clean-resume/h2-s/graph-payload-probe.log`와 위 고정 입력
manifest(final/inputs-before.json)에 증거를 남겼다. 이 suite 밖의 실패를 부분 검증 통과로 표현하지 않는다. graph/임의 key,
관측/읽기 경계와 나머지 owner 계약을 계속 보강한다.

## H2-q RAG 목록·현재 게시 증거 — H2-p/q 묶음 Level 3

H2-p 로컬 커밋 `59cc889461a8468fa18f8239a33693ee8ed04968`에서 이어서 RAG 게시 연결을 보강했다.
이전 고정 소스의 외부 재현은 catalog 소실의 backup 수락과 빈 catalog의 게시 문서 은폐라는
2 failure, error·Skip 0, 1.038초, exit 1이었다. 원래 두 Assertion을 새
`test_rag_catalog_integrity`에 편입했다. 게시 직전 검증도 별도로 재현했다. 유효한 snapshot을
준비한 뒤 catalog가 소실됐는데 저수준 publish가 이를 덮어쓰던 1 failure(1.005초, exit 1)를
같은 테스트에 보존하고, 게시가 현재 snapshot을 다시 검증한 뒤에만 진행하도록 수정했다.

- RAGComponent가 필수 `corpus.manifest.json`의 현재 version·Project/Component 소유권과
  identity/active/corpus/catalog revision을 소유한다. 새 저장소만 명시적인 빈 증거를 만들며
  기존 기록의 소실을 자동 초기화·변환하지 않는다. identity 재발급·이전 active 포인터 재사용이
  최신 원본의 정리 허가가 되지 않는다.
- 목록 owner는 필수 ID/title/generation/revision·선택 description의 타입, 정렬·중복 및 게시
  revision을 검증한다. 목록 조회는 코퍼스 본문을 파싱하지 않는다. snapshot은 corpus의 의미와
  revision, catalog 투영 일치, 실제 DB 존재를 검사하며 cache key에도 corpus revision을 포함한다.
- 새 세대의 catalog/corpus, active와 필수 manifest는 기존 workspace transaction에 함께 참여한다.
  공통 저장 인프라·승인/예산/효과 정책은 변경하지 않는다. 직접 저수준 호출도 기존 ownership.scope
  계약을 따른다. 게시/목록/원본 조회·수정·compact·재선택·backup/clone이 현재 owner 검증을 사용한다.

새 12개 회귀는 원래 2개 재현, 소실·필수 필드·잘못된 타입/버전/소유권·변조·이전 포인터,
link/directory/FIFO 거부, cache와 정상 빠른 목록 조회, 게시 직전 재검사를 포함한다. 거부마다
실제 원본 hash와 모델 효과 파일을 비교한다. 4개 저장 경계의 쓰기 후 예외와 실제 자식 프로세스
exit 77의 5개 경계(catalog/corpus/active/manifest 저장 후, commit 후)를 확인했다. 미확정 상태는
원본 바이트로 되돌아가고 확정된 새 상태만 보존하며 재시작/backup은 추가 모델 효과를 만들지 않는다.
기존 Assertion·skip 조건·CI 필수 Job·정적 검사기를 완화하지 않았다.

| 현재 브랜치 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 집중 1, 6 modules | 71/71 | 71 | 0/0/0 | 37.293초 | 0 |
| 게시 직전 재현 | 1/1 | 0 | 1/0/0 | 1.005초 | 1 |
| 보정 후 집중 2, 4 modules | 66/66 | 66 | 0/0/0 | 29.606초 | 0 |
| 전체 LLM | 1929/1929 | 1929 | 0/0/0 | 663.150초 | 0 |
| 전체 Hub | 540/540 | 540 | 0/0/0 | 712.420초 | 0 |

expected failure/unexpected success/발견 후 미실행/미발견 실행/중복은 각각 0이다. 이번에는 관련
소단계를 묶는 Level 3이고 현재 게시 저장 증거가 변경되므로 부분 Level 2를 중복 실행하지 않고
전체 LLM/Hub로 검증했다. 승인·usage deletion·operation/checkpoint/lifecycle 무결성과 기존
Job 중단·재개·실제 효과, 직접 Repository/Component/Engine/Application/Hub 소비자를 포함한다.
이전 H2-p 부분 결과를 현재 전체 결과로 합산하거나 승계하지 않았다.

첫 정적 감사는 신규 S4 2개로 exit 1이었다. manifest version을 필드 owner의 SCHEMA_VERSION
단일 선언으로 정리한 뒤 정적 감사 new/stale/S1 0, 기존 debt 531, exit 0이다. 새 면제나 baseline
추가는 없다. R3 Mutation은 실제 두 번째 파일 효과 Assertion으로 호출 한도 누락을 검출했다
(True, detector exit 0; 내부 의도된 실패를 일반 회귀에 합산하지 않는다). 문서는 검증 후 커밋보다
먼저 갱신했다. 문서 검사는 131 documents·178 Python fences·953 relative links·34 anchors,
오류 0, exit 0이다. 원격 CI 통과는 확인되지 않았다. 기존 API 접근 Forbidden 상태와 로컬 결과를 구분한다.

실제 명령·전체 argv/요약/로그는 `/workspace/llm-clean-resume/h2-q/`에 보존했다.

```bash
python /workspace/llm-clean-resume/h2-q/run-focused.py llm /workspace/llm-clean-resume/h2-q/focused-1 tests.llm.test_rag_catalog_integrity tests.llm.test_rag_corpus_integrity tests.llm.test_rag_storage tests.llm.test_rag_components tests.llm.test_unified_rag tests.llm.test_record_generations
python -m unittest tests.llm.test_rag_catalog_integrity.CatalogIntegrityTests.test_owner_rechecks_required_catalog_before_publishing_a_prepared_snapshot
python /workspace/llm-clean-resume/h2-q/run-focused.py llm /workspace/llm-clean-resume/h2-q/focused-2 tests.llm.test_rag_catalog_integrity tests.llm.test_rag_optimization tests.llm.test_optimized_queries tests.llm.test_rag_resilience
python /workspace/llm-clean-resume/h2-m/run-full.py llm /workspace/llm-clean-resume/h2-q/final
python /workspace/llm-clean-resume/h2-m/run-full.py hub /workspace/llm-clean-resume/h2-q/final
python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/h2-q/static-3.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_docs --output /workspace/llm-clean-resume/h2-q/docs.json
```

실행은 `.venv-linux312/bin/python` 3.12.14, UID 1000, host
`d833790959a76ac457fdeea3f21a6bea1eecfe12`, 136 packages 및 직전과 같은 bwrap/Xvfb/xclip·
5개 환경 변수를 사용했다. 전후 Python 671개 hash는 `29670d0f5cdcecbd7908a52d03d66ed2e0fbf710b930cff6b0a54e631f11ef89`,
전체 입력 830개/환경 hash는 `67bce3319a50d7d3a90ea57847a3154f226c3ad527db8114d560a84220720c36`로 각각 동일하다.

H2 전체는 아직 미완료다. 다음 배치 owner의 외부 재현은 빈 embedding payload와 일치하는
manifest hash를 backup이 수락하는 1 failure, error·Skip 0, 0.390초, exit 1이다.
`/workspace/llm-clean-resume/h2-r/batch-payload-probe.log`에 원래 Assertion/실제 효과와 원본 보존
검사를 보존했다. 소스는 위 final/inputs-before.json이며 현재 고정 suite 밖의 실패를 전체 회귀의
통과로 표현하지 않는다. 배치 의미·관측/읽기 경계와 나머지 owner 계약을 이어서 보강하며
H2 완료 전 H3–H5·P1·A–F로 진입하지 않는다.

## H2-p RAG 게시 코퍼스 필수 필드 — Level 2

H2-n/o 묶음 Push `b41599b4025428a1597d3eebefde27897042ac31`에서 이어서 게시 코퍼스를 보강했다.
이전 고정 소스의 외부 재현은 corpus.profile/게시 문서 title 소실을 backup이 수락하는 2 failure,
error·Skip 0, 1.149초, exit 1이었다. 두 원래 Assertion을 `test_rag_corpus_integrity`에 편입했다.

- RAGComponent의 `DOCUMENT_FIELDS`/`validate_document`를 준비·게시 문서가 함께 사용한다.
  준비 전용 이름을 바꾸고 모든 JobStorage/테스트 소비자를 직접 이관했으며 호환 wrapper는 없다.
- `validate_corpus`는 현재 root 필드·정확한 schema version·문서 mapping/ID·모든 문서와 corpus의
  profile 일치를 확인한다. 비어 있는 코퍼스만 명시 None profile을 허용한다. 기존 generation/
  positive revision 및 extraction integrity mismatch의 거부 계약과 Assertion을 유지한다.
- publish는 새 세대를 만들기 전에 검증하고, snapshot은 원본을 검증한 뒤 cache에 넣는다.
  기존 독립 cache 복사와 source stat key를 유지해 반환값 수정·파일 손상을 숨기지 않는다.
  원본 조회·추가/수정·compact·재선택·backup/clone에서 누락을 기본값으로 복원하지 않는다.

영향 범위는 RAG 문서 snapshot/prepare/publish/조회·쓰기·복제/백업·cache와 직접 소비자다.
저장 형식·트랜잭션 쓰기 경계·승인/예산/효과 정책은 바꾸지 않았다. H2-n의 실제 호출 관계와
공통 안전성 목록에 prepared/corpus owner 모듈을 추가했다. `modules.json`의 LLM 42개·Hub 6개
모듈 전체를 선택했고 실제 발견 목록에서 누락/중복을 검사했다. interaction_storage·usage_deletion·
operation_integrity·checkpoint_integrity·rag_storage·lifecycle_integrity, 기존 Job/Checkpoint 실제
중단·쓰기 장애와 직접 RAG 소비자 통합 검증을 포함한다. 필드 손상 matrix에서는 원본 hash와
실제 모델 효과 파일을 매 거부 뒤 비교한다. 새 공통 인프라/공개 정책 경계는 추가하지 않았다.

| 현재 브랜치 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 집중, 3 modules | 30/30 | 30 | 0/0/0 | 24.457초 | 0 |
| 초기 Level 2 LLM | 605/605 | 603 | 1/1/0 | 228.178초 | 1 |
| 초기 Level 2 Hub | 33/33 | 33 | 0/0/0 | 86.342초 | 0 |
| 그래프 거부 계약 보정 후 집중 | 23/23 | 23 | 0/0/0 | 22.602초 | 0 |
| Level 2 LLM, 42 modules | 605/605 | 605 | 0/0/0 | 230.035초 | 0 |
| Level 2 Hub, 6 modules | 33/33 | 33 | 0/0/0 | 87.218초 | 0 |

expected failure/unexpected success/발견 후 미실행/미발견 실행/중복은 각각 0이다. 전체 발견 목록은
LLM 1917개·Hub 540개이며 위 실행은 **선택한 모듈의 부분 검증**이다.
초기 Level 2의 그래프 관련 두 실패는 손상이 수락된 경우가 아니다. 누락된 graph의 기존 조회
거부 문구를 문서 owner로 옮겨 유지했다. Writer 시험은 후단 DB 구축의 KeyError 대신 같은
owner의 사전 ValueError와 `require graph` 문구를 검사하도록 명확히 했다. 기존 활성 세대·
보존 문서 Assertion과 새 전체 파일 hash 검사는 유지한다. 보정 후 집중 및 두 Level 2 suite를
새로 실행했으며 초기 실패를 최종 통과로 합산하거나 제외하지 않았다.
정적 감사 신규/stale/S1 0, 기존 debt 531, exit 0이다. R3 Mutation은 실제 두 번째 파일 효과
Assertion으로 호출 한도 누락을 검출(True, detector exit 0)했다. 문서는 이 검증 후 커밋보다 먼저 갱신한다.
문서 검사는 131 documents·178 Python fences·953 relative links·34 anchors, 오류 0, exit 0이다.

별도 Git 메타데이터 없는 복사본의 초안 결과는 현재 브랜치 검증으로 승계하지 않았다.
초안 첫 실행 84개 중 1개는 조기 손상 거부 메시지가 기존 `integrity mismatch` Assertion과 달라
실패했다(83 pass/1 failure, error·Skip 0, 39.964초, exit 1). 구현의 기존 오류 계약을 복원했다.
후속 초안 33개(17.479초)·52개(19.393초)는 exit 0이며 현재 브랜치에서는 위 검증을 새로 수행했다.

실제 명령과 전체 argv/요약/로그는 `/workspace/llm-clean-resume/h2-p/`에 보존했다.

```bash
python -m tests.llm.run_regression --suite llm --modules tests.llm.test_rag_corpus_integrity tests.llm.test_rag_prepared_integrity tests.llm.test_record_generations --output /workspace/llm-clean-resume/h2-p/focused-1.json
python -m tests.llm.run_regression --suite llm --modules tests.llm.test_unified_rag tests.llm.test_rag_corpus_integrity tests.llm.test_rag_prepared_integrity --output /workspace/llm-clean-resume/h2-p/focused-2.json
python /workspace/llm-clean-resume/h2-p/run-level2.py llm /workspace/llm-clean-resume/h2-p/final-2
python /workspace/llm-clean-resume/h2-p/run-level2.py hub /workspace/llm-clean-resume/h2-p/final-2
python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/h2-p/static-2.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_docs --output /workspace/llm-clean-resume/h2-p/docs-final.json
```

실행은 `.venv-linux312/bin/python` 3.12.14, UID 1000, host
`d833790959a76ac457fdeea3f21a6bea1eecfe12`, 136 packages와 직전 H2-o와 동일한 bwrap/Xvfb/xclip·
5개 환경 변수를 사용한다. 전후 Python 670개 hash는 `0e11190ddb10d905451714e18c6c5f8f01500401f64c9e8cb13c36c75124f6f0`,
전체 입력 829개/환경 hash는 `d6f24324e8456b83b1eccee7036449c185d8b5607bd7689a73dbfd2ad1e95a2e`로 각각 동일하다.
초기 Level 2의 source/input 환경 hash도 각각
`95ccc8b2fa239717568dcd3c50957201bf8b4a36fbf2e57791bd46b6a66d351a`와
`2437030f0f495973c9e537f942922c01641d107d4fb071eb75eb2b4103463392`로 실행 전후 동일하다.

전체 LLM/Hub는 이 소단계에서 **실행하지 않았다**. 변경이 RAG owner 검증과 직접 소비자로
한정되고 위 관련 통합·안전성 검증이 통과했으므로 Level 2 로컬 완료로 보존한다. RAG 제어/목록
연결·나머지 배치 의미와 관련 소단계를 묶은 다음 Level 3에서 전체를 실행한 뒤 Push한다.
이전 H2-n/o 전체 결과를 변경 후 코퍼스 구현의 전체 통과로 승계하지 않는다. H2 전체·원격 CI
완료가 아니며 H3–H5·P1 잔여·A–F는 아직 진입하지 않는다.
다음 목록 owner에서는 catalog.json 소실의 backup 수락과 빈 catalog의 게시 문서 은폐를
외부 재현했다(2 failure, error·Skip 0, 1.038초, exit 1). 증거는
`/workspace/llm-clean-resume/h2-q/catalog-loss-probe.log`, 소스는 이 `final-2/inputs-before.json`이다.
고정 suite 밖의 다음 범위 실패를 위 부분 검증의 통과로 표현하지 않으며 목록·게시 세대 연결을
보강하기 전 H2 전체를 완료하지 않는다.

## H2-o RAG 준비 문서·입력 연결 — H2-n/o 묶음 Level 3

H2-n 로컬 커밋 `71d0370cc64ce2442deb177d9fab115797e8fc86`에서 이어서 준비 영수증의 의미를
보강했다. manifest hash까지 갱신한 빈 prepared 객체를 backup이 수락하는 원래 Assertion을
유지했다. 구현 전 1 discovered/started/failure, pass/error/Skip 0, 0.384초, exit 1이다.

- RAGComponent가 준비 문서의 필수 필드·원문 hash·문서/절/청크 연결·벡터/profile·추출 manifest를
  검증한다. 저장 그래프의 완전한 출처·시각·가중치·중복 증거는 extraction owner가 검증한다.
  모델 응답의 생성용 정규화로 저장된 필드를 복원하지 않는다.
- JobStorage는 단건/배치 envelope와 원래 입력의 digest·문서 순서·title/content/metadata/description·
  초기 revision·시도 configuration 연결을 소유한다. `batches/document_N.json`도 같은 문서 owner에
  위임한다. metadata는 정규 JSON hash로 비교해 Python의 숫자/bool 동등 비교를 허용하지 않는다.
  준비 기록은 실행 시도의 증거 없이 존재하거나 새로 기록될 수 없다.
- 불완전/비활성 그래프의 진단, 명시 None description, 정상 재시도와 이미 수행한 모델 효과의
  재사용은 유지한다. 손상 때 재개·backup·clone을 거부하고 실제 효과 파일과 원본 바이트를 보존한다.

변경 영향은 Job의 claim/준비/재개/정리, RAG backup/clone, 공유 그래프 문서 형식과 Hub 소비자다.
H2-n의 영향도 목록과 기존 Job/Checkpoint의 실제 중단·쓰기 장애 테스트를 재사용했다. 이번에는
관련 소단계를 묶는 Level 3이므로 전체 LLM/Hub를 실행했다. 제품의 승인·예산·효과 정책과 CI의
필수 전체 Job은 변경하지 않았다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 초기 집중, 5 modules | 77/77 | 77 | 0/0/0 | 37.398초 | 0 |
| 새 필드·입력 연결 회귀 | 8/8 | 8 | 0/0/0 | 12.072초 | 0 |
| 그래프 보강 포함 집중, 6 modules | 102/102 | 102 | 0/0/0 | 52.946초 | 0 |
| FIFO 대역 수정 후 Job 회귀 | 11/11 | 11 | 0/0/0 | 5.554초 | 0 |
| 최초 전체 LLM | 1908/1908 | 1907 | 1/0/0 | 644.545초 | 1 |
| FIFO 수정 후 전체 LLM, JSON 타입 보강 전 | 1908/1908 | 1908 | 0/0/0 | 625.590초 | 0 |
| 최초 전체 Hub, JSON 타입 보강 전 | 540/540 | 540 | 0/0/0 | 709.571초 | 0 |
| JSON 타입 보강 후 준비 문서 집중 | 11/11 | 11 | 0/0/0 | 15.207초 | 0 |
| 최종 전체 LLM | 1910/1910 | 1910 | 0/0/0 | 640.639초 | 0 |
| 최종 전체 Hub | 540/540 | 540 | 0/0/0 | 707.209초 | 0 |

각 실행의 expected failure/unexpected success/누락/미발견 실행/중복은 모두 0이다.
최초 전체 LLM의 실패는 Job lease/FIFO 테스트 자식 프로세스의 SimpleNamespace owner가 새
문서 검증 메서드를 제공하지 않아 원래 안전성 Assertion 전에 중단된 것이다. 제품 검증기를
그대로 연결했고 FIFO·symlink·identity·실제 모델 효과 횟수 Assertion은 유지했다. 해당 Job 모듈
집중 검증 뒤 LLM 전체를 다시 실행했다. 이때 Job 테스트 대역만 바뀌어 Hub 전체의 소스·환경·
발견 540개 동일성을 `final-2/hub-reuse-evidence.json`으로 입증했다. 그러나 추가 검토의 외부
metadata-type-probe에서 metadata 숫자 1→True 변조의 backup 수락을 1 failure, error/Skip 0,
0.365초, exit 1로 재현했다. Python 동등 비교를 JSON hash 비교로 수정하고 원래 Assertion과
중첩 int/float/bool 4종 변조·재개/복제/실제 효과 보존 검증을 편입했다. 이 제품 변경은 앞선
재사용 근거를 무효화하므로 `reuse-invalidation.json`에 남기고 두 전체 suite를 새로 실행했다.
최종 게이트에는 `final-3/`의 새 전체 결과만 사용한다. 과거 통과/실패는 보존하며 서로 다른
실행을 한 번의 전체 실행으로 합치지 않는다. R3 Mutation은 실제 두 번째 파일 효과 Assertion으로 누락된 호출 한도를
검출(True, detector exit 0)했다. 정적 감사는 신규/stale/S1 0, 기존 debt 531, exit 0이다.
앞선 정적 검사 두 번의 exit 1은 configuration 비교를 포함한 복합 guard의 S2 후보였다.
비교 guard를 분리했으며 검사기·기존 debt·Assertion에 예외를 추가하지 않았다.
문서 검사는 131 documents·178 Python fences·953 relative links·34 anchors, 오류 0, exit 0이다.

실제 명령은 다음과 같다. 모든 실행의 argv/exit/summary와 전체 로그는
`/workspace/llm-clean-resume/h2-o/`에 보존했다. 최종 전체는 `final-3/`의 suite별 execution.json을 따른다.

```bash
python -m tests.llm.run_regression --suite llm --modules tests.llm.test_rag_prepared_integrity tests.llm.test_rag_checkpoint_integrity tests.llm.test_rag_job_state_integrity tests.llm.test_rag_components tests.llm.test_rag_optimization tests.llm.test_rag_resilience --output /workspace/llm-clean-resume/h2-o/focused-3.json
python -m tests.llm.run_regression --suite llm --modules tests.llm.test_rag_job_integrity --output /workspace/llm-clean-resume/h2-o/focused-4.json
python -m tests.llm.run_regression --suite llm --modules tests.llm.test_rag_prepared_integrity --output /workspace/llm-clean-resume/h2-o/focused-5.json
python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/h2-o/final-3/llm.json
python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/h2-o/final-3/hub.json
python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/h2-o/static-4.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_docs --output /workspace/llm-clean-resume/h2-o/docs-final.json
```

실행 Python은 `.venv-linux312/bin/python` 3.12.14, UID 1000이며 실제 bwrap·X11 경로를 사용했다.
TERM=xterm-256color, LITELLM_LOCAL_MODEL_COST_MAP=True, LITELLM_MODE=PRODUCTION,
HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb, HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip이다.
host는 `d833790959a76ac457fdeea3f21a6bea1eecfe12`, 의존성은 136 packages다. 검증 전후 Python
669개 source hash는 `67586ff467d145216a549b44cea55ad8520134e3be36034fa0714bdbd05379b6`, 전체 입력 828개와 환경 hash는
`77cbcc6d1eda3df6981b7814689ca05aeafcf0b15c3e9817c552d5aad3d04e4e`로 각각 동일하다. 이전 Hub 실행의 Python source hash는
`2341b60b08946f7489cfeb2dce79199c05829d2b0dcbc11238594ca96a411397`, 입력/환경 hash는
`5afaad909c1d47c436d7d0e927e116a26fc94f206d0ca1821135cd8c33adaaba`이고 해당 실행 전후도 동일하다.
중간 LLM 실행의 Python source/input 환경 hash도 각각
`05baa7895c9f49dbd6f5c4c2ce8af88f0d61a6db1d2ad4b91d0b111951c9965f`와
`7bfb2425231c2f4cd65ce2cc7866701f2ac00b76e8b4e26e73d736d58670396e`로 실행 전후 동일하다.
문서는 이 동일성을 확인한 뒤 커밋보다 먼저 갱신했다.

이 결과는 H2-n/o의 묶음 로컬 전체 게이트 통과다. 정확한 Push SHA의 원격 CI는 접근 제한으로
성공을 확인하지 못했으며 통과로 기록하지 않는다. H2 전체는 미완료다. 다음 RAG 게시 코퍼스의
profile/문서 title 소실을 backup이 수락하는 외부 재현 2 failure, error/Skip 0, 1.149초, exit 1을
`/workspace/llm-clean-resume/h2-p/corpus-loss-probe.log`에 별도로 보존했다. 고정 suite의 발견 목록에는
포함하지 않은 다음 owner의 미해결 근거이며 이 두 실패를 전체 통과로 표현하지 않는다.
재현 소스는 `final/inputs-before.json`이고 해당 corpus Reader는 이후 변경되지 않았다.
게시 코퍼스·제어/목록 연결·나머지 배치 의미와 owner 계약을 보강한 뒤 H2 최종 전체 게이트를
실행한다. H3–H5, P1 잔여, A–F는 아직 진입하지 않는다.

## H2-n RAG Job 상태·취소·재개 포기 — Level 2

H2-l/m 묶음 Push `419ddd85826099dd661d5abd012dff6f7e46e550`에서 이어서 Job 상태를 보강했다.
취소 표식/포기 영수증 소실 뒤의 실제 모델 파일 효과, 실행 시작 증거/실패 진단 소실의 조회
수락 네 재현을 원래 assertion으로 편입했다. 저장소 구현 전 4 discovered/started/failure,
error·Skip 0, 1.748초, exit 1이다. 빈 prepared 영수증의 backup 수락은 별도 owner 작업으로
남아 있으며 이 소단계의 해결 범위에 포함하지 않는다.

JobStorage가 상태·전이·필수 필드 집합을 선언하고 RAGJobs Writer와 모든 Reader가 사용한다.
생성 시 cancel_requested=False, resume_abandonment=None, failures=[], progress/ingestion={}를
명시한다. Reader는 필드 소실을 정상 미취소·미포기·빈 이력으로 복원하지 않는다. 실행한
Job은 started_at/pid/configuration이 필요하고, 실패·실행 후 취소는 종료 시각/실패 phase/
error/diagnostic_code/diagnostic을 요구한다. 실행 전 취소는 시도 필드 없이 취소 표식을
보존한다. 생성 근거는 불변이고 저장 전 상태 전이를 확인하며 취소와 포기를 되돌릴 수 없다.

진행·진단·출처·포기 영수증은 기존 공개 계약의 선언과 검증기에 위임하며 저장된 필드의
존재를 추가 확인한다. 제공자가 명시한 Diagnostic source는 기존 계약대로 보존한다.
관찰 출처는 실행/재시도 권한이 아니며 Job 상태·취소·포기·lease/configuration을 별도로
검증한다. 재시도는 입장 성공 뒤에만 기존 진단을 이력으로 보존한다. Backend 일괄 실행과
Hub 등록/진행도 명시적인 미포기 None을 사용한다. 저장 트랜잭션 경계는 바꾸지 않았다.

새 13개 시험은 matching-hash 필드 소실·잘못된 타입·상태/phase 모순·진단 code/source 형식·
실패 이력 소실을 조회/진행/재개 계획/backup/clone에서 거부하고 Job 원본과 모델 효과를
보존하는지 검사한다. 정상 취소·재시도와 벡터 재사용, 포기의 재시작 차단, owner의 취소/포기
철회 거부, 기록/목록 쓰기 직후 네 장애 지점의 rollback도 포함한다. 기존 Job·Checkpoint
실제 process-death·원본 보존 시험과 공통 승인/예산/효과/재개/삭제/트랜잭션 검증을 유지했다.

### 실제 실행과 실패 보존

| 실행 | 실제 결과 |
|---|---|
| 최초 LLM 집중 5개 모듈 | 69 pass, failure/error/Skip 0, 29.979초, exit 0 |
| 최초 Hub 집중 3개 모듈 | 22 중 20 pass, 1 failure/1 error, Skip 0, 36.327초, exit 1 |
| Hub 모의 Job의 명시 None 반영 | 22 pass, failure/error/Skip 0, 36.312초, exit 0 |
| 추가 owner 최초 실행 | 12 중 11 pass, 1 error, failure/Skip 0, 6.362초, exit 1 |
| 포기 영수증/진단 출처 필드 보강 | 12 pass, failure/error/Skip 0, 6.753초, exit 0 |
| 최초 Level 2 LLM 선택 | 586 중 585 pass, 1 failure, error/Skip 0, 204.156초, exit 1 |
| 최초 Level 2 Hub 선택 | 33 pass, failure/error/Skip 0, 85.715초, exit 0 |
| 빈 실패 이력 Assertion 갱신 후 Backend 모듈 | 27 pass, failure/error/Skip 0, 7.903초, exit 0 |
| 명시적 제공자 진단 출처의 별도 재현 | 1 error, failure/Skip 0, 0.150초, exit 1 |
| 제공자 출처 보존 후 전체 owner 모듈 | 13 pass, failure/error/Skip 0, 6.914초, exit 0 |
| 최종 Level 2 LLM 선택 | 587 discovered/started/pass, 206.423초, exit 0 |
| 최종 Level 2 Hub 선택 | 33 discovered/started/pass, 83.912초, exit 0 |
| 최종 정적 감사 | 후보 531개, new/stale/S1 0, exit 0 |
| 최종 R3 Mutation | 실제 두 번째 Tool 파일 효과로 한도 누락 탐지, detector exit 0 |
| 문서 검사 | 문서 131개, Python fence 178개, 상대 링크 953개, anchor 34개, 오류 0, exit 0 |

Hub의 초기 실패는 mock Job의 resume_abandonment 누락이었다. 기존 동시 재시도/관찰 수
Assertion을 유지하고 명시 None을 추가했다. owner의 빈 포기 영수증은 공통 dataclass
생성자의 TypeError를 노출했으므로 저장 필드의 완전성을 먼저 확인하도록 수정했다.
최초 Level 2 실패는 stale 재시도 거부 뒤 failures 필드가 없어야 한다는 과거 표현이었다.
이를 정확한 빈 []로 갱신했고 stale CAS 거부·현재 진단 보존·모델 재사용 Assertion을 유지했다.

이때 39개 LLM 모듈 559개/수정 모듈 27개/Hub 33개를 재사용할 동일성 근거를 별도로
계산했지만 최종 검토에서 새 Reader의 진단 출처 제한이 기존 공개 계약보다 좁음을 발견했다.
공개 계약은 명시된 제공자 출처를 보존한다. 해당 경우를 실제 회귀로 추가하고 Reader를
바로잡았으며 제품 변경으로 앞선 재사용 근거를 무효화했다. reuse-invalidation.json에 이유를
남기고 최종 선택 게이트를 다시 실행했다. 과거 실패를 성공 집계로 바꾸지 않는다.

### 범위와 동일성

H2-h의 RAG 영향도 목록 43개를 재사용하고 Job 상태·H2-i checkpoint·Hub 확장 소비자
모듈을 더했다. 실제 Job API 호출과 nullable 포기 표식 소비자를 다시 확인했다. 최종 목록과
근거는 modules.json이다. LLM 40개 모듈/Hub 6개 모듈을 동일한 고정 소스에서 독립 실행했다.
명령은 `.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --modules
<기록한 40개 모듈> --output /workspace/llm-clean-resume/h2-n/final/level2-llm.json`과 같은
형식의 `--suite hub --modules <기록한 6개 모듈>`이다. 정확한 argv/exit code는
final/level2-llm-execution.json과 level2-hub-execution.json에 있다. 두 선택 실행이며 하나의
전체 실행으로 합산하지 않는다. 최종 각각 failure/error/Skip/expected failure/unexpected
success/missing/unexpected/duplicate가 모두 0이고 발견·실행 ID는 모두 보존한다.

최종 전후 Python 668개 manifest는 5ba3f1024b990c7da26cbfe1200a4af1df88a4949037cdcc55d52df90cb57b8a,
파일 827개/의존성 136개 및 host·Python·실행 환경 manifest는 d17efb1089d21aaba54f16ff9427e67e84b3bea060271be12ad4b50e3225b245로 각각 일치한다.
환경은 H2-m과 같은 Python 3.12.14, host d833790959a76ac457fdeea3f21a6bea1eecfe12,
TERM=xterm-256color, LITELLM_LOCAL_MODEL_COST_MAP=True, LITELLM_MODE=PRODUCTION,
HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb, HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip이다.
정적 명령은 check_architecture --require S1, R3는 check_budget_mutation이다. 최초 정적
검사는 new 0/stale 17로 exit 1이었다. 실제 RAGJobs의 소유 상태 선언으로 해소한 S3 17개만
resolved-debt.json에 기록하고 부채 목록에서 제거했다. 새 허용 항목이나 검사기 변경은 없다.
문서 명령은 check_docs --output /workspace/llm-clean-resume/h2-n/docs.json이다.
전체 원시 증거와 사전 격리 사본 시험은 `/workspace/llm-clean-resume/h2-n/`에 보존한다.

이번 소단계는 **전체 LLM/Hub 회귀를 실행하지 않았다**. 영향이 RAG Job owner와 확인한
소비자로 제한됨을 근거로 Level 2를 적용했다. 문서를 우선 갱신한 뒤 로컬 커밋하며, 다음
prepared 영수증 owner 보강과 묶은 Level 3 전체 게이트에서 Push한다. 로컬 완료는 이 Job 상태 범위만을 뜻한다. 별도 prepared 원본의 의미 검증은 미해결로 남아 있다. H2 전체·H3–H5·
P1 잔여·A–F는 미완료이며 기존 38개 데이터군 보고서는 이번 변경 범위만 갱신한다.

## H2-m Memory 기록 이력 — H2-l/m Level 3 묶음 게이트

H2-l 로컬 커밋 `733e953bf163a2c69de03ff6291f217eb0284042`에서 이어서 Memory 기록 이력을
보강했다. 내용 hash를 맞춘 뒤에도 수락하던 revision=True, 생성 시각 변경, Session A→B
범위 변경, 삭제되지 않은 기록의 restore 이력 네 실패를 원래 assertion 그대로 편입했다.
외부 재현은 4 failure, error·Skip 0, 0.406초, exit 1이었다. 저장소 편입 직후도 4 discovered/
started/failure, error·Skip 0, 0.352초였다. 이 실행의 종료 코드는 세션 복원에서 회수하지 못했고
성공으로 처리하지 않는다. 원본 before.json/log와 외부 재현 로그를 모두 보존한다.

MemoryComponent가 이력 필드·연산·불변 identity를 단일 선언으로 소유한다. 이력 revision은
정확한 정수이며 모든 revision은 같은 id/generation/생성 시각/최초 출처/범위를 유지한다.
생성은 미삭제 상태와 동일한 생성·갱신 시각/출처를 요구한다. 이후 create는 거부하며,
update/save/delete는 미삭제 기록, restore는 삭제 기록에서만 가능하다. 삭제·복원은 관리
필드 외 내용을 보존하고, 병합 삭제의 superseded_by 변경만 MemoryConsolidation이 검증한다.
시각은 timezone을 가진 ISO 값이어야 하지만 시스템 시계가 뒤로 조정되는 경우를 revision
역행으로 간주하지 않는다. 공개 CRUD와 병합 Writer가 같은 연산 선언·검증기를 사용한다.

### 검증과 영향 범위

새 시험은 위 네 재현, 필드 소실·bool/float revision·잘못된 시각/연산·불변값 변경·삭제/복원
내용 변경을 matching-hash 자료로 검사한다. 공개 조회·다른 Session 목록·backup/clone은
손상을 거부하고 모든 Memory 소유 파일을 보존한다. 정상 save/delete/restore/clone의 이력과
Session 격리, 기록/목록 쓰기 직후 장애의 원본 보존도 검사한다. H2-l 병합의 실제 exit 77
14개 경계와 기존 승인·예산·효과·재개·삭제·트랜잭션 안전성 시험은 전체 게이트에 포함했다.

집중 명령은 `.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --modules
 tests.llm.test_memory_history_integrity tests.llm.test_memory_component
 tests.llm.test_memory_consolidation_integrity tests.llm.test_record_generations
 tests.llm.test_refinement tests.llm.test_schema_ownership --output
 /workspace/llm-clean-resume/h2-m/focused-1.json`이다. 이후 새 owner 모듈만 focused-2.json으로
재실행했다. 첫 실행의 1 failure는 Memory 원본과 함께 Project 로그까지 바이트 동일성을
요구한 새 fixture에서 발생했다. 모든 Memory 소유 파일을 비교하도록 범위를 바로잡았으며
원래 네 결함 assertion과 파일/목록 rollback 검사는 유지했다.

| 실행 | 실제 결과 |
|---|---|
| 최초 집중 6개 모듈 | 77 discovered/started, 76 pass, 1 failure, error·Skip 0, 28.512초, exit 1 |
| 수정 후 전체 이력 모듈 | 11 discovered/started/pass, failure/error/Skip 0, 2.753초, exit 0 |
| 전체 LLM | 1,886 discovered/started/pass, 610.174초, exit 0 |
| 최초 전체 Hub | 540 discovered/started, 539 pass, 1 error, failure·Skip 0, 717.034초, exit 1 |
| 수정 후 전체 Hub | 540 discovered/started/pass, 685.301초, exit 0 |
| 정적 감사 | 후보 548개, new/stale/S1 0, exit 0 |
| R3 Mutation | 실제 두 번째 Tool 파일 효과로 한도 누락 탐지, detector exit 0 |
| 문서 검사 | 문서 131개, Python fence 178개, 상대 링크 953개, anchor 34개, 오류 0, exit 0 |

최초 전체 Hub는 FeatureUITests의 방향키 선택 대기에서 15초 timeout으로 끝났다. 원래
시험의 threaded completer에 0.1초 지연을 주입해 같은 위치를 재현했다(1 error, 16.340초,
exit 1). 후보 준비 조건을 보강한 다음 실행에서는 줄바꿈 직후의 임시 빈 completion state
Assertion이 드러났다(1 failure, 2.174초, exit 1). 텍스트/후보/취소 완료를 함께 기다리도록
fixture를 수정했고 최종 동일 지연 주입은 1 pass, 2.162초, exit 0이다. 원래 선택·내용·
미전송·취소 Assertion과 15초 timeout은 유지했다. 모듈 단독 실행은 수정 전 7 pass
15.494초, 후보 조건 수정 후 7 pass 15.381초, 각각 exit 0이었다. 마지막 취소 대기 수정은
지연 주입과 아래 전체 Hub에서 검증했다. 실패 로그를 지우거나 성공 집계에서 숨기지 않는다.

전체 실행 명령은 `.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output
/workspace/llm-clean-resume/h2-m/final/llm.json` 및 `--suite hub --output
/workspace/llm-clean-resume/h2-m/final-2/hub.json`이다. 최종 각 suite의 failure/error/Skip/expected
failure/unexpected success/missing/unexpected/duplicate는 모두 0이다. argv·exit code·집계는
final/llm-execution.json과 final-2/hub-execution.json에 기록한다. focused 실행들을 전체 결과로
합산하지 않는다. 정적 명령은 check_architecture --require S1, 안전성 명령은
check_budget_mutation이다. 문서 명령은 check_docs --output
/workspace/llm-clean-resume/h2-m/docs.json이며 문서 우선 갱신 뒤 결과를 검사한다.

LLM 통과 후 바뀐 파일은 tests/hub/test_features.py 하나뿐이다. 제품·LLM 시험/Fixture·
의존성·실행 환경·발견 목록 1,886개가 같음을 final-2/llm-reuse-evidence.json으로 확인했고
LLM 전체 성공·정적 감사·R3를 제한적으로 재사용했다. Hub는 수정 후 전체 실행을 다시
수행했다. 서로 다른 소스 manifest의 독립 실행이며 하나의 전체 실행으로 표현하지 않는다.

최초 전체 검증 전후 Python 667개 manifest는
6bd0c92891dca8763e2260c329c2a3b02215369ae3cf9d9d6d4beaccdeae44aa,
제품·시험·Fixture/자산·문서 826개와 의존성 136개/host/환경 manifest는
a56011c51dadc510dd0579808b473bd82c58bb066c21c32dbdd1e84a0cfd6276으로 각각 일치한다.
Hub 수정 후 전후 Python 667개 manifest는
59fd5733771fb976b831b95e7afa97ed7b7e7d1527a857f14eb583b8fe034100,
826개 파일과 실행 환경 manifest는
98112c92fa626bf4b853c5f65f6d9dddd194a56d05c8ba61ee4f6dff1b9b3cd4로 일치한다.
실행 환경은 H2-l과 같은 Python 3.12.14, host d833790959a76ac457fdeea3f21a6bea1eecfe12,
TERM=xterm-256color, LITELLM_LOCAL_MODEL_COST_MAP=True, LITELLM_MODE=PRODUCTION,
HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb, HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip이다.
검증 중 소스를 고정했고 결과 확인 뒤 문서만 우선 갱신했다. 전체 증거는
`/workspace/llm-clean-resume/h2-m/`에 있다. H2-l/m을 작업 브랜치에 함께 Push하며 GitHub CI
조회의 기존 Forbidden 제한 때문에 원격 CI 통과를 선언하지 않는다.

### 남은 차단 항목

고정된 전체 게이트 밖의 rag-job-state-probe.py는 별도 임시 작업공간에서 RAG 네 실패를
재현했다(4 failure, error·Skip 0, 1.768초, exit 1). 취소 표식 소실의 실제 모델 파일 효과,
실행 시작 증거/실패 진단 소실의 조회 수락, 빈 prepared 영수증의 backup 수락이다. 이는
위 전체 실행에 포함되지 않았고 다음 H2에서 원래 assertion을 편입해 우선 수정한다.
별도 rag-job-abandonment-probe.py도 포기 영수증 소실 뒤 실제 모델 파일 효과를 재현했다
(1 failure, error·Skip 0, 1.000초, exit 1). 이 다섯 미해결 재현을 다음 owner 보강에 포함한다.
H2 전체·H3–H5·P1 잔여·A–F는 미완료이며 위 전체 성공을 이 차단 항목의 해결로 간주하지 않는다.
기존 38개 데이터군 감사는 재작성하지 않고 이번 Memory 범위와 다음 RAG 차단만 갱신했다.

## H2-l Memory 병합 저널·영수증 — Level 2

H2-j/k 묶음 Push `967676ad783162951a2315a546c7b803823c1df6`에서 이어서 병합 owner를
보강했다. 완료 영수증 소실의 backup 수락, pending 저널 소실의 읽기 차단 해제,
entries=[]인 손상 저널의 허위 완료 영수증 생성 세 재현을 회귀에 편입했다.
구현 전 3 failure, error·Skip 0, 0.277초, exit 1이다.

MemoryConsolidation이 consolidations/manifest.json의 현재 버전·Project/Component 소유권,
정확한 pending/receipt 집합과 내용 revision을 소유한다. 새 저장소에서만 초기화하며
원본/목록/필수 디렉터리 소실·미선언 추가·비정규 파일을 보존하고 거부한다. 읽기·쓰기 차단,
명시 복구·backup/clone·재선택·비활성 owner의 보존 검증이 같은 목록을 사용한다.

저널은 이전 전체 이력과 정확히 한 revision 뒤의 검토된 변경을 증명해야 한다. proposal과
서로 다른 superseded 집합, proposal.replaces의 generation/revision, scope, Memory identity,
source·변경 전 digest·변경 후 기록/이력을 검증한다. 복구는 모든 현재 기록이 before 또는
after임을 확인한 뒤에만 적용한다. 기록·완료 영수증·pending 제거·목록은 한 transaction으로
확정하며 조회가 자동 복구하거나 소실을 완료로 바꾸지 않는다. clone은 기록/이력에 대응하는
완료 영수증과 원래 출처를 보존하고 pending 상태는 거부한다. 기존 purge 의미를 새로 제한하지
않으며 과거 영수증의 대상이 현재도 반드시 존재해야 한다는 조건은 추가하지 않았다.

Memory의 candidate/confirmed 및 검색 all 선언은 MemoryComponent가 소유하고 Schema·Writer·
Reader·병합이 함께 사용한다. 실제로 해소한 S3 네 항목만 부채 목록에서 제거했으며 검사기와
새 허용 항목은 변경하지 않았다. 나머지 부채는 548개다.

### 검증과 범위

H2-j에서 확인한 Memory 소비자·공통 안전성 영향도 목록을 재사용하고 H2-k/l의 새 owner
시험을 더했다. Memory 이력·generation·Refinement·Schema·Project/Component·삭제·백업·복제,
승인·예산·효과·재개·원자성 소비자를 포함한다. 74개 모듈 선정 근거는
`/workspace/llm-clean-resume/h2-l/modules.json`, 정확한 argv는 level2-llm-command.json과
level2-hub-command.json이다. 동일한 고정 소스에서 LLM 51개 모듈과 Hub 23개 모듈을
독립 프로세스로 병렬 실행했다. 두 선택 실행이며 하나의 전체 실행으로 합산하지 않는다.

명령은 `.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --modules
<기록한 51개 모듈> --output /workspace/llm-clean-resume/h2-l/level2-llm.json`과 같은 형식의
`--suite hub --modules <기록한 23개 모듈> --output /workspace/llm-clean-resume/h2-l/level2-hub.json`이다.
Python/host/TERM·LiteLLM·Xvfb/xclip 환경 변수는 H2-k와 동일하다.

| 실행 | 실제 결과 |
|---|---|
| 최초 기존 소비자 집중 | 75 pass, failure/error/Skip 0, 28.727초, exit 0 |
| 추가 owner 최초 실행 | 마지막 fixture 교착으로 SIGTERM 종료, exit 143, 정식 집계 JSON 없음; 성공 아님 |
| fixture 수정 후 전체 owner 모듈 | 11 pass, failure/error/Skip 0, 7.832초, exit 0 |
| Level 2 LLM 선택 | 전체 discovery 1,875 중 749 discovered/started/pass, 285.717초, exit 0 |
| Level 2 Hub 선택 | 전체 discovery 540 중 134 discovered/started/pass, 255.458초, exit 0 |
| 정적 감사 | new/stale/S1 0, 후보 548개, exit 0 |
| R3 mutation | 실제 두 번째 Tool 파일 효과로 한도 누락 탐지, detector exit 0 |
| 문서 검사 | 문서 131개, Python fence 178개, 상대 링크 953개, anchor 34개, 오류 0, exit 0 |

최초 정적 감사는 신규 3/stale 1로 exit 1이었다. 상태·manifest 필드를 owner 선언으로
정리한 뒤 신규 0/stale 4였고, 실제로 해소된 네 기존 S3 항목만 제거해 최종 검사를 통과했다.
각 감사 JSON과 resolved-debt.json을 보존한다. 문서 명령은
`.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/h2-l/docs.json`이다.

교착은 시험에서 ownership의 동기 RLock을 잡은 event-loop thread가 같은 잠금을 필요로 하는
StorageIO 조회를 await한 원인이었다. 정확한 실행 PID만 종료하고 대기 상태·원인·로그를
focused-2-interruption.json과 focused-2.log에 보존했다. 조회를 잠금 전에 수행하도록 fixture를
고쳤으며 기존 assertion은 유지했다. 불완전 실행을 pass/skip 0으로 간주하지 않았다.

새 시험은 apply와 명시 recover의 저널·목록·두 기록·영수증·unlink·commit의 실제 exit 77
14개 경계를 도달 witness와 함께 검사한다. 재열기 후 전체 원본 또는 완전한 새 상태,
pending의 읽기 차단 유지, 명시 복구 후 중복 revision 없음, 복제된 영수증 출처를 검증한다.
pending fixture는 정상 Writer의 복구 인계 지점을 주입해 먼저 확정한 것으로, 원래 apply의
실제 process-death가 중간 상태를 확정했다는 주장은 하지 않는다. 실제 전원 단절은 미검증이다.

두 Level 2 각각 failure/error/Skip/expected failure/unexpected success/missing/unexpected/
duplicate는 모두 0이다. 전후 Python 666개 manifest는
dc37a218071fe1eb7585e35d51d55ef045821bfaa4e60a2759eaad58762aa3fc,
제품·시험·Fixture/자산 825개·의존성 136개·host/환경 manifest는
76f33199a872022ba98908efa73225b3bd4f0aa061f661443c97ae02b7666e02로 각각 일치한다.
정적 명령은 check_architecture --require S1, 안전성 명령은 check_budget_mutation이다.
전체 실행/중단/실패 증거는 `/workspace/llm-clean-resume/h2-l/`에 보존한다.

이번 소단계는 **전체 LLM/Hub 회귀를 수행하지 않았다**. 영향은 확인된 Memory owner와
소비자이며 문서를 우선 갱신하고 로컬 커밋한다. 다음 Memory 기록/이력 보강과 묶은 Level 3
전체 게이트에서 Push한다. 상위 H2 완료나 원격 CI 완료를 뜻하지 않는다.

별도 history-contract probe는 저장 내용 hash를 맞춘 뒤에도 history revision=True,
revision 간 created_at 변경, 이전 deleted=False인 restore 이력, Session A→B 범위 변경을
수락하는 네 실패를 재현했다(4 failure, error·Skip 0, 0.406초, exit 1). 이 시험은 위 선택
실행에 아직 포함되지 않았으며 다음 H2에서 우선 수정한다. 나머지 owner 계약·H2 전체·
H3–H5·P1 잔여·A–F는 미완료다. 기존 38개 데이터군 감사와 과거 결과를 다시 작성하지 않는다.

## H2-k Memory 압축 요청·cursor·원본 참조 — H2-j/k Level 3 묶음 게이트

H2-j 로컬 커밋 `73dbe9df1ff1f077ce97ddf3eee255339eeace66`에서 이어서 압축 상태를 보강했다.
요청 파일 소실의 빈 상태 처리, cursor 소실 뒤 실제 두 번째 모델 파일 효과, Tool 참조 소실
뒤 실제 원본 파일 삭제의 세 재현을 회귀에 편입했다. 구현 전 3 failure, error·Skip 0,
1.829초, exit 1이다. 모델과 Tool은 시험용 adapter이며 인증 모델 호출은 아니다.

Compactions가 현재 버전·Project/Component 소유권과 정확한 문맥/Tool 요약 파일 집합,
scope·revision을 compactions.manifest.json으로 소유한다. 새 Memory에만 빈 목록과 두
디렉터리를 만든다. 생성 전/명시 삭제 후 상태만 빈 값이며 소실·미선언 기록·변조·링크·비정규
파일은 보존·거부한다. 기록·목록 및 Tool 원본 파일/참조 생성, 명시 삭제는 기존 workspace
transaction에 함께 참여한다. 비활성 Memory와 backup/clone/재선택도 같은 owner 검증을 쓴다.

현재 문맥의 scope·토큰 Nullable 값·턴 출처, 요청의 상태·옵션·원본 리비전, 실행 Run,
부분/완료 cursor와 적용된 요약, Tool reference/summary mode·원본 참조를 hash와 별도로
검증한다. 정상 압축 옵션 Reader/Writer는 한 검증 함수를 쓴다. Tool mode는 Writer가 명시한다.
조회·재개·참조 수집·삭제에서 손상 자료를 기본값으로 바꾸지 않는다. 원래의 명시 resume,
취소·삭제 허용 상태와 모델/Tool 한도·외부 효과 실행 정책은 유지한다.

### 검증과 범위

Memory 저장 상태·원본 파일 참조·Hub 압축 API가 연결되므로 H2-j/k의 현재 통합 소스를
전체 LLM/Hub로 검증했다. 최초 집중은 memory_compaction_integrity, large_data,
memory_processing, memory_summary_integrity, Hub summary_api 5개 모듈이다. 추가 안전성은
memory_compaction_integrity 전체, 마지막 Reader/Writer 검증은 그 모듈과 large_data 전체다.
실행 명령의 공통 형태는 아래와 같고 각 JSON에 전체 발견/실행 식별자를 보존했다.

```bash
python -m tests.llm.run_regression --suite all --modules tests.llm.test_memory_compaction_integrity tests.llm.test_large_data tests.llm.test_memory_processing tests.llm.test_memory_summary_integrity tests.hub.test_summary_api --output /workspace/llm-clean-resume/h2-k/focused-2.json
python -m tests.llm.run_regression --suite llm --modules tests.llm.test_memory_compaction_integrity tests.llm.test_large_data --output /workspace/llm-clean-resume/h2-k/focused-4.json
python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/h2-k/final/llm.json
python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/h2-k/final/hub.json
python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/h2-k/final/architecture.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_docs --output /workspace/llm-clean-resume/h2-k/final/docs.json
```

python은 `.venv-linux312/bin/python` 3.12.14다. TERM=xterm-256color,
LITELLM_LOCAL_MODEL_COST_MAP=True, LITELLM_MODE=PRODUCTION,
HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb,
HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip을 사용했다. host는 기존
ish d833790959a76ac457fdeea3f21a6bea1eecfe12이며 비root 실제 bwrap/X11 경로를 유지했다.

| 실행 | 실제 결과 |
|---|---|
| 최초 직접 소비자 | 67 실행, 66 pass/error 1, 72.335초, exit 1 |
| owner fixture 이관 후 | 67 pass, failure/error/Skip 0, 72.559초, exit 0 |
| 추가 필드·원본·중단 검증 | 9 pass, 9.191초, exit 0 |
| Reader/Writer 옵션 검증 후 | 28 pass, failure/error/Skip 0, 26.532초, exit 0 |
| 전체 LLM | 1,864 discovered/started/pass, 594.062초, exit 0 |
| 전체 Hub | 540 discovered/started/pass, 692.921초, exit 0 |
| 정적 감사 | new/stale/S1 0, 후보 552개, exit 0 |
| R3 mutation | 실제 두 번째 Tool 파일 효과 assertion으로 한도 누락 탐지, detector exit 0 |
| 문서 검사 | 문서 131개, Python fence 178개, 상대 링크 952개, anchor 34개, 오류 0, exit 0 |

최초 오류는 옵션 원본만 바꾸던 fixture가 새 존재/내용 검증에서 먼저 차단된 경우였다.
정규 writer로 옵션을 바꾸도록 이관하고 기존 cursor로 모델을 다시 호출하지 않는 assertion을
유지했다. 최초 정적 검사의 신규 5개 후보는 owner 상태 상수와 조건별 검증으로 해소했으며
검사기나 부채 baseline을 완화하지 않았다. 모든 실패 로그는 보존한다.

새 시험은 생성/수정/삭제의 원본·목록·commit과 Tool 원본 생성 중간을 포함한 실제 exit 77
13개 경계를 도달 witness로 확인한다. 재열기 후 원본 또는 완전한 새 상태, 원본/참조의 동시
rollback, 확정된 참조의 실제 파일 삭제 차단까지 확인한다. 필수 필드 소실은 해당 기록의
내용 hash를 맞춘 경우에도 의미 검증으로 거부한다. 실제 전원 단절·network filesystem은
이 검증 범위가 아니다.

전체 두 suite 각각 발견=실행=pass이며 failure/error/Skip/expected failure/unexpected success/
missing/unexpected/duplicate는 모두 0이다. 서로 다른 실행을 전체 실행으로 합산하지 않았다.
전체 전후 Python 665개 manifest는 d5a880d938e1981fe96613211e4a607934c01f1eb82058bb011e65b0072960d2,
제품·시험·Fixture/자산 824개·의존성 136개·host/환경 manifest는
3a9f133dcac8b8cb9c156f8e147a555535d38c52becd87d4bb4a26430403c5de로 각각 일치한다.
통과 후 문서를 먼저 갱신하고 H2-j/k를 묶어 작업 브랜치에 Push한다. 필수 GitHub Actions는
그대로 전체 실행한다. API Forbidden으로 원격 CI를 확인하지 못했으므로 통과로 보고하지 않는다.

별도 Memory 병합 probe는 완료 영수증 소실을 backup이 수락, pending 저널 소실 후 읽기
차단 해제, entries=[]인 손상 저널이 제안을 적용하지 않고 완료 영수증을 생성하는 세 실패를
재현했다(3 failure, error·Skip 0, 0.381초, exit 1). pending 상태는 정상 journal Writer의
복구 인계 지점에 장애를 주입해 보존했으며 실제 프로세스 중단 상태라는 주장은 하지 않는다.
이 세 시험은 위 전체 suite에 아직 포함되지 않았으며 다음 H2 보강에서 우선 수정한다.
나머지 owner 계약·H2 전체·H3–H5·P1 잔여·A–F는 미완료다. 기존 38개 데이터군 감사와 과거
결과는 유지하고 이번 owner의 변경 사항만 갱신한다.

## H2-j Memory Session 요약 존재 증거 — Level 2

H2-h/i 묶음 Push `2cd77b5522525ad667e36966acb4513182e28fe3`에서 이어서 진행했다.
저장된 요약 소실을 None으로 읽고 generation/revision을 새로 발급하는 두 실패를 회귀에
편입했다. 구현 전 2 failure, error·Skip 0, 0.168초, exit 1이다.

Memory의 SummaryStorage가 필수 contexts.manifest.json, 현재 버전·Project/Component 소유권,
정확한 Session 요약 집합과 내용 revision을 소유한다. 새 Memory 때만 빈 목록을 만든다.
아직 생성하지 않았거나 명시적으로 지운 요약은 None이지만 선언된 요약/목록/디렉터리 소실,
미선언 추가·손상·링크·비정규 파일은 원본을 보존하고 거부한다. publish/clear는 요약과 목록을
같은 transaction으로 확정한다. 기존 명시 삭제 후 새 generation과 revision 1로 재생성하는
계약, generation+revision CAS는 유지한다. 필수 generation·metadata·source 의미 검증은
hash와 별도로 수행하며 저장 JSON의 비객체 값은 owner의 손상 ValueError로 보고한다.

조회·처리 snapshot·이력 참조·backup/clone·재선택이 같은 owner 검증을 사용한다. 비활성
Memory도 필수 존재를 보호한다. clone이 Session 요약을 복사하지 않는 기존 의미는 유지한다.
공통 저장/실행 정책이나 모델·Tool 한도는 변경하지 않았다.

### 검증과 범위

실제 MemoryComponent/MemoryData/요약/이력 소비자, Hub Memory 선택·관리 소비자에 공통
안전성·Project/Component/삭제·원자성 모듈을 더했다. 선택된 fixture를 import하는 테스트도
역방향 의존성을 따라 포함한 72개 모듈이다. 모듈별 선정 근거는
`/workspace/llm-clean-resume/h2-j/modules.json`, 실제 argv는 `level2-command.json`이다.
명령은 `.venv-linux312/bin/python -m tests.llm.run_regression --suite all --modules
<기록한 72개 모듈> --output /workspace/llm-clean-resume/h2-j/level2.json`이다.
all은 discovery 범위이며 전체 실행이 아니다. Python/host/환경 변수는 H2-i와 동일하다.

| 실행 | 실제 결과 |
|---|---|
| 기존 직접 소비자 집중 | 66 pass, failure/error/Skip 0, 56.971초, exit 0 |
| 추가 손상·중단 최초 | 9 실행, 8 pass/error 2(한 부모 시험), 4.536초, exit 1 |
| owner 오류 통일 후 | 9 pass, failure/error/Skip 0, 4.442초, exit 0 |
| 최종 Level 2 | 863 discovered/started/pass, 507.923초, exit 0 |
| 정적 감사 | new/stale/S1 0, 후보 552개, exit 0 |
| R3 mutation | 실제 두 번째 Tool 파일 효과로 한도 누락 탐지, detector exit 0 |
| 문서 검사 | 문서 131개, Python fence 178개, 상대 링크 951개, anchor 34개, 오류 0, exit 0 |

추가 시험의 두 오류는 Null/array JSON을 공통 deserialize가 TypeError로 거부한 경우였다.
owner에서 손상 ValueError로 전달하도록 수정하고 기존 거부·원본 보존 assertion은 유지했다.
새 시험에는 생성/수정/삭제 각각 원본·목록·commit의 실제 exit 77 9개 경계, 도달 witness,
재열기 후 전체 원본 또는 완전한 새 상태, 장애 후 CAS와 명시 삭제 상태를 포함한다.
실패 로그를 포함한 개별 실행 증거는 `/workspace/llm-clean-resume/h2-j/`에 보존한다.
정적 검사 명령은 check_architecture --require S1, 안전성 검사는 check_budget_mutation이다.
문서 명령은 `.venv-linux312/bin/python -m tests.llm.check_docs --output
/workspace/llm-clean-resume/h2-j/docs.json`이며 갱신 후 실행했다.

Level 2는 전체 discovery 2,395개 중 위 모듈을 선택했으며 failure/error/Skip/
expected failure/unexpected success/missing/unexpected/duplicate는 모두 0이다.
전후 Python 664개 manifest는 1f6d28c21ba4385f7caf567352ab52677fb22e8518a779d7dfa4f3c07dc282a2,
제품·시험·Fixture/자산 823개·설치 의존성 136개·host/환경 manifest는
5ed4043fe6bcc0a3614243bd1b0b6e419ba32ae6491c12e0ac0760c5cbf65e71로 각각 일치한다.

이번 소단계는 **전체 LLM/Hub 회귀를 수행하지 않았다**. 새 저장 owner와 확인한 소비자로
영향이 제한되며 전체 gate는 다음 Memory 압축 상태 보강과 묶는다. 문서를 우선 갱신하고
독립 로컬 커밋으로 보존한다. 상위 H2 완료·Push·원격 CI 완료를 뜻하지 않는다.

다음 별도 압축 상태 probe는 요청 파일 소실의 빈 상태 처리, 저장 cursor 소실 후 실제 모델
파일 효과 반복, Tool 요약 참조 소실 후 실제 원본 파일 삭제를 재현했다(3 failure,
error·Skip 0, 1.872초, exit 1). 최초 probe는 import한 fixture 클래스까지 22개 시험을 실행해
19 pass/3 failure, 18.507초, exit 1이었으며 로그를 보존했다. 발견 범위만 세 재현으로 바로잡고
assertion은 유지했다. 이 세 실패는 위 Level 2에 아직 편입되지 않았다. 다음 H2에서 우선
수정하며 H2 전체·H3–H5·P1 잔여·A–F로 넘어가지 않는다. 나머지 owner 감사는 기존 기록을
유지하며 미완료다. 실제 인증 모델 호출이나 전원 단절은 검증하지 않았다.

## H2-i RAG 준비·배치 영수증 — H2-h/i Level 3 묶음 게이트

H2-h 로컬 커밋 `b362b79f93f7ba107816623da0b9020ec5c842ec`에서 이어서 준비 결과와
배치 영수증의 필수 존재를 보강했다. H2-h 때 별도로 기록한 실제 두 번째 embedding/파일 효과
재현을 저장소 회귀에 편입했다. 구현 전 1 failure, error·Skip 0, 1.125초, exit 1이다.

JobStorage가 각 Job의 revision과 준비/배치 checkpoint 목록·내용 hash를 함께 소유한다.
기록이 아직 생성되지 않은 경우만 None이며 선언된 기록의 소실·Null·변조·미선언 추가는
거부한다. Reader와 Writer, 실행 claim, 재개 계획, backup/clone이 같은 owner 검증을 쓴다.
재개 계획의 missing/corrupt 진단과 실제 효과 재실행 차단을 함께 유지한다. 원본을 자동
복구하거나 현재 형식이 아닌 기록을 migration하지 않는다.

준비/배치 기록과 목록은 한 저장 transaction으로 확정한다. 완료된 corpus 공개와 Job 완료를
확정한 뒤 별도 transaction에서 준비 자료를 정리한다. 정리 실패는 모든 준비 자료와 목록을
되돌리며 이미 확정된 공개는 유지한다. 정리 예외를 공개 transaction 안에서 삼켜 부분 삭제를
확정하지 않는다. Job 삭제 정책이나 승인·예산·외부 효과 실행 규칙은 바꾸지 않았다.

### 검증과 범위

RAG 저장 owner와 상태/재개/공개 경계가 연결되므로 H2-h/i를 묶어 현재 통합 소스의 전체
LLM/Hub 검증으로 승격했다. 집중 실행은 아래 명령의 7개 모듈이며 JSON에 정확한 발견 목록을
보존했다. 이후 추가 원자성 시험은 test_rag_checkpoint_integrity 전체 모듈로 실행했다.

```bash
python -m tests.llm.run_regression --suite llm --modules \
  tests.llm.test_rag_checkpoint_integrity tests.llm.test_rag_job_integrity \
  tests.llm.test_backend_contracts tests.llm.test_checkpoint_lifecycle \
  tests.llm.test_rag_components tests.llm.test_rag_resilience tests.llm.test_rag_optimization \
  --output /workspace/llm-clean-resume/h2-i/focused-2.json
python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/h2-i/final/llm.json
python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/h2-i/final/hub.json
python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/h2-i/final/architecture.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_docs --output /workspace/llm-clean-resume/h2-i/final/docs.json
```

위 python은 `.venv-linux312/bin/python` 3.12.14이며 TERM=xterm-256color,
LITELLM_LOCAL_MODEL_COST_MAP=True, LITELLM_MODE=PRODUCTION,
HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb,
HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip으로 실행했다. host는 기존
ish d833790959a76ac457fdeea3f21a6bea1eecfe12이며 비root 실제 bwrap/X11 경로를 유지했다.

| 실행 | 실제 결과 |
|---|---|
| 최초 집중 | 132 실행, 117 pass, failure 3/error 14, 38.353초, exit 1 |
| 계획 변수 수정 후 집중 | 132 pass, failure/error/Skip 0, 39.620초, exit 0 |
| 추가 owner/정리 장애 검증 | 6 pass, 3.038초, exit 0 |
| 프로세스 중단 최초 검증 | 7 실행, 6 pass/error 7(한 부모 시험), 9.683초, exit 1 |
| 재열기 API 수정 후 | 7 pass, failure/error/Skip 0, 9.710초, exit 0 |
| 전체 LLM | 1,846 discovered/started/pass, 580.481초, exit 0 |
| 전체 Hub | 540 discovered/started/pass, 690.030초, exit 0 |
| 정적 감사 | new/stale/S1 0, 후보 552개, exit 0 |
| R3 mutation | 실제 두 번째 파일 효과 assertion으로 한도 누락 탐지, detector exit 0 |
| 문서 검사 | 문서 131개, Python fence 178개, 상대 링크 949개, anchor 34개, 오류 0, exit 0 |

최초 집중 실패는 계획 경로의 미정의 declarations 변수였고, 프로세스 중단 시험의 오류는
재열기에 존재하지 않는 aget을 사용한 fixture였다. 정규 aload로 고쳤으며 원래 실패 재현과
실제 효과 횟수 assertion은 유지했다. 최종 새 시험은 저장·목록·commit 및 정리의 실제
exit 77 7개 경계에 도달했음을 witness로 확인하고 재열기에서 원본 또는 완전한 새 상태,
확정된 corpus, 추가 효과 없음까지 검증한다. 각 실행의 로그·JSON·실패 증거는
`/workspace/llm-clean-resume/h2-i/`에 보존한다.

전체 두 suite 각각 발견=실행=pass이며 failure/error/Skip/expected failure/unexpected success/
missing/unexpected/duplicate는 모두 0이다. 서로 다른 집중 실행을 전체 실행으로 합산하지
않았다. 전체 전후 Python 662개 manifest는
83d861a6dc92a68417052a84dd8bd9ee8f02386c121af056302e6055e5d31c74,
제품·시험·Fixture/자산 821개와 의존성 136개/host/실행 환경을 포함한 manifest는
d819e8440b2303efb4e348c6e6c5fc429b915716fee9088641d0345c00e487d0로 각각 일치한다.
H2-h와 host/status/Python/kernel/packages/환경/실행 바이너리 동일성도 확인했다.

통과 직후 문서를 먼저 갱신하고 H2-h/i를 묶어 작업 브랜치에 Push한다. GitHub Actions의
필수 전체 검증은 유지한다. 현재 API의 Forbidden으로 정확한 SHA의 원격 CI 결과를 확인할 수
없으므로 원격 CI 통과로 보고하지 않는다. 실제 인증 모델·전원 단절·network filesystem은
이 검증의 보장 범위가 아니다.

별도 Memory summary-loss probe는 저장된 요약 소실을 빈 상태로 읽고 generation/revision을
새로 발급하는 두 실패를 재현했다(2 failure, error·Skip 0, 0.225초, exit 1).
이 시험은 위 전체 suite에 아직 편입되지 않았고 실패가 해결된 것으로 보고하지 않는다.
다음 H2 보강에서 우선 수정한다. Job 상태별 부가 증거·Memory와 나머지 owner 계약,
H2 전체·H3–H5·P1 잔여 작업·A–F는 미완료다. 이전 38개 데이터군 원감사와 과거 실행은 보존한다.

## H2-h RAG Job 존재 목록과 worker lease — Level 2

H2-g/정책 묶음 Push `1b6f824c5684874cd89b298dee813aa3c52afb79`에서 이어서 진행했다.
Job 원본 소실을 빈 큐로 처리하고, 실행 중 worker.lock 소실 후 두 번째 embedding 호출과
실제 파일 효과를 허용하는 두 재현을 회귀에 편입했다. 수정 전 2 failure, error·Skip 0,
1.071초, exit 1이다. 모델은 시험용 adapter이며 실제 인증 모델 호출은 아니다.

JobStorage가 현재 버전·Project/Component 소유권·정확한 Job ID/리비전과 필수 lease를
소유한다. 새 RAG 생성 때만 빈 목록을 만들고 Job 생성 시 입력·identity가 있는 lease·Job·
목록을 함께 확정한다. 조회·재시도·정리는 lease를 재생성하지 않는다. Job 갱신·명시 삭제·
maintenance 삭제도 원본과 목록을 함께 확정하며 초기 생성 증거는 변경하지 못한다.
RAGComponent는 재선택·backup·clone에서 owner 검증에 위임한다. 비활성 owner도 적용한다.

Job 메타데이터 검증과 재개 입력 증거를 구분한다. 입력이 소실돼도 유효한 Job의 재개 계획은
기존 계약대로 missing/재개 불가 진단을 제공한다. 실행 전 입력 해시와 backup/clone의 필수
입력 검증을 유지하며 소실된 입력을 기본값으로 복원하지 않는다. 안전성이 확인된 취소/포기
Job의 기존 명시 삭제 계약도 유지한다. 보존 대상을 고르는 정책은 이번에 변경하지 않았다.

### 검증과 범위

변경은 RAG Job 저장 owner, RAGJobs 상태 writer·lease, Component의 직접 소비자에 한정된다.
실제 RAGComponent/Job 호출 소비자와 공통 안전성 집합의 43개 모듈을 선택했다. Repository,
Component, Engine/RAG 연동, Application·Hub Job 관리, backup·clone·삭제·quota·Checkpoint,
실제 파일 효과·원본 보존·장애 주입을 포함한다. 모듈 선택 근거·정확한 argv는
`/workspace/llm-clean-resume/h2-h/modules.json`과 `level2-final-command.json`에 보존한다.
실행기는 `python -m tests.llm.run_regression --suite all --modules <기록한 43개 모듈>
--output /workspace/llm-clean-resume/h2-h/level2-final.json`이다. all은 discovery 범위이며
전체 실행이 아니다. 실행 환경 변수와 Python/host/lock은 H2-g와 같다.

| 실행 | 실제 결과 |
|---|---|
| 최초 직접 소비자 | 55 pass, 14.304초, exit 0 |
| 손상·원자성 보강 | 9 pass, 4.978초, exit 0; 생성·취소·삭제의 실제 exit 77 10개 지점 포함 |
| 최초 Level 2 | 591 발견/실행, 584 pass, failure 3/error 4/Skip 0, 257.497초, exit 1 |
| 실패 소비자 수정 후 집중 | 106 pass, failure/error/Skip 0, 32.791초, exit 0 |
| 최종 Level 2 | 592 discovered/started/pass, failure/error/Skip 0, 251.499초, exit 0 |
| 정적 감사 | new/stale/S1 0, 후보 552개, exit 0 |
| R3 mutation | 실제 두 번째 파일 효과로 한도 누락 탐지, detector exit 0 |
| 문서 검사 | 131개 문서, Python fence 178개, 상대 링크 948개, anchor 34개, 오류 0, exit 0 |

최초 7개 실패는 입력 소실의 재개 진단/삭제 계획을 너무 일찍 차단한 구현, raw Job을 만들던
fixture 세 곳, 이동한 저장 owner에 도달하지 못한 장애 주입 세 곳이었다. 기존 안전성 assertion을
유지하고 정상 writer로 fixture를 생성하며 주입 대상을 현재 owner로 옮겼다. get의 inline 상태
비교를 JobStorage.STATUSES로 옮긴 S3 한 항목만 부채 목록에서 제거했다. 부채 전체 해결은 아니다.
최종 실행은 전체 discovery 2,379개 중 592개를 선택했다. expected failure/unexpected success/
missing/unexpected/duplicate는 0이다. 체크 명령은 check_architecture --require S1,
check_budget_mutation이며 JSON·로그와 개별 exit를 함께 보존했다.

최초 Level 2 전후 Python 661개 manifest는
72b3941465714c3d10952d72fafa7ae722688cae40db4f73fff7676120b21945,
제품·시험·Fixture/자산 820개·설치 의존성 136개·host/환경을 포함한 manifest는
052f93ce6c99b286c0e8e870166cf0ab77cc4602beb2779ce17afe3a7af44c8b로 각각 일치한다.
최종 실행 전후 Python manifest는 b7e2de3d27684c77627aacc7c091462ec6cb1548a4eaf99d5a1f4c67b43bf4b3,
전체 검증 입력 manifest는 e7731e2270082686144a11315ef195d06754ceb52e3a35fc094c397494d1f415로
각각 일치한다. 서로 다른 실행을 전체 회귀 한 번으로 합산하지 않는다.

이 소단계는 **전체 LLM/Hub 회귀를 수행하지 않았다**. 영향이 RAG owner와 확인한 소비자로
제한됨을 근거로 Level 2 로컬 검증만 완료했다. 문서를 먼저 갱신하고 독립 커밋으로 보존하며,
다음 준비/배치 영수증 보강과 묶은 Level 3 전체 게이트가 통과한 뒤 Push한다. 원격 CI 완료가 아니다.

별도 rag-prepared-loss-probe.py는 공개 실패 후 이미 저장된 prepared.json과 batches가 함께
소실되면 재시도에서 시험용 embedding/실제 파일 효과가 반복됨을 재현했다(1 failure,
error·Skip 0, 1.221초, exit 1). 이 실패는 위 592개에 포함되지 않으며 해결된 것으로 보고하지
않는다. 다음 H2 보강에서 우선 해결한다. Job 상태별 부가 증거·Memory와 나머지 owner,
H2 전체·H3–H5·P1 잔여 작업·A–F는 미완료다.

## H2-g Python Tool 패키지 존재와 worker 검증

H2-f `0a2f3a0`에서 이어서 Tool 패키지 소실의 세 실패를 저장소 회귀에 편입했다.
구현 전 3 failure, error·Skip 0, 0.930초, exit 1이다. 빈 requirements.txt가 사라진 뒤
이미 해석한 worker가 실제 파일에 두 번째 효과를 쓴 재현을 포함한다. 이후 검증 정책을
`a2f9c01`로 독립 커밋했으며 현재 소단계는 [Level 3](verification-policy.md)으로 검증했다.
Tool 저장 형식·worker 입력·Hub 공개 소비자에 걸친 통합 변경이 승격 근거다.

### 변경과 영향

- ToolPaths가 자신의 필수 packages.manifest.json을 소유한다. 새 저장소 생성 때만 빈 목록을
  확정하고 현재 버전·Project/Component·정확한 패키지/파일 집합·원본 SHA-256을 검증한다.
  requirements의 선언된 부재와 명시적 빈 파일을 구별하며 소실을 의존성 없음으로 복원하지 않는다.
- 생성·수정·삭제는 소스·requirements·목록을 기존 transaction으로 함께 확정한다. 원본 소실,
  임의 추가·변경, 잘못된 현재 필드, symlink·FIFO를 보존·거부한다. 의미 검증도 유지한다.
- inspector/execution worker는 부모가 전달한 Project 소유권과 패키지 목록을 import 전에
  검증한다. 기존 source/requirements·descriptor binding, 승인·원장·격리 경계는 유지한다.
- Hub의 외부 패키지 가져오기는 owner의 read_source_package를 호출한다. 신규 입력의 정규 파일·
  정적 텍스트를 검증한 뒤 명시적으로 생성한다. Project 내부 저장소 조회는 필수 목록 검증을
  계속 요구한다. 가져오기에서 소스 실행·의존성 설치는 하지 않는다.

관련 소비자는 Tool CRUD/prepare/resolve, 실제 worker, 비활성 owner 재선택·backup·clone·제거,
Hub Tool 관리다. 15개 회귀에는 manifest 저장 실패와 실제 자식 프로세스 exit 77의 11개 조합
(생성/수정의 source·requirements·manifest·commit 뒤, 삭제의 rename·manifest·commit 뒤),
원본 복구·명시적 빈 의존성 보존·두 번째 실제 효과 차단·외부 가져오기 특수 파일 거부가 포함된다.

### 실행 결과

첫 집중 49개는 45 pass/4 failure(51.888초), 두 번째 83개는 82 pass/1 error(56.410초)였다.
기존 두 테스트의 manifest 경로 기대값과 손상 후 원본 확인 경로를 보강했다. 원래 assertion은
유지했으며 세 번째 75개는 모두 통과했다(10.606초). 최초 전체 LLM은 1,821 pass(563.582초,
exit 0), Hub는 539 pass/1 error(687.997초, exit 1)였다. Hub 외부 가져오기가 내부 ToolPaths의
필수 Project 인자를 누락한 실제 소비자 오류였다. 별도 입력 경계로 수정하고 73개 집중/통합
시험을 34.775초에 통과했다. 이후 현재 통합 소스의 전체 LLM/Hub를 다시 실행했다.

| 검사 | 실제 결과 |
|---|---|
| 최종 전체 LLM | 1,828 discovered/started/pass, failure·error·Skip 0, 561.588초, exit 0 |
| 최종 전체 Hub | 540 discovered/started/pass, failure·error·Skip 0, 689.757초, exit 0 |
| 정적 감사 | new/stale/S1 0, 남은 후보 553개, exit 0 |
| R3 mutation | 실제 두 번째 파일 효과 assertion으로 한도 누락 탐지, detector exit 0 |
| 실제 환경 | LLM/Hub 각각 고정 ish SHA·비 root bwrap·X11 도구, 오류 0, exit 0 |
| 문서 검사 | 131개 문서, Python fence 178개, 상대 링크 945개, anchor 34개, 오류 0, exit 0 |

두 최종 실행 모두 expected failure/unexpected success/missing/unexpected/duplicate 0이다.
새 실행기 시험 5개도 포함하며 --modules 없는 CI와 같은 전체 실행 경로를 사용했다.
명령은 아래와 같고 환경 변수·Python 3.12.14·고정 host/lock은 앞선 정책 검증 기록과 같다.

```sh
.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/h2-g/final/llm.json
.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/h2-g/final/hub.json
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/h2-g/final/architecture-final.json
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_environment --suite hub --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/h2-g/final/environment-hub.json
```

최초 전체 실행 전후 Python 659개 manifest는
44cdbc4090f21e65abe2f502fee4505a0b759ef25a84b5428bb69ac11eee6169,
최종 전체 실행 전후는 7286883be0d3ca201f1220c784f60a1f15968d59ce628a001467b3dcf2f1055b로
각각 같다. 제품·시험·예제 소스를 실행 중 변경하지 않았다. 최초 실행과 최종 실행의 결과는
합산하지 않는다. 로그·JSON·manifest는 /workspace/llm-clean-resume/h2-g/final/에 보존한다.
통과 뒤 문서를 먼저 갱신하고 문서 검사 후 정책 커밋과 묶어 Push한다. GitHub API Forbidden으로
원격 CI 성공은 아직 확인하지 못했다. 누적 I/O·대용량 성능·실제 인증 모델은 미검증이다.

### 다음 H2 차단 항목

별도 rag-job-loss-probe.py는 Job 원본 소실을 빈 큐로 처리하는 경로와, 실행 중 worker.lock
소실 후 시험용 embedding 호출/실제 파일 효과가 두 번 발생하는 경로를 재현했다.
2 failure, error·Skip 0, 1.300초, exit 1이다. 현재 전체 suite에 아직 편입하지 않은 별도
실패이며 H2-g 성공으로 해결됐다고 보고하지 않는다. 다음 H2 보강에서 우선 수정·검증한다.
Memory 부가 자료와 나머지 owner도 남아 있다. H2 전체·H3–H5·P1 잔여 작업·A–F는 미완료다.

## H2-f 개별 Component 원본의 존재·리비전

H2-e 커밋 `2878acbb737ee7a6cc7acde1748bc194ac020904`에서 이어서 작업했다.
일반 Component 기록 소실을 빈 목록으로 읽기, 사라진 Memory 기록을 같은 ID의 새
generation으로 생성하는 두 재현을 저장소 회귀에 편입했다. 구현 전 2 failure,
error·Skip 0, 0.144초, exit 1이었다.

### 구현 경계

- 공통 JSON 기록 owner는 생성 시 필수 records.manifest.json을 확정한다. Component가
  현재 버전·Project/Component 소유권·정확한 ID 집합·원본 바이트 SHA-256을 검증한다.
  빈 목록은 명시된 빈 manifest 또는 한 번도 생성하지 않은 저장소에서만 반환한다.
  기록·목록·records 디렉터리 소실, 미선언 기록, 내용 변경은 보존·거부한다.
- 생성·수정·명시 삭제는 기록과 목록을 기존 workspace transaction으로 함께 확정한다.
  존재 검증과 의미 검증은 별개다. 조회와 목록은 각 owner의 validate_record를 유지하며,
  Memory의 손상 이력과 Vision 버전은 목록 해시가 일치하는 자료에서도 별도로 거부한다.
- Memory는 목록과 원본 해시를 검증한 뒤 캐시를 사용한다. 소실을 빈 검색 결과로 숨기거나
  새 세대를 발급하지 않는다. Vision의 삭제 표식도 목록에 남으며 소실 후 ID 재사용을 막는다.
- FileComponent는 공통 records 경로 대신 자신의 패키지 정의 파일 집합을 제공한다.
  Skill/Data 전체 패키지 소실도 검출하며 기존 첨부 파일 검증과 CAS를 유지한다.
  Python Tool 패키지와 외부 저장 owner의 형식을 이 공통 JSON reader가 해석하지 않는다.
- Component 영구 제거는 아직 유효한 Project 저장소 이력·설정으로 owner 검증을 수행한 뒤
  디렉터리와 이력을 같은 transaction에서 제거한다. 현재 자료가 손상되면 원위치에 보존한다.
  모든 상위 도메인의 삭제·참조 보호가 완료됐다는 뜻은 아니다.

### 집중 검증과 수정

첫 72개 실행은 잘못 지정한 test_skills 모듈의 import error 1개와 Memory 이력 오류
메시지 기대값 failure 1개였다(13.731초). 실제 test_skill_packages로 바로잡고, Memory 시험은
원본 해시 거부와 해시가 일치하는 손상 이력 거부를 각각 검증하도록 보강했다.
이후 95 pass(18.200초)를 확인했다. 추가 176개는 175 pass·error 1개(72.287초)였다.
영속 정의가 없는 RuntimeTools fixture가 공통 파일 목록을 상속한 문제였으며 해당 fixture의
빈 저장 계약을 명시했다. 수정·추가 검증은 90 pass(16.561초), failure·error·Skip 0이다.

첫 전체 LLM의 2 failure는 Memory 세대 불일치/누락 자료가 새 원본 해시 검증에서 먼저
거부되면서 발생한 기존 메시지 기대값 충돌이었다. 테스트를 보강해 해시 불일치 거부와,
해시가 일치해도 세대 필수값·이력 연결을 거부함을 각각 검증한다. 제품 코드는 바꾸지 않았다.
수정 후 62 pass(15.650초), failure·error·Skip 0을 확인하고 전체 LLM을 다시 실행했다.

실제 자식 프로세스 exit 77은 생성·수정·삭제 각각에서 기록 변경 후, manifest 변경 후,
API commit 반환 후의 아홉 조합을 검증한다. 재열기에서 미확정 변경은 원래 기록·목록으로
rollback하고 확정된 변경만 유지한다. manifest 저장 실패도 원본 바이트와 목록을 함께
보존한다. 캐시 이후 소실/변경, 비활성 owner의 재선택·백업·영구 제거, File 패키지 소실,
Vision tombstone 소실과 실패한 clone의 목적지 비공개를 확인했다.

### 전체 회귀 — 2026-10-21

| 검사 | 실제 결과 |
|---|---|
| 최초 전체 LLM | 1,806 pass, failure 2, error·Skip 0, 557.146초, exit 1 |
| 전체 LLM | 1,808 pass, failure·error·Skip 0, 541.819초, exit 0 |
| 전체 Hub | 540 pass, failure·error·Skip 0, 691.145초, exit 0 |
| 정적 감사 | new/stale/S1 0, 미정리 후보 553개, exit 0 |
| R3 mutation | 한도 전달 누락 시 두 번째 실제 파일 효과 검출, 검사 도구 exit 0 |
| 문서 검사 | 130개 문서, Python fence 178개, 상대 링크 932개, anchor 34개, error 0 |

발견/시작 ID의 누락·중복·예상 밖 ID는 0이다. 버전 선언명과 필드명이 다른 첫 정적 감사의
S4 후보 2개는 해당 owner의 SCHEMA_VERSION 선언으로 해소했다. 검사나 debt fixture를
약화하지 않았으며 기존 553개 후보는 승인된 예외가 아니다. 실행 환경·명령은 H2-a/b와 같다.
최초 전체 실행 전후 Python 658개 manifest는
`dbee20127af71882761598b93d52a8b52781a3d8debc53921d26328f4b19357e`로 같다.
Hub 통과 후 변경은 test_record_generations.py의 테스트 자료 하나뿐이다. 제품·Hub 소스와
Hub가 소비하는 자료는 그대로이며 차이를 source-change.json에 기록했다. 전체 Hub는
반복하지 않았다. 최종 LLM 실행 전후 Python 658개 manifest는 `d8b0c5474f22811e03a793f31000bb96d7ccc7818e9f7b25f10f831745d963cd`로 같다.
원시 로그·JSON·소스 manifest는 `/workspace/llm-clean-resume/h2-f/final/`에 보존한다.
원본 해시 검증의 누적 I/O와 대용량 저장소 성능은 별도로 측정하지 않았다.
전체 통과 뒤 문서를 먼저 갱신하고 문서 검사 후 커밋·Push한다.

### H2 잔여 항목

별도 tool-package-loss-probe.py는 Python Tool의 requirements.txt 소실을 의존성 없는 패키지로,
전체 패키지 소실을 빈 목록으로 읽는 경로와 명시적으로 빈 requirements.txt 소실 후 기존
워커가 추가 로컬 파일 효과를 실행하는 경로를 재현했다(3 failure, error·Skip 0, 0.867초,
exit 1). H2-f 성공에 합산하지 않는다. Tool 패키지와 Memory 부가 자료·나머지 owner의
필수 필드·상태·존재 계약, H3–H5·P1 잔여 작업·A–F는 미완료다.

## H2-e Component 저장소 이력·사용량 영수증

H2-d 커밋 `0085b7c47d7498e35b62a6b6f94f69c218c08917`에서 이어서 작업했다.
사용량 파일 소실 후 같은 한도에서 추가 호출, 완료 종료 시각 누락 수락, 선택/비활성
Memory 루트 소실 후 새 identity 발급의 4개 실패를 저장소 회귀에 편입했다.
구현 전 4 failure, error·Skip 0, 0.271초, exit 1이었다.

### 구현 경계

- Project의 필수 initialized_components는 현재 선택과 별도로 기존 저장소 이력을 보존한다.
  선택한 이름은 이 이력에 포함되어야 하며 일반 설정 저장으로 이력을 바꾸지 못한다.
  Registry는 새 owner 초기화와 이력·선택을 같은 transaction으로 확정한다. 비활성화는
  이력을 남기고 안전성 검사를 통과한 명시적 영구 삭제만 이를 제거한다.
- 로컬 Component owner는 선언된 루트 소실을 생성으로 해석하지 않는다. Memory/RAG identity
  생성도 이 경계를 따른다. 선언되지 않은 기존 디렉터리를 임의로 채택하지 않고 보존·거부한다.
  Registry는 외부 owner의 내부 형식을 읽거나 로컬 디렉터리 유무로 사용량 조회를 생략하지 않는다.
- Component 사용량은 생성 때 확정한 usage/manifest.json과 개별 영수증이 원본이다. manifest의
  owner·버전·전체 파일 집합·리비전을 검증하며 파일/manifest/usage 디렉터리 소실을 빈 사용량으로
  읽거나 재초기화하지 않는다. 자료가 한 번도 생성되지 않은 상태만 명시적으로 빈 결과다.
- ModelUsageRecord가 현재 16개 필드·상태·manifest 형식을 소유한다. Project 소유권은 필수이고
  Session/Run은 함께 명시하거나 함께 None이다. 시작/종료 시각, 토큰, 실패 진단과 상태를 검증한다.
  새 영수증은 started 예약이며 현재 예약의 식별자·소유권·시각·예약량을 보존한 terminal 갱신만 허용한다.
- 예약/완료의 영수증·manifest는 각각 기존 workspace transaction으로 확정한다. 외부 모델 효과와
  로컬 저장의 분산 원자성을 주장하지 않는다. 불확실한 started는 재시작 뒤에도 quota를 차지한다.
  비활성·미등록 owner를 집계에서 누락하지 않으며 백업도 보존된 비활성 owner의 scope/validator를 쓴다.
  quota 합산을 생략하는 무제한 호출에서도 저장 owner의 기존 영수증 무결성 검사는 유지한다.

### 집중 검증과 수정

첫 57개에서 기존 디렉터리 자동 채택 기대값 1 error와 RuntimeTools 초기화 자료 1 failure를
확인했다(4.301초). 미선언 원본은 보존·거부하고 정상 초기화 후 재선택의 사용자 파일 보존은
별도로 유지한다. 수정 후 관련 137 pass(24.676초)였다.
추가 114개에서는 Tool 루트가 비어 있다는 옛 기대값 4 failure와 새 Project 필드가 없는
RAG 자료의 13 error를 확인했다(35.285초). 현재 생성 계약으로 자료를 수정한 뒤
107 pass(34.519초), 마지막 무결성 15 pass(1.820초)였다. 성공 묶음의 failure·error·Skip은 0이다.

첫 전체 LLM에서는 외부 owner 시험의 Project 자료에 새 이력 필드가 없는 1 error와 과거 naming
필드 snapshot의 1 failure를 확인했다. 외부 owner는 보존된 이력을 명시하면서 실제 로컬 폴더
없이도 조회됨을 유지했고 naming fixture 원본은 보존한 채 새 관리 필드 하나만 명시적으로 비교에
포함했다. 나머지 필드·설정·Workflow 비교는 유지한다. 수정 후 37 pass(5.924초),
failure·error·Skip 0을 확인하고 전체 LLM을 다시 실행했다.

실제 자식 프로세스 exit 77은 예약 기록 저장 중, 예약 확정 후 효과 직전, 완료 기록 저장 후,
완료 manifest 저장 후의 네 경계를 검증한다. 미확정 예약은 효과 없이 rollback하며 이미 확정된
예약은 started로 유지한다. 실제 파일 효과와 추가 호출 거부를 확인하며 자동 재생하지 않는다.
완료 manifest 저장 실패도 같은 예약·quota를 보존한다. 의미 필드 검증은 manifest 검증과 별도다.

H2-d에서 빠진 성능 예제의 합성 출력 작성도 발견해 현재 OutputJournal 생성/추가 API로 옮겼다.
소규모 CLI 실행은 32 deltas·2 Sessions·2 Graph nodes·1 repeat에서 2 Run 완료와 재열기를
확인했다(exit 0). 이는 기능 smoke이며 대용량 성능 개선이나 실제 인증 모델 검증이 아니다.

### 전체 회귀 — 2026-10-21

| 검사 | 실제 결과 |
|---|---|
| 최초 전체 LLM | 1,795개 중 1,793 pass, failure 1, error 1, Skip 0, 546.443초, exit 1 |
| 전체 LLM | 1,795 pass, failure·error·Skip 0, 533.289초, exit 0 |
| 전체 Hub | 540 pass, failure·error·Skip 0, 685.027초, exit 0 |
| 정적 감사 | new/stale/S1 0, 미정리 후보 553개, exit 0 |
| R3 mutation | 한도 전달 누락 시 두 번째 실제 파일 효과 검출, 검사 도구 exit 0 |
| 문서 검사 | 130개 문서, Python fence 178개, 상대 링크 932개, anchor 34개, error 0 |

발견/시작 ID의 누락·중복·예상 밖 ID는 0이다. 새 S4 후보는 버전/manifest 검증을 선언 owner로
모아 해소했고 감사 검사를 약화하지 않았다. 상태 선언으로 해결한 기존 후보 4개만 제거했다.
나머지 553개는 승인된 예외가 아니다. 실행 환경과 명령은 H2-a/b와 같다.
최초 전체 실행 전후 Python 657개 manifest는
`48a499a7cf1b7d48113a7fa23a7ce7e1ba045ac902028b985202a0a3aa9cc363`로 같다.
Hub 통과 이후 변경은 test_execution_boundaries.py와 test_naming_contract.py 두 자료뿐이며,
제품·Hub 소스와 Hub가 소비하는 자료는 같다. 전체 Hub는 반복하지 않았고 차이를
source-change.json에 기록했다. 최종 LLM 실행 전후 Python 657개 manifest는
`b1b99d9d72d7fabde9c020c01891341c4c5009f5e9de75dd4c059bcf6b598b5b`로 같다.
로그·JSON·소스 manifest는 `/workspace/llm-clean-resume/h2-e/final/`에 보존한다.
전체 통과 뒤 문서를 먼저 갱신하고 문서 검사 후 커밋·Push한다.

### H2 잔여 항목

별도 component-record-loss-probe.py는 2 failure, error·Skip 0, 0.143초, exit 1이다.
일반 Component 개별 기록 소실을 빈 목록으로 읽고, 사라진 Memory 기록을 같은 ID의 새
generation으로 다시 만드는 경로다. 이 두 실패는 위 H2-f에서 보강·검증했다. 나머지 owner의
존재·필드·상태 계약과 H3–H5·P1 잔여 작업·A–F는 아직 완료하지 않았다.

## H2-d 출력 원본·필수 manifest·현재 출력 필드

H2-c와 EOF 공백 정리 커밋 `25fd7c6868f0f09715eba1c877ad7d4efb063a85`에서 이어서 작업했다.
출력 끝 newline 소실을 빈 출력으로 읽기, 손상 끝줄 뒤 append, 저장된 final 누락을 True로
복원하던 3개 실패를 `tests.llm.test_output_integrity`에 편입했다. 구현 전 별도 probe는
3 failure, error·Skip 0, 0.002초, exit 1이었다.

### 구현 경계

- EngineDelta/EngineOutput Reader는 모든 현재 직렬화 필드를 요구한다. 생성 초기값을
  조회에서 적용하지 않으며 명시한 빈 텍스트·None·False는 각 필드 계약대로 보존한다.
- 새 Run 생성 때만 빈 outputs.jsonl과 outputs.manifest.json을 함께 확정한다. 출력 owner의
  필수 manifest는 정확한 버전·바이트 수·최종 순번·원본 SHA-256을 보유한다. 기존 파일·manifest
  소실, 완전한 마지막 기록 소실, 끝 newline 소실, 변조는 조회·append·재열기·백업에서 거부한다.
  원본을 자르거나 빈 출력으로 재초기화하지 않는다.
- 출력 추가·manifest·Conversation 갱신은 기존 workspace transaction에 함께 참여한다.
  atomic_bytes는 기존 atomic_json의 원자 파일 교체를 재사용한다. 입증된 미확정 append의
  transaction undo는 유지하며 도메인 상태를 저장 인프라에서 추측하지 않는다.
- outputs.index.json은 파생 캐시다. 검증된 원본에서만 사용·재생성하고 캐시 필드·순번·앵커·
  prefix 전체 해시를 확인한다. 읽기 전 원본 전체 바이트를 해시하므로 커서의 JSON 디코딩 절감과
  별개로 I/O가 추가된다. 대용량 누적 출력의 운영 비용은 별도 측정하지 않았다.

### 집중 검증과 수정

첫 관련 52개에서 과거의 끝줄 무시 기대값 1 error(9.531초)를 확인해 원본 보존·거부 계약으로
수정했다. 이후 82 pass(13.498초), 추가 인덱스 검증 48 pass(6.383초)였다.
새 13개 회귀는 필드/manifest 소실·타입, 완전한 tail 소실, 손상 prefix와 캐시, 새 Run 재열기와
실제 자식 프로세스 exit 77을 포함한다. 두 번째 출력의 원본 쓰기 직후와 manifest 저장 직후
종료 모두 재시작에서 마지막 확정 출력·대화만 유지하고 실행을 자동 재생하지 않는다.

첫 전체 LLM의 실패는 recovery_probe의 ENOSPC 주입이 새 빈 출력 파일 초기화에 먼저 걸린
경우다. 첫 실제 델타 쓰기에 주입하도록 예제를 수정했다. 실제 프로세스·원본 보존 assertion은
유지했고 관련 52개가 19.188초에 통과한 뒤 전체 LLM을 다시 실행했다.

### 전체 회귀 — 2026-10-21

| 검사 | 실제 결과 |
|---|---|
| 최초 전체 LLM | 1,779개 중 1,778 pass, failure 1, error·Skip 0, 542.199초, exit 1 |
| 최종 전체 LLM | 1,779 pass, failure·error·Skip 0, 535.788초, exit 0 |
| 전체 Hub | 540 pass, failure·error·Skip 0, 683.761초, exit 0 |
| 정적 감사 | new/stale/S1 0, 미정리 후보 557개, exit 0 |
| R3 mutation | 한도 전달 누락 시 두 번째 실제 파일 효과 검출, 검사 도구 exit 0 |
| 문서 검사 | 130개 문서, Python fence 178개, 상대 링크 931개, anchor 34개, 오류 0 |

발견/시작 ID는 각각 1,779/540개이며 누락·중복·예상 밖 ID는 0이다. owner의 버전 선언으로
해소한 정적 후보 3개만 제거했다. 남은 557개는 승인된 예외가 아니다. Hub 이후 변경된 Python은
examples/llm/recovery_probe.py 하나이며 제품·Hub 소스는 같다. Hub가 해당 예제를 소비하지
않음을 확인했고 전체 Hub는 반복하지 않았다. 차이는 source-change.json에 기록했다.

실행 환경과 명령은 H2-a/b와 같다. 최초 전체 실행 전후 Python 655개 manifest는
`aea803d073d6ed32fb0db5f610c31f2523d6c91af37600696bfcafe6eca2450b`로 같다.
최종 LLM 실행 전후 같은 655개 manifest는
`180602e75a48570ea4b119c39d1b2f6c6ffd3a84851a3e937a0cdbd6776ee94a`로 같다.
로그·JSON·전후 manifest는 `/workspace/llm-clean-resume/h2-d/final/`에 보존한다.
전체 검증 성공 후 아래 계약 문서를 먼저 갱신하고 문서 검사 뒤 커밋·Push한다.

### H2 잔여 항목

동일한 소스에서 별도 Component probe 두 묶음이 각각 2 failure였다. 사용량 probe는
영수증 파일 소실 뒤 project_max_calls=1에서 두 번째 실제 파일 효과, 완료 종료 시각 누락
수락을 확인했다(0.098초). 저장소 probe는 선택/비활성 Memory 루트 소실 뒤 재선택이 새
identity를 발급함을 확인했다(0.176초). 모두 error·Skip 0, exit 1이며 H2-d 성공에 합산하지
않는다. 원본은 component-usage-loss-probe.py와 component-root-loss-probe.py 및 각 로그다.
이 4개 실패는 위 H2-e에서 보강·검증했다. 나머지 Component/도메인 계약과
H3–H5·P1 잔여 작업·A–F는 미완료다.

## H2-c Checkpoint 개별 기록·manifest·Run 연결

H2-b 커밋 `b2447472f6ba2e561ee91df2467e98c5f04f9a35`에서 이어서 작업했다.
완료된 Graph 노드의 기록 파일 하나를 제거하면 Resume가 실제 효과를 반복하던 실패를
`tests.llm.test_checkpoint_integrity`에 편입했다. 구현 전 저장소 회귀는 1 failure,
error·Skip 0, 1.181초, exit 1이었다.

### 구현 경계

- Checkpoint owner는 현재 manifest의 모든 필드와 객체 타입을 요구한다. `record_revisions`는
  개별 기록 파일의 존재·내용 리비전을 선언한다. Run의 `checkpoints`와 `checkpoint_revisions`는
  같은 이름 집합을 가지며 manifest의 리비전을 검증한다. 명시적으로 초기화한 빈 기록은 유효하다.
- 필수 필드·manifest·디렉터리·개별 파일 소실, 미선언 파일, key/파일명/리비전 불일치는
  원본을 보존하고 조회·Resume·갱신·백업에서 거부한다. 초기화한 이름을 파일 소실 뒤 다시
  초기화하지 않는다. Engine payload의 의미는 해당 Engine에 남기고 저장 owner가 추측하지 않는다.
- 노드 파일·manifest·Run의 리비전과 연결은 기존 transaction에서 ACK 전에 확정한다.
  명시적인 Checkpoint 삭제는 파일과 두 연결 필드의 제거·삭제 표식을 함께 확정한다.
  초기 Resume 사본 생성 실패에 한정한 검증된 원본 참조는 유지한다.
- EngineContext의 Run은 실행 시작 snapshot이므로 Repository 조회는 현재 Run의 저장된
  리비전을 읽는다. 최신 기록을 읽기 위해 EngineContext를 가변 실행 상태로 바꾸지 않는다.
  갱신 시 manifest와 Run도 쓰며 읽기 시 기록 전체의 리비전을 검사한다. 대용량 부하와
  이 추가 I/O의 운영 비용은 별도 측정하지 않았다.

### 집중 검증과 수정

첫 관련 62개 검증에서 기존 오류 기대값 4개가 실패했다. 저장 리비전 검사가 더 이른 경계에서
거부하기 때문이다. Tool/Agent 사용량 시험은 저장 owner를 통해 손상된 의미 payload를 기록해
물리 무결성과 독립적으로 `tool_usage_corrupt`를 계속 확인한다. 파일 변조/소실 시험은 별도로
리비전 오류와 실제 추가 효과 0·원본 보존을 확인한다. 의미 검증을 checksum 성공으로 대체하지 않는다.

수정 후 90 pass(26.168초)였다. 추가 180개 중 1 failure(52.346초)는 handler가 오래된
EngineContext의 Run으로 최신 Checkpoint를 조회하는 실제 경로였다. Repository의 현재 Run
조회로 수정한 뒤 관련 86 pass(20.247초)를 확인했다. 각 성공 묶음의 failure·error·Skip은 0이다.

새 회귀에는 완료 manifest 저장 실패 rollback과 실제 자식 프로세스 exit 77이 포함된다.
완료 파일 저장 직후 및 manifest 저장 직후/Run 저장 전 종료 모두 재시작 후 started 근거를
유지한다. 실제 파일 효과는 1회이고 자동/승인 없는 Resume의 추가 효과는 0이다.
기존 중첩 Engine·승인·추가 지시·계보·삭제·R3 예산 경계도 함께 검증했다.

### 전체 회귀 — 2026-10-21

| 검사 | 실제 결과 |
|---|---|
| 최초 전체 LLM | 1,766개 중 1,765 pass, 실패 test ID 1개(하위 assertion failure 2), error·Skip 0, 530.276초, exit 1 |
| 최종 전체 LLM | 1,766 pass, failure·error·Skip 0, 519.114초, exit 0 |
| 전체 Hub | 540 pass, failure·error·Skip 0, 681.701초, exit 0 |
| 정적 감사 | new/stale/S1 0, 미정리 후보 560개, exit 0 |
| R3 mutation | 한도 전달 누락 시 두 번째 실제 파일 효과 검출, 검사 도구 exit 0 |
| 문서 검사 | 130개 문서, Python fence 178개, 상대 링크 931개, anchor 34개, 오류 0 |

발견/시작 ID는 각각 1,766/540개이며 누락·중복·예상 밖 ID는 0이다.
상태 선언으로 해소한 기존 정적 후보 1개만 제거했고 남은 560개는 승인된 예외가 아니다.
최초 전체 실패는 재시도 증거를 물리적으로 변조한 시험의 공개 경로 오류 기대값 2개였다.
독립적인 Engine 의미 검증과 실제 효과·원본 보존 assertion은 유지하며 공개 경로의 리비전
오류 기대값만 수정했다. 관련 30개가 10.037초에 통과한 뒤 LLM 전체를 다시 실행했다.
제품과 Hub 소스는 Hub 전체 통과 때와 같고 바뀐 Python 파일은 `test_long_running.py` 하나다.
Hub 전체를 반복하지 않았으며 이 차이는 `source-change.json`에 기록했다.

실행 환경과 명령은 H2-a/b와 같다. 최초 전체 실행 전후 Python 654개 manifest는
`bfcc159602e6188bc7e975e6a98d4fb6b6558188f6236c483746a4467668b0b5`로 같았다.
최종 LLM 실행 전후 같은 654개 manifest는
`1b3ad766bfd7114c0417c4ef921ae7bde755792ecbfb8e45b34504f2c41dc774`로 같다.
로그·JSON·전후 manifest는 `/workspace/llm-clean-resume/h2-c/final/`에 보존한다.
전체 검증 성공 후 문서를 먼저 갱신하고 커밋·Push한다.

### H2 잔여 항목

별도 `output-integrity-probe.py`는 3 failure, error·Skip 0, 0.002초, exit 1이다.
출력 JSONL 끝 newline 소실을 빈 출력으로 읽고, 손상 끝줄 뒤에 append하며, 저장된
EngineOutput의 누락된 final을 True로 채우는 경로를 확인했다. 이 실패는 H2-c 전체 suite
성공에 합산하지 않는다. 이 출력 3개 실패는 위 H2-d에서 보강·검증했다. 나머지
Component/도메인의 존재·필드·상태 계약과 H3–H5·P1 잔여 작업·A–F는 아직 완료하지 않았다.

## H2-b 외부 작업 원장의 필수 존재·상태 증거

H2-a 커밋 `ad0eda7f532a7175293207cfc0bc8e85b2981afd`에서 이어서 작업했다.
아래 H2-a 당시의 원장 소실/무효과 증거 소실 2개 실패를 저장소 회귀로 편입했다.

### 구현 경계

- 새 Session 생성 때 `OperationRepository`가 명시적인 빈 `manifest.json`을 만든다.
  기존 Session의 조회·저장·재시작에서 누락된 manifest나 원장 루트를 다시 만들지 않는다.
  manifest는 현재 schema version·Project/Session owner·원장 파일 목록과 리비전을 요구한다.
  선언된 파일 소실·미선언 파일·링크·현재 내용 리비전 불일치를 거부한다.
- `OperationRecord`는 현재 직렬화 필드 전부를 요구한다. started의 종료 시각·증거·결과는
  명시 null이며, completed/not_applied는 종료 시각과 비어 있지 않은 근거가 필수다.
  완료 결과 null도 유효하다. 요청 digest, Tool 계약, 시각·시도 이력·외부 관찰을 검증하며
  증거가 빠진 not_applied를 새 실행 허가로 바꾸지 않는다.
- 원장 파일·manifest·Step receipt는 기존 workspace transaction에서 함께 확정한다.
  Session 백업은 원장 owner의 전체 검증을 호출한다. 보관 판단도 owner의 기록 존재 조회를
  사용하므로 생성 시 빈 manifest가 모든 Session을 불필요하게 고정하지 않는다.
  manifest와 checksum은 삭제 허가나 외부 효과 취소 증거가 아니다.

### 실패 재현과 검증 — 2026-10-21

보강 전 새 원장 회귀 2개는 모두 실패했다. 이후 집중 검증은 56 pass(10.774초),
69 pass(14.686초), 마지막 원장·정적 검사 계약 22 pass(3.392초)였다.
각 묶음의 failure·error·Skip은 0이다. 중복된 집중 시험 수를 독립 전체 시험 수에 더하지 않는다.

`tests.llm.test_operation_integrity`는 실제 파일 효과, 원장/manifest/루트 소실,
필수 필드·상태 증거, 완료 manifest 저장 실패 rollback을 검증한다. 별도 프로세스가 예약
레코드 저장 직후 manifest 저장 전에 exit 77로 종료되면 기존 transaction 복구로 미확정
예약을 되돌린다. 그때 효과는 0이며 재시작 후 첫 정규 실행만 효과 1회다. 완료 기록 저장
실패 후에는 started를 유지하고 같은 작업을 다시 실행하지 않는다.

| 검사 | 실제 결과 |
|---|---|
| 전체 LLM | 1,759 pass, failure·error·Skip 0, 504.286초, exit 0 |
| 전체 Hub | 540 pass, failure·error·Skip 0, 672.254초, exit 0 |
| 정적 감사 | new/stale/S1 0, 미정리 후보 561개, exit 0 |
| R3 mutation | 한도 전달 누락에 따른 두 번째 실제 파일 효과 검출, 검사 도구 exit 0 |
| 문서 검사 | 130개 문서, Python fence 178개, 상대 링크 931개, anchor 34개, 오류 0 |

발견/시작 ID는 각각 1,759/540개이고 누락·중복·예상 밖 ID는 0이다.
상태 선언으로 해소한 기존 정적 후보 16개만 baseline에서 제거했다. 남은 561개는 예외 승인이 아니다.
Python·잠긴 의존성·ish 호스트·실제 격리/X11 실행 환경은 H2-a와 같다.
전체 회귀 동안 제품·테스트·예제 Python 653개의 SHA-256이 동일했다. manifest SHA-256은
`01e057f3f7dd2f6a17fe27e7a0a1407575305346320b402c73e73ed8f30c81af`이며,
원시 로그·JSON·전후 manifest는 `/workspace/llm-clean-resume/h2-b/final/`에 있다.
사용자 지침에 따라 전체 검증 성공 후 문서를 먼저 갱신하고 커밋·Push한다.

### H2-b 당시 다음 차단 항목

별도 `checkpoint-loss-probe.py`에서 완료한 Graph 노드의 개별 기록 파일을 제거한 뒤
재시작·Resume하면 파일 효과가 `first`에서 `first/first/second`로 늘었다. 1 failure,
error·Skip 0, 1.241초, exit 1이며 전체 suite 성공에 합산하지 않는다.
이 Checkpoint 기록 소실은 위 H2-c에서 보강·검증했다. 출력 저널과 나머지 Component의
존재/필드/상태 계약, H3–H5, P1 잔여 작업과 A–F는 아직 완료하지 않았다.

## H2-a 도메인 필수 기록·사용량·Refinement

재개 시 실제 원격 `refactor/llm-clean`은 `89f3a771d4f21630c92f31827fe86868064c8554`였다.
같은 체크아웃에서 이어가며 실제 ish 호스트 소스는
`E1der0wL/ish@d833790959a76ac457fdeea3f21a6bea1eecfe12`를 `ish.platform`으로 연결한다.

### 구현 경계

- Project·Session·Run·Step·Message의 현재 직렬화 필드는 저장 Reader에서 필수다.
  생성 기본값으로 누락 필드를 채우지 않는다. nullable 필드는 명시적인 null을 유지한다.
  Project의 policies/parameters/data 컨테이너도 저장 시 필수다.
- Session·Run·Step 목록은 소유 디렉터리의 필수 레코드 소실을 건너뛰지 않는다.
  Run의 completions는 생성 때 빈 배열로 저장하고 이후 누락을 거부한다.
  Completion은 모든 필드, 시각·토큰·예약의 타입과 범위, 중복 ID를 검사한다.
  quota 투영도 같은 Completion Reader를 사용한다. 기존 Run 디렉터리에 run.json이
  없으면 오래된 메모리 핸들로 저장해 되살리지 않는다.
- Memory identity는 생성 때만 발급하고 조회·재선택·백업에서 누락을 거부한다.
  끝 newline이 없는 Conversation journal은 읽기·append를 막고 바이트를 보존한다.
  기존 트랜잭션이 입증하는 append rollback은 유지한다.
- Refinement의 상태는 자체 `lifecycle.py`가 선언한다. 비어 있거나 끊어진 승인 이력,
  마지막 이력과 다른 상태, 적용 상태와 맞지 않는 effect receipt, 누락된 평가 컨테이너,
  fork/binding의 누락된 revision 증거를 거부한다. 대상 변경과 적용 receipt·상태·이력은
  기존 workspace transaction에서 확정하며 proposal을 중간 상태로 저장하지 않는다.

### 실패 재현과 집중 검증

재개 작업 산출물은 `/workspace/llm-clean-resume/`에 보존한다. 새
`tests.llm.test_lifecycle_integrity`는 실제 호출 수·대상 snapshot·원본 바이트를 확인한다.

- 보강 전 Completion 컨테이너·run.json·session.json 소실과 Project policies 소실은
  호출 한도 1에서 두 번째 모델 호출로 이어졌다. 승인 이력을 비운 Refinement도 대상 변경을
  실행했다. Memory identity 재발급, Project 삭제 상태 기본값 복원, JSONL 끝줄 손실도 재현했다.
- 첫 관련 집중 묶음은 154개 중 error 2였다. 하나는 새 회귀에서 소실 뒤 핸들의 동적
  data 조회가 먼저 실패한 시험 구성 오류이며, 다른 하나는 기존 시험의 빈 completions
  배열 인덱싱이다. 손상 전 snapshot 확보와 실제 usage가 관측될 때까지 기다리는 조건으로
  수정했다. 기존 중단·사용량 assertion은 유지한다.
- 수정 후 lifecycle integrity·configuration results·Refinement 37개가 9.830초에 통과했다.
  정적 감사에서 새 후보는 없으며 Refinement 상태 선언 이관으로 해소된 기존 후보 3개만
  baseline에서 제거했다. 남은 후보 577개는 승인된 예외가 아니다.

### 전체 회귀와 소스 일치 — 2026-10-21

| 검사 | 실제 결과 |
|---|---|
| 최초 전체 LLM | 1,752개 중 1,745 pass, 실패한 test ID 7개(하위 assertion failure 8, error 4), Skip 0, 496.818초, exit 1 |
| 최초 전체 Hub | 540 pass, failure·error·Skip 0, 673.990초, exit 0 |
| 실패 관련 집중 재검증 | 100 pass, failure·error·Skip 0, 11.039초, exit 0 |
| 최종 전체 LLM | 1,752 pass, failure·error·Skip 0, 500.472초, exit 0 |
| 최종 전체 Hub | 540 pass, failure·error·Skip 0, 672.442초, exit 0 |
| 정적 감사 | new/stale/S1 0, 미정리 후보 577개, exit 0 |
| R3 mutation | 한도 전달 누락 시 두 번째 실제 파일 효과를 assertion이 탐지, 검사 도구 exit 0 |
| 문서 검사 | 130개 문서, Python fence 178개, 상대 링크 931개, anchor 34개, 오류 0 |

최초 전체 실패는 불명확한 JSONL 끝줄을 무시·수리하던 기대값, 필수 필드 오류 문구/타입,
Refinement의 빈 history fixture였다. 저장 오류에는 누락·미지원 필드 이름을 제공하고,
현재 형식의 fixture와 원본 보존 assertion으로 수정했다. 손상 허용·Skip·실제 효과 검사를
완화하지 않았다. 전체 발견/시작 ID는 각각 1,752/540개이며 누락·중복·예상 밖 ID도 0이다.

Python 3.12.14와 hash 고정된 `tests/llm/dependencies-linux.lock`, 위 ish 호스트 소스를 사용했다.
`.venv-linux312/bin/python -m tests.llm.run_regression --suite llm|hub --output <JSON>`으로
검증하고, 실제 Bubblewrap 검사는 일반 사용자 uid의 escalated execution에서 실행했다.
`TERM=xterm-256color`, `LITELLM_LOCAL_MODEL_COST_MAP=True`, `LITELLM_MODE=PRODUCTION`,
`HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb`,
`HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip`을 사용했다. 실제 ish/X11·MCP·프로세스
격리 시험을 포함하며 인증 모델 endpoint 시험은 아니다.

최종 전체 회귀 동안 제품·테스트·예제 Python 652개의 SHA-256이 일치했다.
정렬된 manifest 파일의 SHA-256은
`73593a5ecf288f77e3c3ae6e517e4d421548b8d782d66b6eacd77c6b04c4a6cd`다.
원시 로그·JSON·전후 manifest는 `/workspace/llm-clean-resume/h2-a/final/`에 있다.

### H2-a 당시 추가로 확인한 실패

H2-a 통과는 H2 전체 완료가 아니었다. 전체 회귀의 소스를 고정한 상태에서 별도 실제 효과
시험으로 다음 두 경로를 확인했다. 위 H2-b에서 보강·검증했으며 원래 실패 기록을 보존한다.

1. 완료된 외부 작업 원장 JSON 하나를 제거하고 재시작하면 동일 business key의 Tool이 다시
   실행된다. 기존 Step receipt가 남아 있어도 원장이 재생성되고 파일에 효과 2회가 기록됐다.
2. `not_applied` 원장의 `evidence`를 제거하면 다음 호출을 허용한다. handler 진입 2회,
   새 파일 효과 1회, 손상 원본 덮어쓰기를 관찰했다.

`/workspace/llm-clean-resume/operation-loss-probe.py`의 별도 시험은 2 failure, error·Skip 0,
0.358초, exit 1이다. 기존 전체 suite 성공에 합쳐 통과로 계산하지 않는다.
원장 두 실패는 H2-b의 집중/전체 검증을 통과했다. Checkpoint 개별 기록·출력 저널·
나머지 Component 존재/상태 계약, H3–H5는 별도로 남아 있다.

## H1 승인 기록

### 구현 범위

- `core/interactions.py`의 Request·Option·Response Reader는 현재 직렬화 필드를 모두
  요구한다. 생성 기본값으로 만료·actor·상태를 복구하지 않으며 명시적인 None·False·0·빈 값은
  필드 계약에 따라 보존한다. 날짜·ID·지문·정책 응답의 policy_id도 검증한다.
- `services/runtime/interactions.py:InteractionRecord`가 승인 저장 형식과 버전을 소유한다.
  요청 공개 시 미응답 상태를 명시적으로 저장하고, 이후 조회는 필수 파일을 생성하지 않는다.
  response/replacement의 명시적인 null과 필드·파일 소실을 구분한다.
- 요청 공개와 checkpoint 저장, 갱신된 새 요청과 이전 요청의 연결은 기존 workspace 잠금과
  저장 트랜잭션에 묶는다. 응답·취소·갱신은 같은 owner Reader를 사용한다.
- 새 Resume Run은 기존 요청의 응답·취소·갱신 증거를 보존한다. 결과가 불확실한 실행의
  재시도 요청은 Run·checkpoint·실행 레코드에 묶이며 새 시도의 효과를 과거 승인으로 허용하지 않는다.
- 보존한 Graph 확인 응답과 명시적 `{}` 입력은 Request의 기존 선택·입력 규칙으로 비교한다.
  일반 Tool approval에는 빈 확인을 허용하지 않고, 거절·입력 변경·bool/number 혼동을 거부한다.
- 백업은 RunRepository를 통해 승인 owner의 전체 제어 기록·갱신 연결 검증을 호출한다.
  공통 백업 인프라에 승인 형식 해석을 추가하지 않는다.

손상된 승인 자료는 원위치에서 보존하고 사용을 차단한다. 이 단계에는 격리·물리적 삭제 API,
보존 기간·자동 삭제 정책·구 형식 Reader·자동 migration이 없다. 초기 Resume의 승인 증거
복사가 끝나기 **전** 프로세스가 종료된 경우의 복구는 별도 미검증이며, 필수 증거가 없을 때
미응답으로 복원하지 않는다. 모든 영속 owner의 무결성 보강 완료를 뜻하지 않는다.

### 실행 기록

Python 3.12.14, 실제 ish 호스트 `d833790959a76ac457fdeea3f21a6bea1eecfe12`,
설치된 Chroma/Kuzu와 실제 Xvfb/xclip을 사용한다. 외부 인증 모델은 호출하지 않는다.
Bubblewrap의 user namespace를 허용하는 실행 환경에서 제품 격리 설정은 유지한다.

| 시점·검사 | 실제 결과 |
|---|---|
| 수정 전 승인 저장 시험 | 8개, 실패 34건(하위 사례 포함), error 2, 1.558초, exit 1 |
| 첫 집중 회귀 | 103개, failure 5·error 1, 21.308초 |
| 두 번째 집중 회귀 | 106개, failure 5·error 0, 23.023초 |
| 승인·checkpoint·운영 저장 집중 회귀 | 132개 통과, 26.404초 |
| 승인 저장·정적 검사 자체 시험 | 27개 통과, 6.295초 |
| 첫 전체 LLM | 1,727개 중 1,725 통과, failure 1·error 1·Skip 0, 352.032초 |
| 첫 전체 Hub | 540개 통과, failure·error·Skip 0, 491.982초 |
| 재개 비교 수정 후 집중 회귀 | 70개 통과, failure·error·Skip 0, 11.500초 |
| 수정 후 전체 LLM | 1,728개 통과, failure·error·Skip 0, 352.880초 |
| 수정 후 전체 Hub | 540개 통과, failure·error·Skip 0, 491.762초 |

첫 전체 LLM의 error는 초기 checkpoint 복사 실패 후 재개할 때 보존된 응답과 `{}` 확인을
원시 JSON으로 비교한 것이었다. Request의 선택·입력 해석으로 수정하고 충돌 거부 시험을 추가했다.
failure는 container 필드 소실 시 필수 승인 제어 기록 검증이 더 일찍 거부하여 오류 문구가
달라진 경우다. 엔진의 원래 무결성 거부를 직접 검증하고 공개 API의 조기 거부도 검증한다.
추가 Run·모델 호출·실제 파일 효과가 없고 손상 원본을 보존한다는 assertion은 유지한다.

`test_interaction_storage`는 모든 직렬화 필드 누락, 제어 파일·필드 소실, 잘못된 타입·버전,
손상 백업, 동시 갱신, 재시작 후 응답·재개, 시도별 재승인과 실제 효과 횟수를 검사한다.
갱신 트랜잭션의 각 fsync 직후 별도 프로세스에서 `os._exit(75)`를 주입하여 복구 후
이전 상태 또는 완전한 새 연결만 남는지 확인한다. 이는 커널 short-write·전원 손실 시험이 아니다.

R3 mutation 검사는 `max_tool_calls=1` 전달을 의도적으로 제거했을 때 두 번째 파일 효과로
실패하는지 확인한다. 예상한 실패 1건을 탐지해 검사 도구가 exit 0으로 끝났다.
정적 검사는 new/stale/S1 후보 0, 전체 미정리 후보 580개다. H1에서 기존 후보 7개만
실제 변경에 맞춰 제거했으며 새 면제는 없다. 현재 형식 버전의 정확한 타입 검사도
dataclass의 `self` 필드에 대해 검증하도록 AST 검사를 보강하고 반례 시험을 추가했다.

```bash
python -m unittest tests.llm.test_interaction_storage tests.llm.test_interaction_decisions \
  tests.llm.test_conversation_turns tests.llm.test_long_running -v
python -m tests.llm.run_regression --suite llm --output /tmp/llm-regression.json
python -m tests.llm.run_regression --suite hub --output /tmp/hub-regression.json
python -m tests.llm.check_architecture --require S1 --output /tmp/architecture.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_docs --output /tmp/docs.json
```

실행 환경은 `TERM=xterm-256color`, `LITELLM_LOCAL_MODEL_COST_MAP=True`,
`LITELLM_MODE=PRODUCTION`이고 `HUB_TEST_XVFB`·`HUB_TEST_XCLIP`은 설치된 실행 파일을
가리킨다. 이번 실행의 Python은 `/workspace/.setup/ish-llm/verified-next/bin/python`이다.
원시 로그·JSON·검사 시점 소스 hash는 `/workspace/llm-clean-p1/h1-approvals/`에 보존한다.
전체 회귀 중 제품·테스트 Python 파일은 변경하지 않는다.
최종 전체 회귀에서 648개 Python 파일의 전후 hash가 일치했다. 실제 ish 통합과
Xvfb/xclip 9개 시험도 실행되었으며 Skip은 없다.
기존 Provider 작업은 `7b1120b`, 승인 보강은 `fdc2718`로 분리하여 원격 브랜치에 Push했다.
[승인 단계 GitHub Actions](https://github.com/E1der0wL/ish-llm/actions/runs/39425735628)의
contracts (llm)·contracts (hub)도 모두 success다.

## H1 RAG 활성 포인터

승인 단계 `fdc2718`을 Push한 뒤 시작했다. 수정 전 실제 문서 등록 후 active.json을
제거하면 조회가 빈 코퍼스를 반환하고 compact가 원래 세대를 삭제했다. identity.json을
제거하면 조회가 새 ID를 만들었다. 영구 회귀 두 개 모두 실패했다(0.705초, exit 1).

RAGComponent가 identity/active/generations 경로·UUID 형식·graph schema 버전을 소유한다.
새 Component의 명시적 초기화에서 필수 identity와 `generation=null` 포인터를 함께 만들며,
기존 자료를 조회·재선택할 때는 필수 기록을 검증한다. nullable 포인터와 실제 세대 존재가
모순되거나 활성 corpus가 사라지면 거부한다. 백업도 같은 snapshot 검증을 사용한다.
bool·float로 저장된 버전을 정수 버전으로 수락하지 않는다.

| 검사 | 실제 결과 |
|---|---|
| 수정 전 실제 포인터·identity 소실 | 2개 모두 failure, error·Skip 0 |
| 최초 집중 RAG·운영 저장 회귀 | 60개 통과, 10.563초 |
| 확대 RAG·운영 저장 회귀 | 124개 통과, failure·error·Skip 0, 21.545초 |
| 첫 전체 LLM/Hub | 실행 세션 중단, 완료 JSON 없음; 통과로 집계하지 않음 |
| 옛 빈 상태 기대값의 독립 재현 | 1개 failure, 0.153초 |
| 기대값 수정 후 RAG·checkpoint | 26개 통과, failure·error·Skip 0, 4.919초 |
| 재실행 전체 LLM | 1,734개 통과, failure·error·Skip 0, 354.185초 |
| 재실행 전체 Hub | 540개 통과, failure·error·Skip 0, 491.192초 |

첫 전체 실행 중 `test_cancel_preserves_finished_chunks_then_explicit_delete`가 옛 형식의
active.json 부재를 기대해 실패했다. 공개 세대가 없다는 의미를 현재의 필수
`{"generation": null}` 포인터·세대 디렉터리 부재·빈 문서 목록으로 확인하도록 변경했다.
완료한 배치 보존·lease·명시 삭제의 기존 assertion은 유지했다.

새 시험은 조회·새 등록·compact·백업·재선택의 거부와 원본 파일 hash 보존, 모델 호출 없음,
정상 빈 저장소, 재선택 시 identity 유지, 초기 포인터 저장 실패의 rollback을 검증한다.
실제 Chroma/Kuzu를 사용하고 임베딩은 테스트 모델이다.

```bash
python -m unittest tests.llm.test_rag_storage tests.llm.test_rag_components \
  tests.llm.test_rag_optimization tests.llm.test_rag_resilience \
  tests.llm.test_rag_extraction_repair tests.llm.test_unified_rag \
  tests.llm.test_markdown_rag tests.llm.test_operational_storage -v
```

전체 회귀·정적 검사 명령과 환경은 승인 단계와 같다. 원시 로그·JSON·소스 hash는
`/workspace/llm-clean-p1/h1-rag/`에 보존한다. Component 루트 전체 소실의 존재 증거,
상위 삭제/참조 보호와 기존 publish 자동 compact의 Application 이관은 이 단계에서
해결했다고 주장하지 않는다.
최종 회귀 동안 649개 Python 파일의 hash는 일치했다. 정적 검사는 new/stale/S1 0,
잔여 후보 580개이고, 문서·링크·Python 예제 구문 검사의 오류도 없다.

## H1 Session 삭제와 Project 예산

RAG 단계 `6a6e33f`를 Push한 뒤 시작했다. 기존 Run Completion 영수증을 그대로 사용하며
사용량 합계 파일이나 중앙 데이터 관리자를 추가하지 않는다.

수정 전 영구 Session 삭제로 Project 호출량이 1→0이 되어 한도 1에서 실제 테스트 모델의
두 번째 호출이 실행됐다. 추가로 현재 Project 정책을 완화하고 과거 Session을 삭제하면
이미 실행 중인 Run의 고정된 한도 2도 우회해 세 번째 호출이 실행됐다.
Session/Run 정리 계획 역시 과거 사용량을 보호하지 못했다. 두 시험 모두 실제 후속 호출까지
관찰한 뒤 실패했다(2개 failure, error·Skip 0, 0.562초).

### 변경 소유자와 범위

| 구현체 | 변경 |
|---|---|
| `runtime/usage.py:period_entries` | Project 기간의 단일 계산. 호출 예약·사용량 조회·삭제 보호가 공유하며 누락/잘못된 기간 증거를 0으로 복원하지 않음 |
| `runtime/usage.py:project_usage_policies/protects_project_usage` | 현재 Project 정책과 활성 Session이 소유한 Run 정책 사본을 확인. 어떤 살아 있는 Project quota라도 필요한 영수증을 보호 |
| `lifecycle/sessions.py:SessionManager.delete` | workspace 잠금 안에서 최신 소유자·상태와 quota 증거를 검사한 뒤 명시적 영구 삭제를 허용/거부 |
| `lifecycle/projects.py:model_usage/remove_component` | 조회 기간과 Component 삭제 보호에 같은 사용량 owner 계약 사용. 미완료 예약 보호 유지 |
| `history/retention.py:HistoryRetention` | Session/Run 계획 모두 같은 quota 보호. 기존 revision 재검증과 트랜잭션을 사용하고 새 자동 삭제 정책은 추가하지 않음 |

### 검증

| 검사 | 실제 결과 |
|---|---|
| 수정 전 Project·활성 Run 한도 우회 | 2개 모두 failure |
| 최초 집중 회귀 | 68개 중 error 1, 10.239초. 새 시험에서 삭제 후 핸들의 data를 재조회한 시험 오류 |
| 시험 경로 캡처 수정 후 집중 회귀 | 68개 통과, failure·error·Skip 0, 10.245초 |
| 전체 LLM | 1,741개 통과, failure·error·Skip 0, 356.123초 |
| 전체 Hub | 540개 통과, failure·error·Skip 0, 493.166초 |

시험은 실제 테스트 모델 호출 수, 현재 정책 완화와 활성 Run의 정책 사본, soft delete 후
지속 집계, 현재 토큰 한도에서 미계수 과거 호출 보호, 명시 기간 밖의 정상 삭제,
기간 증거 손상 시 원본 보존과 정확한 경계 시각을 확인한다.

```bash
python -m unittest tests.llm.test_usage_deletion tests.llm.test_operations_schema \
  tests.llm.test_long_running tests.llm.test_lifecycle_logging -v
```

전체 회귀·정적 검사 명령과 환경은 앞 단계와 같다. 로그·JSON·소스 hash는
`/workspace/llm-clean-p1/h1-usage/`에 보존한다. 원본 Completion 컨테이너나 Run 파일 자체의
소실을 포함한 전체 Reader 무결성, Session 상위/하위 참조·효과 원장 수명주기,
손상 자료의 안전한 격리/삭제와 Backend 보존 정책의 Application 이관은 H2–H5에 남아 있다.
최종 전체 회귀 동안 650개 Python 파일의 hash가 일치했다. 정적 검사는 new/stale/S1 0,
잔여 후보 580개이며 새 면제는 없다. R3 한도 누락 mutation도 두 번째 실제 파일 효과로
탐지해 도구 exit 0이었다. 실제 ish 통합과 Xvfb/xclip 9개 시험을 포함해 Skip은 없다.

## 남은 게이트

H1의 승인·RAG 활성 포인터·Session 삭제의 예산 보호는 각 단계의 집중·전체 회귀 검증을 완료했다.
H2–H5의 owner별 필수 존재·손상 처리·안전한 격리/삭제·Application 정리 정책·참조 보호도
남아 있다. 이 안전성 보강 전 P1의 나머지 계약군과 A–F를 완료로 표시하지 않는다.
실제 인증 모델·커널 short-write·전원 손실·네트워크 파일시스템은 미검증이다.

다음 H2는 `core/models.py`, `services/lifecycle/projects.py`,
`services/runtime/runs.py`, `components/memory/component.py`,
`services/history/conversation.py`와 각 Component Reader의 존재·필수 상태 계약부터 다룬다.
Project 삭제 상태·Completion 컨테이너 누락, Memory identity 소실, 불명확한 JSONL tail의
기본값 복원·자동 절단이 남은 주요 위험이다. 변경 시 생성 기본값과 저장 Reader를 분리하고
실제 원본 손상·재시작 시험을 각각 먼저 만든다. H3–H5에서는 소유자별 격리/삭제 증명,
상위 삭제·참조·백업과 Application 정책 이관을 별도로 검증한다.
