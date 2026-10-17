"""한국어 UI 문구."""



MESSAGES = {
    'shortcut_auxiliary': '보조 버튼',
    'shortcut_return': '돌아가기',
    'attach_title': '파일 첨부',
    'attach_hint': '파일 경로를 선택하면 입력창에 추가합니다. Tab으로 경로 자동완성을 확정하세요.',
    'attach_required': '파일 경로를 입력하세요.',
    'settings_json': '설정 JSON',
    'settings_theme_mode': '테마',
    'settings_theme_mode_description': 'dark 또는 light. 자동으로 정해지는 배경과 글자색에 적용합니다.',
    'clipboard_internal': '내부 클립보드',
    'clipboard_title': '클립보드',
    'clipboard_copy_unavailable': '외부 복사 실패 · Hub 내부에만 저장됨',
    'clipboard_read_unavailable': '외부 클립보드를 읽지 못했습니다. 터미널의 붙여넣기를 사용하세요.',
    'clipboard_x11_library': 'X11 클립보드 연결에 필요한 libX11을 찾지 못했습니다.',
    'clipboard_x11_connection': 'X11 클립보드에 연결하지 못했습니다. DISPLAY와 X11 접근 권한을 확인하세요.',
    'clipboard_x11_timeout': 'X11 클립보드 응답 시간이 초과되었습니다. 연결 상태를 확인하세요.',
    'clipboard_x11_transfer': 'X11 클립보드의 텍스트를 전달하지 못했습니다. 터미널의 붙여넣기를 사용할 수 있습니다.',

    "profile_settings": "설정", "profile_home": "홈", "profile_minimize": "최소화",
    "shortcut_profile": "프로필", "shortcut_categories": "카테고리",
    "history_retry_question_unavailable": "취소한 승인·질문은 실행·예약 작업이 없고 가장 최근 요청일 때만 다시 열 수 있습니다. 이전 요청은 편집으로 다시 시작하세요.",
    "settings_discard": "취소", "settings_discarded": "저장하지 않은 변경 내용을 취소했습니다.",
    "settings_engine_item": "엔진 · {name}", "settings_component_item": "컴포넌트 · {name}",
    "project_delete_last": "마지막 프로젝트는 삭제할 수 없습니다. 먼저 다른 프로젝트를 만들어 주세요.",
    "failure_evidence": "진단 근거: {evidence}",
    "failure_details": "자세한 실행 기록은 보조 버튼의 로그에서 확인할 수 있습니다.",
    "failure_unknown": "저장된 기록만으로 정확한 원인을 확인할 수 없습니다. 이미 실행된 파일 변경이나 명령이 있는지 확인한 뒤 보조 버튼의 요청 목록에서 재시도할 수 있습니다. 같은 문제가 반복되면 상세 기록을 함께 전달해 주세요.",
    "failure_rate_limit": "모델 제공자가 사용량 또는 요청 속도 제한으로 요청을 거절했습니다. 잠시 기다린 뒤 다시 시도하고, 계속 발생하면 해당 제공자의 API 사용 한도·잔액·결제 상태를 확인해 주세요. 이 코드만으로 API 키가 잘못됐다고 판단할 수는 없습니다.",
    "failure_unavailable": "모델 제공자에 연결하거나 응답을 받는 과정에서 오류가 발생했습니다. 제공자 서비스 상태를 확인하고 잠시 후 다시 시도해 주세요. 반복되면 다른 사용 가능한 모델로 변경할 수 있습니다.",
    "failure_authentication": "모델 제공자가 인증을 거절했습니다. 선택한 모델에 맞는 제공자의 API 키가 설정되어 있는지, 키가 만료되거나 폐기되지 않았는지 확인해 주세요.",
    "failure_permission": "필요한 접근 권한이 없어 작업을 완료하지 못했습니다. 모델·프로젝트의 사용 권한 또는 대상 파일·디렉터리 권한을 확인해 주세요.",
    "failure_timeout": "정해진 시간 안에 작업을 완료하지 못했습니다. 네트워크와 제공자 상태를 확인하고, 작업을 나누거나 설정의 제한 시간을 검토해 주세요. 재시도 전 이미 수행된 작업도 확인하세요.",
    "failure_connection": "네트워크 연결을 완료하지 못했습니다. 인터넷 연결, DNS, 프록시와 방화벽 설정을 확인해 주세요.",
    "failure_request": "모델 제공자가 요청 형식이나 대상을 받아들이지 않았습니다. 모델 이름·API 주소·지원되는 옵션을 확인하고, 큰 입력이나 첨부가 있다면 줄여서 시도해 주세요.",
    "failure_context": "모델이 한 번에 처리할 수 있는 대화 길이를 초과했습니다. 입력을 나누거나 새 세션에서 필요한 내용만 전달해 주세요.",
    "failure_budget": "프로젝트에 설정된 사용량 한도에 도달했습니다. 사용 통계와 사용량 정책을 확인하고, 필요하다면 한도를 조정해 주세요.",
    "failure_file_missing": "작업에 필요한 파일이나 디렉터리를 찾지 못했습니다. ish의 현재 디렉터리와 요청한 경로가 맞는지 확인해 주세요.",
    "failure_empty": "모델 제공자가 비어 있거나 처리할 수 없는 응답을 반환했습니다. 잠시 후 다시 시도하고, 반복되면 모델 이름과 제공자 연결 설정을 확인해 주세요.",
    "failure_completion_invalid": "모델 응답의 형식이 맞지 않아 처리를 완료하지 못했습니다. 제공자·서버의 스트리밍 및 Tool 호출 호환성을 확인해 주세요. 받은 응답은 유지되며 자동으로 다시 실행하지 않습니다.",
    "failure_completion_length": "모델이 출력 토큰 한도에 도달해 답변을 마치지 못했습니다. 모델의 출력 토큰 설정을 확인하거나 작업을 나누어 요청해 주세요. 받은 응답은 유지됩니다.",
    "failure_completion_filtered": "모델 제공자의 콘텐츠 필터로 응답이 종료되었습니다. 요청 내용과 제공자의 이용 정책을 확인해 주세요. 같은 요청을 자동으로 반복하지 않습니다.",
    "failure_completion_incomplete": "모델 응답이 정상적으로 끝나지 않았습니다. 받은 응답은 유지됩니다. 로그의 종료 사유와 제공자 상태를 확인한 뒤 재시도 여부를 결정해 주세요.",
    "failure_completion_output_limit": "프로젝트에 설정한 출력 문자 한도에 도달했습니다. 엔진의 출력 크기 제한을 검토하거나 작업을 나누어 요청해 주세요.",
    "failure_completion_tool_limit": "모델이 한 번에 요청한 Tool 수가 설정된 한도를 넘었습니다. 엔진의 Tool 호출 수 제한을 검토하거나 작업을 나누어 요청해 주세요.",
    "failure_completion_argument_limit": "모델이 만든 Tool 인자 또는 호출 ID가 설정된 길이 한도를 넘었습니다. 엔진의 Tool 인자 크기 제한을 검토하거나 작업을 나누어 요청해 주세요.",
    "failure_step_timeout": "작업 단계가 설정된 제한 시간을 넘었습니다. 해당 엔진의 단계별 제한 시간을 검토해 주세요. 재시도 전 이미 실행된 작업을 확인하세요.",
    "general_category_diagnostics": "Provider 진단",
    "general_provider_error_details": "서버의 상세 오류 메시지·오류 필드·API 주소를 기록합니다. 기본값 false. 오류 메시지에 작업 내용이 포함될 수 있습니다. Hub 재시작 후 적용.",
    "general_provider_request_summary": "요청의 메시지 역할·길이·Tool 호출과 결과의 대응 개수를 기록합니다. 본문과 Tool 인자는 기록하지 않습니다. 기본값 false. Hub 재시작 후 적용.",
    "general_provider_max_detail_chars": "상세 오류 문자열 하나당 최대 문자 수. 기본값 2000, 1 이상의 정수. 상세 오류 기록을 켰을 때 사용하며 Hub 재시작 후 적용.",
    # Accepted only for saved language packs; no composer preview UI/binding remains.
    "preview": "마크다운 미리보기",
    "command_preview": "마크다운 미리보기 전환",
    "shortcut_preview": "미리보기",
    "history_requests": "요청 목록",
    "settings_current_project": "프로젝트 : {title}",
    "settings_close": "닫기",
    "shortcut_paste": "붙여넣기",
    "object_copy_code": "코드 복사",
    "object_copy_cell": "셀 복사",
    "settings_requires_restart": "재시작 필요",
    "settings_restart_title": "Hub 재시작",
    "settings_restart_now": "재시작",
    "settings_restart_confirm": "저장되었습니다. 다음 설정을 적용하려면 Hub 재시작이 필요합니다.\n\n{fields}\n\n지금 재시작하시겠습니까?\nEnter: 재시작 · Esc: 나중에 (저장값 유지)",
    "settings_restart_busy": "실행·예약 중인 작업 또는 처리 중인 명령이 있어 재시작할 수 없습니다. 작업이 끝난 뒤 다시 저장하거나 ish를 다시 시작하세요. 설정은 저장되어 있습니다.",
    "settings_restarting": "Hub 재시작 중",
    "general_category_storage": "데이터 저장 위치",
    "general_workspace_root": "Hub 데이터 루트. 절대 경로 또는 ~/경로를 입력하세요. 저장 후 Hub를 재시작하면 적용됩니다. 기존 데이터는 자동으로 이동하지 않으며, 빈 폴더는 새 작업 공간으로 시작합니다. 비우면 시작 시 지정한 기본 경로를 사용합니다.",
    'object_query': '검색 / 필터:',
    'object_raw': '원문',
    'object_rendered': '렌더링',
    'object_empty': '일치하는 행이 없습니다.',
    'object_loading': '표를 준비하는 중…',
    'object_table_count': '행 {index}/{visible} · 전체 {total}행 · 열 {column}',
    'shortcut_object_raw': '원문/화면',
    'shortcut_object_sort': '열 정렬',
    'shortcut_cancel': '예약 취소',
    'preparing_response': '준비 중…',
    'status_preparing': '{icon_preparing} 준비 중',
    'status_thinking': '{icon_reasoning} 생각 중',
    'status_retrying': '{icon_preparing} 재시도 중',
    'status_interrupting': '{icon_interrupted} 중단 중',
    'followup_hint': '→ 추천 입력',
    'component_action_install': '내장 스킬 설치 (기존 항목 유지)',
    # Keep the key for saved language packs; the removed install command is not advertised.
    'skills_install_help': '/skills config\n관리 창에서 내장 스킬 Markdown 파일의 경로를 입력하여 가져옵니다. 기존 ID는 덮어쓰지 않습니다.',
    'history_cancel': '예약 취소',
    'history_cancel_confirm': '선택한 예약 요청을 취소할까요? 현재 실행 중인 작업은 계속됩니다.',
    'history_cancel_unavailable': '예약 상태의 요청만 취소할 수 있습니다. 이미 실행 중이면 Ctrl+W로 중단하세요.',
    'history_checkpoint_protected': '미완료 작업의 재개 체크포인트가 이 대화를 참조합니다. 삭제를 다시 선택하여 영향받는 작업의 재개 포기를 확인하세요.',
    'history_abandon_delete': '재개 포기 후 삭제',
    'history_delete_changed': '삭제 확인 이후 관련 작업이나 대화가 변경되었습니다. 삭제를 다시 선택하여 확인하세요.',
    'history_abandon_confirm': '이 대화를 참조하는 아래 {count}개 작업의 재개를 포기하고 삭제할까요?\n이미 수행된 작업은 되돌리지 않습니다.\n대화는 이후 문맥에서 제외되며 실행 기록과 체크포인트는 보존됩니다.',

    'shortcut_movement': '이동키',
    'shortcut_clear': '비우기',
    'shortcut_tags': '태그 바',
    'shortcut_expand': '확대',
    'shortcut_copy': '원문 복사',
    'shortcut_open_directory': '폴더 열기',
    'settings_search': '설정 검색',
    'settings_search_hint': '키·설명·값에 포함된 문자열로 검색합니다. 빈칸이면 전체 표시.',
    'settings_search_empty': '일치하는 설정이 없습니다.',
    'settings_search_active': '필터 : {query}',
    'clipboard_copied': '원문을 복사했습니다.',
    'clipboard_terminal': '터미널에 복사를 요청했습니다(OSC 52). 앱 클립보드에도 저장했습니다.',
    'component_action_config': '컴포넌트 관리 창',
    'component_manager_unavailable': '{name} 관리 창은 아직 지원하지 않습니다. settings 또는 데이터 명령을 사용하세요.',
    'tags_title': '태그 바',
    'tags_empty': '출력 객체가 없습니다.',
    'tools_title': 'Tool 패키지 관리',
    'skills_title': 'Skill 관리',
    'mcp_title': 'MCP 서버 관리',
    'rag_title': 'RAG 문서 관리',
    'workflows_title': 'Workflow 관리',
    'skills_import_hint': 'Markdown 또는 Skill JSON 파일 경로 → Enter: 가져오기',
    'mcp_import_hint': 'MCP 서버 JSON 파일 경로 → Enter: 검증 후 가져오기',
    'rag_import_hint': 'UTF-8 텍스트 문서 경로 → Enter: 문서 및 검색 색인 등록',
    'workflows_import_hint': 'Workflow JSON 파일 경로 → Enter: 검증 후 가져오기',
    'manager_hint': 'Tab 영역 · ↑↓ 선택 · Enter 편집기에서 수정 · d 삭제',
    'manager_empty': '등록된 항목이 없습니다.',
    'manager_imported': '가져왔습니다.',
    'manager_saved': '저장되었습니다.',
    'rag_phase_queued': '등록 대기',
    'rag_phase_preparing': '등록 준비',
    'rag_phase_embedding': '임베딩',
    'rag_phase_extraction_index': '관계 추출·색인 저장',
    'rag_phase_indexing': '색인 저장',
    'rag_phase_completed': '등록 완료',
    'rag_phase_failed': '등록 실패',
    'rag_phase_cancelled': '취소됨',
    'rag_phase_unconfirmed': '실행 상태 확인 필요',
    'rag_job_counts': '임베딩 {done}/{total}청크 · 체크포인트 재사용 {reused} · 관계 추출 {batches}배치',
    'rag_job_times': '임베딩 {embedding}초 · 관계 추출 {extraction}초 · 이번 시도 경과 {elapsed}초',
    'rag_job_retry_hint': '재시도: 완료한 청크를 재사용합니다. 실제 실행 중이면 재개하지 않습니다.',
    'rag_job_readonly': '등록 작업은 편집·삭제하지 않습니다. 취소된 작업은 재개할 수 없습니다.',
    'rag_retry_unavailable': '실행 중이 아닌 미완료 등록 작업을 선택하세요. 완료·취소된 작업은 재시도할 수 없습니다.',
    'manager_delete_confirm': '{name}을 삭제할까요?',
    'tools_import_hint': '디렉토리 경로 → Enter: <이름>/<이름>.py와 선택적인 requirements.txt 가져오기',
    'tools_columns': '이름 / 마지막 수정 / 설명',
    'tools_hint': 'Tab 영역 · ↑↓ 선택 · Space 내장 도구 활성/비활성 · Enter 폴더 · d 삭제',
    'tools_empty': '등록된 Tool이 없습니다.',
    'tools_imported': 'Tool을 가져왔습니다. 사용 전 의존성 준비와 활성화는 별도로 해주세요.',
    'tools_failed': '처리에 실패했습니다. 오류 알림을 확인하세요.',
    'tools_explorer_failed': '파일 탐색기 종료 코드: {code}',
    'tools_delete_confirm': '{name}을 삭제할까요? 활성화되어 있다면 함께 비활성화합니다.',
    'welcome_keys': 'ESC 패널 · 패널에서 c 새 세션 · Ctrl+S 보조 버튼',
    'session_required': '먼저 세션을 생성하세요. ESC 후 세션 목록에서 c를 누르세요.',
    'activity_hint': '활동·프로바이더 각각 최근 300건 · 이동키 스크롤 · ESC / Enter / Ctrl+L 닫기',
    'activity_provider_project': '프로바이더 로그 · 현재 프로젝트',
    'activity_provider_file': '로그 파일: {path}',
    'activity_provider_traceback': 'Traceback · 저장된 예외와 호출 경로 (오류 메시지 원문 제외)',
    'activity_provider_run_details': '이 Run의 프로바이더 상세:',
    'activity_provider_workspace': '프로바이더 로그 · 워크스페이스 공통',
    'status_waiting_input': '{icon_pending} 답변 대기',
    'status_waiting_approval': '{icon_waiting_approval} 승인 대기',
    'status_answer_sending': '{icon_waiting} 답변 전달 중',
    'status_approved': '{icon_completed} 승인 완료',
    'status_denied': '{icon_cancelled} 거절됨',
    'status_answered': '{icon_completed} 답변 전달 완료',
    'status_resuming': '{icon_running} 이어서 실행',
    'help': '## 패널과 홈\n\n- **ESC**: 메인에서 패널로 이동.\n- **Ctrl+Q**: 셸에서 Hub 열기, Hub의 모든 화면에서 최소화. 셸의 Ctrl+S는 변경하지 않습니다.\n- **Tab / Shift+Tab**: 패널의 카테고리 이동. **↑↓**: 현재 카테고리 안에서 이동합니다. **Space / Enter / →**: 메인으로 이동.\n- **Ctrl+← / Ctrl+→**: 패널 너비 조절 및 너비만 자동 저장.\n- **Ctrl+P**: 패널 하단 프로필 영역으로 이동합니다. 홈에서는 설정, 설정에서는 홈 버튼과 최소화 버튼을 표시합니다. **←→**: 버튼 선택. **Enter / Space**: 실행. 프로필의 Tab은 동작하지 않습니다.\n- 홈 위쪽 프로젝트 목록에서 프로젝트를 선택하고, 아래쪽 세션 목록에서 세션을 선택합니다.\n- **c / d / e / r**: 홈 패널에서 생성 / 삭제 / 이름 변경 / 복제.\n- **Enter**: 요청 전송. 실행 중에는 추가 지시 또는 후속 요청 선택.\n- 빈 입력창의 **↑↓**: 이전/다음 사용자 요청 불러오기. 내용을 수정하면 히스토리 탐색을 종료합니다.\n- **Ctrl+Space**: 줄바꿈. **↑↓**: 자동완성 선택, **Tab**: 확정.\n- **Ctrl+W**: 현재 실행 또는 일시 정지된 요청 중단. 예약 요청은 유지.\n- `/engine <엔진명>`으로 엔진 지정.\n- **Ctrl+S**: 입력창 또는 설정 버튼 영역에서 보조 버튼으로 이동. **←→**: 선택. **↑↓**: 동작 없음. **Enter / Space**: 실행. **ESC**: 원래 영역으로 복귀.\n- **보조 버튼 → 찾기**: 세션 내용 찾기. Enter 다음 / Alt+P 이전. 닫으면 강조 해제.\n- **보조 버튼**: 요청 목록 / 태그 바 / 찾기 / 파일 첨부 / 로그 / 설정. 파일 첨부는 선택한 경로를 입력창에 추가합니다. 설정 버튼은 Loop 엔진 설정을 엽니다.\n\n## 스크롤과 팝업\n\n대화 출력은 **Alt + 이동키**로 스크롤합니다.\n도움말·로그·조회 결과 팝업에서는 **이동키만** 사용합니다.\n이동키는 ↑↓←→ / Home / End / PgUp / PgDn 전체입니다.\n읽기 팝업은 **ESC / Enter / Ctrl+L**로 닫습니다.\n\n## 출력 태그 바\n\n**보조 버튼 → 태그 바**로 열고 **↑↓** 키로 객체를 선택하면 출력의 해당 위치로 이동합니다.\n**c**로 원문 복사. **ESC**로 닫습니다. **Tab / Space / Enter**로 전체 화면 팝업을 엽니다.\n코드·표·이미지·diff와 등록된 XML 렌더러 출력이 표시됩니다.\n\n## 설정\n\n- **필터 보조 버튼 / Ctrl+F**: 키·설명·값 필터. 패널 항목을 바꾸어도 유지됩니다. 빈 검색어로 해제합니다. **JSON 보조 버튼**: 현재 설정 초안을 등록된 에디터에서 JSON으로 편집합니다. 종료 후 초안에 반영되며 저장 버튼으로 적용합니다.\n- **저장**: 전역 설정과 현재 프로젝트 변경 적용. **취소**: 저장하지 않은 변경 되돌리기. 둘 다 설정 화면을 유지하며 홈 버튼으로 돌아갑니다.\n- 현재 프로젝트만 편집합니다. 패널에서 일반·엔진·컴포넌트 항목을 선택하세요.\n- **Tab / Shift+Tab**: 항목 / 버튼 영역 이동. **↑↓**: 설정 항목 선택. 버튼은 **←→** 키로 선택합니다. 설정 항목에서 편집 중이 아닐 때 **←**: 패널 복귀, **→**: 동작 없음.\n- **Enter**: 직접 편집 시작(끝으로 이동) / 완료.\n- 항목 선택 상태에서 **e**: 외부 에디터, **d**: 값 전체 비우기.\n- 편집 중 **Ctrl+Space / Alt+Enter**: 줄바꿈.\n- 선택형 값은 Enter → 방향키 → Enter로 변경합니다.\n- 설정 메인 영역에서도 **Alt + 이동키**로 스크롤합니다.\n\n## 명령\n\n`/help`, `/engine`, `/new`, `/clone`, `/details`, `/stop`.\n등록된 컴포넌트만 `/` 자동완성에 나옵니다.\n`/tools config`: 내장 도구 활성/비활성 및 패키지 관리.\n질문이 오면 기존 입력창에서 답변 또는 선택지 번호를 입력하고 Enter로 보냅니다.\n영역을 선택하고 **Ctrl+C**로 복사합니다. 선택이 없으면 입력창의 Ctrl+C는 아무 동작도 하지 않습니다. **Ctrl+V**로 시스템 클립보드를 붙여넣습니다. 편집 가능한 입력창의 **Ctrl+A**는 전체 선택, **Ctrl+X**는 잘라내기, **Ctrl+Z / Ctrl+Y**는 실행 취소 / 다시 실행입니다. **Shift+이동키** 또는 마우스 드래그로 선택합니다.\n파일 경로 자동완성은 텍스트를 삽입하며 파일을 자동 첨부하지 않습니다.\n',
    "settings_dependencies": "필수 컴포넌트: {names}",
    "settings_dependencies_missing": "선택되지 않은 필수 컴포넌트: {names} — 선택 후 저장하세요.",
    "title_model_required": "자동 제목에 사용할 모델이 없습니다. 설정에서 대화 엔진의 모델을 지정하세요.",
    "output_image_limit": "현재 화면의 이미지 미리보기 한도(32개)에 도달했습니다.",
    "welcome_title": "아직 세션이 없습니다",
    "welcome_hint": "새 세션을 만들어 대화를 시작하세요.",
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
    "settings_reasoning_effort": "모델의 추론 강도. 예: low, medium, high. 지원값은 모델마다 다르며, 빈칸은 기본값을 사용합니다.",
    'send_while_running': '실행 중 전송 방식',

    'send_steering': '실행 중 추가 지시 — 현재 작업에 반영',

    'send_follow_up': '후속 지시 — 현재 작업이 끝난 뒤 실행',

    'instruction_run_changed': '대상 실행이 변경되었습니다. 입력을 보존했으니 전송 방식을 다시 선택하세요.',

    'model_required': '설정 보조 버튼 → 엔진 · loop에서 config.parameters.engines.loop.config.completion.model을 지정하세요.',



    "history_title": "요청·작업 목록", "history_empty": "저장된 요청이 없습니다.",

    "history_hint": "↑↓ 이동 · Space 체크 · d 삭제 · e 편집 · r 재시도 · Tab 영역 이동",

    "history_response": "응답", "history_delete": "삭제",
    "history_edit": "편집", "history_retry": "재시도", "history_close": "닫기",
    "history_user_message": "사용자 메시지",
    "history_select_one": "편집·재시도는 요청을 하나만 체크한 상태에서 사용할 수 있습니다.",
    "history_select_items": "삭제할 요청을 체크하세요.",
    "history_retry_unavailable": "중단 혹은 실패된 요청만 재시도할 수 있습니다.",
    "history_edit_unavailable": "실행 중이 아닌 요청만 편집하여 다시 실행할 수 있습니다.",
    "history_retry_blocked": "체크포인트에서 재시도할 수 없습니다: {details}",
    "history_changed": "선택한 요청의 실행 이력이 변경되었습니다. 목록을 다시 열어 선택하세요.",
    "history_delete_selected": "선택한 {count}개 요청을 삭제할까요? 예약 요청은 취소됩니다.",
    "history_deleting": "요청 삭제 중",
    "engine_command_usage": "/engine <엔진명>으로 지정하세요. 사용 가능한 엔진: {engines}",
    "shortcut_retry": "재시도",

    "history_delete_confirm": "선택한 요청·응답을 대화와 이후 모델 문맥에서 삭제할까요?\n실행 기록과 원본 저장 이벤트는 보존됩니다.",

    "history_busy": "실행·예약 요청·자동 이름 생성이 끝난 뒤 대화 이력을 변경하세요.",

    "clone_latest": "최신 대화까지 전체 복제", "clone_boundary": "복제할 마지막 요청·응답 (선택한 쌍 포함)",

    "project_activity": "프로젝트 실행 이력", "activity_empty": "기록된 실행 이력이 없습니다.",


    "activity_source": "세션: {session_id} · Run: {run_id}",

    "activity_step": "Step: {step_id}", "activity_code": "코드: {code}",

    "help_history": "보조 버튼 → 요청 목록: 삭제 · 편집 · 재시도\n보조 버튼 → 로그: 프로젝트 실행 이력\n세션 목록 Ctrl+←→: 패널 너비 조절\n복제 창에서 마지막으로 포함할 요청·응답을 선택할 수 있습니다.",

    "settings_registration": "추가 기능", "settings_engines": "엔진",

    "settings_type": "- 타입 : {type}", "settings_rename": "프로젝트 이름 변경",

    "settings_sidebar_width": "설정·대화 화면의 좌측 패널 너비 (터미널 칸 수, 18~60)",


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

    "component_usage_error": "사용법: /컴포넌트 config | /컴포넌트 help",

    "component_json_object": "데이터는 JSON 객체로 입력하세요.",

    "component_delete": "컴포넌트 데이터 삭제",

    "component_delete_confirm": "{name}의 '{identifier}' 데이터를 삭제할까요? 이 데이터 삭제는 되돌릴 수 없습니다.",

    "component_loading": "컴포넌트 요청을 처리하고 있습니다…", "component_busy": "이전 컴포넌트 요청을 처리 중입니다.",

    "component_help": "/{command} config: 관리 창 열기\n/{command} help: 사용법 보기\n\n관리 창에서 경로를 입력해 가져오고 항목을 편집·삭제할 수 있습니다. 하단 설정 버튼은 이 컴포넌트로 필터링된 프로젝트 설정을 엽니다.\n자료 생성은 대화로 요청할 수 있습니다. Memory·Goals·Refinement는 작업 중 모델 도구와 정책으로 사용합니다.",

    "search_title": "현재 세션에서 찾기", "search_hint": "검색어 입력 · Enter 다음 · Alt+P 이전 · Esc 닫기",

    "search_count": "{index} / {count}개 일치 · Enter 다음 · Alt+P 이전",

    "search_previous": "이전", "search_next": "다음", "search_close": "닫기",

    "notification_title": "다른 세션 상태",






    "settings_title": "설정", "settings_global": "전역 설정", "settings_projects": "프로젝트",

    "settings_profile": "프로필", "settings_appearance": "모양", "settings_main": "메인 설정",

    "settings_components": "컴포넌트", "settings_save": "저장", "settings_saved": "저장했습니다.",

    "settings_create": "프로젝트 생성", "settings_new_title": "새 프로젝트", "settings_reload": "불러오기",

    "settings_open_project": "열기", "settings_clone": "복제", "settings_delete": "삭제",

    "settings_copy_suffix": "복사본", "settings_deleted": "프로젝트를 소프트 삭제했습니다.",

    "settings_delete_confirm": "'{title}' 프로젝트를 소프트 삭제할까요? 대화와 파일은 보존됩니다.",

    "settings_delete_active": "현재 대화 중인 프로젝트입니다. 다른 프로젝트의 '열기'로 전환한 뒤 삭제하세요.",

    "settings_loading": "불러오는 중…", "settings_empty": "프로젝트 없음",

    "settings_footer": "ESC 패널 · e 에디터 · Enter 편집 · Tab 영역 · ↑↓ 항목 · ←→ 버튼",

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

    "general_auto_scroll": "새 응답이 올 때 맨 아래를 보고 있었다면 자동 스크롤. false는 읽던 위치 유지. Alt + 이동키 수동 이동은 항상 가능.",

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
    "settings_editor_failed": "에디터 종료 코드 {code}. 기존 입력을 유지합니다.",
    "settings_host_locked": "호스트 고정값: 읽기 전용입니다.",
    "settings_host_partial": "일부 하위 키는 호스트 고정값입니다. 해당 키의 저장값을 변경할 수 없습니다.",

    "settings_profile_description": "대화에 표시할 이름. 초기값은 현재 OS 계정명입니다.",

    "settings_color_description": "색상: #RRGGBB 또는 default (터미널 기본색)",
    "settings_theme_background": "배경 색", "settings_theme_foreground": "전경 색",
    "settings_theme_accent1": "강조 색 1", "settings_theme_accent2": "강조 색 2",
    "settings_theme_accent3": "강조 색 3", "settings_theme_comment": "주석 색",
    "settings_sidebar_width_title": "좌측 패널 너비",
    "settings_output_refresh_interval": "출력창 갱신 간격 (초)",
    "settings_output_refresh_interval_description": "응답 스트리밍을 묶어 갱신하는 간격. 0.01~10초, 기본 0.1초. 저장 즉시 적용하며 요청 접수와 키 입력은 지연시키지 않습니다.",
    "settings_progress_refresh_interval": "로딩 바 갱신 간격 (초)",
    "settings_progress_refresh_interval_description": "로딩 바·스피너 애니메이션 갱신 간격. 0.01~10초, 기본 0.1초. 설정·대화 화면에 저장 즉시 적용됩니다.",
    "response_failed": "응답을 완료하지 못했습니다.",

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


    "connecting": "연결 중", "connecting_notice": "백엔드를 연결하고 있습니다.",

    "new_session": "새 대화", "sessions": "{icon_sessions} 세션 목록", "workspace": "프로젝트",

    "message": "메시지", "you": "나",

    "assistant": "어시스턴트", "info": "안내", "reasoning": "어시스턴트 생각 과정",

    "session_hint": "c 생성 · d 삭제\ne 이름 · r 복제", "input_hint": "Enter 전송 · Ctrl+Space 줄바꿈 · ↑↓ 후보 선택 · Tab 확정",

    "settings_json_object": "설정 JSON의 최상위 값은 객체여야 합니다.",
    "settings_json_updated": "JSON 편집 내용을 초안에 반영했습니다. 저장 버튼으로 적용하세요.",
    "footer": "ESC 패널 · Ctrl+S 보조 버튼 · Ctrl+W 중단 · Alt + 이동키 스크롤 · Enter 전송 방식",

    "footer_short": "ESC 패널 · Alt + 이동키 스크롤",

    "preview_notice": "샘플 데이터 · 모델 호출과 저장 기능은 연결되지 않았습니다.",

    "preview_submit": "전송 미리보기입니다. 입력은 유지됩니다.",

    "details_narrow": "실행 상세는 너비 116칸 이상에서 표시됩니다.", "details_open": "실행 상세를 표시합니다.",

    "details_closed": "실행 상세를 접었습니다.", "close_hint": "Ctrl+Q로 최소화하여 셸로 돌아갑니다.",

    "error": "오류: {error}", "not_connected": "백엔드 연결을 먼저 완료하세요.",

    "saved": "요청을 저장했습니다.", "instruction_saved": "추가 지시를 저장했습니다. 다음 처리 시점에 반영됩니다.",
    "draft_recovered": "전송하지 않은 입력은 입력창을 비운 뒤 ↑로 불러올 수 있습니다.",

    "empty_message": "메시지를 입력하세요.", "no_active_run": "추가 지시를 보낼 실행이 없습니다.",

    "no_targets": "이 실행은 현재 추가 지시를 받지 않습니다.",

    "choose_target": "추가 지시를 받을 대상",

    "engine_line": "엔진: {engine}", "create_dialog": "대화 생성 또는 복제",

    "create": "새로 생성", "clone": "선택한 대화 복제", "name": "이름 (선택)",

    "auto_name_hint": "비워 두면 응답 후 모델이 대화 이름을 짓습니다.",

    "confirm": "확인", "cancel": "취소", "clone_busy": "원본의 실행과 예약 요청을 마친 뒤 복제하세요.",

    "queued": "{status} · 예약 {count}", "waiting": "{icon_waiting} 모델 응답을 기다리는 중",

    "run_progress": "{status} · {engine} · {seconds}초 · 예약 {count}",

    "elapsed": "소요 {duration}", "elapsed_unknown": "소요 시간 알 수 없음",

    "duration_seconds": "{seconds}초", "duration_minutes": "{minutes}분", "duration_hours": "{hours}시간",

    "phase": "현재 단계: {name} ({status})",

    "paused": "{icon_paused} 일시 정지",

    "memory": "메모리 대화: 종료 시 메시지 소멸",

    "title_failed": "자동 이름 생성 실패: {error}", "unknown_command": "알 수 없는 명령: {command}",

    "command_help": "도움말", "command_engine": "엔진 선택", "command_new": "대화 생성",

    "command_clone": "선택한 대화 복제",

    "command_stop": "현재 실행 중단", "command_details": "출력 태그 바",

    "file": "파일", "directory": "폴더", "engine": "엔진",

    "background": "\n  Hub\n\n  Ctrl+Q: 대화로 돌아가기\n  Ctrl+C: 종료\n\n  Hub를 숨겨도 실행은 계속됩니다.\n",

    "help_title": "Hub 도움말",


    "status_idle": "{icon_idle} 유휴", "status_running": "{icon_running} 실행 중", "status_queued": "{icon_queued} 예약",

    "status_committed": "{icon_committed} 접수됨", "status_streaming": "{icon_streaming} 응답 수신 중", "status_completed": "{icon_completed} 완료",

    "status_interrupted": "{icon_interrupted} 중단됨", "status_cancelled": "{icon_cancelled} 취소됨", "status_failed": "{icon_failed} 실패",

    "status_paused": "{icon_paused} 일시 정지", "status_pending": "{icon_pending} 실행 전", "status_applied": "{icon_applied} 반영됨",

    "status_unapplied": "{icon_unapplied} 미반영", "status_partially_applied": "{icon_partially_applied} 일부 반영",

    'settings_icon_style': '아이콘 스타일',
    'settings_icon_description': 'Enter로 선택을 시작하고 방향키로 변경한 뒤 Enter로 완료합니다.',
    'settings_icon_unicode': '일반 (Unicode/이모지)',
    'shortcut_close': '닫기',
    'shortcut_move': '이동',
    'shortcut_next_match': '다음 결과',
    'shortcut_previous_match': '이전 결과',
    'shortcut_activate': '실행',
    'shortcut_select': '선택',
    'shortcut_confirm': '확정',
    'shortcut_delete': '삭제',
    'shortcut_clone': '복제',
    'shortcut_main': '메인 영역',
    'shortcut_width': '너비',
    'shortcut_create': '생성',
    'shortcut_rename': '이름',
    'shortcut_shell': '셸',
    'shortcut_chat': '대화',
    'shortcut_settings': '설정',
    'shortcut_done': '완료',
    'shortcut_edit': '편집',
    'shortcut_newline': '줄바꿈',
    'shortcut_editor': '에디터',
    'shortcut_items': '항목',
    'shortcut_regions': '영역',
    'shortcut_sidebar': '패널',
    'shortcut_save_chat': '저장 후 대화',
    'shortcut_engine': '엔진',
    'shortcut_find': '찾기',
    'shortcut_history': '요청 목록',
    'shortcut_logs': '로그',
    'shortcut_scroll': '스크롤',
    'shortcut_stop': '중단',
    'shortcut_details': '상세',
    'shortcut_help': '도움말',
    'shortcut_alt_movement': 'Alt + 이동키',
    'shortcut_minimize': '최소화',
    'shortcut_next_session': '다음 세션',
    'shortcut_send': '전송',
    'tools_builtin': '내장 도구',
    'shortcut_tools_toggle': '활성/비활성',
    'question_waiting': '답변 대기 중 · 질문',
    'question_sending': '답변 전달 중 · {title}',
    'question_details_hint': 'Ctrl+O 전체 내용 보기',
    'approval_heading': '{icon_question} 승인 요청',
    'question_heading': '{icon_question} 질문',
    'question_action_hint': 'Ctrl+O: 전체 작업 내용 확인 (인자·명령·파일 내용)',
    'shortcut_approval_details': '요청 상세',
    'approval_tool': '도구: {name}',
    'approval_risk': '위험도: {value}',
    'approval_model_reason': '모델 평가 근거: {reason}',
    'approval_model_risk': '모델 평가(참고): {level} · {reason}',
    'tool_history_more': '이전 도구 {count}건',
    'approval_argument': '{name}: {kind}, {size} (상세에서 확인)',
    'retry_provider': '모델 호출',
    'retry_tool': 'Tool {name}',
    'retry_repairing': '재시도 준비 중 · {target} · {next_attempt}/{maximum}번째 시도 · 수정안 생성 중',
    'retry_wait': '재시도 대기 중 · {target} · {attempt}/{maximum}회 시도 완료 · {next_attempt}번째 시도까지 {seconds}초',
    'retry_active': '재시도 중 · {target} · {attempt}/{maximum}번째 시도',
    'question_text_hint': '선택지 번호를 입력하거나 원하는 답변을 쓰고 Enter를 누르세요.',
    'question_free_text_hint': '원하는 답변을 쓰고 Enter를 누르세요.',
    'question_choice_hint': '선택지 번호를 입력하고 Enter를 누르세요. 추가 값이 필요하면 번호 뒤에 입력하세요.',
    "agents_import_hint": "Agent JSON 파일 경로 → Enter: 검증 후 가져오기",
    "agents_title": "Agent 관리",
}
