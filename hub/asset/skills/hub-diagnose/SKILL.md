---
name: hub-diagnose
description: "Reproduce errors and gather evidence to identify causes and fixes."
metadata:
  hub-template-version: "9"
---
Use for errors, regressions and unexpected behavior. Capture the exact symptom, expected behavior,
reproduction steps and relevant versions or configuration. Inspect only relevant logs and source using
available tools; avoid exposing credentials. Separate observed facts from hypotheses. Trace the failing
boundary and test the smallest useful hypothesis. Prefer correcting the cause over hiding the symptom.
If a fix is authorized, preserve unrelated work, apply it, and rerun the reproduction plus relevant checks.
Gather accessible evidence yourself before asking for missing details. For a local command, inspect
its installed help (--help), manual or source with the shell/file tools and required approvals; do not
ask the user to paste output you can obtain in the host-provided environment. Use bounded read-only
inspection rather than executing its normal workload. If access is blocked, explain the exact limitation
and request only the smallest missing detail.
For large logs, preserve the original in a file and inspect bounded ranges. Use the process_id from
stored command output with process_output rather than rerunning the command. For full-data counts
or patterns, use an authorized streaming filter/aggregation and report coverage; do not paste every
line into the model context or infer whole-log conclusions from a preview alone.
Report the cause supported by evidence and how the fix was verified; do not invent successful results.
