"""한국어 UI 문구."""

from hub.locales.keys import MessageKey



MESSAGES = {
    MessageKey.OPERATION_EXISTING_RAG_JOB: '같은 문서의 미완료 등록 작업이 있습니다. RAG 목록에서 해당 작업을 선택해 재시도하거나 삭제한 뒤 등록해 주세요.',
    MessageKey.OPERATION_DIAGNOSTICS: 'Hub 조작 오류 상세 · 현재 실행 중 기록',
    MessageKey.OPERATION_CHANGED: '처리 중 상태가 바뀌었습니다. 최신 내용을 불러온 뒤 다시 선택해 주세요. 작성 중인 내용은 유지됩니다.',
    MessageKey.OPERATION_INTERACTION: '재개 전에 승인이나 답변이 필요합니다. 입력창의 요청을 확인해 주세요. 거절한 작업은 자동 재실행하지 않습니다.',
    MessageKey.OPERATION_BUSY: '아직 작업이 실행 중입니다. 종료를 기다리거나 중단한 뒤 다시 시도해 주세요.',
    MessageKey.OPERATION_CONFIGURATION: '등록 당시와 RAG 설정이 달라 현재 작업을 이어갈 수 없습니다. 설정을 확인한 뒤 원문을 새로 등록해 주세요.',
    MessageKey.OPERATION_CHECKPOINT_UNAVAILABLE: '이어갈 실행 기록이 없거나 손상되었습니다. 로그에서 상세를 확인해 주세요. 이미 수행된 작업이 있을 수 있어 처음부터 자동 실행하지 않습니다.',
    MessageKey.OPERATION_ENGINE: '원래 사용한 엔진을 찾을 수 없습니다. 프로젝트의 엔진 등록과 설정을 확인해 주세요.',
    MessageKey.OPERATION_ALREADY_RESUMED: '이미 재개된 요청입니다. 요청 목록을 다시 열어 가장 최근 상태를 확인해 주세요.',
    MessageKey.OPERATION_RESTART_UNPROVEN: '처음부터 안전하게 재실행할 수 있는지 확인되지 않았습니다. 이전 작업 결과를 확인한 뒤 새 요청 여부를 결정해 주세요.',
    MessageKey.OPERATION_NOT_RESUMABLE: '현재 상태에서는 재개할 수 없습니다. 요청이나 등록 목록에서 최신 상태를 확인해 주세요. 자세한 원인은 로그에 남겨두었습니다.',
    MessageKey.OPERATION_ABANDON_REQUIRED: '재개에 필요한 기록이어서 바로 삭제할 수 없습니다. 삭제 화면에서 재개 포기 여부를 확인해 주세요.',
    MessageKey.OPERATION_FAILED: '요청한 작업을 처리하지 못했습니다. 입력과 현재 상태를 확인해 주세요. 자세한 오류는 로그 창의 Hub 조작 오류 상세에서 확인할 수 있습니다.',
    MessageKey.SYSTEM: '시스템',
    MessageKey.USAGE_DAILY_TITLE: '일별 토큰 사용량 · 최근 최대 30일',
    MessageKey.USAGE_DAILY_RANGE: '{start} ~ {end} · 프로젝트 통계 조회 범위 합산',
    MessageKey.USAGE_DAILY_UNKNOWN: '선택 기간 사용량 미보고 호출: {count}회 (토큰 합계 제외)',
    MessageKey.PROMPTS_TITLE: 'Prompt 관리',
    MessageKey.PROMPTS_IMPORT_HINT: 'Prompt JSON 파일 경로 → Enter: 검증 후 가져오기',
    MessageKey.DEFINITION_EDIT: '편집',
    MessageKey.DEFINITION_MANAGER_HINT: 'Tab 영역 · ↑↓ 선택 · Enter 팝업에서 편집 · d 삭제 · 하단 생성 버튼',
    MessageKey.DEFINITION_NAME: '이름 (영문·숫자·밑줄·하이픈, 1~64자)',
    MessageKey.DEFINITION_NAME_INVALID: '이름은 영문·숫자·밑줄·하이픈 1~64자로 지정하세요.',
    MessageKey.DEFINITION_OBJECT: 'JSON 객체를 입력하세요.',
    MessageKey.DEFINITION_TOO_LARGE: '팝업 편집은 최대 1,048,576자의 텍스트를 지원합니다.',
    MessageKey.DEFINITION_VALIDATION: '저장 시 백엔드가 정의 전체를 검증합니다. Tab으로 저장·취소 버튼을 선택하세요.',
    MessageKey.DEFINITION_SAVE_FAILED: '저장하지 못했습니다. 오류 안내를 확인하고 수정하세요. 입력 내용은 유지됩니다.',
    MessageKey.WORKFLOW_PREVIEW: '흐름도 미리보기',
    MessageKey.WORKFLOW_INVALID: '편집 중인 JSON으로 흐름도를 그릴 수 없습니다.',
    MessageKey.SHORTCUT_RESTART: '재시작',
    MessageKey.SHORTCUT_TABS: '탭 목록',
    MessageKey.SETTINGS_TAB_CONFIG: '설정',
    MessageKey.SETTINGS_TAB_POLICY: '정책',
    MessageKey.SETTINGS_TAB_EMPTY: '이 탭에 표시할 항목이 없습니다.',
    MessageKey.CANCEL_WAIT: '취소 요청을 전달했습니다. 실행 중인 호출과 저장 정리가 끝나기를 기다리고 있습니다. 현재 실행 방식에서는 셸과 다른 작업을 유지한 채 강제 종료할 수 없습니다.',
    MessageKey.RAG_PHASE_CANCELLING: '중단 중',
    MessageKey.SETTINGS_DESC_TITLE: '화면에 표시할 프로젝트 이름입니다.',
    MessageKey.SETTINGS_DESC_COMPONENTS: '이 프로젝트에서 사용할 컴포넌트 목록입니다.',
    MessageKey.SETTINGS_DESC_MODEL: '호출할 프로바이더 모델의 식별자입니다.',
    MessageKey.SETTINGS_DESC_API_BASE: '프로바이더 또는 호환 추론 서버의 기본 URL입니다.',
    MessageKey.SETTINGS_DESC_API_KEY: '선택한 프로바이더에 전달할 인증 키입니다.',
    MessageKey.SETTINGS_DESC_TEMPERATURE: '출력의 변화 정도를 조절하는 샘플링 온도입니다.',
    MessageKey.SETTINGS_DESC_MAX_TOKENS: '프로바이더에 요청할 최대 출력 토큰 수입니다.',
    MessageKey.SETTINGS_DESC_TIMEOUT: '작업에 허용할 최대 시간(초)입니다.',
    MessageKey.SETTINGS_DESC_SYSTEM_PROMPT: '이 엔진의 모델 요청에 포함할 기본 지침입니다.',
    MessageKey.SETTINGS_DESC_MAX_ATTEMPTS: '최초 시도를 포함한 최대 시도 횟수입니다.',
    MessageKey.SETTINGS_DESC_MAX_RETRIES: '최초 실패 이후 추가로 재시도할 최대 횟수입니다.',
    MessageKey.SETTINGS_DESC_DELAY_SECONDS: '시도 사이의 초기 대기 시간(초)입니다.',
    MessageKey.SETTINGS_DESC_MAX_DELAY_SECONDS: '시도 사이에 대기할 시간의 상한(초)입니다.',
    MessageKey.STOP: '중단',
    MessageKey.RESUME: '재개',
    MessageKey.COMPONENT_IMPORT: '추가',
    MessageKey.SHORTCUT_STOP_RESUME: '중단/재개',
    MessageKey.PRESET_TITLE: '프리셋',
    MessageKey.PRESET_ADD: '새 프리셋 추가하기',
    MessageKey.PRESET_EXPORT_CURRENT: '현재 설정을 프리셋으로 내보내기',
    MessageKey.PRESET_EXPORT_HINT: '현재 저장된 전역·프로젝트 설정을 내보냅니다. 이름을 입력하고 저장하세요. JSON 편집은 선택 사항입니다.',
    MessageKey.MEMORY_TITLE: '메모리 관리',
    MessageKey.SETTINGS_RESTART_WAITING: '작업 취소와 저장 정리를 기다리는 중입니다. 같은 프로세스의 도구가 응답하지 않으면 강제 종료할 수 없습니다. 정리가 끝나면 자동으로 재시작합니다.',
    MessageKey.PRESET_NAME: '프리셋 이름',
    MessageKey.PRESET_EDIT: 'JSON 편집',
    MessageKey.PRESET_APPLIED: '프리셋을 적용했습니다.',
    MessageKey.PRESET_DRAFTS: '편집 중인 설정을 먼저 저장하거나 취소하세요.',
    MessageKey.PRESET_HINT: '전역 설정과 프로젝트 설정을 포함합니다. JSON 편집 후 저장하세요.',
    MessageKey.SETTINGS_GROUP_GENERAL: '기본 설정',
    MessageKey.SETTINGS_GROUP_COMPLETION: '모델 호출',
    MessageKey.SETTINGS_GROUP_PROVIDER: '프로바이더',
    MessageKey.SETTINGS_GROUP_CONTEXT: '문맥',
    MessageKey.SETTINGS_GROUP_RUN: '실행',
    MessageKey.SETTINGS_GROUP_APPROVAL: '승인',
    MessageKey.SETTINGS_GROUP_TOOL_RETRY: '도구 재시도',
    MessageKey.SETTINGS_GROUP_USAGE: '사용량',
    MessageKey.SETTINGS_GROUP_RETENTION: '보관',
    MessageKey.SETTINGS_GROUP_OUTPUT: '출력',
    MessageKey.SETTINGS_GROUP_PROCESSING: '처리',
    MessageKey.SETTINGS_GROUP_INGESTION: '등록',
    MessageKey.SETTINGS_OPTION_DESCRIPTION: '{name}에 적용할 값입니다. 아래 타입과 범위 조건에 맞게 입력하세요.',
    MessageKey.SHORTCUT_AUXILIARY: '보조 버튼',
    MessageKey.SHORTCUT_RETURN: '돌아가기',
    MessageKey.ATTACH_TITLE: '파일 첨부',
    MessageKey.ATTACH_HINT: '파일 경로를 선택하면 입력창에 추가합니다. Tab으로 경로 자동완성을 확정하세요.',
    MessageKey.ATTACH_REQUIRED: '파일 경로를 입력하세요.',
    MessageKey.SETTINGS_JSON: '설정 JSON',
    MessageKey.SETTINGS_THEME_MODE: '테마',
    MessageKey.SETTINGS_THEME_MODE_DESCRIPTION: 'dark 또는 light. 자동으로 정해지는 배경과 글자색에 적용합니다.',
    MessageKey.CLIPBOARD_INTERNAL: '내부 클립보드',
    MessageKey.CLIPBOARD_TITLE: '클립보드',
    MessageKey.CLIPBOARD_COPY_UNAVAILABLE: '외부 복사 실패 · Hub 내부에만 저장됨',
    MessageKey.CLIPBOARD_READ_UNAVAILABLE: '외부 클립보드를 읽지 못했습니다. 터미널의 붙여넣기를 사용하세요.',
    MessageKey.CLIPBOARD_X11_LIBRARY: 'X11 클립보드 연결에 필요한 libX11을 찾지 못했습니다.',
    MessageKey.CLIPBOARD_X11_CONNECTION: 'X11 클립보드에 연결하지 못했습니다. DISPLAY와 X11 접근 권한을 확인하세요.',
    MessageKey.CLIPBOARD_X11_TIMEOUT: 'X11 클립보드 응답 시간이 초과되었습니다. 연결 상태를 확인하세요.',
    MessageKey.CLIPBOARD_X11_TRANSFER: 'X11 클립보드의 텍스트를 전달하지 못했습니다. 터미널의 붙여넣기를 사용할 수 있습니다.',

    MessageKey.PROFILE_SETTINGS: "설정", MessageKey.PROFILE_HOME: "홈", MessageKey.PROFILE_MINIMIZE: "최소화",
    MessageKey.SHORTCUT_PROFILE: "프로필", MessageKey.SHORTCUT_CATEGORIES: "카테고리",
    MessageKey.HISTORY_RETRY_QUESTION_UNAVAILABLE: "취소한 승인·질문은 실행·예약 작업이 없고 가장 최근 요청일 때만 다시 열 수 있습니다. 이전 요청은 편집으로 다시 시작하세요.",
    MessageKey.SETTINGS_DISCARD: "취소", MessageKey.SETTINGS_DISCARDED: "저장하지 않은 변경 내용을 취소했습니다.",
    MessageKey.SETTINGS_ENGINE_ITEM: "엔진 · {name}", MessageKey.SETTINGS_COMPONENT_ITEM: "컴포넌트 · {name}",
    MessageKey.PROJECT_DELETE_LAST: "마지막 프로젝트는 삭제할 수 없습니다. 먼저 다른 프로젝트를 만들어 주세요.",
    MessageKey.FAILURE_EVIDENCE: "진단 근거: {evidence}",
    MessageKey.FAILURE_DETAILS: "자세한 실행 기록은 보조 버튼의 로그에서 확인할 수 있습니다.",
    MessageKey.FAILURE_CHECKPOINT_CONFLICT: "작업 상태가 변경되었습니다. 최신 상태를 확인한 뒤 다시 시도하세요.",
    MessageKey.FAILURE_CHECKPOINT_PROTECTED: "현재 작업은 재개할 수 없습니다. 차단 사유를 확인하세요.",
    MessageKey.FAILURE_UNKNOWN: "저장된 기록만으로 정확한 원인을 확인할 수 없습니다. 이미 실행된 파일 변경이나 명령이 있는지 확인한 뒤 보조 버튼의 요청 목록에서 재시도할 수 있습니다. 같은 문제가 반복되면 상세 기록을 함께 전달해 주세요.",
    MessageKey.FAILURE_RATE_LIMIT: "모델 제공자가 사용량 또는 요청 속도 제한으로 요청을 거절했습니다. 잠시 기다린 뒤 다시 시도하고, 계속 발생하면 해당 제공자의 API 사용 한도·잔액·결제 상태를 확인해 주세요. 이 코드만으로 API 키가 잘못됐다고 판단할 수는 없습니다.",
    MessageKey.FAILURE_UNAVAILABLE: "모델 제공자에 연결하거나 응답을 받는 과정에서 오류가 발생했습니다. 제공자 서비스 상태를 확인하고 잠시 후 다시 시도해 주세요. 반복되면 다른 사용 가능한 모델로 변경할 수 있습니다.",
    MessageKey.FAILURE_AUTHENTICATION: "모델 제공자가 인증을 거절했습니다. 선택한 모델에 맞는 제공자의 API 키가 설정되어 있는지, 키가 만료되거나 폐기되지 않았는지 확인해 주세요.",
    MessageKey.FAILURE_PERMISSION: "필요한 접근 권한이 없어 작업을 완료하지 못했습니다. 모델·프로젝트의 사용 권한 또는 대상 파일·디렉터리 권한을 확인해 주세요.",
    MessageKey.FAILURE_TIMEOUT: "정해진 시간 안에 작업을 완료하지 못했습니다. 네트워크와 제공자 상태를 확인하고, 작업을 나누거나 설정의 제한 시간을 검토해 주세요. 재시도 전 이미 수행된 작업도 확인하세요.",
    MessageKey.FAILURE_CONNECTION: "네트워크 연결을 완료하지 못했습니다. 인터넷 연결, DNS, 프록시와 방화벽 설정을 확인해 주세요.",
    MessageKey.FAILURE_REQUEST: "모델 제공자가 요청 형식이나 대상을 받아들이지 않았습니다. 모델 이름·API 주소·지원되는 옵션을 확인하고, 큰 입력이나 첨부가 있다면 줄여서 시도해 주세요.",
    MessageKey.FAILURE_CONTEXT: "모델이 한 번에 처리할 수 있는 대화 길이를 초과했습니다. 입력을 나누거나 새 세션에서 필요한 내용만 전달해 주세요.",
    MessageKey.FAILURE_BUDGET: "프로젝트에 설정된 사용량 한도에 도달했습니다. 사용 통계와 사용량 정책을 확인하고, 필요하다면 한도를 조정해 주세요.",
    MessageKey.FAILURE_FILE_MISSING: "작업에 필요한 파일이나 디렉터리를 찾지 못했습니다. ish의 현재 디렉터리와 요청한 경로가 맞는지 확인해 주세요.",
    MessageKey.FAILURE_EMPTY: "모델 제공자가 비어 있거나 처리할 수 없는 응답을 반환했습니다. 잠시 후 다시 시도하고, 반복되면 모델 이름과 제공자 연결 설정을 확인해 주세요.",
    MessageKey.FAILURE_COMPLETION_INVALID: "모델 응답의 형식이 맞지 않아 처리를 완료하지 못했습니다. 제공자·서버의 스트리밍 및 Tool 호출 호환성을 확인해 주세요. 받은 응답은 유지되며 자동으로 다시 실행하지 않습니다.",
    MessageKey.FAILURE_COMPLETION_LENGTH: "모델이 출력 토큰 한도에 도달해 답변을 마치지 못했습니다. 모델의 출력 토큰 설정을 확인하거나 작업을 나누어 요청해 주세요. 받은 응답은 유지됩니다.",
    MessageKey.FAILURE_COMPLETION_FILTERED: "모델 제공자의 콘텐츠 필터로 응답이 종료되었습니다. 요청 내용과 제공자의 이용 정책을 확인해 주세요. 같은 요청을 자동으로 반복하지 않습니다.",
    MessageKey.FAILURE_COMPLETION_INCOMPLETE: "모델 응답이 정상적으로 끝나지 않았습니다. 받은 응답은 유지됩니다. 로그의 종료 사유와 제공자 상태를 확인한 뒤 재시도 여부를 결정해 주세요.",
    MessageKey.FAILURE_COMPLETION_OUTPUT_LIMIT: "프로젝트에 설정한 출력 문자 한도에 도달했습니다. 엔진의 출력 크기 제한을 검토하거나 작업을 나누어 요청해 주세요.",
    MessageKey.FAILURE_COMPLETION_TOOL_LIMIT: "모델이 한 번에 요청한 Tool 수가 설정된 한도를 넘었습니다. 엔진의 Tool 호출 수 제한을 검토하거나 작업을 나누어 요청해 주세요.",
    MessageKey.FAILURE_COMPLETION_ARGUMENT_LIMIT: "모델이 만든 Tool 인자 또는 호출 ID가 설정된 길이 한도를 넘었습니다. 엔진의 Tool 인자 크기 제한을 검토하거나 작업을 나누어 요청해 주세요.",
    MessageKey.FAILURE_STEP_TIMEOUT: "작업 단계가 설정된 제한 시간을 넘었습니다. 해당 엔진의 단계별 제한 시간을 검토해 주세요. 재시도 전 이미 실행된 작업을 확인하세요.",
    MessageKey.FAILURE_REQUEST_SIZE: "서버가 요청 크기를 받아들이지 않았습니다. 입력이나 첨부 크기를 줄인 뒤 다시 시도해 주세요.",
    MessageKey.FAILURE_DEPENDENCY: "필요한 라이브러리를 불러오지 못했습니다. 실행 환경에 설치된 의존성을 확인해 주세요.",
    MessageKey.FAILURE_COMMAND: "명령이 오류로 종료되었습니다. 명령 출력과 이미 변경된 내용을 확인한 뒤 재시도해 주세요.",
    MessageKey.FAILURE_TOOL_FAILED: "Tool 작업을 완료하지 못했습니다. 상세 오류와 이미 수행된 작업을 확인한 뒤 재시도해 주세요.",
    MessageKey.FAILURE_VALIDATION: "입력한 값이 요구 조건에 맞지 않습니다. 값을 수정한 뒤 다시 시도해 주세요.",
    MessageKey.FAILURE_DISCONNECTED: "Hub가 백엔드에 연결되지 않았습니다. 백엔드가 준비된 뒤 Hub를 다시 열어 주세요.",
    MessageKey.FAILURE_TOOL_ARGUMENTS: "Tool 이름이나 인자가 요구 조건에 맞지 않습니다. 요청한 작업과 값을 확인해 주세요.",
    MessageKey.FAILURE_TOOL_CONTRACT: "Tool 실행에 필요한 조건을 충족하지 못했습니다. 실행기·격리·작업 키 설정을 확인해 주세요.",
    MessageKey.FAILURE_LIFECYCLE: "현재 작업은 더 이상 활성 상태가 아닙니다. 상태를 다시 확인한 뒤 요청해 주세요.",
    MessageKey.FAILURE_SANDBOX: "필요한 샌드박스를 사용할 수 없습니다. 설치와 설정을 확인해 주세요. 보호 기능을 자동으로 해제하지 않습니다.",
    MessageKey.FAILURE_OPERATION_CONFLICT: "해당 작업 키가 다른 Tool 또는 인자에 이미 사용되었습니다. 기존 작업을 확인한 뒤 진행해 주세요.",
    MessageKey.FAILURE_OPERATION_UNCERTAIN: "작업이 이미 적용되었을 수 있습니다. 실제 결과를 확인한 뒤 진행 여부를 결정해 주세요. 자동으로 다시 실행하지 않습니다.",
    MessageKey.GENERAL_CATEGORY_DIAGNOSTICS: "Provider 진단",
    MessageKey.GENERAL_PROVIDER_REQUEST_SUMMARY: "요청의 메시지 역할·길이·Tool 호출과 결과의 대응 개수를 기록합니다. 본문과 Tool 인자는 기록하지 않습니다. 기본값 true. Hub 재시작 후 적용.",
    MessageKey.GENERAL_CATEGORY_RECOVERY: "오류 분석과 복구",
    MessageKey.GENERAL_RECOVERY_ENABLED: "응답 수신 전 컨텍스트 오류를 축약하여 복구합니다. Loop의 Provider 시도 횟수·기한 안에서만 재호출합니다. 기본값 true. Hub 재시작 후 적용.",
    MessageKey.GENERAL_RECOVERY_MODEL_ANALYSIS: "현재 모델에 짧은 오류 정보로 원인과 조치를 묻습니다. 추가 API 호출이 발생하며 도구 실행 권한은 제공하지 않습니다. 기본값 true.",
    MessageKey.GENERAL_RECOVERY_TIMEOUT_SECONDS: "오류 분석·요약에 사용할 시간(초). 기본값 60. 보정 재호출은 원래 Provider 기한을 따르며 기존 실행 기한을 늘리지 않습니다.",
    MessageKey.GENERAL_RECOVERY_INPUT_CHARS: "분할 요약 한 번에 전달할 이전 요약과 원문 조각의 문자 예산. 기본값 12000. 토큰 수가 아닙니다.",
    MessageKey.GENERAL_RECOVERY_SUMMARY_CHARS: "입력 축약 요약의 최대 문자 수. 기본값 2000. 현재 요청과 추가 지시 원문은 유지합니다.",
    MessageKey.GENERAL_RECOVERY_MAX_SUMMARY_CALLS: "한 번의 복구에서 허용할 요약 모델 호출 수. 기본값 16. 전체를 요약하지 못하면 부분 결과로 재시도하지 않습니다.",
    MessageKey.FAILURE_SERVER_DETAIL: "서버 오류 내용",
    MessageKey.FAILURE_MODEL_ANALYSIS: "모델의 오류 분석 · 추정이 포함될 수 있습니다",
    MessageKey.RECOVERY_REJECTED: "복구 제안 거절 · {attempt}/{maximum}",
    MessageKey.RECOVERY_RESULT_REJECTED: "백엔드가 입력 보정 제안을 허용하지 않아 원래 오류와 함께 중단했습니다.",
    MessageKey.RECOVERY_ANALYZING: "오류 원인 분석 중 · 복구 {attempt}/{maximum}",
    MessageKey.RECOVERY_COMPACTING: "긴 입력 요약 중 · 복구 {attempt}/{maximum}{progress}",
    MessageKey.RECOVERY_RETRYING: "입력을 축약하여 다시 요청 중 · 복구 {attempt}/{maximum}",
    MessageKey.RECOVERY_RESULT_NO_CORRECTION: "자동으로 적용할 수 있는 수정안을 찾지 못했습니다. 위 안내에 따라 설정이나 입력을 확인해 주세요.",
    MessageKey.RECOVERY_RESULT_SUMMARY_INCOMPLETE: "설정한 예산 안에 전체 요약을 완료하지 못해 재시도하지 않았습니다. 원본은 유지됩니다. 입력을 나누거나 복구 예산을 조정해 주세요.",
    MessageKey.RECOVERY_RESULT_NOT_REDUCED: "요약해도 입력 크기가 줄지 않아 재시도하지 않았습니다. 현재 요청이나 도구 정의의 크기를 확인해 주세요.",
    MessageKey.RECOVERY_RESULT_INVALID_ANALYSIS: "모델의 분석 형식이 올바르지 않아 자동 복구를 중단했습니다. 원래 오류 내용을 확인해 주세요.",
    MessageKey.RECOVERY_RESULT_ANALYSIS_FAILED: "오류 분석 또는 요약을 완료하지 못했습니다. 원래 오류와 작업 기록은 유지됩니다.",
    MessageKey.RECOVERY_RESULT_EXHAUSTED: "설정한 자동 복구 횟수 안에 해결하지 못했습니다. 원본과 완료된 작업은 보존됩니다.",
    MessageKey.GENERAL_PROVIDER_MAX_DETAIL_CHARS: "상세 오류 문자열 하나당 최대 문자 수. 기본값 2000, 1 이상의 정수. 상세 오류 기록을 켰을 때 사용하며 Hub 재시작 후 적용.",
    # Accepted only for saved language packs; no composer preview UI/binding remains.
    MessageKey.PREVIEW: "마크다운 미리보기",
    MessageKey.COMMAND_PREVIEW: "마크다운 미리보기 전환",
    MessageKey.SHORTCUT_PREVIEW: "미리보기",
    MessageKey.HISTORY_REQUESTS: "요청 목록",
    MessageKey.SETTINGS_CURRENT_PROJECT: "프로젝트 : {title}",
    MessageKey.SETTINGS_CLOSE: "닫기",
    MessageKey.SHORTCUT_PASTE: "붙여넣기",
    MessageKey.OBJECT_COPY_CODE: "코드 복사",
    MessageKey.OBJECT_COPY_CELL: "셀 복사",
    MessageKey.SETTINGS_REQUIRES_RESTART: "재시작 필요",
    MessageKey.SETTINGS_RESTART_TITLE: "Hub 재시작",
    MessageKey.SETTINGS_RESTART_NOW: "재시작",
    MessageKey.SETTINGS_RESTART_CONFIRM: "저장되었습니다. 다음 설정을 적용하려면 Hub 재시작이 필요합니다.\n\n{fields}\n\n지금 재시작하시겠습니까?\nEnter: 재시작 · Esc: 나중에 (저장값 유지)",
    MessageKey.SETTINGS_RESTARTING: "Hub 재시작 중",
    MessageKey.GENERAL_CATEGORY_STORAGE: "데이터 저장 위치",
    MessageKey.GENERAL_WORKSPACE_ROOT: "Hub 데이터 루트. 절대 경로 또는 ~/경로를 입력하세요. 저장 후 Hub를 재시작하면 적용됩니다. 기존 데이터는 자동으로 이동하지 않으며, 빈 폴더는 새 작업 공간으로 시작합니다. 비우면 시작 시 지정한 기본 경로를 사용합니다.",
    MessageKey.OBJECT_QUERY: '검색 / 필터:',
    MessageKey.OBJECT_RAW: '원문',
    MessageKey.OBJECT_RENDERED: '렌더링',
    MessageKey.OBJECT_EMPTY: '일치하는 행이 없습니다.',
    MessageKey.OBJECT_LOADING: '표를 준비하는 중…',
    MessageKey.OBJECT_TABLE_COUNT: '행 {index}/{visible} · 전체 {total}행 · 열 {column}',
    MessageKey.SHORTCUT_OBJECT_RAW: '원문/화면',
    MessageKey.SHORTCUT_OBJECT_SORT: '열 정렬',
    MessageKey.SHORTCUT_CANCEL: '예약 취소',
    MessageKey.PREPARING_RESPONSE: '준비 중…',
    MessageKey.STATUS_PREPARING: '{icon_preparing} 준비 중',
    MessageKey.STATUS_THINKING: '{icon_reasoning} 생각 중',
    MessageKey.STATUS_RETRYING: '{icon_preparing} 재시도 중',
    MessageKey.STATUS_INTERRUPTING: '{icon_interrupted} 중단 중',
    MessageKey.FOLLOWUP_HINT: '→ 추천 입력',
    MessageKey.COMPONENT_ACTION_INSTALL: '내장 스킬 설치 (기존 항목 유지)',
    # Keep the key for saved language packs; the removed install command is not advertised.
    MessageKey.SKILLS_INSTALL_HELP: '/skills config\n관리 창에서 내장 스킬 Markdown 파일의 경로를 입력하여 가져옵니다. 기존 ID는 덮어쓰지 않습니다.',
    MessageKey.HISTORY_CANCEL: '예약 취소',
    MessageKey.HISTORY_CANCEL_CONFIRM: '선택한 예약 요청을 취소할까요? 현재 실행 중인 작업은 계속됩니다.',
    MessageKey.HISTORY_CANCEL_UNAVAILABLE: '예약 상태의 요청만 취소할 수 있습니다. 이미 실행 중이면 Ctrl+D로 중단하세요.',
    MessageKey.HISTORY_CHECKPOINT_PROTECTED: '미완료 작업의 재개 체크포인트가 이 대화를 참조합니다. 삭제를 다시 선택하여 영향받는 작업의 재개 포기를 확인하세요.',
    MessageKey.HISTORY_ABANDON_DELETE: '재개 포기 후 삭제',
    MessageKey.HISTORY_DELETE_CHANGED: '삭제 확인 이후 관련 작업이나 대화가 변경되었습니다. 삭제를 다시 선택하여 확인하세요.',
    MessageKey.HISTORY_ABANDON_CONFIRM: '이 대화를 참조하는 아래 {count}개 작업의 재개를 포기하고 삭제할까요?\n이미 수행된 작업은 되돌리지 않습니다.\n대화는 이후 문맥에서 제외되며 실행 기록과 체크포인트는 보존됩니다.',

    MessageKey.SHORTCUT_MOVEMENT: '이동키',
    MessageKey.SHORTCUT_CLEAR: '비우기',
    MessageKey.SHORTCUT_TAGS: '태그 바',
    MessageKey.SHORTCUT_EXPAND: '확대',
    MessageKey.SHORTCUT_COPY: '원문 복사',
    MessageKey.SHORTCUT_OPEN_DIRECTORY: '폴더 열기',
    MessageKey.SETTINGS_SEARCH: '설정 검색',
    MessageKey.SETTINGS_SEARCH_HINT: '키·설명·값에 포함된 문자열로 검색합니다. 빈칸이면 전체 표시.',
    MessageKey.SETTINGS_SEARCH_EMPTY: '일치하는 설정이 없습니다.',
    MessageKey.SETTINGS_SEARCH_ACTIVE: '필터 : {query}',
    MessageKey.CLIPBOARD_COPIED: '원문을 복사했습니다.',
    MessageKey.CLIPBOARD_TERMINAL: '터미널에 복사를 요청했습니다(OSC 52). 앱 클립보드에도 저장했습니다.',
    MessageKey.COMPONENT_ACTION_CONFIG: '컴포넌트 관리 창',
    MessageKey.COMPONENT_MANAGER_UNAVAILABLE: '{name} 관리 창은 아직 지원하지 않습니다. settings 또는 데이터 명령을 사용하세요.',
    MessageKey.TAGS_TITLE: '태그 바',
    MessageKey.TAGS_EMPTY: '출력 객체가 없습니다.',
    MessageKey.TOOLS_TITLE: 'Tool 패키지 관리',
    MessageKey.SKILLS_TITLE: 'Skill 관리',
    MessageKey.MCP_TITLE: 'MCP 서버 관리',
    MessageKey.RAG_TITLE: 'RAG 문서 관리',
    MessageKey.WORKFLOWS_TITLE: 'Workflow 관리',
    MessageKey.SKILLS_IMPORT_HINT: '표준 Skill 디렉터리 또는 SKILL.md 경로 → Enter: 가져오기',
    MessageKey.MCP_IMPORT_HINT: 'MCP 서버 JSON 파일 경로 → Enter: 검증 후 가져오기',
    MessageKey.RAG_IMPORT_HINT: '텍스트·Markdown·PNG·JPEG 경로 → Enter: 등록 (이미지는 OCR/모델 분석 선택)',
    MessageKey.WORKFLOWS_IMPORT_HINT: 'Workflow JSON 파일 경로 → Enter: 검증 후 가져오기',
    MessageKey.MANAGER_HINT: 'Tab 영역 · ↑↓ 선택 · Enter 편집기에서 수정 · d 삭제',
    MessageKey.MANAGER_EMPTY: '등록된 항목이 없습니다.',
    MessageKey.MANAGER_IMPORTED: '가져왔습니다.',
    MessageKey.MANAGER_SAVED: '저장되었습니다.',
    MessageKey.RAG_PHASE_QUEUED: '등록 대기',
    MessageKey.RAG_PHASE_PREPARING: '등록 준비',
    MessageKey.RAG_PHASE_EMBEDDING: '임베딩',
    MessageKey.RAG_PHASE_VALIDATING_INPUT: '입력 검증',
    MessageKey.RAG_PHASE_SPLITTING: '청크 분할',
    MessageKey.RAG_PHASE_EXTRACTING: '관계 추출',
    MessageKey.RAG_PHASE_VALIDATING: '색인 검증',
    MessageKey.RAG_PHASE_WRITING_METADATA: '메타데이터 저장',
    MessageKey.RAG_PHASE_PUBLISHING: '색인 공개',
    MessageKey.RAG_PHASE_PREPARING_DOCUMENTS: '문서 준비',
    MessageKey.RAG_PHASE_IMAGE_OCR: '이미지 문자 추출',
    MessageKey.RAG_PHASE_IMAGE_ANALYSIS: '이미지 모델 분석',
    MessageKey.RAG_PHASE_INDEXING: '색인 저장',
    MessageKey.RAG_PHASE_COMPLETED: '등록 완료',
    MessageKey.RAG_PHASE_FAILED: '등록 실패',
    MessageKey.RAG_PHASE_CANCELLED: '취소됨',
    MessageKey.RAG_PHASE_UNCONFIRMED: '실행 상태 확인 필요',
    MessageKey.RAG_JOB_COUNTS: '임베딩 {done}/{total}청크 · 체크포인트 재사용 {reused} · 관계 추출 {batches}배치',
    MessageKey.RAG_JOB_TIMES: '임베딩 {embedding}초 · 관계 추출 {extraction}초 · 이번 시도 경과 {elapsed}초',
    MessageKey.RAG_JOB_RETRY_HINT: '재시도: 완료한 청크를 재사용합니다. 실제 실행 중이면 재개하지 않습니다.',
    MessageKey.RAG_JOB_RETRY_CHECK: "재시도를 누르면 현재 재개 가능 여부를 확인합니다.",
    MessageKey.RAG_JOB_READONLY: '실패·취소 작업은 삭제할 수 있습니다. 취소된 작업은 재개할 수 없습니다.',
    MessageKey.RAG_STAGE_COUNTS: '현재 단계 {done}/{total}{unit}',
    MessageKey.RAG_UNIT_CHUNK: '청크',
    MessageKey.RAG_UNIT_BATCH: '배치',
    MessageKey.RAG_UNIT_DOCUMENT: '문서',
    MessageKey.RAG_UNIT_JOB: '작업',
    MessageKey.RAG_OBSERVED_TIMES: '관찰 시간: ',
    MessageKey.RAG_CANCEL_JOB: '작업 취소',
    MessageKey.RAG_CANCEL_UNAVAILABLE: '대기 또는 실행 중인 등록 작업을 선택하세요.',
    MessageKey.RAG_DELETE_UNAVAILABLE: '종료된 실패·취소 작업만 삭제할 수 있습니다. 실행 중이면 먼저 취소하세요.',
    MessageKey.RAG_DELETE_CONFIRM: '{name}의 원문·체크포인트({bytes}바이트)를 삭제할까요?\n삭제 후 재개할 수 없습니다. 완료된 문서·색인은 유지됩니다.',
    MessageKey.RAG_IMAGE_TITLE: '이미지 문서 등록',
    MessageKey.RAG_IMAGE_ANALYSIS: '모델 분석',
    MessageKey.RAG_VISION_SETTINGS: 'Vision 설정',
    MessageKey.RAG_IMAGE_HINT: 'OCR: vision에 설정한 OCR로 문자를 추출합니다.\n모델 분석: vision의 이미지 지원 모델로 표·그림을 해석합니다. 추가 호출 비용이 발생할 수 있습니다.\nVision 설정 버튼은 해당 컴포넌트를 활성화하고 설정 페이지를 엽니다.\n원본 이미지 출처와 추출 방식을 문서에 함께 보존합니다.',
    MessageKey.RAG_RETRY_UNAVAILABLE: '실행 중이 아닌 미완료 등록 작업을 선택하세요. 완료·취소된 작업은 재시도할 수 없습니다.',
    MessageKey.MANAGER_DELETE_CONFIRM: '{name}을 삭제할까요?',
    MessageKey.TOOLS_IMPORT_HINT: '디렉토리 경로 → Enter: <이름>/<이름>.py와 선택적인 requirements.txt 가져오기',
    MessageKey.TOOLS_COLUMNS: '이름 / 마지막 수정 / 설명',
    MessageKey.TOOLS_HINT: 'Tab 영역 · ↑↓ 선택 · Space 내장 도구 활성/비활성 · Enter 폴더 · d 삭제',
    MessageKey.TOOLS_EMPTY: '등록된 Tool이 없습니다.',
    MessageKey.TOOLS_IMPORTED: 'Tool을 가져왔습니다. 사용 전 의존성 준비와 활성화는 별도로 해주세요.',
    MessageKey.TOOLS_FAILED: '처리에 실패했습니다. 오류 알림을 확인하세요.',
    MessageKey.TOOLS_EXPLORER_FAILED: '파일 탐색기 종료 코드: {code}',
    MessageKey.TOOLS_DELETE_CONFIRM: '{name}을 삭제할까요? 활성화되어 있다면 함께 비활성화합니다.',
    MessageKey.WELCOME_KEYS: 'ESC 패널 · 패널에서 c 새 세션 · Ctrl+S 보조 버튼',
    MessageKey.SESSION_REQUIRED: '먼저 세션을 생성하세요. ESC 후 세션 목록에서 c를 누르세요.',
    MessageKey.ACTIVITY_HINT: '활동·프로바이더 각각 최근 300건 · 이동키 스크롤 · ESC / Enter / Ctrl+L 닫기',
    MessageKey.ACTIVITY_PROVIDER_PROJECT: '프로바이더 로그 · 현재 프로젝트',
    MessageKey.ACTIVITY_PROVIDER_FILE: '로그 파일: {path}',
    MessageKey.ACTIVITY_PROVIDER_TRACEBACK: 'Traceback · 저장된 예외와 호출 경로 (오류 메시지 원문 제외)',
    MessageKey.ACTIVITY_PROVIDER_RUN_DETAILS: '이 Run의 프로바이더 상세:',
    MessageKey.ACTIVITY_PROVIDER_WORKSPACE: '프로바이더 로그 · 워크스페이스 공통',
    MessageKey.STATUS_WAITING_INPUT: '{icon_pending} 답변 대기',
    MessageKey.STATUS_WAITING_APPROVAL: '{icon_waiting_approval} 승인 대기',
    MessageKey.STATUS_ANSWER_SENDING: '{icon_waiting} 답변 전달 중',
    MessageKey.STATUS_APPROVED: '{icon_completed} 승인 완료',
    MessageKey.STATUS_DENIED: '{icon_cancelled} 거절됨',
    MessageKey.STATUS_ANSWERED: '{icon_completed} 답변 전달 완료',
    MessageKey.STATUS_RESUMING: '{icon_running} 이어서 실행',
    MessageKey.HELP: '## 패널과 홈\n\n- **ESC**: 메인에서 패널로 이동.\n- **Ctrl+Q**: 셸에서 Hub 열기, Hub의 모든 화면에서 최소화. 셸의 Ctrl+S는 변경하지 않습니다.\n- **Tab / Shift+Tab**: 패널의 카테고리 이동. **↑↓**: 현재 카테고리 안에서 이동합니다. **Space / Enter / →**: 메인으로 이동.\n- **Ctrl+← / Ctrl+→**: 패널 너비 조절 및 너비만 자동 저장.\n- **Ctrl+P**: 패널 하단 프로필 영역으로 이동합니다. 홈/설정 버튼과 설정 프리셋을 표시합니다. **←→**: 버튼 선택. **Enter / Space**: 실행. 프로필의 Tab은 동작하지 않습니다.\n- 홈 위쪽 프로젝트 목록에서 프로젝트를 선택하고, 아래쪽 세션 목록에서 세션을 선택합니다.\n- **c / d / e / r**: 홈 패널에서 생성 / 삭제 / 이름 변경 / 복제.\n- **Enter**: 요청 전송. 실행 중에는 추가 지시 또는 후속 요청 선택.\n- 빈 입력창의 **↑↓**: 이전/다음 사용자 요청 불러오기. 내용을 수정하면 히스토리 탐색을 종료합니다.\n- **Ctrl+Space**: 줄바꿈. **↑↓**: 자동완성 선택, **Tab**: 확정.\n- **Ctrl+W**: Hub의 모든 화면에서 재시작. 실행 중인 작업이 있으면 안내 후 재시작을 보류합니다.\n- **Ctrl+D**: 홈 입력창에서 현재 실행 또는 일시 정지된 요청 중단 / 마지막 중단 작업 재개. 예약 요청은 유지.\n- `/agent <엔진명>`으로 엔진 지정.\n- **Ctrl+S**: 입력창 또는 설정 버튼 영역에서 보조 버튼으로 이동. **←→**: 선택. **↑↓**: 동작 없음. **Enter / Space**: 실행. **ESC**: 원래 영역으로 복귀.\n- **보조 버튼 → 찾기**: 세션 내용 찾기. Enter 다음 / Alt+P 이전. 닫으면 강조 해제.\n- **보조 버튼**: 요청 목록 / 태그 바 / 찾기 / 파일 첨부 / 로그 / 설정. 파일 첨부는 선택한 경로를 입력창에 추가합니다. 설정 버튼은 Loop 엔진 설정을 엽니다.\n\n## 스크롤과 팝업\n\n대화 출력은 **Alt + 이동키**로 스크롤합니다.\n도움말·로그·조회 결과 팝업에서는 **이동키만** 사용합니다.\n이동키는 ↑↓←→ / Home / End / PgUp / PgDn 전체입니다.\n읽기 팝업은 **ESC / Enter / Ctrl+L**로 닫습니다.\n\n## 출력 태그 바\n\n**보조 버튼 → 태그 바**로 열고 **↑↓** 키로 객체를 선택하면 출력의 해당 위치로 이동합니다.\n**c**로 원문 복사. **ESC**로 닫습니다. **Tab / Space / Enter**로 전체 화면 팝업을 엽니다.\n코드·표·이미지·diff와 등록된 XML 렌더러 출력이 표시됩니다.\n\n## 설정\n\n- **필터 보조 버튼 / Ctrl+F**: 키·설명·값 필터. 패널 항목을 바꾸어도 유지됩니다. 빈 검색어로 해제합니다. **JSON 보조 버튼**: 현재 설정 초안을 등록된 에디터에서 JSON으로 편집합니다. 종료 후 초안에 반영되며 저장 버튼으로 적용합니다.\n- **저장**: 전역 설정과 현재 프로젝트 변경 적용. **취소**: 저장하지 않은 변경 되돌리기. 둘 다 설정 화면을 유지하며 홈 버튼으로 돌아갑니다.\n- 현재 프로젝트만 편집합니다. 패널에서 일반·엔진·컴포넌트 항목을 선택하세요.\n- **Tab / Shift+Tab**: 항목 / 버튼 영역 이동. **↑↓**: 설정 항목 선택. 버튼은 **←→** 키로 선택합니다. 설정 항목에서 편집 중이 아닐 때 **←**: 패널 복귀, **→**: 동작 없음.\n- **Enter**: 직접 편집 시작(끝으로 이동) / 완료.\n- 항목 선택 상태에서 **e**: 외부 에디터, **d**: 값 전체 비우기.\n- 편집 중 **Ctrl+Space / Alt+Enter**: 줄바꿈.\n- 선택형 값은 Enter → 방향키 → Enter로 변경합니다.\n- 설정 메인 영역에서도 **Alt + 이동키**로 스크롤합니다.\n\n## 명령\n\n`/help`, `/agent`, `/new`, `/clone`, `/details`, `/stop`.\n등록된 컴포넌트만 `/` 자동완성에 나옵니다.\n`/tools config`: 내장 도구 활성/비활성 및 패키지 관리.\n질문이 오면 기존 입력창에서 답변 또는 선택지 번호를 입력하고 Enter로 보냅니다.\n영역을 선택하고 **Ctrl+C**로 복사합니다. 선택이 없으면 입력창의 Ctrl+C는 아무 동작도 하지 않습니다. **Ctrl+V**로 시스템 클립보드를 붙여넣습니다. 편집 가능한 입력창의 **Ctrl+A**는 전체 선택, **Ctrl+X**는 잘라내기, **Ctrl+Z / Ctrl+Y**는 실행 취소 / 다시 실행입니다. **Shift+이동키** 또는 마우스 드래그로 선택합니다.\n파일 경로 자동완성은 텍스트를 삽입하며 파일을 자동 첨부하지 않습니다.\n',
    MessageKey.SETTINGS_DEPENDENCIES: "필수 컴포넌트: {names}",
    MessageKey.SETTINGS_DEPENDENCIES_MISSING: "선택되지 않은 필수 컴포넌트: {names} — 선택 후 저장하세요.",
    MessageKey.TITLE_MODEL_REQUIRED: "자동 제목에 사용할 모델이 없습니다. 설정에서 대화 엔진의 모델을 지정하세요.",
    MessageKey.OUTPUT_IMAGE_LIMIT: "현재 화면의 이미지 미리보기 한도(32개)에 도달했습니다.",
    MessageKey.WELCOME_TITLE: "아직 세션이 없습니다",
    MessageKey.WELCOME_HINT: "새 세션을 만들어 대화를 시작하세요.",
    MessageKey.OUTPUT_PENDING: "출력 블록 수신 중…",
    MessageKey.OUTPUT_IMAGE: "이미지 · {caption}",
    MessageKey.OUTPUT_IMAGE_LOADING: "이미지를 변환하는 중…",
    MessageKey.OUTPUT_ERROR: "출력을 표시할 수 없습니다: {error}",
    MessageKey.PROGRESS_MORE: " · 외 {count}개",
    MessageKey.PROGRESS_SETTINGS_LOAD: "설정 불러오는 중",
    MessageKey.PROGRESS_SETTINGS_WORK: "설정 처리 중",
    MessageKey.PROGRESS_COMPONENT: "{command} 처리 중",
    MessageKey.PROGRESS_COMPONENT_DONE: "{identifier} 처리 완료",
    MessageKey.SETTINGS_STORAGE_DESCRIPTION: "대화 저장 방식. 새 프로젝트에서 비우면 file로 저장합니다. Session이 있으면 변경할 수 없으며, null은 주입 저장소 전용입니다.",
    MessageKey.SETTINGS_REASONING_EFFORT: "모델의 추론 강도. 예: low, medium, high. 지원값은 모델마다 다르며, 빈칸은 기본값을 사용합니다.",
    MessageKey.SEND_WHILE_RUNNING: '실행 중 전송 방식',

    MessageKey.SEND_STEERING: '실행 중 추가 지시 — 현재 작업에 반영',

    MessageKey.SEND_FOLLOW_UP: '후속 지시 — 현재 작업이 끝난 뒤 실행',

    MessageKey.INSTRUCTION_RUN_CHANGED: '대상 실행이 변경되었습니다. 입력을 보존했으니 전송 방식을 다시 선택하세요.',

    MessageKey.MODEL_REQUIRED: '설정 보조 버튼 → 엔진 · loop에서 config.parameters.engines.loop.config.completion.model을 지정하세요.',



    MessageKey.HISTORY_TITLE: "요청·작업 목록", MessageKey.HISTORY_EMPTY: "저장된 요청이 없습니다.",

    MessageKey.HISTORY_HINT: "↑↓ 이동 · Space 체크 · d 삭제 · e 편집 · r 재시도 · Tab 영역 이동",

    MessageKey.HISTORY_RESPONSE: "응답", MessageKey.HISTORY_DELETE: "삭제",
    MessageKey.HISTORY_EDIT: "편집", MessageKey.HISTORY_RETRY: "재시도", MessageKey.HISTORY_CLOSE: "닫기",
    MessageKey.HISTORY_USER_MESSAGE: "사용자 메시지",
    MessageKey.HISTORY_SELECT_ONE: "편집·재시도는 요청을 하나만 체크한 상태에서 사용할 수 있습니다.",
    MessageKey.HISTORY_SELECT_ITEMS: "삭제할 요청을 체크하세요.",
    MessageKey.HISTORY_RETRY_UNAVAILABLE: "중단 혹은 실패된 요청만 재시도할 수 있습니다.",
    MessageKey.HISTORY_EDIT_UNAVAILABLE: "실행 중이 아닌 요청만 편집하여 다시 실행할 수 있습니다.",
    MessageKey.HISTORY_RETRY_BLOCKED: "체크포인트에서 재시도할 수 없습니다: {details}",
    MessageKey.HISTORY_CHANGED: "선택한 요청의 실행 이력이 변경되었습니다. 목록을 다시 열어 선택하세요.",
    MessageKey.HISTORY_DELETE_SELECTED: "선택한 {count}개 요청을 삭제할까요? 예약 요청은 취소됩니다.",
    MessageKey.HISTORY_DELETING: "요청 삭제 중",
    MessageKey.AGENT_COMMAND_USAGE: "/agent <ID>로 지정하세요. 사용 가능한 에이전트: {agents}",
    MessageKey.SHORTCUT_RETRY: "재시도",

    MessageKey.HISTORY_DELETE_CONFIRM: "선택한 요청·응답을 대화와 이후 모델 문맥에서 삭제할까요?\n실행 기록과 원본 저장 이벤트는 보존됩니다.",

    MessageKey.HISTORY_BUSY: "실행·예약 요청·자동 이름 생성이 끝난 뒤 대화 이력을 변경하세요.",

    MessageKey.CLONE_LATEST: "최신 대화까지 전체 복제", MessageKey.CLONE_BOUNDARY: "복제할 마지막 요청·응답 (선택한 쌍 포함)",

    MessageKey.PROJECT_ACTIVITY: "프로젝트 실행 이력", MessageKey.ACTIVITY_EMPTY: "기록된 실행 이력이 없습니다.",


    MessageKey.ACTIVITY_SOURCE: "세션: {session_id} · Run: {run_id}",

    MessageKey.ACTIVITY_STEP: "Step: {step_id}", MessageKey.ACTIVITY_CODE: "코드: {code}",

    MessageKey.HELP_HISTORY: "보조 버튼 → 요청 목록: 삭제 · 편집 · 재시도\n보조 버튼 → 로그: 프로젝트 실행 이력\n세션 목록 Ctrl+←→: 패널 너비 조절\n복제 창에서 마지막으로 포함할 요청·응답을 선택할 수 있습니다.",

    MessageKey.SETTINGS_REGISTRATION: "추가 기능", MessageKey.SETTINGS_ENGINES: "엔진",

    MessageKey.SETTINGS_TYPE: "- 타입 : {type}", MessageKey.SETTINGS_RENAME: "프로젝트 이름 변경",

    MessageKey.SETTINGS_SIDEBAR_WIDTH: "설정·대화 화면의 좌측 패널 너비 (터미널 칸 수, 18~60)",


    MessageKey.SETTINGS_GLOBAL_REGISTRATION: "프로젝트를 선택하면 컴포넌트와 엔진을 등록할 수 있습니다.",

    MessageKey.SETTINGS_PROJECT_KEYS: "c 생성 · d 삭제\ne 이름 · r 복제",

    MessageKey.SETTINGS_CANCELLED: "이 페이지의 변경 사항을 취소했습니다.",

    MessageKey.SETTINGS_ENGINE_REQUIRED: "사용할 엔진을 하나 이상 선택하세요.",

    MessageKey.SETTINGS_ENGINE_UNAVAILABLE: "이 프로젝트에서 선택되지 않은 엔진입니다: {name}",

    MessageKey.SETTINGS_HUB_DATA_OBJECT: "config.data와 config.data.hub는 JSON 객체여야 합니다.",

    MessageKey.MODEL_LINE: "모델: {model}",

    MessageKey.SESSION_DELETE: "세션 삭제", MessageKey.SESSION_RENAME: "세션 이름 변경",

    MessageKey.SESSION_DELETE_CONFIRM: "'{title}' 세션을 소프트 삭제할까요? 기록은 보존됩니다.",

    MessageKey.SESSION_NAME_REQUIRED: "세션 이름을 입력하세요.",

    MessageKey.SESSION_BUSY: "실행·예약 요청·자동 이름 생성이 끝난 뒤 세션을 삭제하세요.",

    MessageKey.COMMAND_COMPONENT: "{name} 데이터·설정",

    MessageKey.COMPONENT_ACTION_LIST: "데이터 목록", MessageKey.COMPONENT_ACTION_GET: "ID로 조회",

    MessageKey.COMPONENT_ACTION_CREATE: "ID와 JSON으로 생성", MessageKey.COMPONENT_ACTION_UPDATE: "ID와 JSON으로 수정",

    MessageKey.COMPONENT_ACTION_DELETE: "ID로 삭제", MessageKey.COMPONENT_ACTION_SETTINGS: "컴포넌트 설정 열기",

    MessageKey.COMPONENT_ACTION_HELP: "명령 사용법",

    MessageKey.COMPONENT_UNAVAILABLE: "현재 프로젝트에서 활성화되지 않은 컴포넌트: {name}",

    MessageKey.COMPONENT_USAGE_ERROR: "사용법: /컴포넌트 config | /컴포넌트 help",

    MessageKey.COMPONENT_JSON_OBJECT: "데이터는 JSON 객체로 입력하세요.",

    MessageKey.COMPONENT_DELETE: "컴포넌트 데이터 삭제",

    MessageKey.COMPONENT_DELETE_CONFIRM: "{name}의 '{identifier}' 데이터를 삭제할까요? 이 데이터 삭제는 되돌릴 수 없습니다.",

    MessageKey.COMPONENT_LOADING: "컴포넌트 요청을 처리하고 있습니다…", MessageKey.COMPONENT_BUSY: "이전 컴포넌트 요청을 처리 중입니다.",

    MessageKey.COMPONENT_HELP: "/{command} config: 관리 창 열기\n/{command} help: 사용법 보기\n\n관리 창에서 경로를 입력해 가져오고 항목을 편집·삭제할 수 있습니다. Prompt·Agent·Workflow는 생성 버튼과 팝업 JSON 편집을 제공하며 Workflow는 흐름도를 함께 표시합니다. 하단 설정 버튼은 이 컴포넌트의 프로젝트 설정 페이지를 엽니다.\nMemory·Goals·Refinement는 작업 중 모델 도구와 정책으로 사용합니다.",

    MessageKey.SEARCH_TITLE: "현재 세션에서 찾기", MessageKey.SEARCH_HINT: "검색어 입력 · Enter 다음 · Alt+P 이전 · Esc 닫기",

    MessageKey.SEARCH_COUNT: "{index} / {count}개 일치 · Enter 다음 · Alt+P 이전",

    MessageKey.SEARCH_PREVIOUS: "이전", MessageKey.SEARCH_NEXT: "다음", MessageKey.SEARCH_CLOSE: "닫기",

    MessageKey.NOTIFICATION_TITLE: "다른 세션 상태",






    MessageKey.SETTINGS_TITLE: "설정", MessageKey.SETTINGS_GLOBAL: "전역 설정", MessageKey.SETTINGS_PROJECTS: "프로젝트",

    MessageKey.SETTINGS_PROFILE: "프로필", MessageKey.SETTINGS_APPEARANCE: "모양", MessageKey.SETTINGS_MAIN: "메인 설정",

    MessageKey.SETTINGS_COMPONENTS: "컴포넌트", MessageKey.SETTINGS_SAVE: "저장", MessageKey.SETTINGS_SAVED: "저장했습니다.",

    MessageKey.SETTINGS_CREATE: "프로젝트 생성", MessageKey.SETTINGS_NEW_TITLE: "새 프로젝트", MessageKey.SETTINGS_RELOAD: "불러오기",

    MessageKey.SETTINGS_OPEN_PROJECT: "열기", MessageKey.SETTINGS_CLONE: "복제", MessageKey.SETTINGS_DELETE: "삭제",

    MessageKey.SETTINGS_COPY_SUFFIX: "복사본", MessageKey.SETTINGS_DELETED: "프로젝트를 소프트 삭제했습니다.",

    MessageKey.SETTINGS_DELETE_CONFIRM: "'{title}' 프로젝트를 소프트 삭제할까요? 대화와 파일은 보존됩니다.",

    MessageKey.SETTINGS_DELETE_ACTIVE: "현재 대화 중인 프로젝트입니다. 다른 프로젝트의 '열기'로 전환한 뒤 삭제하세요.",

    MessageKey.SETTINGS_LOADING: "불러오는 중…", MessageKey.SETTINGS_EMPTY: "프로젝트 없음",

    MessageKey.SETTINGS_FOOTER: "ESC 패널 · e 에디터 · Enter 편집 · Tab 영역 · ↑↓ 항목 · ←→ 버튼",

    MessageKey.SETTINGS_GENERAL: "일반",

    MessageKey.GENERAL_CATEGORY_LANGUAGE: "언어 팩",

    MessageKey.GENERAL_CATEGORY_NOTIFICATIONS: "알림",

    MessageKey.GENERAL_CATEGORY_STARTUP: "시작 동작",

    MessageKey.GENERAL_CATEGORY_OUTPUT: "대화 출력",

    MessageKey.GENERAL_CATEGORY_EDITOR: "외부 편집기",

    MessageKey.GENERAL_LANGUAGE: "사용할 언어 코드: ko, en 또는 추가한 팩 코드. 비우면 기존 프로필·시작 언어 사용. 다음 Hub 시작부터 적용.",

    MessageKey.GENERAL_NOTIFICATION_KINDS: "표시할 알림 종류를 JSON 배열로 입력: error, info, warning, success. []는 모든 팝업 알림 끄기.",

    MessageKey.GENERAL_NOTIFICATION_SECONDS: "새 알림의 표시 시간(초). 저장 즉시 이후 알림에 적용.",

    MessageKey.GENERAL_RESTORE_LAST_PROJECT: "시작할 때 마지막 프로젝트 복원. 명시적으로 지정한 프로젝트 우선. 삭제된 프로젝트는 기본 프로젝트로 대체.",

    MessageKey.GENERAL_RESTORE_LAST_SESSION: "프로젝트를 열 때 마지막 세션 복원. 명시적으로 지정한 세션 우선. 삭제된 세션은 활성 세션으로 대체.",

    MessageKey.GENERAL_AUTO_SCROLL: "새 응답이 올 때 맨 아래를 보고 있었다면 자동 스크롤. false는 읽던 위치 유지. Alt + 이동키 수동 이동은 항상 가능.",

    MessageKey.GENERAL_EDITOR: "외부 에디터 명령: vim, nano, code --wait 등. 비우면 VISUAL → EDITOR → vi 순서로 선택.",

    MessageKey.LANGUAGE_PACK_ADD: "팩 추가/갱신",

    MessageKey.LANGUAGE_PACK_DELETE: "팩 삭제",

    MessageKey.LANGUAGE_PACK_NAMES: "사용 가능: {names} · 빠진 번역은 영어로 표시",

    MessageKey.LANGUAGE_PACK_CODE: "팩 코드 (예: ja, custom-ko). 내장 ko/en은 변경·삭제할 수 없습니다.",

    MessageKey.LANGUAGE_PACK_JSON: "메시지 키 → 번역 문자열 JSON을 붙여넣으세요. 기존 코드면 교체합니다.",

    MessageKey.LANGUAGE_PACK_NONE: "삭제할 사용자 언어 팩이 없습니다.",

    MessageKey.LANGUAGE_PACK_DRAFT: "언어 팩 초안을 변경했습니다. 일반 설정의 저장 버튼으로 확정하세요.",

    MessageKey.SETTINGS_LANGUAGE_DESCRIPTION: "UI 언어: ko(한국어), en(English). 비우면 시작 설정 사용. 다음 Hub 시작부터 적용됩니다.",

    MessageKey.SETTINGS_EMAIL_DESCRIPTION: "프로필 이메일 주소(선택).",

    MessageKey.SETTINGS_PHONE_DESCRIPTION: "프로필 전화번호(선택).",

    MessageKey.SETTINGS_EDITOR_DESCRIPTION: "외부 편집 작업용 에디터 명령. 예: vim, nano, code --wait. 비우면 VISUAL → EDITOR → vi 순서로 선택합니다.",

    MessageKey.SETTINGS_USAGE: "사용 통계",

    MessageKey.SETTINGS_USAGE_SCOPE: "워크스페이스의 활성 프로젝트 기준 · 불러오기로 갱신 · 프로젝트별 집계 기간 적용",

    MessageKey.SETTINGS_USAGE_EMPTY: "조회된 사용 통계가 없습니다.",

    MessageKey.SETTINGS_USAGE_ERROR: "{title}: 통계 조회 실패 — {error}",

    MessageKey.SETTINGS_USAGE_ALL: "전체 보관 기간",

    MessageKey.SETTINGS_USAGE_PERIOD: "최근 {seconds}초",

    MessageKey.SETTINGS_USAGE_ROW: "{title} ({period})\n  호출 {call_count}회 · 확인된 토큰 {known_tokens} · 예약 토큰 {reserved_tokens} · 사용량 미확인 {unknown_calls}회",

    MessageKey.SETTINGS_NO_DESCRIPTION: "스키마에 별도 설명이 없습니다.", MessageKey.SETTINGS_NO_FIELDS: "공개된 설정 항목이 없습니다.",
    MessageKey.SETTINGS_EDITOR_FAILED: "에디터 종료 코드 {code}. 기존 입력을 유지합니다.",
    MessageKey.SETTINGS_HOST_LOCKED: "호스트 고정값: 읽기 전용입니다.",
    MessageKey.SETTINGS_HOST_PARTIAL: "일부 하위 키는 호스트 고정값입니다. 해당 키의 저장값을 변경할 수 없습니다.",

    MessageKey.SETTINGS_PROFILE_DESCRIPTION: "대화에 표시할 이름. 초기값은 현재 OS 계정명입니다.",

    MessageKey.SETTINGS_COLOR_DESCRIPTION: "색상: #RRGGBB 또는 default (터미널 기본색)",
    MessageKey.SETTINGS_THEME_BACKGROUND: "배경 색", MessageKey.SETTINGS_THEME_FOREGROUND: "전경 색",
    MessageKey.SETTINGS_THEME_ACCENT1: "강조 색 1", MessageKey.SETTINGS_THEME_ACCENT2: "강조 색 2",
    MessageKey.SETTINGS_THEME_ACCENT3: "강조 색 3", MessageKey.SETTINGS_THEME_COMMENT: "주석 색",
    MessageKey.SETTINGS_SIDEBAR_WIDTH_TITLE: "좌측 패널 너비",
    MessageKey.SETTINGS_OUTPUT_REFRESH_INTERVAL: "출력창 갱신 간격 (초)",
    MessageKey.SETTINGS_OUTPUT_REFRESH_INTERVAL_DESCRIPTION: "응답 스트리밍을 묶어 갱신하는 간격. 0.01~10초, 기본 0.1초. 저장 즉시 적용하며 요청 접수와 키 입력은 지연시키지 않습니다.",
    MessageKey.SETTINGS_PROGRESS_REFRESH_INTERVAL: "로딩 바 갱신 간격 (초)",
    MessageKey.SETTINGS_PROGRESS_REFRESH_INTERVAL_DESCRIPTION: "로딩 바·스피너 애니메이션 갱신 간격. 0.01~10초, 기본 0.1초. 설정·대화 화면에 저장 즉시 적용됩니다.",
    MessageKey.RESPONSE_FAILED: "응답을 완료하지 못했습니다.",

    MessageKey.SETTINGS_VALUE_HINT: "빈칸=미설정 · 문자열은 그대로, 숫자/불리언/배열/객체는 JSON · null은 명시적 값 · 불러오기는 입력 초기화",

    MessageKey.SETTINGS_COMPONENTS_IMMEDIATE: "컴포넌트·엔진 선택은 저장 시 반영됩니다. 해제해도 기존 데이터와 설정은 유지됩니다.",

    MessageKey.SETTINGS_COMPONENTS_DRAFT: "선택한 컴포넌트는 프로젝트 생성 시 활성화됩니다.",

    MessageKey.SETTINGS_COMPONENT_APPLIED: "컴포넌트 선택을 반영했습니다. 설정 값은 저장 버튼으로 적용하세요.",

    MessageKey.SETTINGS_COMPONENTS_CHANGED: "컴포넌트 선택이 변경되었습니다. 설정을 다시 읽어주세요.",

    MessageKey.SETTINGS_PROJECT_NAME: "프로젝트 이름",

    MessageKey.SETTINGS_TITLE_REQUIRED: "프로젝트 이름을 입력하세요.",

    MessageKey.SETTINGS_PROJECT_BUSY: "실행 또는 예약 요청이 있는 프로젝트는 복제·삭제할 수 없습니다.",

    MessageKey.VIEW_STATE_ERROR: "읽던 위치를 저장하지 못했습니다: {error}",

    MessageKey.SESSION: "대화", MessageKey.RUN: "실행", MessageKey.STEPS: "단계",


    MessageKey.CONNECTING: "연결 중", MessageKey.CONNECTING_NOTICE: "백엔드를 연결하고 있습니다.",

    MessageKey.NEW_SESSION: "새 대화", MessageKey.SESSIONS: "{icon_sessions} 세션 목록", MessageKey.WORKSPACE: "프로젝트",

    MessageKey.MESSAGE: "메시지", MessageKey.YOU: "나",

    MessageKey.ASSISTANT: "어시스턴트", MessageKey.INFO: "안내", MessageKey.REASONING: "어시스턴트 생각 과정",

    MessageKey.SESSION_HINT: "c 생성 · d 삭제\ne 이름 · r 복제", MessageKey.INPUT_HINT: "Enter 전송 · Ctrl+Space 줄바꿈 · ↑↓ 후보 선택 · Tab 확정",

    MessageKey.SETTINGS_JSON_OBJECT: "설정 JSON의 최상위 값은 객체여야 합니다.",
    MessageKey.SETTINGS_JSON_UPDATED: "JSON 편집 내용을 초안에 반영했습니다. 저장 버튼으로 적용하세요.",
    MessageKey.FOOTER: "ESC 패널 · Ctrl+S 보조 버튼 · Ctrl+D 중단 · Alt + 이동키 스크롤 · Enter 전송 방식",

    MessageKey.FOOTER_SHORT: "ESC 패널 · Alt + 이동키 스크롤",

    MessageKey.PREVIEW_NOTICE: "샘플 데이터 · 모델 호출과 저장 기능은 연결되지 않았습니다.",

    MessageKey.PREVIEW_SUBMIT: "전송 미리보기입니다. 입력은 유지됩니다.",

    MessageKey.DETAILS_NARROW: "실행 상세는 너비 116칸 이상에서 표시됩니다.", MessageKey.DETAILS_OPEN: "실행 상세를 표시합니다.",

    MessageKey.DETAILS_CLOSED: "실행 상세를 접었습니다.", MessageKey.CLOSE_HINT: "Ctrl+Q로 최소화하여 셸로 돌아갑니다.",

    MessageKey.ERROR: "오류: {error}", MessageKey.NOT_CONNECTED: "백엔드 연결을 먼저 완료하세요.",

    MessageKey.SAVED: "요청을 저장했습니다.", MessageKey.INSTRUCTION_SAVED: "추가 지시를 저장했습니다. 다음 처리 시점에 반영됩니다.",
    MessageKey.DRAFT_RECOVERED: "전송하지 않은 입력은 입력창을 비운 뒤 ↑로 불러올 수 있습니다.",

    MessageKey.EMPTY_MESSAGE: "메시지를 입력하세요.", MessageKey.NO_ACTIVE_RUN: "추가 지시를 보낼 실행이 없습니다.",

    MessageKey.NO_TARGETS: "이 실행은 현재 추가 지시를 받지 않습니다.",

    MessageKey.CHOOSE_TARGET: "추가 지시를 받을 대상",

    MessageKey.ENGINE_LINE: "엔진: {engine}", MessageKey.CREATE_DIALOG: "대화 생성 또는 복제",

    MessageKey.CREATE: "새로 생성", MessageKey.CLONE: "선택한 대화 복제", MessageKey.NAME: "이름 (선택)",

    MessageKey.AUTO_NAME_HINT: "비워 두면 응답 후 모델이 대화 이름을 짓습니다.",

    MessageKey.CONFIRM: "확인", MessageKey.CANCEL: "취소", MessageKey.CLONE_BUSY: "원본의 실행과 예약 요청을 마친 뒤 복제하세요.",

    MessageKey.QUEUED: "{status} · 예약 {count}", MessageKey.WAITING: "{icon_waiting} 모델 응답을 기다리는 중",

    MessageKey.RUN_PROGRESS: "{status} · {engine} · {seconds}초 · 예약 {count}",

    MessageKey.ELAPSED: "소요 {duration}", MessageKey.ELAPSED_UNKNOWN: "소요 시간 알 수 없음",

    MessageKey.DURATION_SECONDS: "{seconds}초", MessageKey.DURATION_MINUTES: "{minutes}분", MessageKey.DURATION_HOURS: "{hours}시간",

    MessageKey.PHASE: "현재 단계: {name} ({status})",

    MessageKey.PAUSED: "{icon_paused} 일시 정지",

    MessageKey.MEMORY: "메모리 대화: 종료 시 메시지 소멸",

    MessageKey.TITLE_FAILED: "자동 이름 생성 실패: {error}", MessageKey.UNKNOWN_COMMAND: "알 수 없는 명령: {command}",

    MessageKey.COMMAND_HELP: "도움말", MessageKey.COMMAND_AGENT: "에이전트 선택", MessageKey.COMMAND_NEW: "대화 생성",

    MessageKey.COMMAND_CLONE: "선택한 대화 복제",

    MessageKey.COMMAND_STOP: "현재 실행 중단", MessageKey.COMMAND_DETAILS: "출력 태그 바",

    MessageKey.FILE: "파일", MessageKey.DIRECTORY: "폴더", MessageKey.ENGINE: "엔진", MessageKey.AGENT: "에이전트",

    MessageKey.BACKGROUND: "\n  Hub\n\n  Ctrl+Q: 대화로 돌아가기\n  Ctrl+C: 종료\n\n  Hub를 숨겨도 실행은 계속됩니다.\n",

    MessageKey.HELP_TITLE: "Hub 도움말",


    MessageKey.STATUS_IDLE: "{icon_idle} 유휴", MessageKey.STATUS_RUNNING: "{icon_running} 실행 중", MessageKey.STATUS_QUEUED: "{icon_queued} 예약",

    MessageKey.STATUS_COMMITTED: "{icon_committed} 접수됨", MessageKey.STATUS_STREAMING: "{icon_streaming} 응답 수신 중", MessageKey.STATUS_COMPLETED: "{icon_completed} 완료",

    MessageKey.STATUS_INTERRUPTED: "{icon_interrupted} 중단됨", MessageKey.STATUS_CANCELLED: "{icon_cancelled} 취소됨", MessageKey.STATUS_FAILED: "{icon_failed} 실패",

    MessageKey.STATUS_PAUSED: "{icon_paused} 일시 정지", MessageKey.STATUS_PENDING: "{icon_pending} 실행 전", MessageKey.STATUS_APPLIED: "{icon_applied} 반영됨",

    MessageKey.STATUS_UNAPPLIED: "{icon_unapplied} 미반영", MessageKey.STATUS_PARTIALLY_APPLIED: "{icon_partially_applied} 일부 반영",

    MessageKey.SETTINGS_ICON_STYLE: '아이콘 스타일',
    MessageKey.SETTINGS_ICON_DESCRIPTION: 'Enter로 선택을 시작하고 방향키로 변경한 뒤 Enter로 완료합니다.',
    MessageKey.SETTINGS_ICON_UNICODE: '일반 (Unicode/이모지)',
    MessageKey.SHORTCUT_CLOSE: '닫기',
    MessageKey.SHORTCUT_MOVE: '이동',
    MessageKey.SHORTCUT_NEXT_MATCH: '다음 결과',
    MessageKey.SHORTCUT_PREVIOUS_MATCH: '이전 결과',
    MessageKey.SHORTCUT_ACTIVATE: '실행',
    MessageKey.SHORTCUT_SELECT: '선택',
    MessageKey.SHORTCUT_CONFIRM: '확정',
    MessageKey.SHORTCUT_DELETE: '삭제',
    MessageKey.SHORTCUT_CLONE: '복제',
    MessageKey.SHORTCUT_MAIN: '메인 영역',
    MessageKey.SHORTCUT_WIDTH: '너비',
    MessageKey.SHORTCUT_CREATE: '생성',
    MessageKey.SHORTCUT_RENAME: '이름',
    MessageKey.SHORTCUT_SHELL: '셸',
    MessageKey.SHORTCUT_CHAT: '대화',
    MessageKey.SHORTCUT_SETTINGS: '설정',
    MessageKey.SHORTCUT_DONE: '완료',
    MessageKey.SHORTCUT_EDIT: '편집',
    MessageKey.SHORTCUT_NEWLINE: '줄바꿈',
    MessageKey.SHORTCUT_EDITOR: '에디터',
    MessageKey.SHORTCUT_ITEMS: '항목',
    MessageKey.SHORTCUT_REGIONS: '영역',
    MessageKey.SHORTCUT_SIDEBAR: '패널',
    MessageKey.SHORTCUT_SAVE_CHAT: '저장 후 대화',
    MessageKey.SHORTCUT_ENGINE: '엔진',
    MessageKey.SHORTCUT_FIND: '찾기',
    MessageKey.SHORTCUT_HISTORY: '요청 목록',
    MessageKey.SHORTCUT_LOGS: '로그',
    MessageKey.SHORTCUT_SCROLL: '스크롤',
    MessageKey.SHORTCUT_STOP: '중단',
    MessageKey.SHORTCUT_DETAILS: '상세',
    MessageKey.SHORTCUT_HELP: '도움말',
    MessageKey.SHORTCUT_ALT_MOVEMENT: 'Alt + 이동키',
    MessageKey.SHORTCUT_MINIMIZE: '최소화',
    MessageKey.SHORTCUT_NEXT_SESSION: '다음 세션',
    MessageKey.SHORTCUT_SEND: '전송',
    MessageKey.TOOLS_BUILTIN: '내장 도구',
    MessageKey.SHORTCUT_TOOLS_TOGGLE: '활성/비활성',
    MessageKey.QUESTION_WAITING: '답변 대기 중 · 질문',
    MessageKey.QUESTION_SENDING: '답변 전달 중 · {title}',
    MessageKey.QUESTION_DETAILS_HINT: 'Ctrl+O 전체 내용 보기',
    MessageKey.APPROVAL_HEADING: '{icon_question} 승인 요청',
    MessageKey.QUESTION_HEADING: '{icon_question} 질문',
    MessageKey.QUESTION_ACTION_HINT: 'Ctrl+O: 전체 작업 내용 확인 (인자·명령·파일 내용)',
    MessageKey.SHORTCUT_APPROVAL_DETAILS: '요청 상세',
    MessageKey.APPROVAL_TOOL: '도구: {name}',
    MessageKey.APPROVAL_RISK: '위험도: {value}',
    MessageKey.APPROVAL_MODEL_REASON: '모델 평가 근거: {reason}',
    MessageKey.APPROVAL_MODEL_RISK: '모델 평가(참고): {level} · {reason}',
    MessageKey.TOOL_HISTORY_MORE: '이전 도구 {count}건',
    MessageKey.APPROVAL_ARGUMENT: '{name}: {kind}, {size} (상세에서 확인)',
    MessageKey.RETRY_PROVIDER: '모델 호출',
    MessageKey.RETRY_TOOL: 'Tool {name}',
    MessageKey.RETRY_REPAIRING: '재시도 준비 중 · {target} · {next_attempt}/{maximum}번째 시도 · 수정안 생성 중',
    MessageKey.RETRY_WAIT: '재시도 대기 중 · {target} · {attempt}/{maximum}회 시도 완료 · {next_attempt}번째 시도까지 {seconds}초',
    MessageKey.RETRY_ACTIVE: '재시도 중 · {target} · {attempt}/{maximum}번째 시도',
    MessageKey.QUESTION_TEXT_HINT: '선택지 번호를 입력하거나 원하는 답변을 쓰고 Enter를 누르세요.',
    MessageKey.QUESTION_FREE_TEXT_HINT: '원하는 답변을 쓰고 Enter를 누르세요.',
    MessageKey.QUESTION_CHOICE_HINT: '선택지 번호를 입력하고 Enter를 누르세요. 추가 값이 필요하면 번호 뒤에 입력하세요.',
    MessageKey.AGENTS_IMPORT_HINT: "Agent JSON 파일 경로 → Enter: 검증 후 가져오기",
    MessageKey.AGENTS_TITLE: "Agent 관리",
}
