"""MCP elicitation -> shared interaction values; no UI, approval or resume policy."""

from copy import deepcopy
from llm.core.interactions import InteractionRequest, InteractionResponse, InteractionOption
from llm.core.questions import validate_question
from llm.core.schema import object_schema, field, nest_schema


class MCPElicitation:
    ACTIONS = ("accept", "decline", "cancel")
    FORM = "form"
    URL = "url"
    ACTION = "action"
    CONTENT = "content"
    REPLY = "reply"
    CATEGORY = "mcp.elicitation"

    @classmethod
    def request(cls, params, *, connection_id, request_id):
        # Protocol choices, not approval choices. The response envelope lets a
        # declined form omit its required fields without weakening acceptance.
        if params.mode not in (cls.FORM, cls.URL):
            raise ValueError("Unsupported MCP elicitation mode")
        schema = (deepcopy(params.requested_schema)
                  if params.mode == cls.FORM else {"type": "null"})
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(schema)
        content_schema = {"anyOf": [nest_schema(schema, ("properties", cls.CONTENT, "anyOf", 0)), {"type": "null"}]}
        reply = object_schema({cls.ACTION: field("string", enum=list(cls.ACTIONS)),
                               cls.CONTENT: content_schema}, required=[cls.ACTION])
        reply["allOf"] = [{
            "if": {"properties": {cls.ACTION: {"const": "accept"}}},
            "then": ({"required": [cls.CONTENT], "properties": {cls.CONTENT: {"not": {"type": "null"}}}}
                     if params.mode == cls.FORM else {}),
            "else": {"properties": {cls.CONTENT: {"type": "null"}}},
        }]
        action = {"mode": params.mode}
        if params.mode == cls.URL:
            action["url"] = str(params.url)
        request = InteractionRequest(title=params.message,
            options=(InteractionOption(cls.REPLY, "Respond", value=cls.REPLY),),
            kind=InteractionRequest.KIND_INPUT, category=cls.CATEGORY, input_schema=reply,
            source={"connection_id": connection_id, "remote_request_id": str(request_id)}, action=action)
        validate_question(request)
        return request

    @staticmethod
    def response(request, response):
        if not isinstance(response, InteractionResponse):
            raise TypeError("MCP elicitation requires an InteractionResponse")
        # Existing revision/fingerprint/input validation; no new approval receipt.
        return response.decision(request)
