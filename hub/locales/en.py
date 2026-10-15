"""English UI strings."""



MESSAGES = {
    "profile_settings": "Settings", "profile_home": "Home", "profile_minimize": "Minimize",
    "shortcut_profile": "profile", "shortcut_categories": "categories",
    "history_retry_question_unavailable": "Cancelled questions can reopen only for the latest request with no running or queued work. Edit an older request to start it again.",
    "settings_discard": "Cancel", "settings_discarded": "Discarded unsaved changes.",
    "settings_engine_item": "Engine · {name}", "settings_component_item": "Component · {name}",
    "project_delete_last": "Create another project before deleting the last project.",
    "failure_evidence": "Diagnostic evidence: {evidence}",
    "failure_details": "Detailed execution records are available with Ctrl+L.",
    "failure_unknown": "The stored evidence does not identify the exact cause. Check for file changes or commands already executed before retrying from request history (Ctrl+G). If it happens again, include the detailed records when reporting it.",
    "failure_rate_limit": "The model provider rejected the request due to a usage or rate limit. Wait before retrying. If it persists, check the provider's API quota, balance and billing status. This code alone does not mean the API key is invalid.",
    "failure_unavailable": "An error occurred while connecting to the model provider or receiving its response. Check the provider's service status and retry later. If it persists, consider another available model.",
    "failure_authentication": "The model provider rejected authentication. Check that the configured API key belongs to the selected provider and has not expired or been revoked.",
    "failure_permission": "The operation lacked the required access. Check model/project access or the permissions of the target file and directory.",
    "failure_timeout": "The operation exceeded its time limit. Check network and provider status, split the task, or review timeout settings. Check for completed effects before retrying.",
    "failure_connection": "The network connection failed. Check internet access, DNS, proxy and firewall settings.",
    "failure_request": "The model provider rejected the request format or target. Check the model name, API endpoint and supported options; reduce large inputs or attachments if needed.",
    "failure_context": "The conversation exceeded the model's input capacity. Split the input or start a new session with only the necessary context.",
    "failure_budget": "The project's configured usage budget has been reached. Review usage statistics and the usage policy, and adjust the limit if needed.",
    "failure_file_missing": "A required file or directory could not be found. Check the path and ish's current working directory.",
    "failure_empty": "The model provider returned an empty or unreadable response. Retry later; if it persists, check the model name and provider connection settings.",
    # Accepted only for saved language packs; no composer preview UI/binding remains.
    "preview": "MARKDOWN PREVIEW",
    "command_preview": "Toggle Markdown preview",
    "shortcut_preview": "preview",
    "history_requests": "Requests",
    "history_response": "Response",
    "settings_current_project": "Project : {title}",
    "settings_close": "Close",
    "shortcut_paste": "Paste",
    "object_copy_code": "Copy code",
    "object_copy_cell": "Copy cell",
    "settings_requires_restart": "Restart required",
    "settings_restart_title": "Restart Hub",
    "settings_restart_now": "Restart",
    "settings_restart_confirm": "Saved. Restart Hub to apply these settings:\n\n{fields}\n\nRestart now?\nEnter: restart · Esc: later (keep saved values)",
    "settings_restart_busy": "Hub has running or queued work, or a command in progress. Retry saving after it finishes, or restart ish later. Your settings have been saved.",
    "settings_restarting": "Restarting Hub",
    "general_category_storage": "Data storage",
    "general_workspace_root": "Hub data root. Enter an absolute path or ~/path. Takes effect after restarting Hub. Existing data is not moved; an empty directory starts a new workspace. Leave blank to use the original launch path.",
    'object_query': 'Search / filter:',
    'object_raw': 'Source',
    'object_rendered': 'Rendered',
    'object_empty': 'No matching rows.',
    'object_loading': 'Preparing table…',
    'object_table_count': 'Row {index}/{visible} · {total} total · Column {column}',
    'shortcut_object_raw': 'Source/rendered',
    'shortcut_object_sort': 'Sort column',
    'shortcut_cancel': 'Cancel queued',
    'preparing_response': 'Preparing…',
    'status_preparing': '{icon_preparing} Preparing',
    'status_thinking': '{icon_reasoning} Thinking',
    'status_retrying': '{icon_preparing} Retrying',
    'status_interrupting': '{icon_interrupted} Stopping',
    'followup_hint': '→ accept suggestion',
    'component_action_install': 'Install a built-in skill (preserve existing entries)',
    # Keep the key for saved language packs; the removed install command is not advertised.
    'skills_install_help': '/skill config\nEnter a built-in skill Markdown path in the manager to import it. Existing IDs are never overwritten.',
    'history_cancel': 'Cancel queued',
    'history_cancel_confirm': 'Cancel this queued request? The active Run will continue.',
    'history_cancel_unavailable': 'Only queued requests can be cancelled. Use Ctrl+X to interrupt an active Run.',
    'history_checkpoint_protected': 'An unfinished Run checkpoint references this turn. Choose delete again to review and confirm abandoning its dependent resumes.',
    'history_abandon_delete': 'Abandon resumes and delete',
    'history_delete_changed': 'The conversation or dependent Runs changed after review. Choose delete again to review the current plan.',
    'history_abandon_confirm': 'Abandon resume for the following {count} Runs and delete this turn?\nPreviously performed effects will not be undone.\nThe turn is excluded from future context; execution records and checkpoints are retained.',

    'shortcut_movement': 'Navigation',
    'shortcut_clear': 'Clear',
    'shortcut_tags': 'Tags',
    'shortcut_expand': 'Expand',
    'shortcut_copy': 'Copy raw',
    'shortcut_open_directory': 'Open directory',
    'settings_search': 'Find settings',
    'settings_search_hint': 'Match a key, description or value. Leave empty to show all.',
    'settings_search_empty': 'No matching settings.',
    'settings_search_active': 'Filter: {query}',
    'clipboard_copied': 'Raw text copied.',
    'clipboard_terminal': 'Copy requested through the terminal (OSC 52); text also stored in the application clipboard.',
    'component_action_config': 'Open component manager',
    'component_manager_unavailable': 'The {name} manager is not available yet. Use settings or data commands.',
    'tags_title': 'Output tags',
    'tags_empty': 'No output objects.',
    'tools_title': 'Tools',
    'skills_title': 'Skills',
    'rag_title': 'RAG documents',
    'workflows_title': 'Workflows',
    'skills_import_hint': 'Markdown or Skill JSON file path → Enter to import',
    'rag_import_hint': 'UTF-8 text file path → Enter to register the document and search index',
    'workflows_import_hint': 'Workflow JSON file path → Enter to validate and import',
    'manager_hint': 'Tab: region · ↑↓: select · Enter: edit in editor · d: delete',
    'manager_empty': 'No items registered.',
    'manager_imported': 'Imported.',
    'manager_saved': 'Saved.',
    'manager_delete_confirm': 'Delete {name}?',
    'tools_import_hint': 'Directory path → Enter to import <name>/<name>.py and optional requirements.txt',
    'tools_columns': 'Name / Last modified / Description',
    'tools_hint': 'Tab: region · ↑↓: select · Space: toggle built-in · Enter: folder · d: delete',
    'tools_empty': 'No tools registered.',
    'tools_imported': 'Tool imported. Prepare dependencies and enable it separately before use.',
    'tools_failed': 'Operation failed; see the error notification.',
    'tools_explorer_failed': 'File manager exited with code {code}',
    'tools_delete_confirm': 'Delete {name}? If enabled, it will also be disabled.',
    'welcome_keys': 'ESC Panel · c in panel: New session · Ctrl+S Settings',
    'session_required': 'Create a session first: ESC, then c in the session list.',
    'command_details': 'Output tag bar',
    'activity_hint': 'Last 300 activity / provider entries each · Navigation: scroll · ESC / Enter / Ctrl+L: close',
    'activity_provider_project': 'Provider log · Current project',
    'activity_provider_file': 'Log file: {path}',
    'activity_provider_traceback': 'Traceback · Stored exceptions and frames (original error messages omitted)',
    'activity_provider_run_details': 'Provider details for this Run:',
    'activity_provider_workspace': 'Provider log · Workspace-wide',
    'status_waiting_input': '{icon_pending} Awaiting reply',
    'status_waiting_approval': '{icon_waiting_approval} Awaiting approval',
    'status_answer_sending': '{icon_waiting} Sending answer',
    'status_approved': '{icon_completed} Approved',
    'status_denied': '{icon_cancelled} Denied',
    'status_answered': '{icon_completed} Answer delivered',
    'status_resuming': '{icon_running} Resuming',
    'help': '## Panels and Home\n\n- **ESC** moves from main content to the panel.\n- **Ctrl+Q** opens Hub from the shell and minimizes it from any Hub screen. Shell Ctrl+S stays unchanged.\n- **Tab / Shift+Tab** changes panel categories. **↑↓** stays within the current category. **Space / Enter / →** moves to main content.\n- **Ctrl+← / Ctrl+→** adjusts and automatically saves only panel width.\n- **Ctrl+P** focuses the shared profile footer. It offers Settings on Home, Home in settings, and Minimize. **←→** selects a button; **Enter / Space** activates it. Tab does nothing in the profile footer.\n- Select projects in the upper Home category and sessions below.\n- **c / d / e / r** in the panel creates / deletes / renames / clones.\n- **Enter** sends; during execution choose steering or follow-up.\n- **↑↓** in an empty composer recalls previous/next user requests. Editing exits history navigation.\n- **Ctrl+Space** inserts a newline. **↑↓** selects completions; **Tab** confirms.\n- **Ctrl+X** stops the running or paused request, preserving queued requests.\n- `/engine <name>` sets the engine.\n- **Ctrl+S** opens project settings filtered to the selected engine.\n- **Ctrl+F** searches the session. Enter next / Alt+P previous. Closing clears highlights.\n- **Ctrl+G** shows turns; **Ctrl+L** opens project activity.\n\n## Scrolling and readers\n\nConversation output uses **Alt + Navigation**.\nHelp, activity and query popups use **Navigation alone**.\nNavigation includes ↑↓←→ / Home / End / PgUp / PgDn.\nClose readers with **ESC / Enter / Ctrl+L**.\n\n## Output tag bar\n\n**Ctrl+T** opens it; **↑↓** selects an object and scrolls to its position.\n**c** copies raw source; **Ctrl+T / ESC** closes it. **Tab / Space / Enter** opens a full-screen popup.\nCode, tables, images, diffs and registered XML renderers appear here.\n\n## Settings\n\n- **Ctrl+F** filters keys, descriptions and values; empty query restores all.\n- **Save** applies global and current-project drafts; **Cancel** discards unsaved changes. Both stay in settings. Use Home to return. Only the current project is editable; choose General, an engine or a component in the panel.\n- **Tab / Shift+Tab** moves between fields and actions; **↑↓** selects fields; **←→** selects action buttons. In fields, **←** returns to the panel and **→** does nothing outside editing.\n- **Enter** starts inline editing at the end / finishes editing.\n- In field navigation **e** opens the editor; **d** clears the value.\n- **Ctrl+Space / Alt+Enter** inserts a newline during editing.\n- Choices use Enter → arrows → Enter. Alt + Navigation also scrolls settings.\n\n## Commands\n\n`/help`, `/engine`, `/new`, `/clone`, `/details`, `/stop`.\nOnly registered components appear in slash completion.\n`/tools config`: toggle built-in tools and manage packages.\nAnswer questions in the existing composer, then press Enter. **Ctrl+C** copies selected text; without a selection it does nothing in the composer. **Ctrl+V** pastes the system clipboard.\nPath completion inserts text; it does not attach files.\n',
    "settings_dependencies": "Required components: {names}",
    "settings_dependencies_missing": "Missing required components: {names} — select them before saving.",
    "title_model_required": "No title model is configured. Set the conversation engine model or parameters.engines._hub_title.completion.model.",
    "output_image_limit": "Image preview limit reached for this view (32 images).",
    "welcome_title": "No sessions yet",
    "welcome_hint": "Create a session to start a conversation.",
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
    "settings_reasoning_effort": "Model reasoning effort, e.g. low, medium or high. Supported values vary by model; blank uses the default.",
    'send_while_running': 'Send while running',

    'send_steering': 'Live instruction — steer the current Run',

    'send_follow_up': 'Follow-up — run after the current work',

    'instruction_run_changed': 'The active Run changed. Your draft is preserved; choose how to send it again.',

    'model_required': 'Set config.parameters.engines.loop.completion.model in Ctrl+S → Project settings.',



    "history_title": "Requests and work", "history_empty": "No saved requests.",

    "history_hint": "↑↓ move · Space check · d delete · e edit · r retry · Tab regions",

    "history_response": "Response", "history_delete": "Delete",
    "history_edit": "Edit", "history_retry": "Retry", "history_close": "Close",
    "history_user_message": "User message",
    "history_select_one": "Check exactly one request to edit or retry.",
    "history_select_items": "Check the requests to delete.",
    "history_retry_unavailable": "Only interrupted or failed requests can be retried.",
    "history_edit_unavailable": "Only inactive requests can be edited and run again.",
    "history_retry_blocked": "Cannot retry from the checkpoint: {details}",
    "history_changed": "The selected request changed. Reopen the list and select it again.",
    "history_delete_selected": "Delete {count} selected requests? Queued requests will be cancelled.",
    "history_deleting": "Deleting requests",
    "engine_command_usage": "Use /engine <name>. Available engines: {engines}",
    "shortcut_retry": "retry",

    "history_delete_confirm": "Remove this request and response from the conversation and future context?\nExecution records and original storage events are retained.",

    "history_busy": "Finish active work, queued requests and automatic naming before changing conversation history.",

    "clone_latest": "Clone all conversation", "clone_boundary": "Last request and response to include",

    "project_activity": "Project execution history", "activity_empty": "No execution history recorded.",


    "activity_source": "Session: {session_id} · Run: {run_id}",

    "activity_step": "Step: {step_id}", "activity_code": "Code: {code}",

    "help_history": "Ctrl+G: Request/work pairs (d delete · r clone through selection)\nCtrl+L: Project execution history\nSession list Ctrl+←→: Resize sidebar\nChoose the last request and response to include in the clone dialog.",

    "settings_registration": "Additional features", "settings_engines": "Engines",

    "settings_type": "- Type : {type}", "settings_rename": "Rename project",

    "settings_sidebar_width": "Shared settings/chat sidebar width (terminal columns, 18–60)",


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

    "component_usage_error": "Usage: /component config | /component help",

    "component_json_object": "Enter data as a JSON object.",

    "component_delete": "Delete component data",

    "component_delete_confirm": "Delete '{identifier}' from {name}? This data deletion cannot be undone.",

    "component_loading": "Processing the component request…", "component_busy": "A component request is already in progress.",

    "component_help": "/{command} config: open manager\n/{command} help: show help\n\nImport a path, edit or delete entries in the manager. Its Settings button opens project settings filtered to this component.\nAsk the assistant to create resources. Memory, Goals and Refinement work through model tools and processing policies.",

    "search_title": "Find in this session", "search_hint": "Type to search · Enter next · Alt+P previous · Esc close",

    "search_count": "{index} / {count} matches · Enter next · Alt+P previous",

    "search_previous": "Previous", "search_next": "Next", "search_close": "Close",

    "notification_title": "Other sessions",






    "settings_title": "Settings", "settings_global": "Global settings", "settings_projects": "Projects",

    "settings_profile": "Profile", "settings_appearance": "Appearance", "settings_main": "Main settings",

    "settings_components": "Components", "settings_save": "Save", "settings_saved": "Saved.",

    "settings_create": "New project", "settings_new_title": "New project", "settings_reload": "Load",

    "settings_open_project": "Open", "settings_clone": "Clone", "settings_delete": "Delete",

    "settings_copy_suffix": "copy", "settings_deleted": "Project soft-deleted.",

    "settings_delete_confirm": "Soft-delete '{title}'? Conversation history and files are retained.",

    "settings_delete_active": "This project is open in chat. Open another project's chat before deleting it.",

    "settings_loading": "Loading…", "settings_empty": "No projects",

    "settings_footer": "ESC panel · ↑↓ fields · ←→ buttons · e editor · Enter edit · Tab regions",

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
    "settings_output_refresh_interval": "Output refresh interval (seconds)",
    "settings_output_refresh_interval_description": "Interval for batching streamed response updates: 0.01–10 seconds, default 0.1. Applies on save without delaying request acknowledgements or keyboard input.",
    "settings_progress_refresh_interval": "Progress refresh interval (seconds)",
    "settings_progress_refresh_interval_description": "Refresh interval for progress bars and spinner animation: 0.01–10 seconds, default 0.1. Applies on save to both settings and chat.",
    "response_failed": "The response could not be completed.",

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


    "connecting": "Connecting", "connecting_notice": "Connecting to the backend.",

    "new_session": "New conversation", "sessions": "{icon_sessions} SESSIONS", "workspace": "WORKSPACE",

    "message": "MESSAGE", "you": "YOU",

    "assistant": "ASSISTANT", "info": "INFO", "reasoning": "ASSISTANT REASONING",

    "session_hint": "c new · d delete\ne rename · r clone", "input_hint": "Enter send · Ctrl+Space newline · ↑↓ select · Tab confirm",

    "footer": "ESC panel · Ctrl+F find · Ctrl+S settings · Ctrl+X stop · Alt + Navigation scroll · Enter send mode",

    "footer_short": "ESC panel · Ctrl+F find · Alt + Navigation scroll",

    "preview_notice": "Sample data · No model calls or storage.",

    "preview_submit": "Send preview only. Your draft is preserved.",

    "details_narrow": "Run details need at least 116 columns.", "details_open": "Run details shown.",

    "details_closed": "Run details hidden.", "close_hint": "Press Ctrl+Q to minimize Hub and return to the shell.",

    "error": "Error: {error}", "not_connected": "Wait for the backend connection.",

    "saved": "Request saved.", "instruction_saved": "Instruction saved; it will be applied at the next boundary.",
    "draft_recovered": "Clear the composer and press ↑ to recall the unsent input.",

    "empty_message": "Enter a message.", "no_active_run": "There is no active Run to instruct.",

    "no_targets": "This execution is not accepting instructions.",

    "choose_target": "Instruction target",

    "engine_line": "Engine: {engine}", "create_dialog": "Create or clone a conversation",

    "create": "Create", "clone": "Clone selected conversation", "name": "Name (optional)",

    "auto_name_hint": "Leave blank to let the model name it after a response.",

    "confirm": "Confirm", "cancel": "Cancel", "clone_busy": "Stop or finish the source Run and queued requests before cloning.",

    "queued": "{status} · queued {count}", "waiting": "{icon_waiting} Waiting for the model response",

    "run_progress": "{status} · {engine} · {seconds}s · queued {count}",

    "elapsed": "Elapsed {duration}", "elapsed_unknown": "Elapsed time unavailable",

    "duration_seconds": "{seconds}s", "duration_minutes": "{minutes}m", "duration_hours": "{hours}h",

    "phase": "Current step: {name} ({status})",

    "paused": "{icon_paused} Paused",

    "memory": "Memory history: messages are lost on shutdown",

    "title_failed": "Automatic naming failed: {error}", "unknown_command": "Unknown command: {command}",

    "command_help": "Show help", "command_engine": "Choose an engine", "command_new": "Create a conversation",

    "command_clone": "Clone this conversation",

    "command_stop": "Interrupt the active Run", "command_details": "Output tag bar",

    "file": "File", "directory": "Directory", "engine": "Engine",

    "background": "\n  Hub\n\n  Ctrl+Q: return to the conversation\n  Ctrl+C: exit\n\n  Hiding Hub keeps Runs executing.\n",

    "help_title": "Hub help",


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
    'shortcut_details': 'details',
    'shortcut_help': 'help',
    'shortcut_alt_movement': 'Alt + Navigation',
    'shortcut_minimize': 'minimize',
    'shortcut_next_session': 'next session',
    'shortcut_send': 'send',
    'tools_builtin': 'Built-in tool',
    'shortcut_tools_toggle': 'enable/disable',
    'question_waiting': 'Waiting for your answer · Question',
    'question_sending': 'Sending answer · {title}',
    'question_details_hint': 'Ctrl+O view full details',
    'approval_heading': '{icon_question} Approval request',
    'question_heading': '{icon_question} Question',
    'question_action_hint': 'Ctrl+O: review the complete action (arguments, commands, file contents)',
    'shortcut_approval_details': 'Request details',
    'approval_tool': 'Tool: {name}',
    'approval_risk': 'Risk: {value}',
    'approval_model_reason': 'Model assessment reason: {reason}',
    'approval_model_risk': 'Model assessment (advisory): {level} · {reason}',
    'tool_history_more': '{count} earlier tools',
    'approval_argument': '{name}: {kind}, {size} (see details)',
    'retry_provider': 'Model call',
    'retry_tool': 'Tool {name}',
    'retry_repairing': 'Preparing retry · {target} · attempt {next_attempt}/{maximum} · correcting arguments',
    'retry_wait': 'Waiting to retry · {target} · {attempt}/{maximum} attempts completed · attempt {next_attempt} in {seconds}s',
    'retry_active': 'Retrying · {target} · attempt {attempt}/{maximum}',
    'question_text_hint': 'Type a choice number or your own answer, then press Enter.',
    'question_free_text_hint': 'Type your answer, then press Enter.',
    'question_choice_hint': 'Type a choice number, then Enter. Append an input value after the number if required.',
    "agents_import_hint": "Agent JSON file path → Enter to validate and import",
    "agents_title": "Agent manager",
}
