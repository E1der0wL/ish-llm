# 현재 문서 검토 — 2026-10-26

이 문서는 아래 기준 커밋의 Tool scope 문서 정리 기록입니다. 이후 Agent 모델 설정 분리는
[모델 소유권 계약](agent-models.md), 실제 최신 실행 결과는 [작업 인계](handoff.md)를 따릅니다.
아래 LLM/Hub 통과 수를 이후 설정 계약의 검증 결과로 승계하지 않습니다.

main `b4bd478f7f8ccc7326a25173e9bbae6398b69bd4`와 이번 Tool scope 보강을 기준으로 문서를 정리했습니다.
이 문서는 문서 변경의 범위를 설명합니다. 모든 제품 문장·예제를 실제 외부 모델로 실행했다는 뜻은 아닙니다.
이전 상세 감사표는 [보관 목록](documentation-history.md)의 고정 커밋에서 확인할 수 있습니다.

## 수정한 불일치

| 이전 안내 | 현재 안내와 근거 |
|---|---|
| 리팩터링 브랜치가 아직 작업 기준이며 A–F가 미착수 | main에 병합된 현재 API를 안내. 단계별 결과는 [전수 점검](contract-closure.md)에 연결 |
| 오래된 LLM 실패/Hub 통과 수를 현재 기준선으로 안내 | [최신 인계](handoff.md)에 이번 전체 LLM 2,510개와 Hub 공개 API 29개를 범위별로 기록 |
| 필수 scope가 없으면 기본 scope로 계속 실행 | `ToolExecutionScope.require`와 새 `test_tool_scope`에 따른 fail-fast 계약 |
| 최종 Completion 검토를 미구현 목표로 안내 | [Completion 처리](completion-processing.md)와 현재 `review_final` 계약 |
| 과거 API/아키텍처 누적 본문을 활성 문서와 함께 탐색 | 고정 커밋과 바이트 사본으로 보관하고 현행 안내·검증 기록·보관 안내를 색인에서 분리 |
| Tool Owner 소단계 이후 작업이 계속 미완료라고 표현 | 소단계 기록은 보존하되 최신 전수 검토와 이번 변경의 범위를 구분 |

## 보존한 계약과 자료

Project 정책 authority, Missing/Null/Value, 구현체별 설정 소유권, 승인·명시적 재개·완료 Tool 재사용,
uncertain effect 보호를 유지합니다. 문서 정리를 위해 코드·Schema·저장 형식을 완화하지 않았습니다.
Hub 제품 코드, 제품 prompt/skill, 개인 스크립트, AGENTS.md, 외부 reference host는 변경하거나 추가하지 않습니다.

CI의 소유권 검토 JSON과 단계별 검증 기록은 계속 유지합니다. 수정된 제품 README의 설명 근거/hash만
검토 후 갱신하며 검토 항목을 일괄 면제하지 않습니다. 과거 보고서 자체를 최신화하지 않습니다.

## 검증 범위

문서 정리 뒤 `check_docs`, S1–S7 `check_architecture`, `check_contract_coverage --require-complete`를
재실행해 통과했습니다. 결과 원본은 `tests/llm/reports/documentation-20261026/`에 보존합니다.
문서 171개·Python 블록 161개·상대 링크 1,212개·앵커 34개에서 오류 0,
S1–S7 미해결 0, 소유권 inventory 639개 파일·15,124개 후보에서 미검토/stale/위반 0입니다.
이 검사는 링크·예제 문법·정적 계약을 검사하며 문서 전체의 의미나 실서버 성공을 증명하지 않습니다.
실행 코드에 대한 전체 LLM 및 Hub 공개 API 결과는 [인계 기록](handoff.md)의 실제 실행을 사용합니다.
문서 수정만으로 동일 코드의 전체 실행 결과를 새 실행이라고 보고하지 않습니다.
