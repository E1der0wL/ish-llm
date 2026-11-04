# 기본 설정

현재 사용자 설정을 dok의 기본값으로 반영했다. **날씨 위치는 제외**하여 `widgets.weather.location` 기본값은 `null`이다. 위치를 지정하기 전에는 위치 설정 안내를 표시하고 날씨 요청을 보내지 않는다. 날짜·시간의 시간대는 `Asia/Seoul`이다.

| 설정 | 기본값 |
| --- | --- |
| 숨김/펼침 | Ctrl+W, 펼침 크기는 공간에 따라 자동 조절 |
| dock 너비 | 고정 80칸 |
| 비주얼라이저 | 길이 38, 청록 `#67e8f9` → 보라 `#c084fc` → 분홍 `#fb7185` |
| 재생바 | 길이 48 |
| 음악 버튼 | Nerd Font 이전 / 재생·정지 / 다음, `#6b7280` |
| 요일 아트 | `future` |
| 바로가기 가로 간격 | 2칸 |
| 날씨와 요일 아트 사이 | 1줄 |

바로가기와 음악 버튼의 글꼴은 Nerd Font를 사용한다. 문자나 색은 기존 설정에서 다시 변경할 수 있다. 등록된 프로그램은 자동 설치하지 않는다.

| 슬롯 | 이름 | 명령 | 단축키 |
| --- | --- | --- | --- |
| 1 | Find | `fzf` | Ctrl+1 |
| 2 | File | `yazi` | Ctrl+2 |
| 3 | Sys | `htop` | Ctrl+3 |
| 4 | Vim | `vim` | Ctrl+4 |
| 5 | Git | `lazygit` | Ctrl+5 |
| 6 | Web | `firefox &` | Ctrl+6 |
| 7 | 구분선 | `│` | 없음 |
| 8 | Dock | `vim ~/ish/dok/config.jsonc` | Ctrl+7 |
| 9 | Conf | `vim ~/ish/.ishrc.py` | Ctrl+8 |
| 10 | Rld | `ish_reload` | Ctrl+9 |

명령은 기존 규칙대로 프롬프트 버퍼 전체와 결합해 실행한다. 경로는 현재 사용자의 `~/ish` 설정을 기준으로 한다. 다른 ish 홈을 사용하면 두 편집 명령의 경로를 직접 바꾼다. Ctrl+숫자는 터미널에서 해당 조합의 키 입력을 보내도록 설정되어 있어야 한다.

설정 파일이 없거나 `{}`이면 전체 기본값을 사용한다. 최초 설치에서는 모든 값과 설명 주석을 담은 `config.jsonc`를 생성한다. 개별 필드를 생략하면 그 필드의 기본값을 사용하고, 명시한 설정은 그대로 우선한다. `widgets.shortcuts.items` 목록을 지정하면 기본 목록을 대체하며, `widgets.shortcuts.items: []`는 모든 슬롯을 비운다. 기존 사용자 설정 파일을 자동으로 덮어쓰거나 위치를 채우지 않는다. 다른 설정 경로를 사용하면 독 편집 바로가기의 경로도 그 파일에 맞춰 지정한다.

기본값은 해당 Owner의 선언에만 저장한다. `ShortcutsConfig`는 기본 바로가기 목록을, `VisualizerConfig`는 길이를, `VisualizerStyle`은 색과 그라데이션을, `ShortcutSpacing`은 항목 간격을, `WeekdayConfig`는 폰트를, `WeatherConfig`는 위치 기본값을 소유한다. 버튼 기호는 `PlaybackAction.symbol`을 `TransportConfig`가 참조한다. schema와 설정 파일이 없을 때의 로드도 같은 선언을 사용한다.

코드 적용은 `dok/`을 설치된 플러그인 폴더에 복사한 뒤 ish를 재시작한다. 이후 JSONC 설정 변경은 `ish_reload`로 반영한다. 이전 버전에서 생략된 위치에 의존하던 사용자는 새 버전 적용 후 자신의 위치를 명시해야 날씨 조회가 계속된다.
