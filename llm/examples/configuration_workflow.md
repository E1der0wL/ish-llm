# 문서 기반 설정 변경 Workflow

`configuration_workflow.py`는 사용자가 지정한 문서와 파일을 대상으로 동작하는 범용 예제다.
질문, 프로그램명, 옵션, 설정 파일 구조를 예제에 포함하지 않는다.
Markdown을 RAG의 BM25·Chroma·Kuzu에 색인하고, Graph의 Agent가 검색해 변경안을 만든다.

```text
사용자 요청 → 파일 스냅샷 → 문서 검색·변경안 작성 → 후보 사본 검증
                                      ↑                │ 실패: 재수정
                                      └────────────────┘
검증 성공 → 사용자 승인 대기 → 승인 → 원본 버전 확인 → 적용
비교 전용 → 보고서 생성 (원본 변경 없음)
```

각 `plan`은 별도 Project·Task를 만든다. 문서 색인, Agent·Workflow 정의, Run·Step,
승인·체크포인트는 기존 공개 API로 저장한다. `approve`는 새 Run으로 명시적 재개한다.
Agent에는 검색 Tool만 제공하고, 적용 Tool은 검증 후 Graph의 마지막 노드에서 호출한다.

## 개인 설정

`configuration_workflow.config.example.json`을 개인 파일로 복사하고 자리 표시자를 교체한다.

| 키 | 의미 |
| --- | --- |
| `source_root` | 수정 대상 파일이 있는 절대 디렉토리 |
| `files` | 수정 가능하며 후보 사본에 포함할 상대 경로/glob 목록. 반드시 직접 지정 |
| `reference_files` | 읽기 전용 참고 자료의 이름 → 절대 파일 경로 |
| `documents` | Markdown 파일 또는 디렉토리 목록. 디렉토리는 하위 `*.md` 포함 |
| `validators` | 사람이 지정한 검증 명령 목록. 모델은 명령을 변경할 수 없음 |
| `max_attempts` | 변경안 작성·검증 시도 상한 |
| `max_source_bytes` | 모델에 전달할 선택 파일·참고 파일의 전체 바이트 상한 |
| `validator_timeout`, `validator_output_bytes` | 검증 명령당 시간·출력 상한 |
| `project_config` | ProjectConfig JSON. 대화 모델과 RAG embedding/extraction의 모델·API 키 등 |

모든 자료는 UTF-8이어야 한다. 기존 파일만 변경하며 생성·삭제·숨김 경로·심볼릭 링크는
이 예제에서 지원하지 않는다. 프로그램별 파일 의존성을 자동 해석하지 않으므로 검증에
필요한 파일을 모두 `files`에 지정한다. 참고 파일은 모델에 전달되지만 후보 사본에는 복사하지 않는다.

실제 요청·문서·파일 내용·API 키는 개인 설정과 실행 workspace에서 다룬다.
질문을 소스에 작성할 필요는 없지만, 입력한 요청과 결과는 기존 Run/대화/보고서에 저장된다.
`--request-file` 또는 표준 입력을 사용하면 요청 본문을 명령행 인자에 넣지 않아도 된다.
이 입력 방식이 실행 기록 저장이나 설정한 모델로의 전송을 끄는 것은 아니다.

## 질문 입력

터미널에서 다음 명령을 실행하면 질문을 직접 입력할 수 있다.

```sh
python -m llm.examples.configuration_workflow --config ~/workflow-config.json plan
```

여러 줄 요청은 개인 UTF-8 파일로 전달한다.

```sh
python -m llm.examples.configuration_workflow --config ~/workflow-config.json \
  plan --request-file ~/request.txt
```

표준 입력도 사용할 수 있다.

```sh
python -m llm.examples.configuration_workflow --config ~/workflow-config.json \
  plan --request-file - < ~/request.txt
```

