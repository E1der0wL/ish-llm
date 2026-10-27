Validate the actual modified files against the original acceptance criteria and earlier reports.
Inspect the diff, run the selected tests/build/type/syntax checks, and check important edge cases.
For failures caused by this work, debug and fix the files and rerun the failing checks. Limit this
stage to three repair-and-retest cycles; if still blocked, preserve the evidence and report what
remains. Do not repeat a command with uncertain side effects simply because its result is missing.
Distinguish passed, failed, skipped and unavailable checks. Provide commands, exit codes and concise
evidence. Never report success based solely on the earlier Agent's claim or on code shown in chat.
