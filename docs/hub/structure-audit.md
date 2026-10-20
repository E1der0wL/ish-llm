# Hub 구조 감사 — 2026-10-13

> **과거 요구·작업·검증 기록.** 본문과 당시 결과를 보존한다. “현재/최신/통과”는 작성 시점의 표현이며
> 새 구현의 호환성 의무나 오늘의 회귀 성공을 뜻하지 않는다. [문서 우선순위](../README.md)와
> [현행 검토 결과](../llm/documentation-review.md)를 먼저 확인한다.


구조 변경 전 Python 파일 전체를 분류한 기록입니다. 직전 선택지 전체 너비 변경을 기준 동작으로 포함합니다.

| 변경 전 파일 | 판정 | 목적지 / 판단 근거 |
| --- | --- | --- |
| `hub/__init__.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/__main__.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/asset/__init__.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/asset/guides.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/asset/icon.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/backend/__init__.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/backend/approval.py` | MOVE | hub/backend/interactions/approval.py |
| `hub/backend/bootstrap.py` | MOVE | hub/backend/project/bootstrap.py |
| `hub/backend/builtin_tools.py` | MOVE | hub/backend/tools/builtin.py |
| `hub/backend/completion.py` | MOVE | hub/backend/execution/completion.py |
| `hub/backend/component_commands.py` | MOVE | hub/backend/project/component_commands.py |
| `hub/backend/context.py` | MOVE | hub/backend/execution/context.py |
| `hub/backend/continuations.py` | MOVE | hub/backend/execution/continuations.py |
| `hub/backend/defaults.py` | MOVE | hub/backend/project/defaults.py |
| `hub/backend/engine_selection.py` | MOVE | hub/backend/execution/engine_selection.py |
| `hub/backend/environment.py` | MOVE | hub/backend/execution/environment.py |
| `hub/backend/followup.py` | MOVE | hub/backend/execution/followup.py |
| `hub/backend/naming.py` | MOVE | hub/backend/project/naming.py |
| `hub/backend/processes.py` | MOVE | hub/backend/tools/processes.py |
| `hub/backend/provider_logs.py` | MOVE | hub/backend/observability/provider_logs.py |
| `hub/backend/questions.py` | MOVE | hub/backend/interactions/questions.py |
| `hub/backend/retries.py` | MOVE | hub/backend/execution/retries.py |
| `hub/backend/runtime.py` | OBJECTIFY | 알림 상태를 NotificationBuffer로 이동; 세션 작업 순서는 Runtime 유지 |
| `hub/backend/settings_service.py` | MOVE | hub/backend/project/settings.py |
| `hub/backend/skills.py` | MOVE | hub/backend/tools/skills.py |
| `hub/backend/snapshot.py` | MOVE | hub/backend/observability/snapshot.py |
| `hub/backend/steps.py` | MOVE | hub/backend/execution/steps.py |
| `hub/backend/test_workspaces.py` | MOVE | hub/backend/tools/test_workspaces.py |
| `hub/backend/thinking.py` | MOVE | hub/backend/execution/thinking.py |
| `hub/backend/tool_advice.py` | MOVE | hub/backend/tools/advice.py |
| `hub/backend/tool_management.py` | OBJECTIFY | backend/tools/management.py — ToolManagement가 프로젝트 데이터·버전 검증 소유 |
| `hub/backend/tool_paths.py` | MOVE | hub/backend/tools/paths.py |
| `hub/backend/trace.py` | MOVE | hub/backend/execution/trace.py |
| `hub/backend/worker.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/config/__init__.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/config/environment.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/config/general.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/config/preferences.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/config/profile.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/config/restart.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/config/store.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/config/theme.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/config/view_state.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/config/workspace.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/hub.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/locales/__init__.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/locales/en.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/locales/ko.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/model.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/__init__.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/application.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/chat/__init__.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/chat/completion.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/chat/component_ui.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/chat/history_ui.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/chat/session_actions.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/dialogs.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/input/__init__.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/input/bindings.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/input/commands.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/input/context.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/input/history.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/input/registry.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/input/submission.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/layout/__init__.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/layout/page.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/live.py` | OBJECTIFY | latest-wins 전달 상태를 SnapshotInbox로 이동; UI intent 조정 유지 |
| `hub/ui/output/__init__.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/output/images.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/output/markdown.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/output/model.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/output/parser.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/output/registry.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/output/structured.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/output/table.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/output/worker.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/presentation.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/restart.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/settings/__init__.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/settings/editor.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/settings/pages.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/settings/screen.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/terminal_screen.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/ui/view.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/widget/__init__.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/widget/actions.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/widget/answer_choices.py` | MOVE | hub/widget/forms/answer_choices.py |
| `hub/widget/answer_feedback.py` | MOVE | hub/widget/feedback/answer_feedback.py |
| `hub/widget/buttons.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/widget/character_stream.py` | MOVE | hub/widget/transcript/character_stream.py |
| `hub/widget/clipboard.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/widget/controls.py` | KEEP | 모든 위젯 category가 함께 쓰는 기반 컨트롤 |
| `hub/widget/conversation.py` | MOVE | hub/widget/transcript/conversation.py |
| `hub/widget/dialog.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/widget/execution.py` | MOVE | hub/widget/forms/execution.py |
| `hub/widget/followup.py` | MOVE | hub/widget/forms/followup.py |
| `hub/widget/general_settings.py` | MOVE | hub/widget/forms/general_settings.py |
| `hub/widget/header.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/widget/interrupt_feedback.py` | MOVE | hub/widget/feedback/interrupt_feedback.py |
| `hub/widget/message_cache.py` | MOVE | hub/widget/transcript/message_cache.py |
| `hub/widget/notifications.py` | MOVE | hub/widget/feedback/notifications.py |
| `hub/widget/output_inspector.py` | KEEP | 문서화된 ExpandedOutputDialog 확장 import 경로 유지 |
| `hub/widget/output_viewport.py` | MOVE | hub/widget/output/output_viewport.py |
| `hub/widget/preparing.py` | MOVE | hub/widget/feedback/preparing.py |
| `hub/widget/progress.py` | MOVE | hub/widget/feedback/progress.py |
| `hub/widget/question.py` | MOVE | hub/widget/forms/question.py |
| `hub/widget/question_card.py` | MOVE | hub/widget/forms/question_card.py |
| `hub/widget/reader.py` | MOVE | hub/widget/output/reader.py |
| `hub/widget/retry_status.py` | MOVE | hub/widget/feedback/retry_status.py |
| `hub/widget/search.py` | MOVE | hub/widget/transcript/search.py |
| `hub/widget/search_index.py` | MOVE | hub/widget/output/search_index.py |
| `hub/widget/section.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/widget/session_form.py` | MOVE | hub/widget/forms/session_form.py |
| `hub/widget/settings.py` | MOVE | hub/widget/forms/settings.py |
| `hub/widget/settings_viewport.py` | MOVE | hub/widget/forms/settings_viewport.py |
| `hub/widget/shortcuts.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/widget/sidebar.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/widget/summary.py` | KEEP | 현재 책임과 위치 유지 |
| `hub/widget/table_inspector.py` | MOVE | hub/widget/output/table_inspector.py |
| `hub/widget/tag_bar.py` | MOVE | hub/widget/output/tag_bar.py |
| `hub/widget/text_viewport.py` | MOVE | hub/widget/output/text_viewport.py |
| `hub/widget/thought_trace.py` | MOVE | hub/widget/transcript/thought_trace.py |
| `hub/widget/tool_manager.py` | MOVE | hub/widget/forms/tool_manager.py |
| `hub/widget/welcome.py` | KEEP | 현재 책임과 위치 유지 |

SessionCatalog는 REVIEW 후 보류: 실행 시작·제목 생성·선택·관찰 순서를 그대로 조정해야 하므로 단순 Runtime wrapper가 됩니다.
상태 없는 포맷·파서·경로 함수는 이동만 하며 객체화하지 않습니다. ui 하위 category와 렌더링 수명은 유지합니다.
