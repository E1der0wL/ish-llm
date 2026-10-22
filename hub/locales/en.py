"""English UI strings."""



MESSAGES = {
    'operation_existing_rag_job': 'An unfinished registration exists for this document. Select it in the RAG list to retry, or delete it before registering again.',
    'operation_diagnostics': 'Hub operation diagnostics · current process',
    'operation_changed': 'The state changed during this operation. Reload and review it before trying again. Your draft is preserved.',
    'operation_interaction': 'Approval or an answer is required before resuming. Review the request in the input area. Denied work is never replayed automatically.',
    'operation_busy': 'Work is still running. Wait for it to finish, or stop it before trying again.',
    'operation_configuration': 'RAG settings differ from those used to prepare this job. Check the settings and register the source again.',
    'operation_checkpoint_unavailable': 'Execution records are missing or damaged. Review the log details. Work may already have taken effect, so it will not restart automatically.',
    'operation_engine': 'The original engine is unavailable. Check the project engine registration and configuration.',
    'operation_already_resumed': 'This request has already been resumed. Reopen request history to review the latest attempt.',
    'operation_restart_unproven': 'A safe restart from the beginning cannot be established. Review previous effects before deciding to submit a new request.',
    'operation_not_resumable': 'This operation cannot resume in its current state. Refresh the request or registration list. Detailed diagnostics are available in the log.',
    'operation_abandon_required': 'These records are needed for resume. Review abandonment in the deletion dialog before deleting them.',
    'operation_failed': 'The operation could not be completed. Check your input and current state. Open the log for Hub operation diagnostics.',
    'system': 'System',
    'usage_daily_title': 'Daily token usage · up to 30 recent days',
    'usage_daily_range': '{start} ~ {end} · combined project reporting windows',
    'usage_daily_unknown': 'Calls without reported usage: {count} (excluded from token totals)',
    'prompts_title': 'Prompt manager',
    'prompts_import_hint': 'Prompt JSON path → Enter: validate and import',
    'definition_edit': 'Edit',
    'definition_manager_hint': 'Tab regions · ↑↓ select · Enter edit in popup · d delete · Create button below',
    'definition_name': 'Name (letters, digits, underscore, hyphen; 1–64 characters)',
    'definition_name_invalid': 'Use 1–64 letters, digits, underscores or hyphens for the name.',
    'definition_object': 'Enter a JSON object.',
    'definition_too_large': 'Popup editing supports up to 1,048,576 characters.',
    'definition_validation': 'The backend validates the full definition on Save. Tab selects Save/Cancel.',
    'definition_save_failed': 'Save failed. Review the error and correct the draft; your input is preserved.',
    'workflow_preview': 'Live workflow preview',
    'workflow_invalid': 'Cannot draw the current JSON draft.',
    'shortcut_restart': 'Restart',
    'shortcut_tabs': 'Tabs',
    'settings_tab_config': 'Settings',
    'settings_tab_policy': 'Policy',
    'settings_tab_empty': 'No settings in this tab.',
    'cancel_wait': 'Cancellation requested. Waiting for the active call and storage cleanup to finish. This execution mode cannot force termination while preserving the shell and other work.',
    'rag_phase_cancelling': 'Stopping',
    'settings_desc_title': 'Project display name.',
    'settings_desc_components': 'Components available to this project.',
    'settings_desc_model': 'Provider model identifier used for this model call.',
    'settings_desc_api_base': 'Base URL of the provider or compatible inference server.',
    'settings_desc_api_key': 'Credential supplied to the selected provider.',
    'settings_desc_temperature': 'Sampling temperature controlling variation in generated output.',
    'settings_desc_max_tokens': 'Maximum number of output tokens requested from the provider.',
    'settings_desc_timeout': 'Maximum time allowed for this operation, in seconds.',
    'settings_desc_system_prompt': 'Instructions included in model requests for this engine.',
    'settings_desc_max_attempts': 'Maximum number of attempts, including the initial attempt.',
    'settings_desc_max_retries': 'Maximum number of additional attempts after the initial failure.',
    'settings_desc_delay_seconds': 'Initial waiting time between attempts, in seconds.',
    'settings_desc_max_delay_seconds': 'Upper bound on the waiting time between attempts, in seconds.',
    'stop': 'Stop',
    'resume': 'Resume',
    'component_import': 'Add',
    'shortcut_stop_resume': 'Stop/resume',
    'preset_title': 'Preset',
    'preset_add': 'Add new preset',
    'preset_export_current': 'Export current settings as preset',
    'preset_export_hint': 'Exports the saved global and project settings. Enter a name and save; editing JSON is optional.',
    'memory_title': 'Memory manager',
    'settings_restart_waiting': 'Waiting for cancellation and storage cleanup. An unresponsive in-process tool cannot be forcibly killed. Hub will restart when cleanup completes.',
    'preset_name': 'Preset name',
    'preset_edit': 'Edit JSON',
    'preset_applied': 'Preset applied.',
    'preset_drafts': 'Save or discard your settings drafts first.',
    'preset_hint': 'Includes global and project settings. Edit the JSON, then save.',
    'settings_group_general': 'General',
    'settings_group_completion': 'Model calls',
    'settings_group_provider': 'Provider',
    'settings_group_context': 'Context',
    'settings_group_run': 'Execution',
    'settings_group_approval': 'Approval',
    'settings_group_tool_retry': 'Tool retries',
    'settings_group_usage': 'Usage',
    'settings_group_retention': 'Retention',
    'settings_group_output': 'Output',
    'settings_group_processing': 'Processing',
    'settings_group_ingestion': 'Registration',
    'settings_option_description': 'Value for {name}. Use the type and constraints shown below.',
    'shortcut_auxiliary': 'Auxiliary buttons',
    'shortcut_return': 'Return',
    'attach_title': 'Attach file',
    'attach_hint': 'Choose a file path to insert into the composer. Tab confirms path completion.',
    'attach_required': 'Enter a file path.',
    'settings_json': 'Settings JSON',
    'settings_theme_mode': 'Theme',
    'settings_theme_mode_description': 'dark or light. Used for automatically derived surface and text colors.',
    'clipboard_internal': 'Internal clipboard',
    'clipboard_title': 'Clipboard',
    'clipboard_copy_unavailable': 'External copy failed; saved in Hub only',
    'clipboard_read_unavailable': 'Cannot read the external clipboard. Use your terminal paste command.',
    'clipboard_x11_library': 'The libX11 library required for clipboard access is unavailable.',
    'clipboard_x11_connection': 'Cannot connect to the X11 clipboard. Check DISPLAY and X11 access permissions.',
    'clipboard_x11_timeout': 'The X11 clipboard timed out. Check the display connection.',
    'clipboard_x11_transfer': 'Cannot transfer X11 clipboard text. Your terminal paste command may still work.',

    "profile_settings": "Settings", "profile_home": "Home", "profile_minimize": "Minimize",
    "shortcut_profile": "profile", "shortcut_categories": "categories",
    "history_retry_question_unavailable": "Cancelled questions can reopen only for the latest request with no running or queued work. Edit an older request to start it again.",
    "settings_discard": "Cancel", "settings_discarded": "Discarded unsaved changes.",
    "settings_engine_item": "Engine · {name}", "settings_component_item": "Component · {name}",
    "project_delete_last": "Create another project before deleting the last project.",
    "failure_evidence": "Diagnostic evidence: {evidence}",
    "failure_details": "Detailed execution records are available from the auxiliary Log button.",
    "failure_checkpoint_conflict": "The operation changed. Review its current state before trying again.",
    "failure_checkpoint_protected": "This operation cannot resume. Review the blocking conditions.",
    "failure_unknown": "The stored evidence does not identify the exact cause. Check for file changes or commands already executed before retrying from the auxiliary Request history button. If it happens again, include the detailed records when reporting it.",
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
    "failure_completion_invalid": "The model response did not match the expected format. Check the provider/server's streaming and Tool calling compatibility. Received output is preserved; the operation is not automatically replayed.",
    "failure_completion_length": "The model reached its output token limit before finishing. Review the model's output token setting or split the task. Received output is preserved.",
    "failure_completion_filtered": "The provider's content filter ended the response. Review the request and the provider's usage policy. The same request is not automatically repeated.",
    "failure_completion_incomplete": "The model response did not finish normally. Received output is preserved. Check the finish reason in the logs and the provider's status before deciding whether to retry.",
    "failure_completion_output_limit": "The configured output character limit was reached. Review the engine's output size limit or split the task.",
    "failure_completion_tool_limit": "The model requested more Tools in one response than permitted. Review the engine's Tool call limit or split the task.",
    "failure_completion_argument_limit": "Generated Tool arguments or a call ID exceeded the configured length limit. Review the engine's Tool argument size limit or split the task.",
    "failure_step_timeout": "An execution step exceeded its configured deadline. Review the engine's step time limits. Check for completed effects before retrying.",
    "failure_request_size": "The server rejected the request size. Reduce the input or attachment size before retrying.",
    "failure_dependency": "A required library could not be loaded. Check the installed dependencies in the execution environment.",
    "failure_command": "A command exited with an error. Check its output and any changes already made before retrying.",
    "failure_tool_failed": "A Tool could not finish its operation. Check its diagnostic details and any completed effects before retrying.",
    "failure_validation": "The supplied value does not meet the input requirements. Correct the value and try again.",
    "failure_disconnected": "Hub is not connected to its backend. Reopen Hub after the backend is ready.",
    "failure_tool_arguments": "The Tool name or arguments did not meet the Tool's requirements. Check the requested operation and values.",
    "failure_tool_contract": "The Tool's execution requirements were not satisfied. Check its runner, isolation and operation-key configuration.",
    "failure_lifecycle": "The operation is no longer active. Refresh its state before making another request.",
    "failure_sandbox": "The required sandbox is unavailable. Check the sandbox installation and configuration; protection is not disabled automatically.",
    "failure_operation_conflict": "This operation key belongs to a different Tool or set of arguments. Check the existing operation before continuing.",
    "failure_operation_uncertain": "This operation may already have taken effect. Verify the actual result before deciding how to continue; it is not automatically replayed.",
    "general_category_diagnostics": "Provider diagnostics",
    "general_provider_request_summary": "Record message roles, lengths and Tool call/result pairing counts. Message bodies and Tool arguments are excluded. Default true. Applies after restarting Hub.",
    "general_category_recovery": "Error analysis and recovery",
    "general_recovery_enabled": "Reduce rejected context before any response arrives. Corrected calls stay within the Loop Provider attempt and deadline limits. Default true. Applies after restarting Hub.",
    "general_recovery_model_analysis": "Ask the current model to explain bounded error evidence and recommend an action. Requires an extra API call; no tools are enabled. Default true.",
    "general_recovery_timeout_seconds": "Seconds available for analysis and summaries. Default 60. Corrected calls retain the original Provider deadline; execution deadlines are never extended.",
    "general_recovery_input_chars": "Character budget for the prior summary and source fragment per summary call. Default 12000. This is not a token count.",
    "general_recovery_summary_chars": "Maximum summary characters. Default 2000. The current request and steering instructions are preserved.",
    "general_recovery_max_summary_calls": "Maximum summary calls in one recovery. Default 16. Partial summaries are never used for retry.",
    "failure_server_detail": "Server error details",
    "failure_model_analysis": "Model error analysis · may include inference",
    "recovery_rejected": "Recovery proposal rejected · {attempt}/{maximum}",
    "recovery_result_rejected": "The backend rejected the input repair. The original failure is preserved.",
    "recovery_analyzing": "Analyzing the error · recovery {attempt}/{maximum}",
    "recovery_compacting": "Summarizing long input · recovery {attempt}/{maximum}{progress}",
    "recovery_retrying": "Retrying with reduced input · recovery {attempt}/{maximum}",
    "recovery_result_no_correction": "No supported automatic correction was found. Check the input or settings using the guidance above.",
    "recovery_result_summary_incomplete": "The summary budget was exhausted before completion; no retry was made. Originals are preserved. Split the input or adjust the recovery budget.",
    "recovery_result_not_reduced": "The summary did not reduce input size; no retry was made. Check the current request and tool definitions.",
    "recovery_result_invalid_analysis": "The model returned invalid analysis, so automatic recovery stopped. See the original error.",
    "recovery_result_analysis_failed": "Error analysis or summarization could not finish. The original error and work records are preserved.",
    "recovery_result_exhausted": "The configured recovery attempts were exhausted. Original records and completed work are preserved.",
    "general_provider_max_detail_chars": "Maximum characters per detailed error string. Default 2000; a positive integer. Used when error details are enabled. Applies after restarting Hub.",
    # Accepted only for saved language packs; no composer preview UI/binding remains.
    "preview": "MARKDOWN PREVIEW",
    "command_preview": "Toggle Markdown preview",
    "shortcut_preview": "preview",
    "history_requests": "Requests",
    "settings_current_project": "Project : {title}",
    "settings_close": "Close",
    "shortcut_paste": "Paste",
    "object_copy_code": "Copy code",
    "object_copy_cell": "Copy cell",
    "settings_requires_restart": "Restart required",
    "settings_restart_title": "Restart Hub",
    "settings_restart_now": "Restart",
    "settings_restart_confirm": "Saved. Restart Hub to apply these settings:\n\n{fields}\n\nRestart now?\nEnter: restart · Esc: later (keep saved values)",
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
    'skills_install_help': '/skills config\nEnter a built-in skill Markdown path in the manager to import it. Existing IDs are never overwritten.',
    'history_cancel': 'Cancel queued',
    'history_cancel_confirm': 'Cancel this queued request? The active Run will continue.',
    'history_cancel_unavailable': 'Only queued requests can be cancelled. Use Ctrl+D to interrupt an active Run.',
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
    'tags_title': 'Tag bar',
    'tags_empty': 'No output objects.',
    'tools_title': 'Tools',
    'skills_title': 'Skills',
    'mcp_title': 'MCP servers',
    'rag_title': 'RAG documents',
    'workflows_title': 'Workflows',
    'skills_import_hint': 'Markdown or Skill JSON file path → Enter to import',
    'mcp_import_hint': 'MCP server JSON file path → Enter to validate and import',
    'rag_import_hint': 'Text/Markdown/PNG/JPEG path → Enter to import (images: choose OCR or model analysis)',
    'workflows_import_hint': 'Workflow JSON file path → Enter to validate and import',
    'manager_hint': 'Tab: region · ↑↓: select · Enter: edit in editor · d: delete',
    'manager_empty': 'No items registered.',
    'manager_imported': 'Imported.',
    'manager_saved': 'Saved.',
    'rag_phase_queued': 'Queued for registration',
    'rag_phase_preparing': 'Preparing registration',
    'rag_phase_embedding': 'Embedding',
    'rag_phase_validating_input': 'Validating input',
    'rag_phase_splitting': 'Splitting chunks',
    'rag_phase_extracting': 'Extracting relations',
    'rag_phase_validating': 'Validating index',
    'rag_phase_writing_metadata': 'Writing metadata',
    'rag_phase_publishing': 'Publishing index',
    'rag_phase_preparing_documents': 'Preparing documents',
    'rag_phase_image_ocr': 'Extracting image text',
    'rag_phase_image_analysis': 'Analyzing image',
    'rag_phase_indexing': 'Storing index',
    'rag_phase_completed': 'Registered',
    'rag_phase_failed': 'Registration failed',
    'rag_phase_cancelled': 'Cancelled',
    'rag_phase_unconfirmed': 'Execution state needs verification',
    'rag_job_counts': 'Embedding {done}/{total} chunks · Checkpoints reused {reused} · Extraction {batches} batches',
    'rag_job_times': 'Embedding {embedding}s · Extraction {extraction}s · This attempt elapsed {elapsed}s',
    'rag_job_retry_hint': 'Retry reuses completed chunks. An active job cannot be resumed.',
    "rag_job_retry_check": "Retry checks whether this job can currently resume.",
    'rag_job_readonly': 'Failed/cancelled jobs can be deleted. Cancelled jobs cannot be resumed.',
    'rag_stage_counts': 'Current stage {done}/{total} {unit}',
    'rag_unit_chunk': 'chunks',
    'rag_unit_batch': 'batches',
    'rag_unit_document': 'documents',
    'rag_unit_job': 'jobs',
    'rag_observed_times': 'Observed time: ',
    'rag_cancel_job': 'Cancel job',
    'rag_cancel_unavailable': 'Select a queued or running registration job.',
    'rag_delete_unavailable': 'Only inactive failed/cancelled jobs can be deleted. Cancel active jobs first.',
    'rag_delete_confirm': 'Delete saved input and checkpoints for {name} ({bytes} bytes)?\nResume will be unavailable. Published documents and indexes are preserved.',
    'rag_image_title': 'Import image document',
    'rag_image_analysis': 'Model analysis',
    'rag_vision_settings': 'Vision setup',
    'rag_image_hint': 'OCR extracts text using the configured Vision OCR backend.\nModel analysis interprets diagrams/tables with the configured vision model and may incur API costs.\nVision setup enables the component and opens its settings page.\nThe document preserves image provenance and the extraction method.',
    'rag_retry_unavailable': 'Select an inactive unfinished registration job. Completed or cancelled jobs cannot be retried.',
    'manager_delete_confirm': 'Delete {name}?',
    'tools_import_hint': 'Directory path → Enter to import <name>/<name>.py and optional requirements.txt',
    'tools_columns': 'Name / Last modified / Description',
    'tools_hint': 'Tab: region · ↑↓: select · Space: toggle built-in · Enter: folder · d: delete',
    'tools_empty': 'No tools registered.',
    'tools_imported': 'Tool imported. Prepare dependencies and enable it separately before use.',
    'tools_failed': 'Operation failed; see the error notification.',
    'tools_explorer_failed': 'File manager exited with code {code}',
    'tools_delete_confirm': 'Delete {name}? If enabled, it will also be disabled.',
    'welcome_keys': 'ESC Panel · c in panel: New session · Ctrl+S Auxiliary',
    'session_required': 'Create a session first: ESC, then c in the session list.',
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
    'help': '## Panels and Home\n\n- **ESC** moves from main content to the panel.\n- **Ctrl+Q** opens Hub from the shell and minimizes it from any Hub screen. Shell Ctrl+S stays unchanged.\n- **Tab / Shift+Tab** changes panel categories. **↑↓** stays within the current category. **Space / Enter / →** moves to main content.\n- **Ctrl+← / Ctrl+→** adjusts and automatically saves only panel width.\n- **Ctrl+P** focuses the shared profile footer. It offers Home/Settings and a settings preset dropdown. **←→** selects a button; **Enter / Space** activates it. Tab does nothing in the profile footer.\n- Select projects in the upper Home category and sessions below.\n- **c / d / e / r** in the panel creates / deletes / renames / clones.\n- **Enter** sends; during execution choose steering or follow-up.\n- **↑↓** in an empty composer recalls previous/next user requests. Editing exits history navigation.\n- **Ctrl+Space** inserts a newline. **↑↓** selects completions; **Tab** confirms.\n- **Ctrl+W** restarts Hub from any Hub screen. Active work blocks restart with an explanation.\n- **Ctrl+D** in the Home composer stops the running or paused request, or resumes the last interrupted request; queued requests are preserved.\n- `/agent <name>` sets the engine.\n- **Ctrl+S** focuses auxiliary buttons from the composer or settings actions. Left / Right selects; Up / Down does nothing. Enter / Space activates, and ESC returns.\n- **Auxiliary → Find** searches the session. Enter next / Alt+P previous. Closing clears highlights.\n- **Auxiliary buttons** open request history, tags, search, file attachment, logs, and settings. File attachment inserts a path into the composer. Settings opens the Loop engine settings.\n\n## Scrolling and readers\n\nConversation output uses **Alt + Navigation**.\nHelp, activity and query popups use **Navigation alone**.\nNavigation includes ↑↓←→ / Home / End / PgUp / PgDn.\nClose readers with **ESC / Enter / Ctrl+L**.\n\n## Output tag bar\n\n**Auxiliary → Tags** opens it; **↑↓** selects an object and scrolls to its position.\n**c** copies raw source; **ESC** closes it. **Tab / Space / Enter** opens a full-screen popup.\nCode, tables, images, diffs and registered XML renderers appear here.\n\n## Settings\n\n- **Filter button / Ctrl+F** filters keys, descriptions and values across panel selections; empty query clears it. The JSON button opens the current draft in your configured editor. Changes return to the draft; use Save to apply them.\n- **Save** applies global and current-project drafts; **Cancel** discards unsaved changes. Both stay in settings. Use Home to return. Only the current project is editable; choose General, an engine or a component in the panel.\n- **Tab / Shift+Tab** moves between fields and actions; **↑↓** selects fields; **←→** selects action buttons. In fields, **←** returns to the panel and **→** does nothing outside editing.\n- **Enter** starts inline editing at the end / finishes editing.\n- In field navigation **e** opens the editor; **d** clears the value.\n- **Ctrl+Space / Alt+Enter** inserts a newline during editing.\n- Choices use Enter → arrows → Enter. Alt + Navigation also scrolls settings.\n\n## Commands\n\n`/help`, `/agent`, `/new`, `/clone`, `/details`, `/stop`.\nOnly registered components appear in slash completion.\n`/tools config`: toggle built-in tools and manage packages.\nAnswer questions in the existing composer, then press Enter. **Ctrl+C** copies selected text; without a selection it does nothing in the composer. **Ctrl+V** pastes the system clipboard. In writable inputs, **Ctrl+A** selects all, **Ctrl+X** cuts, and **Ctrl+Z / Ctrl+Y** undoes / redoes. Select with **Shift+Navigation** or mouse dragging.\nPath completion inserts text; it does not attach files.\n',
    "settings_dependencies": "Required components: {names}",
    "settings_dependencies_missing": "Missing required components: {names} — select them before saving.",
    "title_model_required": "No model is available for automatic titles. Set the conversation engine model in Settings.",
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

    'model_required': 'Set config.parameters.engines.loop.config.completion.model in Settings auxiliary button → Engine loop.',



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
    "agent_command_usage": "Use /agent <id>. Available Agents: {agents}",
    "shortcut_retry": "retry",

    "history_delete_confirm": "Remove this request and response from the conversation and future context?\nExecution records and original storage events are retained.",

    "history_busy": "Finish active work, queued requests and automatic naming before changing conversation history.",

    "clone_latest": "Clone all conversation", "clone_boundary": "Last request and response to include",

    "project_activity": "Project execution history", "activity_empty": "No execution history recorded.",


    "activity_source": "Session: {session_id} · Run: {run_id}",

    "activity_step": "Step: {step_id}", "activity_code": "Code: {code}",

    "help_history": "Auxiliary → Requests: delete, edit, retry\nAuxiliary → Logs: project execution history\nSession list Ctrl+←→: Resize sidebar\nChoose the last request and response to include in the clone dialog.",

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

    "component_help": "/{command} config: open manager\n/{command} help: show help\n\nImport a path, edit or delete entries in the manager. Prompt, Agent and Workflow support Create and popup JSON editing; Workflow also shows a live diagram. Settings opens this component's project settings page.\nMemory, Goals and Refinement work through model tools and processing policies.",

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

    "settings_json_object": "The settings JSON must be an object.",
    "settings_json_updated": "JSON changes are in the draft. Use Save to apply them.",
    "footer": "ESC panel · Ctrl+S auxiliary · Ctrl+D stop · Alt + Navigation scroll · Enter send mode",

    "footer_short": "ESC panel · Alt + Navigation scroll",

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

    "command_help": "Show help", "command_agent": "Choose an Agent", "command_new": "Create a conversation",

    "command_clone": "Clone this conversation",

    "command_stop": "Interrupt the active Run", "command_details": "Output tag bar",

    "file": "File", "directory": "Directory", "engine": "Engine", "agent": "Agent",

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
