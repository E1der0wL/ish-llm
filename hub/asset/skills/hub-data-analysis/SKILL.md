---
name: hub-data-analysis
description: "Inspect large command output and data through saved references and bounded reads."
metadata:
  hub-template-version: "9"
---
Use for large logs, tables or command results. Discover the actual schemas of the available Data,
file and process-output tools. Use returned identifiers and references; never invent storage paths.
Read metadata and a small relevant range first. Keep byte ranges and character offsets distinct and
follow returned cursors and revision checks. Do not execute a command again to retrieve its old output.

For whole-data questions, use an authorized streaming filter or aggregation. Keep the source unchanged,
retain the command and source revision, and report unreadable or truncated portions. A preview or sample
does not establish whole-file counts. Do not put the complete source into another model request.

Separate source observations from inferred explanations. Keep derived summaries linked to their source
and describe coverage. Store derived data only through the provided tools and respect references and
explicit cleanup approval. Missing tools or denied access are limitations, not permission to bypass them.
