"""Slash-command grammar and public component data adapters."""

import json


BUILTINS = ("help", "engine", "new", "clone", "preview", "stop", "details")
ACTIONS = ("list", "get", "create", "update", "delete", "settings", "config", "help")


def component_commands(names: tuple[str, ...]) -> dict[str, str]:
    return {("component:" + name if name in BUILTINS else name): name for name in names}


def parse_arguments(argument: str, language) -> tuple[str, str, dict]:
    parts = argument.split(maxsplit=2)
    action = parts[0] if parts else "help"
    identifier = parts[1] if len(parts) > 1 else ""
    if action not in ACTIONS:
        raise ValueError(language("component_usage_error"))
    expected = 3 if action in ("create", "update") else 2 if action in ("get", "delete") else 1
    if parts and len(parts) != expected:
        raise ValueError(language("component_usage_error"))
    payload = json.loads(parts[2]) if expected == 3 else {}
    if not isinstance(payload, dict):
        raise ValueError(language("component_json_object"))
    return action, identifier, payload


async def execute_data(data, action: str, identifier: str, payload: dict):
    # RAG owns an indexed corpus; generic JSON record writes cannot maintain it.
    from llm.components.rag.data import RAGData

    if isinstance(data, RAGData):
        if action == "list":
            return await data.alist_documents()
        if action == "get":
            return await data.aget_document(identifier)
        if action == "create":
            return await data.aadd_document(identifier=identifier, **payload)
        if action == "update":
            return await data.aupdate_document(identifier, **payload)
        if action == "delete":
            await data.adelete_document(identifier)
            return {"deleted": identifier}
    else:
        if action == "list":
            return await data.alist()
        if action == "get":
            return await data.aload(identifier)
        if action == "create":
            return {"created": await data.acreate(payload, identifier=identifier)}
        if action == "update":
            return await data.aupdate(identifier, payload)
        if action == "delete":
            await data.adelete(identifier)
            return {"deleted": identifier}
    raise ValueError(f"Unknown component data operation: {action}")
