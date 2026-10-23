"""English UI strings."""

from hub.locales.keys import MessageKey



MESSAGES = {
    MessageKey.OPERATION_EXISTING_RAG_JOB: 'An unfinished registration exists for this document. Select it in the RAG list to retry, or delete it before registering again.',
    MessageKey.OPERATION_DIAGNOSTICS: 'Hub operation diagnostics · current process',
    MessageKey.OPERATION_CHANGED: 'The state changed during this operation. Reload and review it before trying again. Your draft is preserved.',
    MessageKey.OPERATION_INTERACTION: 'Approval or an answer is required before resuming. Review the request in the input area. Denied work is never replayed automatically.',
    MessageKey.OPERATION_BUSY: 'Work is still running. Wait for it to finish, or stop it before trying again.',
    MessageKey.OPERATION_CONFIGURATION: 'RAG settings differ from those used to prepare this job. Check the settings and register the source again.',
    MessageKey.OPERATION_CHECKPOINT_UNAVAILABLE: 'Execution records are missing or damaged. Review the log details. Work may already have taken effect, so it will not restart automatically.',
    MessageKey.OPERATION_ENGINE: 'The original engine is unavailable. Check the project engine registration and configuration.',
    MessageKey.OPERATION_ALREADY_RESUMED: 'This request has already been resumed. Reopen request history to review the latest attempt.',
    MessageKey.OPERATION_RESTART_UNPROVEN: 'A safe restart from the beginning cannot be established. Review previous effects before deciding to submit a new request.',
    MessageKey.OPERATION_NOT_RESUMABLE: 'This operation cannot resume in its current state. Refresh the request or registration list. Detailed diagnostics are available in the log.',
    MessageKey.OPERATION_ABANDON_REQUIRED: 'These records are needed for resume. Review abandonment in the deletion dialog before deleting them.',
    MessageKey.OPERATION_FAILED: 'The operation could not be completed. Check your input and current state. Open the log for Hub operation diagnostics.',
    MessageKey.SYSTEM: 'System',
    MessageKey.USAGE_DAILY_TITLE: 'Daily token usage · up to 30 recent days',
    MessageKey.USAGE_DAILY_RANGE: '{start} ~ {end} · combined project reporting windows',
    MessageKey.USAGE_DAILY_UNKNOWN: 'Calls without reported usage: {count} (excluded from token totals)',
    MessageKey.PROMPTS_TITLE: 'Prompt manager',
    MessageKey.PROMPTS_IMPORT_HINT: 'Prompt JSON path → Enter: validate and import',
    MessageKey.DEFINITION_EDIT: 'Edit',
    MessageKey.DEFINITION_MANAGER_HINT: 'Tab regions · ↑↓ select · Enter edit in popup · d delete · Create button below',
    MessageKey.DEFINITION_NAME: 'Name (letters, digits, underscore, hyphen; 1–64 characters)',
    MessageKey.DEFINITION_NAME_INVALID: 'Use 1–64 letters, digits, underscores or hyphens for the name.',
    MessageKey.DEFINITION_OBJECT: 'Enter a JSON object.',
    MessageKey.DEFINITION_TOO_LARGE: 'Popup editing supports up to 1,048,576 characters.',
    MessageKey.DEFINITION_VALIDATION: 'The backend validates the full definition on Save. Tab selects Save/Cancel.',
    MessageKey.DEFINITION_SAVE_FAILED: 'Save failed. Review the error and correct the draft; your input is preserved.',
    MessageKey.WORKFLOW_PREVIEW: 'Live workflow preview',
    MessageKey.WORKFLOW_INVALID: 'Cannot draw the current JSON draft.',
    MessageKey.SHORTCUT_RESTART: 'Restart',
    MessageKey.SHORTCUT_TABS: 'Tabs',
    MessageKey.SETTINGS_TAB_CONFIG: 'Settings',
    MessageKey.SETTINGS_TAB_POLICY: 'Policy',
    MessageKey.SETTINGS_TAB_EMPTY: 'No settings in this tab.',
    MessageKey.CANCEL_WAIT: 'Cancellation requested. Waiting for the active call and storage cleanup to finish. This execution mode cannot force termination while preserving the shell and other work.',
    MessageKey.RAG_PHASE_CANCELLING: 'Stopping',
    MessageKey.SETTINGS_DESC_TITLE: 'Project display name.',
    MessageKey.SETTINGS_DESC_COMPONENTS: 'Components available to this project.',
    MessageKey.SETTINGS_DESC_MODEL: 'Provider model identifier used for this model call.',
    MessageKey.SETTINGS_DESC_API_BASE: 'Base URL of the provider or compatible inference server.',
    MessageKey.SETTINGS_DESC_API_KEY: 'Credential supplied to the selected provider.',
    MessageKey.SETTINGS_DESC_TEMPERATURE: 'Sampling temperature controlling variation in generated output.',
    MessageKey.SETTINGS_DESC_MAX_TOKENS: 'Maximum number of output tokens requested from the provider.',
    MessageKey.SETTINGS_DESC_TIMEOUT: 'Maximum time allowed for this operation, in seconds.',
    MessageKey.SETTINGS_DESC_SYSTEM_PROMPT: 'Instructions included in model requests for this engine.',
    MessageKey.SETTINGS_DESC_MAX_ATTEMPTS: 'Maximum number of attempts, including the initial attempt.',
    MessageKey.SETTINGS_DESC_MAX_RETRIES: 'Maximum number of additional attempts after the initial failure.',
    MessageKey.SETTINGS_DESC_DELAY_SECONDS: 'Initial waiting time between attempts, in seconds.',
    MessageKey.SETTINGS_DESC_MAX_DELAY_SECONDS: 'Upper bound on the waiting time between attempts, in seconds.',
    MessageKey.STOP: 'Stop',
    MessageKey.RESUME: 'Resume',
    MessageKey.COMPONENT_IMPORT: 'Add',
    MessageKey.SHORTCUT_STOP_RESUME: 'Stop/resume',
    MessageKey.PRESET_TITLE: 'Preset',
    MessageKey.PRESET_ADD: 'Add new preset',
    MessageKey.PRESET_EXPORT_CURRENT: 'Export current settings as preset',
    MessageKey.PRESET_EXPORT_HINT: 'Exports the saved global and project settings. Enter a name and save; editing JSON is optional.',
    MessageKey.MEMORY_TITLE: 'Memory manager',
    MessageKey.SETTINGS_RESTART_WAITING: 'Waiting for cancellation and storage cleanup. An unresponsive in-process tool cannot be forcibly killed. Hub will restart when cleanup completes.',
    MessageKey.PRESET_NAME: 'Preset name',
    MessageKey.PRESET_EDIT: 'Edit JSON',
    MessageKey.PRESET_APPLIED: 'Preset applied.',
    MessageKey.PRESET_DRAFTS: 'Save or discard your settings drafts first.',
    MessageKey.PRESET_HINT: 'Includes global and project settings. Edit the JSON, then save.',
    MessageKey.SETTINGS_GROUP_GENERAL: 'General',
    MessageKey.SETTINGS_GROUP_COMPLETION: 'Model calls',
    MessageKey.SETTINGS_GROUP_PROVIDER: 'Provider',
    MessageKey.SETTINGS_GROUP_CONTEXT: 'Context',
    MessageKey.SETTINGS_GROUP_RUN: 'Execution',
    MessageKey.SETTINGS_GROUP_APPROVAL: 'Approval',
    MessageKey.SETTINGS_GROUP_TOOL_RETRY: 'Tool retries',
    MessageKey.SETTINGS_GROUP_USAGE: 'Usage',
    MessageKey.SETTINGS_GROUP_RETENTION: 'Retention',
    MessageKey.SETTINGS_GROUP_OUTPUT: 'Output',
    MessageKey.SETTINGS_GROUP_PROCESSING: 'Processing',
    MessageKey.SETTINGS_GROUP_INGESTION: 'Registration',
    MessageKey.SETTINGS_OPTION_DESCRIPTION: 'Value for {name}. Use the type and constraints shown below.',
    MessageKey.SHORTCUT_AUXILIARY: 'Auxiliary buttons',
    MessageKey.SHORTCUT_RETURN: 'Return',
    MessageKey.ATTACH_TITLE: 'Attach file',
    MessageKey.ATTACH_HINT: 'Choose a file path to insert into the composer. Tab confirms path completion.',
    MessageKey.ATTACH_REQUIRED: 'Enter a file path.',
    MessageKey.SETTINGS_JSON: 'Settings JSON',
    MessageKey.SETTINGS_THEME_MODE: 'Theme',
    MessageKey.SETTINGS_THEME_MODE_DESCRIPTION: 'dark or light. Used for automatically derived surface and text colors.',
    MessageKey.CLIPBOARD_INTERNAL: 'Internal clipboard',
    MessageKey.CLIPBOARD_TITLE: 'Clipboard',
    MessageKey.CLIPBOARD_COPY_UNAVAILABLE: 'External copy failed; saved in Hub only',
    MessageKey.CLIPBOARD_READ_UNAVAILABLE: 'Cannot read the external clipboard. Use your terminal paste command.',
    MessageKey.CLIPBOARD_X11_LIBRARY: 'The libX11 library required for clipboard access is unavailable.',
    MessageKey.CLIPBOARD_X11_CONNECTION: 'Cannot connect to the X11 clipboard. Check DISPLAY and X11 access permissions.',
    MessageKey.CLIPBOARD_X11_TIMEOUT: 'The X11 clipboard timed out. Check the display connection.',
    MessageKey.CLIPBOARD_X11_TRANSFER: 'Cannot transfer X11 clipboard text. Your terminal paste command may still work.',

    MessageKey.PROFILE_SETTINGS: "Settings", MessageKey.PROFILE_HOME: "Home", MessageKey.PROFILE_MINIMIZE: "Minimize",
    MessageKey.SHORTCUT_PROFILE: "profile", MessageKey.SHORTCUT_CATEGORIES: "categories",
    MessageKey.HISTORY_RETRY_QUESTION_UNAVAILABLE: "Cancelled questions can reopen only for the latest request with no running or queued work. Edit an older request to start it again.",
    MessageKey.SETTINGS_DISCARD: "Cancel", MessageKey.SETTINGS_DISCARDED: "Discarded unsaved changes.",
    MessageKey.SETTINGS_ENGINE_ITEM: "Engine · {name}", MessageKey.SETTINGS_COMPONENT_ITEM: "Component · {name}",
    MessageKey.PROJECT_DELETE_LAST: "Create another project before deleting the last project.",
    MessageKey.FAILURE_EVIDENCE: "Diagnostic evidence: {evidence}",
    MessageKey.FAILURE_DETAILS: "Detailed execution records are available from the auxiliary Log button.",
    MessageKey.FAILURE_CHECKPOINT_CONFLICT: "The operation changed. Review its current state before trying again.",
    MessageKey.FAILURE_CHECKPOINT_PROTECTED: "This operation cannot resume. Review the blocking conditions.",
    MessageKey.FAILURE_UNKNOWN: "The stored evidence does not identify the exact cause. Check for file changes or commands already executed before retrying from the auxiliary Request history button. If it happens again, include the detailed records when reporting it.",
    MessageKey.FAILURE_RATE_LIMIT: "The model provider rejected the request due to a usage or rate limit. Wait before retrying. If it persists, check the provider's API quota, balance and billing status. This code alone does not mean the API key is invalid.",
    MessageKey.FAILURE_UNAVAILABLE: "An error occurred while connecting to the model provider or receiving its response. Check the provider's service status and retry later. If it persists, consider another available model.",
    MessageKey.FAILURE_AUTHENTICATION: "The model provider rejected authentication. Check that the configured API key belongs to the selected provider and has not expired or been revoked.",
    MessageKey.FAILURE_PERMISSION: "The operation lacked the required access. Check model/project access or the permissions of the target file and directory.",
    MessageKey.FAILURE_TIMEOUT: "The operation exceeded its time limit. Check network and provider status, split the task, or review timeout settings. Check for completed effects before retrying.",
    MessageKey.FAILURE_CONNECTION: "The network connection failed. Check internet access, DNS, proxy and firewall settings.",
    MessageKey.FAILURE_REQUEST: "The model provider rejected the request format or target. Check the model name, API endpoint and supported options; reduce large inputs or attachments if needed.",
    MessageKey.FAILURE_CONTEXT: "The conversation exceeded the model's input capacity. Split the input or start a new session with only the necessary context.",
    MessageKey.FAILURE_BUDGET: "The project's configured usage budget has been reached. Review usage statistics and the usage policy, and adjust the limit if needed.",
    MessageKey.FAILURE_FILE_MISSING: "A required file or directory could not be found. Check the path and ish's current working directory.",
    MessageKey.FAILURE_EMPTY: "The model provider returned an empty or unreadable response. Retry later; if it persists, check the model name and provider connection settings.",
    MessageKey.FAILURE_COMPLETION_INVALID: "The model response did not match the expected format. Check the provider/server's streaming and Tool calling compatibility. Received output is preserved; the operation is not automatically replayed.",
    MessageKey.FAILURE_COMPLETION_LENGTH: "The model reached its output token limit before finishing. Review the model's output token setting or split the task. Received output is preserved.",
    MessageKey.FAILURE_COMPLETION_FILTERED: "The provider's content filter ended the response. Review the request and the provider's usage policy. The same request is not automatically repeated.",
    MessageKey.FAILURE_COMPLETION_INCOMPLETE: "The model response did not finish normally. Received output is preserved. Check the finish reason in the logs and the provider's status before deciding whether to retry.",
    MessageKey.FAILURE_COMPLETION_OUTPUT_LIMIT: "The configured output character limit was reached. Review the engine's output size limit or split the task.",
    MessageKey.FAILURE_COMPLETION_TOOL_LIMIT: "The model requested more Tools in one response than permitted. Review the engine's Tool call limit or split the task.",
    MessageKey.FAILURE_COMPLETION_ARGUMENT_LIMIT: "Generated Tool arguments or a call ID exceeded the configured length limit. Review the engine's Tool argument size limit or split the task.",
    MessageKey.FAILURE_STEP_TIMEOUT: "An execution step exceeded its configured deadline. Review the engine's step time limits. Check for completed effects before retrying.",
    MessageKey.FAILURE_REQUEST_SIZE: "The server rejected the request size. Reduce the input or attachment size before retrying.",
    MessageKey.FAILURE_DEPENDENCY: "A required library could not be loaded. Check the installed dependencies in the execution environment.",
    MessageKey.FAILURE_COMMAND: "A command exited with an error. Check its output and any changes already made before retrying.",
    MessageKey.FAILURE_TOOL_FAILED: "A Tool could not finish its operation. Check its diagnostic details and any completed effects before retrying.",
    MessageKey.FAILURE_VALIDATION: "The supplied value does not meet the input requirements. Correct the value and try again.",
    MessageKey.FAILURE_DISCONNECTED: "Hub is not connected to its backend. Reopen Hub after the backend is ready.",
    MessageKey.FAILURE_TOOL_ARGUMENTS: "The Tool name or arguments did not meet the Tool's requirements. Check the requested operation and values.",
    MessageKey.FAILURE_TOOL_CONTRACT: "The Tool's execution requirements were not satisfied. Check its runner, isolation and operation-key configuration.",
    MessageKey.FAILURE_LIFECYCLE: "The operation is no longer active. Refresh its state before making another request.",
    MessageKey.FAILURE_SANDBOX: "The required sandbox is unavailable. Check the sandbox installation and configuration; protection is not disabled automatically.",
    MessageKey.FAILURE_OPERATION_CONFLICT: "This operation key belongs to a different Tool or set of arguments. Check the existing operation before continuing.",
    MessageKey.FAILURE_OPERATION_UNCERTAIN: "This operation may already have taken effect. Verify the actual result before deciding how to continue; it is not automatically replayed.",
    MessageKey.GENERAL_CATEGORY_DIAGNOSTICS: "Provider diagnostics",
    MessageKey.GENERAL_PROVIDER_REQUEST_SUMMARY: "Record message roles, lengths and Tool call/result pairing counts. Message bodies and Tool arguments are excluded. Default true. Applies after restarting Hub.",
    MessageKey.GENERAL_CATEGORY_RECOVERY: "Error analysis and recovery",
    MessageKey.GENERAL_RECOVERY_ENABLED: "Reduce rejected context before any response arrives. Corrected calls stay within the Loop Provider attempt and deadline limits. Default true. Applies after restarting Hub.",
    MessageKey.GENERAL_RECOVERY_MODEL_ANALYSIS: "Ask the current model to explain bounded error evidence and recommend an action. Requires an extra API call; no tools are enabled. Default true.",
    MessageKey.GENERAL_RECOVERY_TIMEOUT_SECONDS: "Seconds available for analysis and summaries. Default 60. Corrected calls retain the original Provider deadline; execution deadlines are never extended.",
    MessageKey.GENERAL_RECOVERY_INPUT_CHARS: "Character budget for the prior summary and source fragment per summary call. Default 12000. This is not a token count.",
    MessageKey.GENERAL_RECOVERY_SUMMARY_CHARS: "Maximum summary characters. Default 2000. The current request and steering instructions are preserved.",
    MessageKey.GENERAL_RECOVERY_MAX_SUMMARY_CALLS: "Maximum summary calls in one recovery. Default 16. Partial summaries are never used for retry.",
    MessageKey.FAILURE_SERVER_DETAIL: "Server error details",
    MessageKey.FAILURE_MODEL_ANALYSIS: "Model error analysis · may include inference",
    MessageKey.RECOVERY_REJECTED: "Recovery proposal rejected · {attempt}/{maximum}",
    MessageKey.RECOVERY_RESULT_REJECTED: "The backend rejected the input repair. The original failure is preserved.",
    MessageKey.RECOVERY_ANALYZING: "Analyzing the error · recovery {attempt}/{maximum}",
    MessageKey.RECOVERY_COMPACTING: "Summarizing long input · recovery {attempt}/{maximum}{progress}",
    MessageKey.RECOVERY_RETRYING: "Retrying with reduced input · recovery {attempt}/{maximum}",
    MessageKey.RECOVERY_RESULT_NO_CORRECTION: "No supported automatic correction was found. Check the input or settings using the guidance above.",
    MessageKey.RECOVERY_RESULT_SUMMARY_INCOMPLETE: "The summary budget was exhausted before completion; no retry was made. Originals are preserved. Split the input or adjust the recovery budget.",
    MessageKey.RECOVERY_RESULT_NOT_REDUCED: "The summary did not reduce input size; no retry was made. Check the current request and tool definitions.",
    MessageKey.RECOVERY_RESULT_INVALID_ANALYSIS: "The model returned invalid analysis, so automatic recovery stopped. See the original error.",
    MessageKey.RECOVERY_RESULT_ANALYSIS_FAILED: "Error analysis or summarization could not finish. The original error and work records are preserved.",
    MessageKey.RECOVERY_RESULT_EXHAUSTED: "The configured recovery attempts were exhausted. Original records and completed work are preserved.",
    MessageKey.GENERAL_PROVIDER_MAX_DETAIL_CHARS: "Maximum characters per detailed error string. Default 2000; a positive integer. Used when error details are enabled. Applies after restarting Hub.",
    # Accepted only for saved language packs; no composer preview UI/binding remains.
    MessageKey.PREVIEW: "MARKDOWN PREVIEW",
    MessageKey.COMMAND_PREVIEW: "Toggle Markdown preview",
    MessageKey.SHORTCUT_PREVIEW: "preview",
    MessageKey.HISTORY_REQUESTS: "Requests",
    MessageKey.SETTINGS_CURRENT_PROJECT: "Project : {title}",
    MessageKey.SETTINGS_CLOSE: "Close",
    MessageKey.SHORTCUT_PASTE: "Paste",
    MessageKey.OBJECT_COPY_CODE: "Copy code",
    MessageKey.OBJECT_COPY_CELL: "Copy cell",
    MessageKey.SETTINGS_REQUIRES_RESTART: "Restart required",
    MessageKey.SETTINGS_RESTART_TITLE: "Restart Hub",
    MessageKey.SETTINGS_RESTART_NOW: "Restart",
    MessageKey.SETTINGS_RESTART_CONFIRM: "Saved. Restart Hub to apply these settings:\n\n{fields}\n\nRestart now?\nEnter: restart · Esc: later (keep saved values)",
    MessageKey.SETTINGS_RESTARTING: "Restarting Hub",
    MessageKey.GENERAL_CATEGORY_STORAGE: "Data storage",
    MessageKey.GENERAL_WORKSPACE_ROOT: "Hub data root. Enter an absolute path or ~/path. Takes effect after restarting Hub. Existing data is not moved; an empty directory starts a new workspace. Leave blank to use the original launch path.",
    MessageKey.OBJECT_QUERY: 'Search / filter:',
    MessageKey.OBJECT_RAW: 'Source',
    MessageKey.OBJECT_RENDERED: 'Rendered',
    MessageKey.OBJECT_EMPTY: 'No matching rows.',
    MessageKey.OBJECT_LOADING: 'Preparing table…',
    MessageKey.OBJECT_TABLE_COUNT: 'Row {index}/{visible} · {total} total · Column {column}',
    MessageKey.SHORTCUT_OBJECT_RAW: 'Source/rendered',
    MessageKey.SHORTCUT_OBJECT_SORT: 'Sort column',
    MessageKey.SHORTCUT_CANCEL: 'Cancel queued',
    MessageKey.PREPARING_RESPONSE: 'Preparing…',
    MessageKey.STATUS_PREPARING: '{icon_preparing} Preparing',
    MessageKey.STATUS_THINKING: '{icon_reasoning} Thinking',
    MessageKey.STATUS_RETRYING: '{icon_preparing} Retrying',
    MessageKey.STATUS_INTERRUPTING: '{icon_interrupted} Stopping',
    MessageKey.FOLLOWUP_HINT: '→ accept suggestion',
    MessageKey.COMPONENT_ACTION_INSTALL: 'Install a built-in skill (preserve existing entries)',
    # Keep the key for saved language packs; the removed install command is not advertised.
    MessageKey.SKILLS_INSTALL_HELP: '/skills config\nEnter a built-in skill Markdown path in the manager to import it. Existing IDs are never overwritten.',
    MessageKey.HISTORY_CANCEL: 'Cancel queued',
    MessageKey.HISTORY_CANCEL_CONFIRM: 'Cancel this queued request? The active Run will continue.',
    MessageKey.HISTORY_CANCEL_UNAVAILABLE: 'Only queued requests can be cancelled. Use Ctrl+D to interrupt an active Run.',
    MessageKey.HISTORY_CHECKPOINT_PROTECTED: 'An unfinished Run checkpoint references this turn. Choose delete again to review and confirm abandoning its dependent resumes.',
    MessageKey.HISTORY_ABANDON_DELETE: 'Abandon resumes and delete',
    MessageKey.HISTORY_DELETE_CHANGED: 'The conversation or dependent Runs changed after review. Choose delete again to review the current plan.',
    MessageKey.HISTORY_ABANDON_CONFIRM: 'Abandon resume for the following {count} Runs and delete this turn?\nPreviously performed effects will not be undone.\nThe turn is excluded from future context; execution records and checkpoints are retained.',

    MessageKey.SHORTCUT_MOVEMENT: 'Navigation',
    MessageKey.SHORTCUT_CLEAR: 'Clear',
    MessageKey.SHORTCUT_TAGS: 'Tags',
    MessageKey.SHORTCUT_EXPAND: 'Expand',
    MessageKey.SHORTCUT_COPY: 'Copy raw',
    MessageKey.SHORTCUT_OPEN_DIRECTORY: 'Open directory',
    MessageKey.SETTINGS_SEARCH: 'Find settings',
    MessageKey.SETTINGS_SEARCH_HINT: 'Match a key, description or value. Leave empty to show all.',
    MessageKey.SETTINGS_SEARCH_EMPTY: 'No matching settings.',
    MessageKey.SETTINGS_SEARCH_ACTIVE: 'Filter: {query}',
    MessageKey.CLIPBOARD_COPIED: 'Raw text copied.',
    MessageKey.CLIPBOARD_TERMINAL: 'Copy requested through the terminal (OSC 52); text also stored in the application clipboard.',
    MessageKey.COMPONENT_ACTION_CONFIG: 'Open component manager',
    MessageKey.COMPONENT_MANAGER_UNAVAILABLE: 'The {name} manager is not available yet. Use settings or data commands.',
    MessageKey.TAGS_TITLE: 'Tag bar',
    MessageKey.TAGS_EMPTY: 'No output objects.',
    MessageKey.TOOLS_TITLE: 'Tools',
    MessageKey.SKILLS_TITLE: 'Skills',
    MessageKey.MCP_TITLE: 'MCP servers',
    MessageKey.RAG_TITLE: 'RAG documents',
    MessageKey.WORKFLOWS_TITLE: 'Workflows',
    MessageKey.SKILLS_IMPORT_HINT: 'Standard Skill directory or SKILL.md path → Enter to import',
    MessageKey.MCP_IMPORT_HINT: 'MCP server JSON file path → Enter to validate and import',
    MessageKey.RAG_IMPORT_HINT: 'Text/Markdown/PNG/JPEG path → Enter to import (images: choose OCR or model analysis)',
    MessageKey.WORKFLOWS_IMPORT_HINT: 'Workflow JSON file path → Enter to validate and import',
    MessageKey.MANAGER_HINT: 'Tab: region · ↑↓: select · Enter: edit in editor · d: delete',
    MessageKey.MANAGER_EMPTY: 'No items registered.',
    MessageKey.MANAGER_IMPORTED: 'Imported.',
    MessageKey.MANAGER_SAVED: 'Saved.',
    MessageKey.RAG_PHASE_QUEUED: 'Queued for registration',
    MessageKey.RAG_PHASE_PREPARING: 'Preparing registration',
    MessageKey.RAG_PHASE_EMBEDDING: 'Embedding',
    MessageKey.RAG_PHASE_VALIDATING_INPUT: 'Validating input',
    MessageKey.RAG_PHASE_SPLITTING: 'Splitting chunks',
    MessageKey.RAG_PHASE_EXTRACTING: 'Extracting relations',
    MessageKey.RAG_PHASE_VALIDATING: 'Validating index',
    MessageKey.RAG_PHASE_WRITING_METADATA: 'Writing metadata',
    MessageKey.RAG_PHASE_PUBLISHING: 'Publishing index',
    MessageKey.RAG_PHASE_PREPARING_DOCUMENTS: 'Preparing documents',
    MessageKey.RAG_PHASE_IMAGE_OCR: 'Extracting image text',
    MessageKey.RAG_PHASE_IMAGE_ANALYSIS: 'Analyzing image',
    MessageKey.RAG_PHASE_INDEXING: 'Storing index',
    MessageKey.RAG_PHASE_COMPLETED: 'Registered',
    MessageKey.RAG_PHASE_FAILED: 'Registration failed',
    MessageKey.RAG_PHASE_CANCELLED: 'Cancelled',
    MessageKey.RAG_PHASE_UNCONFIRMED: 'Execution state needs verification',
    MessageKey.RAG_JOB_COUNTS: 'Embedding {done}/{total} chunks · Checkpoints reused {reused} · Extraction {batches} batches',
    MessageKey.RAG_JOB_TIMES: 'Embedding {embedding}s · Extraction {extraction}s · This attempt elapsed {elapsed}s',
    MessageKey.RAG_JOB_RETRY_HINT: 'Retry reuses completed chunks. An active job cannot be resumed.',
    MessageKey.RAG_JOB_RETRY_CHECK: "Retry checks whether this job can currently resume.",
    MessageKey.RAG_JOB_READONLY: 'Failed/cancelled jobs can be deleted. Cancelled jobs cannot be resumed.',
    MessageKey.RAG_STAGE_COUNTS: 'Current stage {done}/{total} {unit}',
    MessageKey.RAG_UNIT_CHUNK: 'chunks',
    MessageKey.RAG_UNIT_BATCH: 'batches',
    MessageKey.RAG_UNIT_DOCUMENT: 'documents',
    MessageKey.RAG_UNIT_JOB: 'jobs',
    MessageKey.RAG_OBSERVED_TIMES: 'Observed time: ',
    MessageKey.RAG_CANCEL_JOB: 'Cancel job',
    MessageKey.RAG_CANCEL_UNAVAILABLE: 'Select a queued or running registration job.',
    MessageKey.RAG_DELETE_UNAVAILABLE: 'Only inactive failed/cancelled jobs can be deleted. Cancel active jobs first.',
    MessageKey.RAG_DELETE_CONFIRM: 'Delete saved input and checkpoints for {name} ({bytes} bytes)?\nResume will be unavailable. Published documents and indexes are preserved.',
    MessageKey.RAG_IMAGE_TITLE: 'Import image document',
    MessageKey.RAG_IMAGE_ANALYSIS: 'Model analysis',
    MessageKey.RAG_VISION_SETTINGS: 'Vision setup',
    MessageKey.RAG_IMAGE_HINT: 'OCR extracts text using the configured Vision OCR backend.\nModel analysis interprets diagrams/tables with the configured vision model and may incur API costs.\nVision setup enables the component and opens its settings page.\nThe document preserves image provenance and the extraction method.',
    MessageKey.RAG_RETRY_UNAVAILABLE: 'Select an inactive unfinished registration job. Completed or cancelled jobs cannot be retried.',
    MessageKey.MANAGER_DELETE_CONFIRM: 'Delete {name}?',
    MessageKey.TOOLS_IMPORT_HINT: 'Directory path → Enter to import <name>/<name>.py and optional requirements.txt',
    MessageKey.TOOLS_COLUMNS: 'Name / Last modified / Description',
    MessageKey.TOOLS_HINT: 'Tab: region · ↑↓: select · Space: toggle built-in · Enter: folder · d: delete',
    MessageKey.TOOLS_EMPTY: 'No tools registered.',
    MessageKey.TOOLS_IMPORTED: 'Tool imported. Prepare dependencies and enable it separately before use.',
    MessageKey.TOOLS_FAILED: 'Operation failed; see the error notification.',
    MessageKey.TOOLS_EXPLORER_FAILED: 'File manager exited with code {code}',
    MessageKey.TOOLS_DELETE_CONFIRM: 'Delete {name}? If enabled, it will also be disabled.',
    MessageKey.WELCOME_KEYS: 'ESC Panel · c in panel: New session · Ctrl+S Auxiliary',
    MessageKey.SESSION_REQUIRED: 'Create a session first: ESC, then c in the session list.',
    MessageKey.ACTIVITY_HINT: 'Last 300 activity / provider entries each · Navigation: scroll · ESC / Enter / Ctrl+L: close',
    MessageKey.ACTIVITY_PROVIDER_PROJECT: 'Provider log · Current project',
    MessageKey.ACTIVITY_PROVIDER_FILE: 'Log file: {path}',
    MessageKey.ACTIVITY_PROVIDER_TRACEBACK: 'Traceback · Stored exceptions and frames (original error messages omitted)',
    MessageKey.ACTIVITY_PROVIDER_RUN_DETAILS: 'Provider details for this Run:',
    MessageKey.ACTIVITY_PROVIDER_WORKSPACE: 'Provider log · Workspace-wide',
    MessageKey.STATUS_WAITING_INPUT: '{icon_pending} Awaiting reply',
    MessageKey.STATUS_WAITING_APPROVAL: '{icon_waiting_approval} Awaiting approval',
    MessageKey.STATUS_ANSWER_SENDING: '{icon_waiting} Sending answer',
    MessageKey.STATUS_APPROVED: '{icon_completed} Approved',
    MessageKey.STATUS_DENIED: '{icon_cancelled} Denied',
    MessageKey.STATUS_ANSWERED: '{icon_completed} Answer delivered',
    MessageKey.STATUS_RESUMING: '{icon_running} Resuming',
    MessageKey.HELP: '## Panels and Home\n\n- **ESC** moves from main content to the panel.\n- **Ctrl+Q** opens Hub from the shell and minimizes it from any Hub screen. Shell Ctrl+S stays unchanged.\n- **Tab / Shift+Tab** changes panel categories. **↑↓** stays within the current category. **Space / Enter / →** moves to main content.\n- **Ctrl+← / Ctrl+→** adjusts and automatically saves only panel width.\n- **Ctrl+P** focuses the shared profile footer. It offers Home/Settings and a settings preset dropdown. **←→** selects a button; **Enter / Space** activates it. Tab does nothing in the profile footer.\n- Select projects in the upper Home category and sessions below.\n- **c / d / e / r** in the panel creates / deletes / renames / clones.\n- **Enter** sends; during execution choose steering or follow-up.\n- **↑↓** in an empty composer recalls previous/next user requests. Editing exits history navigation.\n- **Ctrl+Space** inserts a newline. **↑↓** selects completions; **Tab** confirms.\n- **Ctrl+W** restarts Hub from any Hub screen. Active work blocks restart with an explanation.\n- **Ctrl+D** in the Home composer stops the running or paused request, or resumes the last interrupted request; queued requests are preserved.\n- `/agent <name>` sets the engine.\n- **Ctrl+S** focuses auxiliary buttons from the composer or settings actions. Left / Right selects; Up / Down does nothing. Enter / Space activates, and ESC returns.\n- **Auxiliary → Find** searches the session. Enter next / Alt+P previous. Closing clears highlights.\n- **Auxiliary buttons** open request history, tags, search, file attachment, logs, and settings. File attachment inserts a path into the composer. Settings opens the Loop engine settings.\n\n## Scrolling and readers\n\nConversation output uses **Alt + Navigation**.\nHelp, activity and query popups use **Navigation alone**.\nNavigation includes ↑↓←→ / Home / End / PgUp / PgDn.\nClose readers with **ESC / Enter / Ctrl+L**.\n\n## Output tag bar\n\n**Auxiliary → Tags** opens it; **↑↓** selects an object and scrolls to its position.\n**c** copies raw source; **ESC** closes it. **Tab / Space / Enter** opens a full-screen popup.\nCode, tables, images, diffs and registered XML renderers appear here.\n\n## Settings\n\n- **Filter button / Ctrl+F** filters keys, descriptions and values across panel selections; empty query clears it. The JSON button opens the current draft in your configured editor. Changes return to the draft; use Save to apply them.\n- **Save** applies global and current-project drafts; **Cancel** discards unsaved changes. Both stay in settings. Use Home to return. Only the current project is editable; choose General, an engine or a component in the panel.\n- **Tab / Shift+Tab** moves between fields and actions; **↑↓** selects fields; **←→** selects action buttons. In fields, **←** returns to the panel and **→** does nothing outside editing.\n- **Enter** starts inline editing at the end / finishes editing.\n- In field navigation **e** opens the editor; **d** clears the value.\n- **Ctrl+Space / Alt+Enter** inserts a newline during editing.\n- Choices use Enter → arrows → Enter. Alt + Navigation also scrolls settings.\n\n## Commands\n\n`/help`, `/agent`, `/new`, `/clone`, `/details`, `/stop`.\nOnly registered components appear in slash completion.\n`/tools config`: toggle built-in tools and manage packages.\nAnswer questions in the existing composer, then press Enter. **Ctrl+C** copies selected text; without a selection it does nothing in the composer. **Ctrl+V** pastes the system clipboard. In writable inputs, **Ctrl+A** selects all, **Ctrl+X** cuts, and **Ctrl+Z / Ctrl+Y** undoes / redoes. Select with **Shift+Navigation** or mouse dragging.\nPath completion inserts text; it does not attach files.\n',
    MessageKey.SETTINGS_DEPENDENCIES: "Required components: {names}",
    MessageKey.SETTINGS_DEPENDENCIES_MISSING: "Missing required components: {names} — select them before saving.",
    MessageKey.TITLE_MODEL_REQUIRED: "No model is available for automatic titles. Set the conversation engine model in Settings.",
    MessageKey.OUTPUT_IMAGE_LIMIT: "Image preview limit reached for this view (32 images).",
    MessageKey.WELCOME_TITLE: "No sessions yet",
    MessageKey.WELCOME_HINT: "Create a session to start a conversation.",
    MessageKey.OUTPUT_PENDING: "Receiving output block…",
    MessageKey.OUTPUT_IMAGE: "Image · {caption}",
    MessageKey.OUTPUT_IMAGE_LOADING: "Converting image…",
    MessageKey.OUTPUT_ERROR: "Unable to display output: {error}",
    MessageKey.PROGRESS_MORE: " · +{count} more",
    MessageKey.PROGRESS_SETTINGS_LOAD: "Loading settings",
    MessageKey.PROGRESS_SETTINGS_WORK: "Applying settings operation",
    MessageKey.PROGRESS_COMPONENT: "Processing {command}",
    MessageKey.PROGRESS_COMPONENT_DONE: "Completed: {identifier}",
    MessageKey.SETTINGS_STORAGE_DESCRIPTION: "Conversation storage. Blank uses file for a new project. Cannot change while Sessions exist; null is reserved for an injected store.",
    MessageKey.SETTINGS_REASONING_EFFORT: "Model reasoning effort, e.g. low, medium or high. Supported values vary by model; blank uses the default.",
    MessageKey.SEND_WHILE_RUNNING: 'Send while running',

    MessageKey.SEND_STEERING: 'Live instruction — steer the current Run',

    MessageKey.SEND_FOLLOW_UP: 'Follow-up — run after the current work',

    MessageKey.INSTRUCTION_RUN_CHANGED: 'The active Run changed. Your draft is preserved; choose how to send it again.',

    MessageKey.MODEL_REQUIRED: 'Set config.parameters.engines.loop.config.completion.model in Settings auxiliary button → Engine loop.',



    MessageKey.HISTORY_TITLE: "Requests and work", MessageKey.HISTORY_EMPTY: "No saved requests.",

    MessageKey.HISTORY_HINT: "↑↓ move · Space check · d delete · e edit · r retry · Tab regions",

    MessageKey.HISTORY_RESPONSE: "Response", MessageKey.HISTORY_DELETE: "Delete",
    MessageKey.HISTORY_EDIT: "Edit", MessageKey.HISTORY_RETRY: "Retry", MessageKey.HISTORY_CLOSE: "Close",
    MessageKey.HISTORY_USER_MESSAGE: "User message",
    MessageKey.HISTORY_SELECT_ONE: "Check exactly one request to edit or retry.",
    MessageKey.HISTORY_SELECT_ITEMS: "Check the requests to delete.",
    MessageKey.HISTORY_RETRY_UNAVAILABLE: "Only interrupted or failed requests can be retried.",
    MessageKey.HISTORY_EDIT_UNAVAILABLE: "Only inactive requests can be edited and run again.",
    MessageKey.HISTORY_RETRY_BLOCKED: "Cannot retry from the checkpoint: {details}",
    MessageKey.HISTORY_CHANGED: "The selected request changed. Reopen the list and select it again.",
    MessageKey.HISTORY_DELETE_SELECTED: "Delete {count} selected requests? Queued requests will be cancelled.",
    MessageKey.HISTORY_DELETING: "Deleting requests",
    MessageKey.AGENT_COMMAND_USAGE: "Use /agent <id>. Available Agents: {agents}",
    MessageKey.SHORTCUT_RETRY: "retry",

    MessageKey.HISTORY_DELETE_CONFIRM: "Remove this request and response from the conversation and future context?\nExecution records and original storage events are retained.",

    MessageKey.HISTORY_BUSY: "Finish active work, queued requests and automatic naming before changing conversation history.",

    MessageKey.CLONE_LATEST: "Clone all conversation", MessageKey.CLONE_BOUNDARY: "Last request and response to include",

    MessageKey.PROJECT_ACTIVITY: "Project execution history", MessageKey.ACTIVITY_EMPTY: "No execution history recorded.",


    MessageKey.ACTIVITY_SOURCE: "Session: {session_id} · Run: {run_id}",

    MessageKey.ACTIVITY_STEP: "Step: {step_id}", MessageKey.ACTIVITY_CODE: "Code: {code}",

    MessageKey.HELP_HISTORY: "Auxiliary → Requests: delete, edit, retry\nAuxiliary → Logs: project execution history\nSession list Ctrl+←→: Resize sidebar\nChoose the last request and response to include in the clone dialog.",

    MessageKey.SETTINGS_REGISTRATION: "Additional features", MessageKey.SETTINGS_ENGINES: "Engines",

    MessageKey.SETTINGS_TYPE: "- Type : {type}", MessageKey.SETTINGS_RENAME: "Rename project",

    MessageKey.SETTINGS_SIDEBAR_WIDTH: "Shared settings/chat sidebar width (terminal columns, 18–60)",


    MessageKey.SETTINGS_GLOBAL_REGISTRATION: "Select a project to choose its components and engines.",

    MessageKey.SETTINGS_PROJECT_KEYS: "c new · d delete\ne rename · r clone",

    MessageKey.SETTINGS_CANCELLED: "Discarded changes on this page.",

    MessageKey.SETTINGS_ENGINE_REQUIRED: "Select at least one engine.",

    MessageKey.SETTINGS_ENGINE_UNAVAILABLE: "Engine is not selected in this project: {name}",

    MessageKey.SETTINGS_HUB_DATA_OBJECT: "config.data and config.data.hub must be JSON objects.",

    MessageKey.MODEL_LINE: "Model: {model}",

    MessageKey.SESSION_DELETE: "Delete session", MessageKey.SESSION_RENAME: "Rename session",

    MessageKey.SESSION_DELETE_CONFIRM: "Soft-delete '{title}'? Its history will be preserved.",

    MessageKey.SESSION_NAME_REQUIRED: "Enter a session name.",

    MessageKey.SESSION_BUSY: "Finish active Runs, queued requests and automatic naming before deleting the session.",

    MessageKey.COMMAND_COMPONENT: "{name} data and settings",

    MessageKey.COMPONENT_ACTION_LIST: "List records", MessageKey.COMPONENT_ACTION_GET: "Get by ID",

    MessageKey.COMPONENT_ACTION_CREATE: "Create with ID and JSON", MessageKey.COMPONENT_ACTION_UPDATE: "Update with ID and JSON",

    MessageKey.COMPONENT_ACTION_DELETE: "Delete by ID", MessageKey.COMPONENT_ACTION_SETTINGS: "Open component settings",

    MessageKey.COMPONENT_ACTION_HELP: "Command usage",

    MessageKey.COMPONENT_UNAVAILABLE: "Component is not enabled in this project: {name}",

    MessageKey.COMPONENT_USAGE_ERROR: "Usage: /component config | /component help",

    MessageKey.COMPONENT_JSON_OBJECT: "Enter data as a JSON object.",

    MessageKey.COMPONENT_DELETE: "Delete component data",

    MessageKey.COMPONENT_DELETE_CONFIRM: "Delete '{identifier}' from {name}? This data deletion cannot be undone.",

    MessageKey.COMPONENT_LOADING: "Processing the component request…", MessageKey.COMPONENT_BUSY: "A component request is already in progress.",

    MessageKey.COMPONENT_HELP: "/{command} config: open manager\n/{command} help: show help\n\nImport a path, edit or delete entries in the manager. Prompt, Agent and Workflow support Create and popup JSON editing; Workflow also shows a live diagram. Settings opens this component's project settings page.\nMemory, Goals and Refinement work through model tools and processing policies.",

    MessageKey.SEARCH_TITLE: "Find in this session", MessageKey.SEARCH_HINT: "Type to search · Enter next · Alt+P previous · Esc close",

    MessageKey.SEARCH_COUNT: "{index} / {count} matches · Enter next · Alt+P previous",

    MessageKey.SEARCH_PREVIOUS: "Previous", MessageKey.SEARCH_NEXT: "Next", MessageKey.SEARCH_CLOSE: "Close",

    MessageKey.NOTIFICATION_TITLE: "Other sessions",






    MessageKey.SETTINGS_TITLE: "Settings", MessageKey.SETTINGS_GLOBAL: "Global settings", MessageKey.SETTINGS_PROJECTS: "Projects",

    MessageKey.SETTINGS_PROFILE: "Profile", MessageKey.SETTINGS_APPEARANCE: "Appearance", MessageKey.SETTINGS_MAIN: "Main settings",

    MessageKey.SETTINGS_COMPONENTS: "Components", MessageKey.SETTINGS_SAVE: "Save", MessageKey.SETTINGS_SAVED: "Saved.",

    MessageKey.SETTINGS_CREATE: "New project", MessageKey.SETTINGS_NEW_TITLE: "New project", MessageKey.SETTINGS_RELOAD: "Load",

    MessageKey.SETTINGS_OPEN_PROJECT: "Open", MessageKey.SETTINGS_CLONE: "Clone", MessageKey.SETTINGS_DELETE: "Delete",

    MessageKey.SETTINGS_COPY_SUFFIX: "copy", MessageKey.SETTINGS_DELETED: "Project soft-deleted.",

    MessageKey.SETTINGS_DELETE_CONFIRM: "Soft-delete '{title}'? Conversation history and files are retained.",

    MessageKey.SETTINGS_DELETE_ACTIVE: "This project is open in chat. Open another project's chat before deleting it.",

    MessageKey.SETTINGS_LOADING: "Loading…", MessageKey.SETTINGS_EMPTY: "No projects",

    MessageKey.SETTINGS_FOOTER: "ESC panel · ↑↓ fields · ←→ buttons · e editor · Enter edit · Tab regions",

    MessageKey.SETTINGS_GENERAL: "General",

    MessageKey.GENERAL_CATEGORY_LANGUAGE: "Language packs",

    MessageKey.GENERAL_CATEGORY_NOTIFICATIONS: "Notifications",

    MessageKey.GENERAL_CATEGORY_STARTUP: "Startup",

    MessageKey.GENERAL_CATEGORY_OUTPUT: "Conversation output",

    MessageKey.GENERAL_CATEGORY_EDITOR: "External editor",

    MessageKey.GENERAL_LANGUAGE: "Language code: ko, en or an added pack. Empty inherits legacy profile/startup language. Applies on next Hub startup.",

    MessageKey.GENERAL_NOTIFICATION_KINDS: "JSON array of alert types: error, info, warning, success. [] disables all popup alerts.",

    MessageKey.GENERAL_NOTIFICATION_SECONDS: "Display duration in seconds. Applies to new alerts after saving.",

    MessageKey.GENERAL_RESTORE_LAST_PROJECT: "Restore the last project on startup. Explicit project takes priority; deleted projects fall back to the default.",

    MessageKey.GENERAL_RESTORE_LAST_SESSION: "Restore the last session when opening a project. Explicit session takes priority; deleted sessions fall back to an active session.",

    MessageKey.GENERAL_AUTO_SCROLL: "Follow new responses only when already at the bottom. false keeps the reading position. Alt+Arrows always scroll manually.",

    MessageKey.GENERAL_EDITOR: "External editor command: vim, nano, code --wait, etc. Empty resolves VISUAL, EDITOR, then vi.",

    MessageKey.LANGUAGE_PACK_ADD: "Add/update pack",

    MessageKey.LANGUAGE_PACK_DELETE: "Delete pack",

    MessageKey.LANGUAGE_PACK_NAMES: "Available: {names} · Missing translations use English",

    MessageKey.LANGUAGE_PACK_CODE: "Pack code (e.g. ja, custom-ko). Built-in ko/en cannot be replaced or deleted.",

    MessageKey.LANGUAGE_PACK_JSON: "Paste a JSON object mapping message keys to translations. An existing code is replaced.",

    MessageKey.LANGUAGE_PACK_NONE: "No custom language packs to delete.",

    MessageKey.LANGUAGE_PACK_DRAFT: "Language pack draft changed. Save General settings to commit it.",

    MessageKey.SETTINGS_LANGUAGE_DESCRIPTION: "UI language: ko (Korean), en (English). Empty uses startup settings. Applies on next Hub startup.",

    MessageKey.SETTINGS_EMAIL_DESCRIPTION: "Profile email address (optional).",

    MessageKey.SETTINGS_PHONE_DESCRIPTION: "Profile phone number (optional).",

    MessageKey.SETTINGS_EDITOR_DESCRIPTION: "Editor command for external editing, e.g. vim, nano, code --wait. Empty resolves VISUAL, EDITOR, then vi.",

    MessageKey.SETTINGS_USAGE: "Usage statistics",

    MessageKey.SETTINGS_USAGE_SCOPE: "Active workspace projects · Reload to refresh · Each project uses its own accounting period",

    MessageKey.SETTINGS_USAGE_EMPTY: "No usage statistics available.",

    MessageKey.SETTINGS_USAGE_ERROR: "{title}: usage query failed — {error}",

    MessageKey.SETTINGS_USAGE_ALL: "All retained history",

    MessageKey.SETTINGS_USAGE_PERIOD: "Last {seconds}s",

    MessageKey.SETTINGS_USAGE_ROW: "{title} ({period})\n  Calls {call_count} · Known tokens {known_tokens} · Reserved tokens {reserved_tokens} · Unknown usage {unknown_calls} calls",

    MessageKey.SETTINGS_NO_DESCRIPTION: "No additional schema description.", MessageKey.SETTINGS_NO_FIELDS: "No public settings fields.",
    MessageKey.SETTINGS_EDITOR_FAILED: "Editor exited with status {code}. The previous draft is unchanged.",
    MessageKey.SETTINGS_HOST_LOCKED: "Host override: read only.",
    MessageKey.SETTINGS_HOST_PARTIAL: "Some nested keys are host overrides. Their stored values cannot be changed.",

    MessageKey.SETTINGS_PROFILE_DESCRIPTION: "Name shown in conversations. Initially the current OS account name.",

    MessageKey.SETTINGS_COLOR_DESCRIPTION: "Color: #RRGGBB or default (terminal color)",
    MessageKey.SETTINGS_THEME_BACKGROUND: "Background", MessageKey.SETTINGS_THEME_FOREGROUND: "Foreground",
    MessageKey.SETTINGS_THEME_ACCENT1: "Accent 1", MessageKey.SETTINGS_THEME_ACCENT2: "Accent 2",
    MessageKey.SETTINGS_THEME_ACCENT3: "Accent 3", MessageKey.SETTINGS_THEME_COMMENT: "Comment",
    MessageKey.SETTINGS_SIDEBAR_WIDTH_TITLE: "Sidebar width",
    MessageKey.SETTINGS_OUTPUT_REFRESH_INTERVAL: "Output refresh interval (seconds)",
    MessageKey.SETTINGS_OUTPUT_REFRESH_INTERVAL_DESCRIPTION: "Interval for batching streamed response updates: 0.01–10 seconds, default 0.1. Applies on save without delaying request acknowledgements or keyboard input.",
    MessageKey.SETTINGS_PROGRESS_REFRESH_INTERVAL: "Progress refresh interval (seconds)",
    MessageKey.SETTINGS_PROGRESS_REFRESH_INTERVAL_DESCRIPTION: "Refresh interval for progress bars and spinner animation: 0.01–10 seconds, default 0.1. Applies on save to both settings and chat.",
    MessageKey.RESPONSE_FAILED: "The response could not be completed.",

    MessageKey.SETTINGS_VALUE_HINT: "Blank=unset · strings as text; numbers/booleans/arrays/objects as JSON · null is explicit · Reload discards edits",

    MessageKey.SETTINGS_COMPONENTS_IMMEDIATE: "Component and engine selection applies on Save. Disabling retains data and settings.",

    MessageKey.SETTINGS_COMPONENTS_DRAFT: "Selected components will be enabled when the project is created.",

    MessageKey.SETTINGS_COMPONENT_APPLIED: "Component selection applied. Use Save to apply its settings.",

    MessageKey.SETTINGS_COMPONENTS_CHANGED: "Component selection changed. Reload settings.",

    MessageKey.SETTINGS_PROJECT_NAME: "Project name",

    MessageKey.SETTINGS_TITLE_REQUIRED: "Enter a project title.",

    MessageKey.SETTINGS_PROJECT_BUSY: "Cannot clone or delete a project with active or queued requests.",

    MessageKey.VIEW_STATE_ERROR: "Could not save reading position: {error}",

    MessageKey.SESSION: "SESSION", MessageKey.RUN: "RUN", MessageKey.STEPS: "STEPS",


    MessageKey.CONNECTING: "Connecting", MessageKey.CONNECTING_NOTICE: "Connecting to the backend.",

    MessageKey.NEW_SESSION: "New conversation", MessageKey.SESSIONS: "{icon_sessions} SESSIONS", MessageKey.WORKSPACE: "WORKSPACE",

    MessageKey.MESSAGE: "MESSAGE", MessageKey.YOU: "YOU",

    MessageKey.ASSISTANT: "ASSISTANT", MessageKey.INFO: "INFO", MessageKey.REASONING: "ASSISTANT REASONING",

    MessageKey.SESSION_HINT: "c new · d delete\ne rename · r clone", MessageKey.INPUT_HINT: "Enter send · Ctrl+Space newline · ↑↓ select · Tab confirm",

    MessageKey.SETTINGS_JSON_OBJECT: "The settings JSON must be an object.",
    MessageKey.SETTINGS_JSON_UPDATED: "JSON changes are in the draft. Use Save to apply them.",
    MessageKey.FOOTER: "ESC panel · Ctrl+S auxiliary · Ctrl+D stop · Alt + Navigation scroll · Enter send mode",

    MessageKey.FOOTER_SHORT: "ESC panel · Alt + Navigation scroll",

    MessageKey.PREVIEW_NOTICE: "Sample data · No model calls or storage.",

    MessageKey.PREVIEW_SUBMIT: "Send preview only. Your draft is preserved.",

    MessageKey.DETAILS_NARROW: "Run details need at least 116 columns.", MessageKey.DETAILS_OPEN: "Run details shown.",

    MessageKey.DETAILS_CLOSED: "Run details hidden.", MessageKey.CLOSE_HINT: "Press Ctrl+Q to minimize Hub and return to the shell.",

    MessageKey.ERROR: "Error: {error}", MessageKey.NOT_CONNECTED: "Wait for the backend connection.",

    MessageKey.SAVED: "Request saved.", MessageKey.INSTRUCTION_SAVED: "Instruction saved; it will be applied at the next boundary.",
    MessageKey.DRAFT_RECOVERED: "Clear the composer and press ↑ to recall the unsent input.",

    MessageKey.EMPTY_MESSAGE: "Enter a message.", MessageKey.NO_ACTIVE_RUN: "There is no active Run to instruct.",

    MessageKey.NO_TARGETS: "This execution is not accepting instructions.",

    MessageKey.CHOOSE_TARGET: "Instruction target",

    MessageKey.ENGINE_LINE: "Engine: {engine}", MessageKey.CREATE_DIALOG: "Create or clone a conversation",

    MessageKey.CREATE: "Create", MessageKey.CLONE: "Clone selected conversation", MessageKey.NAME: "Name (optional)",

    MessageKey.AUTO_NAME_HINT: "Leave blank to let the model name it after a response.",

    MessageKey.CONFIRM: "Confirm", MessageKey.CANCEL: "Cancel", MessageKey.CLONE_BUSY: "Stop or finish the source Run and queued requests before cloning.",

    MessageKey.QUEUED: "{status} · queued {count}", MessageKey.WAITING: "{icon_waiting} Waiting for the model response",

    MessageKey.RUN_PROGRESS: "{status} · {engine} · {seconds}s · queued {count}",

    MessageKey.ELAPSED: "Elapsed {duration}", MessageKey.ELAPSED_UNKNOWN: "Elapsed time unavailable",

    MessageKey.DURATION_SECONDS: "{seconds}s", MessageKey.DURATION_MINUTES: "{minutes}m", MessageKey.DURATION_HOURS: "{hours}h",

    MessageKey.PHASE: "Current step: {name} ({status})",

    MessageKey.PAUSED: "{icon_paused} Paused",

    MessageKey.MEMORY: "Memory history: messages are lost on shutdown",

    MessageKey.TITLE_FAILED: "Automatic naming failed: {error}", MessageKey.UNKNOWN_COMMAND: "Unknown command: {command}",

    MessageKey.COMMAND_HELP: "Show help", MessageKey.COMMAND_AGENT: "Choose an Agent", MessageKey.COMMAND_NEW: "Create a conversation",

    MessageKey.COMMAND_CLONE: "Clone this conversation",

    MessageKey.COMMAND_STOP: "Interrupt the active Run", MessageKey.COMMAND_DETAILS: "Output tag bar",

    MessageKey.FILE: "File", MessageKey.DIRECTORY: "Directory", MessageKey.ENGINE: "Engine", MessageKey.AGENT: "Agent",

    MessageKey.BACKGROUND: "\n  Hub\n\n  Ctrl+Q: return to the conversation\n  Ctrl+C: exit\n\n  Hiding Hub keeps Runs executing.\n",

    MessageKey.HELP_TITLE: "Hub help",


    MessageKey.STATUS_IDLE: "{icon_idle} Idle", MessageKey.STATUS_RUNNING: "{icon_running} Running", MessageKey.STATUS_QUEUED: "{icon_queued} Queued",

    MessageKey.STATUS_COMMITTED: "{icon_committed} Accepted", MessageKey.STATUS_STREAMING: "{icon_streaming} Streaming", MessageKey.STATUS_COMPLETED: "{icon_completed} Completed",

    MessageKey.STATUS_INTERRUPTED: "{icon_interrupted} Interrupted", MessageKey.STATUS_CANCELLED: "{icon_cancelled} Cancelled", MessageKey.STATUS_FAILED: "{icon_failed} Failed",

    MessageKey.STATUS_PAUSED: "{icon_paused} Paused", MessageKey.STATUS_PENDING: "{icon_pending} Pending", MessageKey.STATUS_APPLIED: "{icon_applied} Applied",

    MessageKey.STATUS_UNAPPLIED: "{icon_unapplied} Not applied", MessageKey.STATUS_PARTIALLY_APPLIED: "{icon_partially_applied} Partially applied",

    MessageKey.SETTINGS_ICON_STYLE: 'Icon style',
    MessageKey.SETTINGS_ICON_DESCRIPTION: 'Press Enter to choose, use arrows to change, then Enter to finish.',
    MessageKey.SETTINGS_ICON_UNICODE: 'Standard (Unicode/emoji)',
    MessageKey.SHORTCUT_CLOSE: 'close',
    MessageKey.SHORTCUT_MOVE: 'move',
    MessageKey.SHORTCUT_NEXT_MATCH: 'next match',
    MessageKey.SHORTCUT_PREVIOUS_MATCH: 'previous match',
    MessageKey.SHORTCUT_ACTIVATE: 'activate',
    MessageKey.SHORTCUT_SELECT: 'select',
    MessageKey.SHORTCUT_CONFIRM: 'confirm',
    MessageKey.SHORTCUT_DELETE: 'delete',
    MessageKey.SHORTCUT_CLONE: 'clone',
    MessageKey.SHORTCUT_MAIN: 'main',
    MessageKey.SHORTCUT_WIDTH: 'width',
    MessageKey.SHORTCUT_CREATE: 'new',
    MessageKey.SHORTCUT_RENAME: 'rename',
    MessageKey.SHORTCUT_SHELL: 'shell',
    MessageKey.SHORTCUT_CHAT: 'chat',
    MessageKey.SHORTCUT_SETTINGS: 'settings',
    MessageKey.SHORTCUT_DONE: 'done',
    MessageKey.SHORTCUT_EDIT: 'edit',
    MessageKey.SHORTCUT_NEWLINE: 'newline',
    MessageKey.SHORTCUT_EDITOR: 'editor',
    MessageKey.SHORTCUT_ITEMS: 'items',
    MessageKey.SHORTCUT_REGIONS: 'regions',
    MessageKey.SHORTCUT_SIDEBAR: 'panel',
    MessageKey.SHORTCUT_SAVE_CHAT: 'save & chat',
    MessageKey.SHORTCUT_ENGINE: 'engine',
    MessageKey.SHORTCUT_FIND: 'find',
    MessageKey.SHORTCUT_HISTORY: 'turns',
    MessageKey.SHORTCUT_LOGS: 'logs',
    MessageKey.SHORTCUT_SCROLL: 'scroll',
    MessageKey.SHORTCUT_STOP: 'stop',
    MessageKey.SHORTCUT_DETAILS: 'details',
    MessageKey.SHORTCUT_HELP: 'help',
    MessageKey.SHORTCUT_ALT_MOVEMENT: 'Alt + Navigation',
    MessageKey.SHORTCUT_MINIMIZE: 'minimize',
    MessageKey.SHORTCUT_NEXT_SESSION: 'next session',
    MessageKey.SHORTCUT_SEND: 'send',
    MessageKey.TOOLS_BUILTIN: 'Built-in tool',
    MessageKey.SHORTCUT_TOOLS_TOGGLE: 'enable/disable',
    MessageKey.QUESTION_WAITING: 'Waiting for your answer · Question',
    MessageKey.QUESTION_SENDING: 'Sending answer · {title}',
    MessageKey.QUESTION_DETAILS_HINT: 'Ctrl+O view full details',
    MessageKey.APPROVAL_HEADING: '{icon_question} Approval request',
    MessageKey.QUESTION_HEADING: '{icon_question} Question',
    MessageKey.QUESTION_ACTION_HINT: 'Ctrl+O: review the complete action (arguments, commands, file contents)',
    MessageKey.SHORTCUT_APPROVAL_DETAILS: 'Request details',
    MessageKey.APPROVAL_TOOL: 'Tool: {name}',
    MessageKey.APPROVAL_RISK: 'Risk: {value}',
    MessageKey.APPROVAL_MODEL_REASON: 'Model assessment reason: {reason}',
    MessageKey.APPROVAL_MODEL_RISK: 'Model assessment (advisory): {level} · {reason}',
    MessageKey.TOOL_HISTORY_MORE: '{count} earlier tools',
    MessageKey.APPROVAL_ARGUMENT: '{name}: {kind}, {size} (see details)',
    MessageKey.RETRY_PROVIDER: 'Model call',
    MessageKey.RETRY_TOOL: 'Tool {name}',
    MessageKey.RETRY_REPAIRING: 'Preparing retry · {target} · attempt {next_attempt}/{maximum} · correcting arguments',
    MessageKey.RETRY_WAIT: 'Waiting to retry · {target} · {attempt}/{maximum} attempts completed · attempt {next_attempt} in {seconds}s',
    MessageKey.RETRY_ACTIVE: 'Retrying · {target} · attempt {attempt}/{maximum}',
    MessageKey.QUESTION_TEXT_HINT: 'Type a choice number or your own answer, then press Enter.',
    MessageKey.QUESTION_FREE_TEXT_HINT: 'Type your answer, then press Enter.',
    MessageKey.QUESTION_CHOICE_HINT: 'Type a choice number, then Enter. Append an input value after the number if required.',
    MessageKey.AGENTS_IMPORT_HINT: "Agent JSON file path → Enter to validate and import",
    MessageKey.AGENTS_TITLE: "Agent manager",
}
