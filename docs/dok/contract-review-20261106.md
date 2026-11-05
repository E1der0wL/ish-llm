# dok 계약 검토 — 2026-11-06

검토 기준 커밋은 `4c74b8b629a23fc830f8fb9744d753e3a34acd21`이며, 그 이후의 `dok/`, `docs/dok`, `tests/dok` 변경만 대상으로 했다. 대상에는 MPRIS·마우스 지원과 시스템 수집·toolbar 구현이 포함된다. 아래 두 문제를 수정한 뒤 재검증했으며, 검토 범위에서 커밋을 막는 미해결 문제는 발견하지 못했다.

## 발견 및 수정

1. **날짜 형식·갱신 규칙 중복**: toolbar가 날짜 토큰 해석과 `%S` 유무에 따른 갱신 주기를 따로 구현했다. `ClockFormat.render()`에 선택적 요일 문구를 전달하고 `ClockSchedule.for_formats()`로 주기를 해석하도록 모았다. 기존 시계와 toolbar가 같은 Owner API를 사용한다. 기존 호출 인자는 유지하며 `%%S`·`%%%S`의 구분을 검증했다.
2. **빈 선택 합계 문구의 좁은 화면 처리**: `label`과 `empty_text`가 모두 비어 있어도 합계 항목을 보존하려다 입력 항목을 제거할 수 있었다. 실제로 표시할 문자열이 없는 날짜·합계는 공간과 우선순위를 차지하지 않도록 수정했다. 5칸 화면에서 `Len:0`이 유지되는 회귀 테스트를 추가했다.

## Owner와 수명주기

- 설정 필드·기본값·검증·schema·직렬화·JSONC 영어 주석은 각 `Record` 선언에서 나온다. 이전 설정 변환이나 별칭은 추가하지 않았다. 런타임 빈 바로가기와 최초 생성 예시는 계속 분리한다.
- MPRIS 외부 필드·단위는 어댑터가 해석한다. `PlayerState`는 제어 가능 여부와 seek 범위를, `MediaRuntime`은 선택·접속·명령 수명을, `MediaInput`은 드래그를 소유한다. 명령 직전 소유자·곡·기능을 다시 확인하고 불확실한 명령을 재실행하지 않는다.
- `SystemSnapshot.between()`이 구간 사용률·네트워크 속도·재기준화·메모리 해석을 제공한다. 수집은 UI 밖에서 한 작업만 수행하고 취소된 읽기를 정리한 다음 교체한다. 기록 길이는 설정 한도 내로 제한된다.
- `NumberCalculator`가 숫자 문법·자원 한도·합계·평균·반올림을 소유한다. toolbar는 계산 결과를 표시하고 프롬프트 텍스트·선택·기존 편집 키는 변경하지 않는다.
- toolbar와 마우스는 자신이 등록한 속성·컨트롤·Filter만 복원한다. 후속 플러그인의 변경을 덮어쓰지 않는다. 숨김·비활성화·프롬프트 종료·해제 시 해당 작업을 정리한다. 시스템 수집은 dock 표시와 별개로 toolbar 활성화 조건을 따른다.
- 날씨 클릭 동작, GUI 프롬프트 편집, Windows 전체 시스템 수집은 추가하지 않았다. 마우스 기본값은 `false`다.

## 공개 계약과 검증

기존 공개 내보내기 **39개**, 컨트롤러의 기존 메서드 인자와 property 형태, `Width`·`DockKeys`·`DockSettings`·`DockConfig`의 기존 생성자 인자 순서를 유지했다. 새 필드는 끝에 추가했다. 기본 JSONC는 `Jsonc.dumps(DockConfig.initial())`와 일치하며 문서 설정 예시 **11개**가 Owner와 schema 검증을 통과했다. [API·설정 검사 결과](../../tests/dok/reports/review-20261106/audit.json).

Linux Python 3.12.14에서 `tests/dok/run_linux.py`를 사용해 해시 검증된 Linux 파일시스템 스냅샷의 dok 테스트 **177개가 통과**했다(38.774초). 경고·건너뛴 테스트는 없었다. 실제 임시 D-Bus·Linux 시스템 측정·ish 레이아웃·PTY 검증을 포함한다. [실행 로그](../../tests/dok/reports/dok-3xuiyxqb/dok.txt), [소스 해시](../../tests/dok/reports/dok-3xuiyxqb/snapshot.json).

ish 본체·llm·hub는 수정하지 않았으며 llm/hub 전체 회귀는 실행하지 않았다. 개인 설정과 배포 디렉터리도 변경하지 않았다. 검증 스크립트·실행 보고서는 기존 정책대로 `tests/dok/reports/`에 두고 Git에는 포함하지 않는다.
