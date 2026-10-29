"""Per-invocation optional context, using public CompletionSession hooks."""

from contextlib import aclosing

from llm.components.processing import CompletionSession
from llm.core.model_protocol import ModelRole

from .request_profile import RequestMode
from .tool_exposure import InvocationMode
from .input_context import HubInputContext


class RoutedSession(CompletionSession):
    def __init__(self, processor, context, mode):
        self.delegate = processor.session(context)
        self.request_id = context.run.input_message_id
        self.mode = InvocationMode(mode)
        self.active = False

    async def prepare(self, request):
        self.active = self.mode.request(request, self.request_id) != RequestMode.CONVERSATION
        if self.active:
            async with aclosing(self.delegate.prepare(request)) as events:
                async for event in events:
                    yield event

    async def after_completion(self, observation):
        if self.active:
            async with aclosing(self.delegate.after_completion(observation)) as events:
                async for event in events:
                    yield event

    async def finish(self, observation):
        if self.active:
            async with aclosing(self.delegate.finish(observation)) as events:
                async for event in events:
                    yield event

    async def aclose(self, error):
        await self.delegate.aclose(error)


class RoutedProcessor:
    """Only stock non-reviewers are gated; custom/review processors stay intact."""
    def __init__(self, processor, mode):
        self.processor, self.mode = processor, mode
        self.name, self.priority, self.close_timeout = processor.name, processor.priority, processor.close_timeout

    def session(self, context):
        return RoutedSession(self.processor, context, self.mode)


class RequestScopeSession(CompletionSession):
    LOOKUP = (
        "This request asks for information. Use relevant read tools, including RAG or available "
        "external search when needed. shell_execute remains available for bounded investigation "
        "such as help or status; it is not a read-only sandbox. Follow normal approval and "
        "execution policies. Do not make unrelated changes or run a command's normal workload "
        "merely to explain it. If a Tool is missing, hub_help(tool_query=...) can search the "
        "Agent's allowed catalog and expose matching Tools on the next call."
    )

    def __init__(self, context, mode, restore_prompt):
        self.request_id = context.run.input_message_id
        self.mode = InvocationMode(mode)
        self.restore_prompt = restore_prompt

    async def prepare(self, request):
        from ...asset.guides import SYSTEM_PROMPT
        mode = self.mode.request(request, self.request_id)
        if mode == RequestMode.CONVERSATION:
            index = next(i for i, m in enumerate(request.messages) if m.source_id == self.request_id)
            request.messages = [m for m in request.messages[:index]
                                if m.message.role in (ModelRole.SYSTEM, ModelRole.DEVELOPER)] + request.messages[index:]
        else:
            if self.restore_prompt:
                HubInputContext.insert(request, SYSTEM_PROMPT)
            if mode == RequestMode.LOOKUP:
                HubInputContext.insert(request, self.LOOKUP)
        if False:
            yield


class RequestScopeProcessor:
    name = "hub.request_scope"
    priority = -10000
    close_timeout = None

    def __init__(self, mode, *, restore_prompt=False):
        self.mode, self.restore_prompt = mode, restore_prompt

    def session(self, context):
        return RequestScopeSession(context, self.mode, self.restore_prompt)
