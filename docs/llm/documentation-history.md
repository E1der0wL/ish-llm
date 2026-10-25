# 문서 보관 목록 — 2026-10-26

오래된 누적 사용 안내와 검토 기록을 활성 진입점에서 분리했습니다. 원문은 아래 고정 커밋과
`tests/llm/reports/documentation-archive-20261026/`의 로컬 바이트 사본으로 보존합니다.
로컬 manifest는 원문 SHA-256을 기록합니다. 과거 본문의 경로·판정·시험 수를 현재 내용으로 고치지 않았습니다.

| 기존 문서 | 보관된 원문 | 현재 안내 |
|---|---|---|
| `docs/README.md` | [고정 커밋](https://github.com/E1der0wL/ish-llm/blob/b4bd478f7f8ccc7326a25173e9bbae6398b69bd4/docs/README.md) | [문서 안내](README.md) |
| `docs/document-map.md` | [고정 커밋](https://github.com/E1der0wL/ish-llm/blob/b4bd478f7f8ccc7326a25173e9bbae6398b69bd4/docs/document-map.md) | [문서 안내](README.md) |
| `docs/llm/handoff.md` | [고정 커밋](https://github.com/E1der0wL/ish-llm/blob/b4bd478f7f8ccc7326a25173e9bbae6398b69bd4/docs/llm/handoff.md) | [문서 안내](README.md) |
| `docs/llm/documentation-review.md` | [고정 커밋](https://github.com/E1der0wL/ish-llm/blob/b4bd478f7f8ccc7326a25173e9bbae6398b69bd4/docs/llm/documentation-review.md) | [문서 안내](README.md) |
| `docs/llm/production-readiness.md` | [고정 커밋](https://github.com/E1der0wL/ish-llm/blob/b4bd478f7f8ccc7326a25173e9bbae6398b69bd4/docs/llm/production-readiness.md) | [문서 안내](README.md) |
| `docs/llm/architecture-history.md` | [고정 커밋](https://github.com/E1der0wL/ish-llm/blob/b4bd478f7f8ccc7326a25173e9bbae6398b69bd4/docs/llm/architecture-history.md) | [문서 안내](README.md) |
| `docs/llm/backend-api-history.md` | [고정 커밋](https://github.com/E1der0wL/ish-llm/blob/b4bd478f7f8ccc7326a25173e9bbae6398b69bd4/docs/llm/backend-api-history.md) | [문서 안내](README.md) |
| `docs/llm/production-readiness-history.md` | [고정 커밋](https://github.com/E1der0wL/ish-llm/blob/b4bd478f7f8ccc7326a25173e9bbae6398b69bd4/docs/llm/production-readiness-history.md) | [문서 안내](README.md) |
| `docs/llm/code-simplicity-review.md` | [고정 커밋](https://github.com/E1der0wL/ish-llm/blob/b4bd478f7f8ccc7326a25173e9bbae6398b69bd4/docs/llm/code-simplicity-review.md) | [문서 안내](README.md) |

`*-verification.md`와 계약 검토 JSON은 CI·검토 근거로 연결된 자료이므로 유지합니다.
단계별 실패/성공 기록을 현재 실패 목록이나 현재 회귀 결과로 읽지 않습니다.
새 일반 실행 로그·임시 스크립트는 `tests/<plugin>/reports/`에 두며 배포하거나 커밋하지 않습니다.