호출 프로그램은 `--request`에 사용자가 입력한 문자열을 전달할 수도 있다.
비대화형 환경에서는 `--request` 또는 `--request-file`을 명시해야 한다.
빈 요청은 실행하지 않는다. 비교·설명만 필요한 경우 같은 명령에 `--report-only`를 추가한다.
이 모드는 모델이 변경을 제안해도 적용 경로로 들어가지 못하도록 검사한다.

## 검증기 연결

`validators[].argv`에 실제 프로그램의 검사/dry-run을 연결한다.
`YOUR_TRUSTED_VALIDATOR`는 교체해야 하는 자리 표시자다.

```json
"validators": [
  {"name": "configuration-check",
   "argv": ["/absolute/path/to/YOUR_TRUSTED_VALIDATOR", "{candidate}"]}
]
```

명령은 후보 디렉토리에서 실행된다. `{candidate}`는 후보 절대 경로로 치환되며,
셸 문자열 대신 argv를 사용한다. 프로그램은 절대 경로로 지정한다.
검증기는 원본이 아닌 후보 파일 전체와 의존성을 검사하고, 성공은 종료 코드 0,
실패는 다른 코드와 원인을 반환해야 한다. 여러 검증기는 모두 통과해야 한다.
검증기 미설정, 시간 초과, 출력 잘림, 후보 입력의 변조도 적용을 막는다.

검증 프로세스에는 시간·출력 제한과 프로세스 그룹 종료가 적용된다. 작업 디렉토리는
OS 샌드박스가 아니므로 후보만 읽는 신뢰한 검사를 연결한다. 후보 코드를 평가하는 검증기는
호스트의 격리 실행 래퍼를 사용한다. 검증기가 원본이나 외부 시스템을 변경하면 안 된다.

## 검토·승인

검증 후 변경이 있으면 `status=paused`, 보고서 경로, 변경안 해시가 출력된다.
`review.md`에서 전체 diff·변경 이유·문서 근거·검증 출력을 확인한다.
`candidate-*`는 검증 사본이고 `validation.json`에는 마지막 초안·오류가 남는다.

```sh
python -m llm.examples.configuration_workflow --config ~/workflow-config.json \
  approve --report /.../report.json --package-sha256 <출력된해시>

python -m llm.examples.configuration_workflow --config ~/workflow-config.json \
  deny --report /.../report.json --package-sha256 <출력된해시>
```

거절하면 원본을 변경하지 않고 재개 Run을 사용자 거절로 실패 처리한다.
설정이나 원본·참고 파일이 검토 이후 달라졌다면 새 `plan`이 필요하다.
검토 Markdown을 편집해도 적용 내용은 바뀌지 않는다. 승인 대상은 저장된 Tool 인자다.

## ish 등록

```python
from functools import partial

if plugin.get("llm") is not None:
    from llm.examples.configuration_workflow import main as configuration_test
    prompt.set_tool("configuration-test", function=partial(
        configuration_test, "--config", "/home/user/workflow-config.json"))
```

```sh
configuration-test plan --request-file /home/user/request.txt
configuration-test approve --report /.../report.json --package-sha256 <출력된해시>
```

ish worker가 대화형 표준 입력을 제공하지 않으면 파일 입력을 사용한다.

## 적용·복구

원본 버전을 확인한 뒤 공통 트랜잭션으로 여러 파일을 변경한다. 원본 디렉토리에
`.ish.lock`, `.transactions` 관리 경로가 생긴다. 파일 권한은 보존하고 이전 내용은
`before-apply.json`에 보관한다. 외부 편집기·프로그램은 이 잠금에 참여하지 않으므로
적용 중 해당 파일을 병행 수정하거나 실행하지 않는다.

원본 파일 적용과 Run의 Tool 원장은 서로 다른 저장 단위다. 적용 후 원장 저장 전에
프로세스가 종료되면 자동 재적용하지 않는다. 원본·백업·원장 확인이 필요하다.

자동 검사는 합성 자료·고정 모델 응답과 실제 로컬 RAG/Graph를 사용한다.
사용자 프로그램의 형식·의미적 정확성은 연결한 검증기와 실제 환경에서 확인한다.
