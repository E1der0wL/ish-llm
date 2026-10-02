# BuiltinTools — 호스트가 선택해 제공하는 Tool

파일 읽기·편집과 선택적인 셸/프로세스·서비스 어댑터를 ToolRegistry로 묶습니다. Project의 Python Tool 패키지와 별개이며 호스트 코드가 등록하고 수명을 관리합니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | BuiltinTools 공개 import입니다. |
| [catalog.py](catalog.py) | Tool schema와 구현을 조립하고 명시적으로 주입한 서비스 어댑터를 연결합니다. |
| [files.py](files.py) | 지정한 root 범위의 파일 조회·읽기·작성·수정·삭제를 구현합니다. |
| [processes.py](processes.py) | 커널/셸 명령과 프로세스 종료·출력 회수를 구현합니다. |

## 연결 방법

`BuiltinTools(root, ...)`의 `registry`를 호스트가 정의한 Component의 `tools` capability에서 반환합니다. `ToolComponent`에 catalog를 넣거나 Project source로 변환할 필요는 없습니다.

파일 root를 명시하고, 셸 실행은 `allow_commands=True`를 선택한 경우에만 노출합니다. 웹 검색·브라우저·외부 서비스 기능은 `adapters`로 실제 구현을 전달해야 합니다. 이름이 등록되었다고 외부 서비스가 자동 구성되지는 않습니다.

BuiltinTools의 async 수명을 닫아 자식 프로세스를 정리해야 합니다. 파일 root 검사는 셸 전체를 격리하는 sandbox가 아닙니다. 승인·retry·실행 이력은 여전히 공통 ToolExecutor를 통과합니다.

등록 코드와 개별 Tool 목록은 [기본 Tool 안내](../../../../docs/llm/builtin-tools.md), Project Python Tool은 [상위 폴더](../README.md)를 참고하세요.

[상위 안내](../README.md)
