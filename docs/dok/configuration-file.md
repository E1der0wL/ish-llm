# 주석 있는 설정 파일

표준 JSON에는 주석이 없다. dok는 JSON 문법과 `null`에 `//`와 `/* ... */` 주석을 더한 [JSONC](https://jsonc.org/)만 설정 파일 형식으로 사용한다. 별도 파서 패키지는 필요하지 않다.

## 처음 시작할 때

현재 옵션은 [설정 구조](options.md)를 따른다. [전체 기본 JSONC](config.example.jsonc)는 실제 Owner에서 생성한 값과 영어 설명을 포함한다. 이전 옵션 경로를 그대로 사용하는 파일은 검증 오류로 거부한다.

설정 파일 없이 플러그인을 처음 설치하면 `<ish home>/dok/config.jsonc`를 생성한다. 기본 ish 홈에서는 `~/ish/dok/config.jsonc`다. 모든 설정값, 중첩 항목, 바로가기·버튼의 각 필드와 영어 설명을 포함한다. 기본 위치는 `null`이며 특정 지역을 자동으로 넣지 않는다. 기본 바로가기 이름은 `Find`, `File`, `Sys`, `Vim`, `Git`, `Web`, `Dock`, `Conf`, `Rld`다. 타이틀 접두 문구나 오류 안내 등 실제 설정값의 언어는 설명 주석과 별개다.

생성 파일 일부는 다음과 같다. 설명과 값은 실제 설정 Owner에서 가져온다.

```json
{
  "widgets": {
    "visualizer": {
      "enabled": true,
      "spacing": {
        "after_rows": 0,
        "basic_after_rows": null
      },
      "style": {
        "colors": [
          "#67e8f9",
          "#c084fc",
          "#fb7185"
        ]
      },
      "width_columns": 38
    }
  }
}
```

이 예시는 일부만 보여준다. 실제 최초 생성 파일에는 날짜·시간·날씨·요일 아트·바로가기·오디오·버튼 등의 모든 현재 값이 들어간다. `location`이 `null`인 경우에는 주석으로 위도·경도·지역 이름을 넣는 객체 형식을 안내한다. 위치를 설정한 파일을 생성하면 해당 하위 필드에도 각각 설명이 붙는다.

## 편집과 파일 선택

- 값 변경 후 `ish_reload`로 적용한다. 재시작과 재로드는 기존 파일을 다시 쓰지 않으므로 직접 추가한 주석과 서식도 보존된다.
- 기본 경로는 항상 `config.jsonc`다. 내용이 잘못되면 오류를 알리며 기존 실행 설정을 유지한다.
- 이전 `config.json`은 자동으로 찾거나 읽지 않는다. 그 파일만 있어도 최초 설치 시 별도의 `config.jsonc`를 생성한다. 이전 파일은 수정하거나 삭제하지 않는다.
- `config_path`를 명시한 경우 그 파일만 사용한다. 확장자와 무관하게 JSONC로 읽고 쓰며, 확장자별 별도 파서는 없다.
- 이전 설정 버전, 필드명 별칭과 자동 변환은 지원하지 않는다. 주석 없는 JSON 문법도 JSONC 문법의 일부이므로 같은 파서가 처리한다.

JSONC에서는 큰따옴표 키·문자열과 `true`/`false`/`null` 등 JSON 문법을 사용한다. `//` 줄 주석과 `/* ... */` 블록 주석을 지원한다. 마지막 항목 뒤 쉼표, 작은따옴표 문자열, 따옴표 없는 키, `#` 주석, 중첩 블록 주석은 허용하지 않는다. 문자열 안의 URL·셸 명령·이스케이프·`#RRGGBB`는 그대로 보존한다. 중복 키도 오류다. 오류 위치는 원래 파일의 줄·열을 기준으로 표시한다.

## 프로그램에서 생성하거나 저장하기

```python
from pathlib import Path
from dok import ConfigStore

# 현재 적용된 모든 값에 Owner 설명을 붙인 새 파일을 만듭니다.
# 이미 파일이 있으면 덮어쓰지 않고 False를 반환합니다.
target = ConfigStore(Path.home() / "ish/dok/config.jsonc")
created = target.create(dock.config)
```

이 API는 현재 설정을 새 JSONC 파일로 기록한다. 다른 경로를 사용한다면 독 편집 바로가기의 `command`도 그 경로에 맞춰 지정한다.

`ConfigStore.save(config)`와 `dock.save()`는 명시적으로 전체 파일을 재생성한다. JSONC에는 Owner 설명을 다시 넣고 모든 현재 값을 기록한다. **직접 추가한 주석과 배치는 이 명시적 저장에서 유지하지 않는다.** 일반적인 파일 편집 후 `ish_reload`는 재생성을 하지 않는다.

`ConfigFiles`가 파일명·기본 경로를, `ConfigStore`가 로드·저장·최초 생성의 원자성을 소유한다. 최초 생성은 완성된 임시 파일을 대상 이름에 배치할 때 기존 파일이 없는 경우에만 성공한다. `setting(..., description=...)`으로 Owner가 선언한 설명을 `Record.documented_fields()`와 schema가 공유한다. `Jsonc`는 공개 문서 정보를 소비하며 필드 이름·기본값·검증 규칙을 별도로 선언하지 않는다.
