"""한국어 UI 문구."""



MESSAGES = {
    "title_model_required": "자동 제목 모델이 없습니다. 대화 엔진 또는 parameters.engines._hub_title.completion.model을 설정하세요.",
    "output_image_limit": "현재 화면의 이미지 미리보기 한도(32개)에 도달했습니다.",
    "welcome_title": "아직 세션이 없습니다",
    "welcome_hint": "새 세션을 만들어 대화를 시작하세요.",
    "welcome_keys": "F4 새 세션 · Ctrl+S 프로젝트 설정 · Ctrl+Q 셸로 돌아가기",
    "session_required": "먼저 세션을 생성하세요. F4 또는 세션 목록에서 c를 누르세요.",
    "output_pending": "출력 블록 수신 중…",
    "output_image": "이미지 · {caption}",
    "output_image_loading": "이미지를 변환하는 중…",
    "output_error": "출력을 표시할 수 없습니다: {error}",
    "progress_more": " · 외 {count}개",
    "progress_settings_load": "설정 불러오는 중",
    "progress_settings_work": "설정 처리 중",
    "progress_component": "{command} 처리 중",
    "progress_component_done": "{identifier} 처리 완료",
    "settings_storage_description": "대화 저장 방식. 새 프로젝트에서 비우면 file로 저장합니다. Session이 있으면 변경할 수 없으며, null은 주입 저장소 전용입니다.",
    "settings_reasoning_effort": "생각 과정 강도. 예: low, medium, high (따옴표 없이 입력). 지원값은 모델마다 다릅니다. 빈칸은 프로젝트 설정 해제이며 비활성화를 뜻하지 않습니다. 제공된 생각 과정 텍스트만 표시합니다. .ishrc.py의 completion_kwargs에 같은 옵션이 있으면 그 값이 우선합니다.",
    'send_while_running': '실행 중 전송 방식',

    'send_steering': '실행 중 추가 지시 — 현재 작업에 반영',

    'send_follow_up': '후속 지시 — 현재 작업이 끝난 뒤 실행',

    'instruction_run_changed': '대상 실행이 변경되었습니다. 입력을 보존했으니 전송 방식을 다시 선택하세요.',

    'model_required': 'Ctrl+S → 프로젝트 설정에서 config.parameters.engines.loop.completion.model을 지정하세요.',



    "history_title": "요청·작업 목록", "history_empty": "저장된 요청이 없습니다.",

    "history_hint": "↑↓ 선택 · d 삭제 · r 이 시점에서 복제 · Tab 버튼 이동",

    "history_response": "응답", "history_delete": "대화 삭제", "history_clone": "이 시점에서 복제",

    "history_delete_confirm": "선택한 요청·응답을 대화와 이후 모델 문맥에서 삭제할까요?\n실행 기록과 원본 저장 이벤트는 보존됩니다.",

    "history_busy": "실행·예약 요청·자동 이름 생성이 끝난 뒤 대화를 삭제하세요.",

    "clone_latest": "최신 대화까지 전체 복제", "clone_boundary": "복제할 마지막 요청·응답 (선택한 쌍 포함)",

    "project_activity": "프로젝트 실행 이력", "activity_empty": "기록된 실행 이력이 없습니다.",

    "activity_hint": "최근 300건 · Alt+방향키/PageUp/PageDown/Home/End 스크롤 · Enter/Esc/Ctrl+L 닫기",

    "activity_source": "세션: {session_id} · Run: {run_id}",

    "activity_step": "Step: {step_id}", "activity_code": "코드: {code}",

    "help_history": "Ctrl+G: 요청·작업 쌍 목록 (d 삭제 · r 선택 시점 복제)\nCtrl+L: 프로젝트 실행 이력\n세션 목록 ←→: 패널 너비 조절\n복제 창에서 마지막으로 포함할 요청·응답을 선택할 수 있습니다.",

    "settings_registration": "추가 기능", "settings_engines": "엔진",

    "settings_type": "- 타입 : {type}", "settings_rename": "프로젝트 이름 변경",

    "settings_sidebar_width": "설정·대화 화면의 좌측 패널 너비 (터미널 칸 수, 18~60)",

    "settings_width_preview": "너비 미리보기 적용 · 모양에서 저장하거나 취소하세요.",

    "settings_global_registration": "프로젝트를 선택하면 컴포넌트와 엔진을 등록할 수 있습니다.",

    "settings_project_keys": "c 생성 · d 삭제\ne 이름 · r 복제",

    "settings_cancelled": "이 페이지의 변경 사항을 취소했습니다.",

    "settings_engine_required": "사용할 엔진을 하나 이상 선택하세요.",

    "settings_engine_unavailable": "이 프로젝트에서 선택되지 않은 엔진입니다: {name}",

    "settings_hub_data_object": "config.data와 config.data.hub는 JSON 객체여야 합니다.",

    "model_line": "모델: {model}",

    "session_delete": "세션 삭제", "session_rename": "세션 이름 변경",

    "session_delete_confirm": "'{title}' 세션을 소프트 삭제할까요? 기록은 보존됩니다.",

    "session_name_required": "세션 이름을 입력하세요.",

    "session_busy": "실행·예약 요청·자동 이름 생성이 끝난 뒤 세션을 삭제하세요.",

    "command_component": "{name} 데이터·설정",

    "component_action_list": "데이터 목록", "component_action_get": "ID로 조회",

    "component_action_create": "ID와 JSON으로 생성", "component_action_update": "ID와 JSON으로 수정",

    "component_action_delete": "ID로 삭제", "component_action_settings": "컴포넌트 설정 열기",

    "component_action_help": "명령 사용법",

    "component_unavailable": "현재 프로젝트에서 활성화되지 않은 컴포넌트: {name}",

    "component_usage_error": "사용법: list | get ID | create ID JSON | update ID JSON | delete ID | settings | help",

    "component_json_object": "데이터는 JSON 객체로 입력하세요.",

    "component_delete": "컴포넌트 데이터 삭제",

    "component_delete_confirm": "{name}의 '{identifier}' 데이터를 삭제할까요? 이 데이터 삭제는 되돌릴 수 없습니다.",

    "component_loading": "컴포넌트 요청을 처리하고 있습니다…", "component_busy": "이전 컴포넌트 요청을 처리 중입니다.",

    "component_help": "/{command} list\n/{command} get ID\n/{command} create ID JSON\n/{command} update ID JSON\n/{command} delete ID\n/{command} settings\n\n생성·수정은 JSON 객체를 받습니다. 일반 데이터 수정은 최상위 키를 병합합니다.\n컴포넌트 설정은 settings 화면의 저장 버튼으로 적용합니다.\nRAG는 title/content/metadata 문서 데이터를 받으며 생성·수정 시 모델을 호출해 색인을 갱신할 수 있습니다.",

    "search_title": "현재 세션에서 찾기", "search_hint": "검색어 입력 · Enter 다음 · Alt+P 이전 · Esc 닫기",

    "search_count": "{index} / {count}개 일치 · Enter 다음 · Alt+P 이전",

    "search_previous": "이전", "search_next": "다음", "search_close": "닫기",

    "notification_title": "다른 세션 상태",

    "execution_title": "실행 방식 선택", "execution_engine": "엔진",

    "execution_workflow": "워크플로", "execution_plain": "이 엔진은 별도의 요청 옵션이 필요하지 않습니다.",

    "execution_no_workflows": "선택할 워크플로가 없습니다.",

    "execution_workflows_hint": "프로젝트의 workflows 컴포넌트를 활성화하고 Workflow를 등록하세요.",

    "execution_summary": "시작: {entry} · 최상위 노드: {count}개",

    "settings_title": "설정", "settings_global": "전역 설정", "settings_projects": "프로젝트",

    "settings_profile": "프로필", "settings_appearance": "모양", "settings_main": "메인 설정",

    "settings_components": "컴포넌트", "settings_save": "저장", "settings_saved": "저장했습니다.",

    "settings_create": "프로젝트 생성", "settings_new_title": "새 프로젝트", "settings_reload": "불러오기",

    "settings_open_project": "열기", "settings_clone": "복제", "settings_delete": "삭제",

    "settings_copy_suffix": "복사본", "settings_deleted": "프로젝트를 소프트 삭제했습니다.",

    "settings_delete_confirm": "'{title}' 프로젝트를 소프트 삭제할까요? 대화와 파일은 보존됩니다.",

    "settings_delete_active": "현재 대화 중인 프로젝트입니다. 다른 프로젝트의 '열기'로 전환한 뒤 삭제하세요.",

    "settings_loading": "불러오는 중…", "settings_empty": "프로젝트 없음",

    "settings_footer": "ESC 좌측 패널 · Tab 영역 · 방향키 항목 · Enter 편집 · Ctrl+S 저장 후 대화 · Ctrl+Q 셸",

    "settings_general": "일반",

    "general_category_language": "언어 팩",

    "general_category_notifications": "알림",

    "general_category_startup": "시작 동작",

    "general_category_output": "대화 출력",

    "general_category_editor": "외부 편집기",

    "general_language": "사용할 언어 코드: ko, en 또는 추가한 팩 코드. 비우면 기존 프로필·시작 언어 사용. 다음 Hub 시작부터 적용.",

    "general_notification_kinds": "표시할 알림 종류를 JSON 배열로 입력: error, info, warning, success. []는 모든 팝업 알림 끄기.",

    "general_notification_seconds": "새 알림의 표시 시간(초). 저장 즉시 이후 알림에 적용.",

    "general_restore_last_project": "시작할 때 마지막 프로젝트 복원. 명시적으로 지정한 프로젝트 우선. 삭제된 프로젝트는 기본 프로젝트로 대체.",

    "general_restore_last_session": "프로젝트를 열 때 마지막 세션 복원. 명시적으로 지정한 세션 우선. 삭제된 세션은 활성 세션으로 대체.",

    "general_auto_scroll": "새 응답이 올 때 맨 아래를 보고 있었다면 자동 스크롤. false는 읽던 위치 유지. Alt+방향키 수동 이동은 항상 가능.",

    "general_editor": "외부 에디터 명령: vim, nano, code --wait 등. 비우면 VISUAL → EDITOR → vi 순서로 선택.",

    "language_pack_add": "팩 추가/갱신",

    "language_pack_delete": "팩 삭제",

    "language_pack_names": "사용 가능: {names} · 빠진 번역은 영어로 표시",

    "language_pack_code": "팩 코드 (예: ja, custom-ko). 내장 ko/en은 변경·삭제할 수 없습니다.",

    "language_pack_json": "메시지 키 → 번역 문자열 JSON을 붙여넣으세요. 기존 코드면 교체합니다.",

    "language_pack_none": "삭제할 사용자 언어 팩이 없습니다.",

    "language_pack_draft": "언어 팩 초안을 변경했습니다. 일반 설정의 저장 버튼으로 확정하세요.",

    "settings_language_description": "UI 언어: ko(한국어), en(English). 비우면 시작 설정 사용. 다음 Hub 시작부터 적용됩니다.",

    "settings_email_description": "프로필 이메일 주소(선택).",

    "settings_phone_description": "프로필 전화번호(선택).",

    "settings_editor_description": "외부 편집 작업용 에디터 명령. 예: vim, nano, code --wait. 비우면 VISUAL → EDITOR → vi 순서로 선택합니다.",

    "settings_usage": "사용 통계",

    "settings_usage_scope": "워크스페이스의 활성 프로젝트 기준 · 불러오기로 갱신 · 프로젝트별 집계 기간 적용",

    "settings_usage_empty": "조회된 사용 통계가 없습니다.",

    "settings_usage_error": "{title}: 통계 조회 실패 — {error}",

    "settings_usage_all": "전체 보관 기간",

    "settings_usage_period": "최근 {seconds}초",

    "settings_usage_row": "{title} ({period})\n  호출 {call_count}회 · 확인된 토큰 {known_tokens} · 예약 토큰 {reserved_tokens} · 사용량 미확인 {unknown_calls}회",

    "settings_no_description": "스키마에 별도 설명이 없습니다.", "settings_no_fields": "공개된 설정 항목이 없습니다.",
    "settings_stored": "저장값 (편집 대상)",
    "settings_effective": "현재 적용값 [{name}]: {value} · 출처: {source}",
    "settings_unset": "미설정", "settings_runtime_value": "실행 시 호스트에서 결정",
    "settings_host_locked": "호스트 고정값: 읽기 전용입니다.",
    "settings_host_partial": "일부 하위 키는 호스트 고정값입니다. 해당 키의 저장값을 변경할 수 없습니다.",

    "settings_profile_description": "대화에 표시할 이름. 초기값은 현재 OS 계정명입니다.",

    "settings_color_description": "색상: #RRGGBB 또는 default (터미널 기본색)",
    "settings_theme_background": "배경 색", "settings_theme_foreground": "전경 색",
    "settings_theme_accent1": "강조 색 1", "settings_theme_accent2": "강조 색 2",
    "settings_theme_accent3": "강조 색 3", "settings_theme_comment": "주석 색",
    "settings_sidebar_width_title": "좌측 패널 너비",

    "settings_value_hint": "빈칸=미설정 · 문자열은 그대로, 숫자/불리언/배열/객체는 JSON · null은 명시적 값 · 불러오기는 입력 초기화",

    "settings_components_immediate": "컴포넌트·엔진 선택은 저장 시 반영됩니다. 해제해도 기존 데이터와 설정은 유지됩니다.",

    "settings_components_draft": "선택한 컴포넌트는 프로젝트 생성 시 활성화됩니다.",

    "settings_component_applied": "컴포넌트 선택을 반영했습니다. 설정 값은 저장 버튼으로 적용하세요.",

    "settings_components_changed": "컴포넌트 선택이 변경되었습니다. 설정을 다시 읽어주세요.",

    "settings_project_name": "프로젝트 이름",

    "settings_title_required": "프로젝트 이름을 입력하세요.",

    "settings_project_busy": "실행 또는 예약 요청이 있는 프로젝트는 복제·삭제할 수 없습니다.",

    "view_state_error": "읽던 위치를 저장하지 못했습니다: {error}",

    "session": "대화", "run": "실행", "steps": "단계",

    "completion_detail": "모델: {model}\n호출: {count}회\n최근 호출 토큰: {tokens}",

    "connecting": "연결 중", "connecting_notice": "백엔드를 연결하고 있습니다.",

    "new_session": "새 대화", "sessions": "{icon_sessions} 세션 목록", "workspace": "프로젝트",

    "message": "메시지", "preview": "마크다운 미리보기", "you": "나",

    "assistant": "어시스턴트", "info": "안내", "reasoning": "{icon_reasoning} 모델이 제공한 생각 과정",

    "session_hint": "c 생성 · d 삭제\ne 이름 · r 복제", "input_hint": "Enter 전송 · Ctrl+Space 줄바꿈 · ↑↓ 후보 선택 · Tab 확정",

    "footer": "Ctrl+Q 셸 · Ctrl+S 설정 · ESC 패널 · Ctrl+F 찾기 · Alt+이동키 스크롤 · Ctrl+E 엔진 · Ctrl+X 중단 · Enter 전송 방식",

    "footer_short": "ESC 패널 · Ctrl+F 찾기 · Alt+이동키 스크롤",

    "preview_notice": "샘플 데이터 · 모델 호출과 저장 기능은 연결되지 않았습니다.",

    "preview_submit": "전송 미리보기입니다. 입력은 유지됩니다.",

    "details_narrow": "실행 상세는 너비 116칸 이상에서 표시됩니다.", "details_open": "실행 상세를 표시합니다.",

    "details_closed": "실행 상세를 접었습니다.", "close_hint": "Ctrl+Q로 셸로 돌아갑니다.",

    "error": "오류: {error}", "not_connected": "백엔드 연결을 먼저 완료하세요.",

    "saved": "요청을 저장했습니다.", "instruction_saved": "추가 지시를 저장했습니다. 다음 처리 시점에 반영됩니다.",

    "empty_message": "메시지를 입력하세요.", "no_active_run": "추가 지시를 보낼 실행이 없습니다.",

    "no_targets": "이 실행은 현재 추가 지시를 받지 않습니다.",

    "choose_target": "추가 지시를 받을 대상", "choose_engine": "다음 요청에 사용할 엔진",

    "engine_line": "엔진: {engine} · Ctrl+E 변경", "create_dialog": "대화 생성 또는 복제",

    "create": "새로 생성", "clone": "선택한 대화 복제", "name": "이름 (선택)",

    "auto_name_hint": "비워 두면 응답 후 모델이 대화 이름을 짓습니다.",

    "confirm": "확인", "cancel": "취소", "clone_busy": "원본의 실행과 예약 요청을 마친 뒤 복제하세요.",

    "queued": "{status} · 예약 {count}", "waiting": "{icon_waiting} 모델 응답을 기다리는 중",

    "run_progress": "{status} · {engine} · {seconds}초 · 예약 {count}",

    "elapsed": "소요 {duration}", "elapsed_unknown": "소요 시간 알 수 없음",

    "duration_seconds": "{seconds:.1f}초", "duration_minutes": "{minutes}분", "duration_hours": "{hours}시간",

    "phase": "현재 단계: {name} ({status})", "no_reasoning": "모델이 아직 생각 과정 텍스트를 제공하지 않았습니다.",

    "paused": "{icon_paused} 일시 정지됨 · 승인/재개는 llm API에서 처리하세요.",

    "memory": "메모리 대화: 종료 시 메시지 소멸",

    "title_failed": "자동 이름 생성 실패: {error}", "unknown_command": "알 수 없는 명령: {command}",

    "command_help": "도움말", "command_engine": "엔진 선택", "command_new": "대화 생성",

    "command_clone": "선택한 대화 복제", "command_preview": "마크다운 미리보기 전환",

    "command_stop": "현재 실행 중단", "command_details": "실행 상세 전환",

    "file": "파일", "directory": "폴더", "engine": "엔진",

    "background": "\n  Hub\n\n  Ctrl+Q: 대화로 돌아가기\n  Ctrl+C: 종료\n\n  Hub를 숨겨도 실행은 계속됩니다.\n",

    "help_title": "Hub 도움말",

    "help": "Enter: 선택한 엔진으로 전송\nCtrl+Space: 줄바꿈\nCtrl+S: 설정 화면\nCtrl+F: 현재 세션 출력 찾기\n명령·엔진·@파일 경로: 입력 중 자동완성\n↑↓: 자동완성 후보 선택 · Tab: 확정 (미선택 시 첫 후보)\n목록이 닫혀 있으면 Tab: 자동완성 목록 열기\nESC: 좌측 패널로 이동 (패널에서는 유지)\n패널에서 Tab / Space / Enter: 메인 영역으로 이동\n세션 목록: c 생성 · d 삭제 · e 이름 변경 · r 복제\n/컴포넌트: 활성 컴포넌트의 데이터·설정 (help로 사용법)\nAlt+↑↓: 줄 스크롤 · Alt+←→: 가로 스크롤 (넘친 내용)\nAlt+PageUp/PageDown: 페이지 스크롤 · Alt+Home/End: 처음/끝\n실행 중 Enter: 추가 지시 / 후속 지시 선택\nCtrl+X: 현재 실행 중단\n팝업에서는 ESC: 닫기\nF2: 다음 대화 · Ctrl+E: 엔진 선택\nF4: 이름을 지정하여 대화 생성/복제\nF5: 입력 마크다운 미리보기 · F6: 실행 상세\nCtrl+Q: 셸로 돌아가기\n설정: ESC 좌측 패널 · Tab 영역 이동 · Enter 편집/완료\n대화상자: Tab / Shift+Tab 항목 이동\n\n추가 지시는 엔진의 다음 처리 시점에 반영됩니다.\n파일 경로는 텍스트로 삽입하며 파일을 자동 첨부하지 않습니다.",

    "status_idle": "{icon_idle} 유휴", "status_running": "{icon_running} 실행 중", "status_queued": "{icon_queued} 예약",

    "status_committed": "{icon_committed} 접수됨", "status_streaming": "{icon_streaming} 응답 수신 중", "status_completed": "{icon_completed} 완료",

    "status_interrupted": "{icon_interrupted} 중단됨", "status_cancelled": "{icon_cancelled} 취소됨", "status_failed": "{icon_failed} 실패",

    "status_paused": "{icon_paused} 일시 정지", "status_pending": "{icon_pending} 실행 전", "status_applied": "{icon_applied} 반영됨",

    "status_unapplied": "{icon_unapplied} 미반영", "status_partially_applied": "{icon_partially_applied} 일부 반영",

}
