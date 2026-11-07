# 공통 색 테마

독 본체, 하단 바, 일반 팝업과 알림 팝업은 `theme`을 공유한다. 바로가기/구분선의 RGB와 비주얼라이저의 단색·그라데이션은 독립적이다. `accent_1`–`accent_5` 설정은 사용하지 않는다.

`theme.colors`는 기본 8색과 `comment`, 밝은 8색과 `bright_comment` 총 18색의 RGB를 소유한다. 밝은 색은 별도 값이며 Bold와 무관하다. 전체 기본값과 영어 설명은 [생성 설정 폼](config.example.jsonc)에 있다.

`theme.roles`는 의미별로 팔레트 이름을 연결한다. 값은 색상 이름 또는 `null`이고, `null`은 터미널 기본색이다. 다른 역할 참조나 RGB 직접 입력은 받지 않아 순환 참조가 없다. 일반 텍스트와 배경의 기본값은 `null`이다. 색상 지정 없이 터미널 배경/투명도를 유지할 수 있다. 불투명 RGB 배경을 지정하면 독의 빈 행과 좌우 여백도 해당 색으로 채운다.

```jsonc
{
  "theme": {
    "colors": {"red": "#E58C76", "green": "#a3e635", "cyan": "#67e8f9"},
    "roles": {
      "foreground": null,
      "background": null,
      "title": "cyan",
      "button_bg": "black",
      "save": "green",
      "delete": "red",
      "muted": "bright_comment",
      "separator": "bright_black"
    }
  },
  "widgets": {
    "time": {"style": {"color": "foreground"}},
    "progress": {"style": {"color": "muted"}},
    "controls": {"buttons": [
      {"action": "play_pause", "style": {"color": "foreground"}, "key": ["f6"]}
    ]}
  },
  "toolbar": {
    "background": "background",
    "regions": {
      "cpu": {"style": {"color": null, "background": "black"}}
    }
  }
}
```

영역·위젯의 `style.color`, `style.background`, `border_color` 등은 역할 이름 또는 팔레트 이름을 선택한다. 예를 들어 `cpu`는 테마의 CPU 역할을 따르고, `cyan`은 팔레트의 청록을 직접 선택한다. 공통 RGB를 바꿀 때는 `theme.colors`를 수정한다. `toolbar.background`는 하단 바에만 적용할 `#RRGGBB`도 받는다. 런타임 기본값은 기존 `background`이며 새 설정 파일은 `#1D2035`를 사용한다. 이 최초 생성 값은 공통 팔레트·팝업 배경을 바꾸지 않는다. 툴바 영역의 `null`은 해당 필드의 상속 의미를 유지한다: 전경은 개별 요소 색, 배경은 툴바 배경을 상속한다. 터미널 기본색과 상속을 혼동하지 않도록 생성 폼에 각각 설명한다.

| 용도 | 기본 역할 → 색상 |
| --- | --- |
| 본문·날짜·시간·음악 버튼·재생된 트랙 | foreground → 터미널 기본 |
| 제목·선택 일정·날씨 그래프 | title → cyan |
| 제목 바·버튼·메뉴 배경 | button_bg → black |
| 일정 영역 전체 배경 | editor_bg → black |
| 텍스트 입력창 배경 | input_bg → bright_black |
| 포커스된 버튼 글자 | title → cyan (버튼 배경 유지) |
| 저장 / 삭제·최고 기온 | save → green / delete → red |
| 설명·재생바 남은 부분·강수확률 0% | muted → bright_comment |
| 최저 기온 | low_temp → comment |
| 테두리·구분선 | separator → bright_black |
| 경고 / 오류 | warning → yellow / error → red |
| CPU / MEM / NET | cpu → green / memory → cyan / network → purple |
| 토요일 / 일요일·사용자 휴일 | saturday → blue / sunday·holiday → red |
| 드롭다운 일반 글자 | menu_text → white |
| 선택 항목 글자 / 배경 | selected_fg → black / selected_bg → white |
| 스크롤바 | scrollbar → cyan |

음악 버튼의 사용 불가 상태는 설정된 색 위에 dim을 적용한다. 바로가기 색과 비주얼라이저 그라데이션은 테마 전경색 변경의 영향을 받지 않지만, 그 뒤의 배경은 독의 배경을 따른다. 터미널이 컬러 이모지를 그리는 경우 이모지 자체의 색은 폰트/터미널에서 결정한다.

`ThemeColors`와 `ThemeRoles`의 Record 선언에서 기본값·field 이름·enum·schema·JSONC 주석을 생성한다. `ThemeReference`가 참조를 검증하며 `Theme.resolve/paint/span/literal/menu_styles`가 해석과 스타일 생성을 소유한다. 컨트롤러는 불변 Theme를 렌더러에 전달하고, 재로드하면 팝업의 렌더 캐시도 새 테마로 갱신한다. 전역 활성 테마나 호스트 스타일 변경은 없다.

하단 바 배경의 예외는 ThemeBackground가 검증·schema를 제공하고 Theme.resolve_background가 해석한다. ToolbarConfig는 이 선언을 참조하며 렌더러에서 HEX 여부나 색상 규칙을 다시 구현하지 않는다. 영역별 색상 참조 규칙과 공통 18색 팔레트는 유지한다.

이전 위젯별 RGB 설정의 자동 변환/호환 경로는 제공하지 않는다. RGB는 팔레트로 옮기고 해당 필드는 이름 참조로 바꿔야 한다. 개인 WSL 설정은 이번 작업에서 수정하지 않았다.

2026-11-07 사용자가 합계 라벨을 `Total`로 변경하는 조건으로 UI를 확정했다. `tests/dok/reports/theme-preview-2026-11-07-final/`은 당시 시안이고, 기능 연결 후의 실제 캘린더 편집·시간별 날씨 화면은 `tests/dok/reports/connected-popups-2026-11-07/`에 있다. 화면 캡처에는 합성 데이터를 사용한다. 캘린더 저장·알림 및 실제 날씨 조회를 포함한 검증 범위는 [검증 기록](validation.md)에 기록한다.
