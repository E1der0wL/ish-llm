"""Bounded GitHub workflow annotations; complete reports remain in artifacts."""

import json
import os


# Observed Checks messages are truncated at 4096 characters. Bound UTF-8 bytes
# conservatively and preserve valid JSON even for long Unicode diagnostics.
ANNOTATION_BYTES = 3500


def emit_annotation(title, payload, *, level):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    message = json.dumps(payload, ensure_ascii=False)
    if len(message.encode("utf-8")) > ANNOTATION_BYTES:
        excerpt = message
        while True:
            excerpt = excerpt[:len(excerpt) // 2]
            message = json.dumps({"truncated": True, "excerpt": excerpt}, ensure_ascii=False)
            if len(message.encode("utf-8")) <= ANNOTATION_BYTES:
                break
    message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level} title={title}::{message}")
