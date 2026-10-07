"""English UI strings."""



MESSAGES = {
    "settings_dependencies": "Required components: {names}",
    "settings_dependencies_missing": "Missing required components: {names} — select them before saving.",
    "title_model_required": "No title model is configured. Set the conversation engine model or parameters.engines._hub_title.completion.model.",
    "output_image_limit": "Image preview limit reached for this view (32 images).",
    "welcome_title": "No sessions yet",
    "welcome_hint": "Create a session to start a conversation.",
    "welcome_keys": "ESC Panel · Ctrl+S Project settings · F4 New session · ESC in panel to minimize",
    "session_required": "Create a session first. Press F4, or c in the session list.",
    "output_pending": "Receiving output block…",
    "output_image": "Image · {caption}",
    "output_image_loading": "Converting image…",
    "output_error": "Unable to display output: {error}",
    "progress_more": " · +{count} more",
    "progress_settings_load": "Loading settings",
    "progress_settings_work": "Applying settings operation",
    "progress_component": "Processing {command}",
    "progress_component_done": "Completed: {identifier}",
    "settings_storage_description": "Conversation storage. Blank uses file for a new project. Cannot change while Sessions exist; null is reserved for an injected store.",
    "settings_reasoning_effort": "Reasoning effort, for example low, medium or high (no quotes needed). Supported values depend on the model. Blank removes the project setting; it does not disable reasoning. Only returned reasoning text is displayed. The same option in .ishrc.py completion_kwargs takes precedence.",
    'send_while_running': 'Send while running',

    'send_steering': 'Live instruction — steer the current Run',

    'send_follow_up': 'Follow-up — run after the current work',

    'instruction_run_changed': 'The active Run changed. Your draft is preserved; choose how to send it again.',

    'model_required': 'Set config.parameters.engines.loop.completion.model in Ctrl+S → Project settings.',



    "history_title": "Requests and work", "history_empty": "No saved requests.",

    "history_hint": "↑↓ select · d delete · r clone through here · Tab buttons",

    "history_response": "Response", "history_delete": "Delete turn", "history_clone": "Clone through here",

    "history_delete_confirm": "Remove this request and response from the conversation and future context?\nExecution records and original storage events are retained.",

    "history_busy": "Finish active work, queued requests and automatic naming before deleting turns.",

    "clone_latest": "Clone all conversation", "clone_boundary": "Last request and response to include",

    "project_activity": "Project execution history", "activity_empty": "No execution history recorded.",

    "activity_hint": "Latest 300 events · ESC close · Ctrl+L close · Alt + Navigation scroll · Enter close",

    "activity_source": "Session: {session_id} · Run: {run_id}",

    "activity_step": "Step: {step_id}", "activity_code": "Code: {code}",

    "help_history": "Ctrl+G: Request/work pairs (d delete · r clone through selection)\nCtrl+L: Project execution history\nSession list ←→: Resize sidebar\nChoose the last request and response to include in the clone dialog.",

    "settings_registration": "Additional features", "settings_engines": "Engines",

    "settings_type": "- Type : {type}", "settings_rename": "Rename project",

    "settings_sidebar_width": "Shared settings/chat sidebar width (terminal columns, 18–60)",

    "settings_width_preview": "Width preview applied · Save or cancel under Appearance.",

    "settings_global_registration": "Select a project to choose its components and engines.",

    "settings_project_keys": "c new · d delete\ne rename · r clone",

    "settings_cancelled": "Discarded changes on this page.",

    "settings_engine_required": "Select at least one engine.",

    "settings_engine_unavailable": "Engine is not selected in this project: {name}",

    "settings_hub_data_object": "config.data and config.data.hub must be JSON objects.",

    "model_line": "Model: {model}",

    "session_delete": "Delete session", "session_rename": "Rename session",

    "session_delete_confirm": "Soft-delete '{title}'? Its history will be preserved.",

    "session_name_required": "Enter a session name.",

    "session_busy": "Finish active Runs, queued requests and automatic naming before deleting the session.",

    "command_component": "{name} data and settings",

    "component_action_list": "List records", "component_action_get": "Get by ID",

    "component_action_create": "Create with ID and JSON", "component_action_update": "Update with ID and JSON",

    "component_action_delete": "Delete by ID", "component_action_settings": "Open component settings",

    "component_action_help": "Command usage",

    "component_unavailable": "Component is not enabled in this project: {name}",

    "component_usage_error": "Usage: list | get ID | create ID JSON | update ID JSON | delete ID | settings | help",

    "component_json_object": "Enter data as a JSON object.",

    "component_delete": "Delete component data",

    "component_delete_confirm": "Delete '{identifier}' from {name}? This data deletion cannot be undone.",

    "component_loading": "Processing the component request…", "component_busy": "A component request is already in progress.",

    "component_help": "/{command} list\n/{command} get ID\n/{command} create ID JSON\n/{command} update ID JSON\n/{command} delete ID\n/{command} settings\n\nCreate and update accept JSON objects. Generic updates merge top-level keys.\nUse Save on the settings page to apply component configuration.\nRAG accepts title/content/metadata document data; create/update may call models to update indexes.",

    "search_title": "Find in this session", "search_hint": "Type to search · Enter next · Alt+P previous · Esc close",

    "search_count": "{index} / {count} matches · Enter next · Alt+P previous",

    "search_previous": "Previous", "search_next": "Next", "search_close": "Close",

    "notification_title": "Other sessions",

    "execution_title": "Execution options", "execution_engine": "Engine",

    "execution_workflow": "Workflow", "execution_plain": "This engine needs no additional request options.",

    "execution_no_workflows": "No workflows available.",

    "execution_workflows_hint": "Enable the project's workflows component and register a Workflow.",

    "execution_summary": "Entry: {entry} · Top-level nodes: {count}",

    "settings_title": "Settings", "settings_global": "Global settings", "settings_projects": "Projects",

    "settings_profile": "Profile", "settings_appearance": "Appearance", "settings_main": "Main settings",

    "settings_components": "Components", "settings_save": "Save", "settings_saved": "Saved.",

    "settings_create": "New project", "settings_new_title": "New project", "settings_reload": "Load",

    "settings_open_project": "Open", "settings_clone": "Clone", "settings_delete": "Delete",

    "settings_copy_suffix": "copy", "settings_deleted": "Project soft-deleted.",

    "settings_delete_confirm": "Soft-delete '{title}'? Conversation history and files are retained.",

    "settings_delete_active": "This project is open in chat. Open another project's chat before deleting it.",

    "settings_loading": "Loading…", "settings_empty": "No projects",

    "settings_footer": "ESC panel · Ctrl+S save & chat · Arrows items · e editor · Enter edit · Tab regions",

    "settings_general": "General",

    "general_category_language": "Language packs",

    "general_category_notifications": "Notifications",

    "general_category_startup": "Startup",

    "general_category_output": "Conversation output",

    "general_category_editor": "External editor",

    "general_language": "Language code: ko, en or an added pack. Empty inherits legacy profile/startup language. Applies on next Hub startup.",

    "general_notification_kinds": "JSON array of alert types: error, info, warning, success. [] disables all popup alerts.",

    "general_notification_seconds": "Display duration in seconds. Applies to new alerts after saving.",

    "general_restore_last_project": "Restore the last project on startup. Explicit project takes priority; deleted projects fall back to the default.",

    "general_restore_last_session": "Restore the last session when opening a project. Explicit session takes priority; deleted sessions fall back to an active session.",

    "general_auto_scroll": "Follow new responses only when already at the bottom. false keeps the reading position. Alt+Arrows always scroll manually.",

    "general_editor": "External editor command: vim, nano, code --wait, etc. Empty resolves VISUAL, EDITOR, then vi.",

    "language_pack_add": "Add/update pack",

    "language_pack_delete": "Delete pack",

    "language_pack_names": "Available: {names} · Missing translations use English",

    "language_pack_code": "Pack code (e.g. ja, custom-ko). Built-in ko/en cannot be replaced or deleted.",

    "language_pack_json": "Paste a JSON object mapping message keys to translations. An existing code is replaced.",

    "language_pack_none": "No custom language packs to delete.",

    "language_pack_draft": "Language pack draft changed. Save General settings to commit it.",

    "settings_language_description": "UI language: ko (Korean), en (English). Empty uses startup settings. Applies on next Hub startup.",

    "settings_email_description": "Profile email address (optional).",

    "settings_phone_description": "Profile phone number (optional).",

    "settings_editor_description": "Editor command for external editing, e.g. vim, nano, code --wait. Empty resolves VISUAL, EDITOR, then vi.",

    "settings_usage": "Usage statistics",

    "settings_usage_scope": "Active workspace projects · Reload to refresh · Each project uses its own accounting period",

    "settings_usage_empty": "No usage statistics available.",

    "settings_usage_error": "{title}: usage query failed — {error}",

    "settings_usage_all": "All retained history",

    "settings_usage_period": "Last {seconds}s",

    "settings_usage_row": "{title} ({period})\n  Calls {call_count} · Known tokens {known_tokens} · Reserved tokens {reserved_tokens} · Unknown usage {unknown_calls} calls",

    "settings_no_description": "No additional schema description.", "settings_no_fields": "No public settings fields.",
    "settings_editor_failed": "Editor exited with status {code}. The previous draft is unchanged.",
    "settings_host_locked": "Host override: read only.",
    "settings_host_partial": "Some nested keys are host overrides. Their stored values cannot be changed.",

    "settings_profile_description": "Name shown in conversations. Initially the current OS account name.",

    "settings_color_description": "Color: #RRGGBB or default (terminal color)",
    "settings_theme_background": "Background", "settings_theme_foreground": "Foreground",
    "settings_theme_accent1": "Accent 1", "settings_theme_accent2": "Accent 2",
    "settings_theme_accent3": "Accent 3", "settings_theme_comment": "Comment",
    "settings_sidebar_width_title": "Sidebar width",

    "settings_value_hint": "Blank=unset · strings as text; numbers/booleans/arrays/objects as JSON · null is explicit · Reload discards edits",

    "settings_components_immediate": "Component and engine selection applies on Save. Disabling retains data and settings.",

    "settings_components_draft": "Selected components will be enabled when the project is created.",

    "settings_component_applied": "Component selection applied. Use Save to apply its settings.",

    "settings_components_changed": "Component selection changed. Reload settings.",

    "settings_project_name": "Project name",

    "settings_title_required": "Enter a project title.",

    "settings_project_busy": "Cannot clone or delete a project with active or queued requests.",

    "view_state_error": "Could not save reading position: {error}",

    "session": "SESSION", "run": "RUN", "steps": "STEPS",

    "completion_detail": "Model: {model}\nCalls: {count}\nLatest call tokens: {tokens}",

    "connecting": "Connecting", "connecting_notice": "Connecting to the backend.",

    "new_session": "New conversation", "sessions": "{icon_sessions} SESSIONS", "workspace": "WORKSPACE",

    "message": "MESSAGE", "preview": "MARKDOWN PREVIEW", "you": "YOU",

    "assistant": "ASSISTANT", "info": "INFO", "reasoning": "{icon_reasoning} MODEL REASONING",

    "session_hint": "c new · d delete\ne rename · r clone", "input_hint": "Enter send · Ctrl+Space newline · ↑↓ select · Tab confirm",

    "footer": "ESC panel · Ctrl+E engine · Ctrl+F find · Ctrl+S settings · Ctrl+X stop · Alt + Navigation scroll · Enter send mode",

    "footer_short": "ESC panel · Ctrl+F find · Alt + Navigation scroll",

    "preview_notice": "Sample data · No model calls or storage.",

    "preview_submit": "Send preview only. Your draft is preserved.",

    "details_narrow": "Run details need at least 116 columns.", "details_open": "Run details shown.",

    "details_closed": "Run details hidden.", "close_hint": "Press ESC in the panel to minimize Hub and return to the shell.",

    "error": "Error: {error}", "not_connected": "Wait for the backend connection.",

    "saved": "Request saved.", "instruction_saved": "Instruction saved; it will be applied at the next boundary.",

    "empty_message": "Enter a message.", "no_active_run": "There is no active Run to instruct.",

    "no_targets": "This execution is not accepting instructions.",

    "choose_target": "Instruction target", "choose_engine": "Engine for the next request",

    "engine_line": "Engine: {engine} · Ctrl+E change", "create_dialog": "Create or clone a conversation",

    "create": "Create", "clone": "Clone selected conversation", "name": "Name (optional)",

    "auto_name_hint": "Leave blank to let the model name it after a response.",

    "confirm": "Confirm", "cancel": "Cancel", "clone_busy": "Stop or finish the source Run and queued requests before cloning.",

    "queued": "{status} · queued {count}", "waiting": "{icon_waiting} Waiting for the model response",

    "run_progress": "{status} · {engine} · {seconds}s · queued {count}",

    "elapsed": "Elapsed {duration}", "elapsed_unknown": "Elapsed time unavailable",

    "duration_seconds": "{seconds:.1f}s", "duration_minutes": "{minutes}m", "duration_hours": "{hours}h",

    "phase": "Current step: {name} ({status})", "no_reasoning": "The provider has not supplied reasoning text.",

    "paused": "{icon_paused} Paused · approval/resume is available through the llm API.",

    "memory": "Memory history: messages are lost on shutdown",

    "title_failed": "Automatic naming failed: {error}", "unknown_command": "Unknown command: {command}",

    "command_help": "Show help", "command_engine": "Choose an engine", "command_new": "Create a conversation",

    "command_clone": "Clone this conversation", "command_preview": "Toggle Markdown preview",

    "command_stop": "Interrupt the active Run", "command_details": "Toggle Run details",

    "file": "File", "directory": "Directory", "engine": "Engine",

    "background": "\n  Hub\n\n  Ctrl+Q: return to the conversation\n  Ctrl+C: exit\n\n  Hiding Hub keeps Runs executing.\n",

    "help_title": "Hub help",

    "help": "# Hub help\n\n## Panels and pages\n\n| Key | Action |\n| --- | --- |\n| ESC | Move from main content to the panel |\n| ESC in the panel | Minimize Hub and return to the shell |\n| Ctrl+Q in the shell | Open Hub |\n| Ctrl+S in Hub | Open settings; save and return to chat from settings content |\n| Tab / Space / Enter in the panel | Move to main content |\n\nHub leaves the shell's Ctrl+S binding unchanged.\n\n## Conversation input\n\n- **Enter** sends a request. During a Run, choose steering or a follow-up request.\n- **Ctrl+Space** inserts a newline.\n- **↑↓** selects completions; **Tab** confirms or opens the menu.\n- **Ctrl+X** interrupts the active Run, preserving queued requests.\n- **Ctrl+E** selects an engine and Workflow.\n- **Ctrl+F** searches this session. Enter goes to the next match; Alt+P goes to the previous match.\n- **Ctrl+G** opens the request/response list for deletion and cloning.\n- **Ctrl+L** opens project activity.\n\n## Scrolling\n\n**Alt + Navigation** always includes all of these keys:\n\n| Navigation key | Action |\n| --- | --- |\n| ↑ / ↓ | One line up / down |\n| ← / → | Horizontal overflow |\n| Home / End | Start / end |\n| PgUp / PgDn | One page up / down |\n\nConversation output and read-only dialogs share this behavior.\nSettings uses arrow-key item navigation; Alt scrolling is disabled there.\n\n## Help, activity and query results\n\nContent never receives focus. Scroll with **Alt + Navigation**.\nClose with **ESC**, **Enter** or **Ctrl+L** to restore your previous focus.\n\n## Session panel\n\n**c** create · **d** delete · **e** rename · **r** clone.\n`← / →` adjusts panel width. An empty name enables automatic naming.\nCloning lets you choose a conversation boundary.\n\n## Settings\n\n- **Tab / Shift+Tab** switches between fields and actions.\n- **Arrows** select a field.\n- **Enter** starts / finishes inline editing.\n- **e** opens the configured external editor.\n- **Ctrl+Space / Alt+Enter** inserts a newline while editing.\n- For choices such as icon style, use Enter → arrows → Enter.\n- Project panel c / d / e / r creates / deletes / renames / clones.\n\n## Commands and display\n\nType `/` for enabled component commands.\nUse `/help`, `/engine`, `/new`, `/clone`, `/preview`, `/details`, `/stop`.\nPath completions insert text; they do not attach files automatically.\n\n**F1** help · **F2** next session · **F4** create/clone · **F5** draft preview · **F6** Run details.\n",

    "status_idle": "{icon_idle} Idle", "status_running": "{icon_running} Running", "status_queued": "{icon_queued} Queued",

    "status_committed": "{icon_committed} Accepted", "status_streaming": "{icon_streaming} Streaming", "status_completed": "{icon_completed} Completed",

    "status_interrupted": "{icon_interrupted} Interrupted", "status_cancelled": "{icon_cancelled} Cancelled", "status_failed": "{icon_failed} Failed",

    "status_paused": "{icon_paused} Paused", "status_pending": "{icon_pending} Pending", "status_applied": "{icon_applied} Applied",

    "status_unapplied": "{icon_unapplied} Not applied", "status_partially_applied": "{icon_partially_applied} Partially applied",

    'settings_icon_style': 'Icon style',
    'settings_icon_description': 'Press Enter to choose, use arrows to change, then Enter to finish.',
    'settings_icon_unicode': 'Standard (Unicode/emoji)',
    'shortcut_close': 'close',
    'shortcut_move': 'move',
    'shortcut_next_match': 'next match',
    'shortcut_previous_match': 'previous match',
    'shortcut_activate': 'activate',
    'shortcut_select': 'select',
    'shortcut_confirm': 'confirm',
    'shortcut_delete': 'delete',
    'shortcut_clone': 'clone',
    'shortcut_main': 'main',
    'shortcut_width': 'width',
    'shortcut_create': 'new',
    'shortcut_rename': 'rename',
    'shortcut_shell': 'shell',
    'shortcut_chat': 'chat',
    'shortcut_settings': 'settings',
    'shortcut_done': 'done',
    'shortcut_edit': 'edit',
    'shortcut_newline': 'newline',
    'shortcut_editor': 'editor',
    'shortcut_items': 'items',
    'shortcut_regions': 'regions',
    'shortcut_sidebar': 'panel',
    'shortcut_save_chat': 'save & chat',
    'shortcut_engine': 'engine',
    'shortcut_find': 'find',
    'shortcut_history': 'turns',
    'shortcut_logs': 'logs',
    'shortcut_scroll': 'scroll',
    'shortcut_stop': 'stop',
    'shortcut_preview': 'preview',
    'shortcut_details': 'details',
    'shortcut_help': 'help',
    'shortcut_alt_movement': 'Alt + Navigation',
    'shortcut_minimize': 'minimize',
    'shortcut_next_session': 'next session',
    'shortcut_send': 'send',
}
