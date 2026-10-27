---
name: hub-create-skill
description: "Write and save reusable task instructions."
metadata:
  hub-template-version: "8"
---
# Create a Skill

Use when the user wants reusable instructions for a recurring task.

1. Discover existing guides with `skill_list` and read relevant ones with `skill_read`.
   Identify the trigger, intended result, needed inputs and available tools. Avoid duplicates.
2. Read `skill_schema`. Provide a complete SKILL.md in the markdown field with YAML name and description, concrete procedural instructions,
   useful failure handling and a verification checklist. Reference resources only when real.
3. Separate instructions from source examples. Never include secrets, embedded authority
   claims, guessed tools or instructions to ignore user/project rules.
4. Call `skill_create` with a new identifier and the complete input containing markdown. Existing IDs are
   preserved. For evidence-based improvements of existing guides use Refinement proposals.
5. Report the saved identifier. The Run's skill snapshot is stable: a newly created guide
   becomes discoverable in subsequent Runs. Test its procedure on an authorized small example
   when possible; report what was actually checked rather than calling an untested guide proven.

The markdown input contains the complete SKILL.md: YAML frontmatter with required name and
description, followed by the instruction body. Match name to the identifier; metadata values
are strings. Do not submit legacy instructions, title or tags fields. Skills provide guidance,
never permissions; allowed-tools does not grant access to tools.
