# Directory maps and compatible Tool-call messages

Hub uses public Tool and completion-adapter extension points. The backend still
owns admission, permission, execution receipts, retries, checkpoints and storage.

## OpenAI-compatible message transport

The default Hub Loop uses `HubCompletionAdapter`. The default Graph Agent handler
uses that same Loop. Explicit custom engine factories and adapters remain unchanged.

For an explicit HTTP(S) `api_base` with the `openai` or `hosted_vllm` provider
(model prefix or `custom_llm_provider`), an assistant message containing Tool calls
and no text receives `content: ""` in its transport copy. The official OpenAI
endpoint and other provider protocols retain the original encoding. An ordinary
text message is unchanged. The codec's original missing/null evidence, Tool IDs,
arguments, reasoning extensions and stored conversations/checkpoints are preserved.
The native Tool exchange validator still rejects missing/mismatched results; Hub
does not invent an empty Tool result or rerun a completed Tool to repair a request.

This is proactive serialization, not a new HTTP retry loop. Unchanged HTTP 422
requests remain non-transient under the native retry policy. It does not fix every
possible 422, such as an unsupported parameter or an actually missing Tool result.
Request diagnostics retain the server's error details. When request-summary
diagnostics are enabled, `hub_message_shapes` adds the index, role, original
content presence/type and whether empty Tool-call content is normalized for the
first 256 messages. It includes no message bodies, Tool arguments or call IDs.

`tests.hub.test_provider_messages` tests missing/null/empty content, preserved
opaque fields, native validation/retries and a real LiteLLM HTTP roundtrip against
a local server that rejects omitted/null content. The server requests `file_list`
and `repo_map`; completed Tool Steps must occur once each.

## `repo_map`

This read-only builtin maps the requested directory relative to the captured ish
working directory. New Projects include it in the default builtin selection.
Existing explicit selections are not expanded automatically: enable `repo_map`
in the `/tool` manager if needed, and include it in an Agent's explicit Tool list
if that Agent restricts its Tools. Lookup/work profiles expose it through the
file/read tags; greetings do not invoke or expose the mapper.

Example Tool arguments:

```json
{"path": ".", "focus": "auth", "max_chars": 6000}
```

The map lists relative paths, prioritizing matching paths and common entry/config
files. Python files additionally show class/function declarations and import
locations. Other languages currently receive file structure, not inferred symbol
relationships. Python argument defaults, implementation bodies and docstrings are
not included. This is a navigation aid, not a verified call graph or full analysis.

| Host bound | Default |
| --- | --- |
| Files | 1,000 |
| Directory entries | 5,000 |
| Child directory depth | 8 |
| Map text | 12,000 characters |
| File bytes parsed | 128 KiB |
| Cooperative scan time | 3 seconds |
| Cached Python file projections per toolkit | 256 |

`HubDefaults` declares the bounds. Larger requested `max_files`, `max_depth` and
`max_chars` values are capped rather than rejected with a constant/maximum schema
error. Smaller selections are respected. Native `max_file_bytes` can narrow the
per-file cap. `incomplete` and `omissions` identify bounded/partial observations;
use a narrower directory or `file_read` for details. A map is never proof that
all file contents were read. Unsupported/non-source files are listed without
reading their contents.

The scanner respects local/nested `.gitignore` rules through `pathspec`, declared
in Hub's plugin dependencies. It skips generated directories and backend-reserved
Tool paths. It does not run Git, execute source, make network/model calls or write
index files. Child symlinks are skipped to avoid cycles and implicit traversal;
an explicitly requested root can resolve through a symlink only under the existing
read grant. It uses native `PathAccess` for root, directory, ignore and source reads,
including safe descriptor-based opening. Project overrides are rechecked before
serving cached projections. Write/cwd grants are never expanded.

Scanning/parsing runs off the event loop with cooperative cancellation and a
drained worker. A blocked filesystem operation cannot be forcibly terminated by
this time budget. A bounded toolkit-local cache reuses Python declarations only
when the opened file's identity, size, modification time and change time match.
Directory traversal and ignore rules are refreshed on every invocation. Cached
projections are observations, not durable backend data or authorization.

`tests.hub.test_repo_map` covers ignore rules, limits, source changes, narrowed
permissions, symlink cycles, cancellation and profile selection. Existing large
Tool-result retention/paging and HTTP tests remain regression gates.
