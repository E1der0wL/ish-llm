"""Independent standard text fixtures, without using the production writer."""

import json


def skill_markdown(name, body, description="Guide for verifying the requested task."):
    return ("---\nname: " + json.dumps(name, ensure_ascii=False) + "\ndescription: "
            + json.dumps(description, ensure_ascii=False) + "\n---\n" + body)
