"""Automatic titles use an ordinary, isolated backend Session/Run."""


from hub.locales.keys import MessageKey

from llm.core.configuration_targets import (
    ResolvedEngineConfiguration,
)


from llm.core.models import StepKind

from llm.core.agents import AgentBinding, AgentDefinition
from llm.providers.request_values import ModelParameters
from llm.core.model_protocol import ModelMessage, ModelRequest, ModelRole, copy_model

from llm.core.models import RunStatus
import asyncio
from contextlib import aclosing
from copy import copy, deepcopy

from llm.engines.base import BaseEngine


from .title_contract import TitleEngineIdentity
from .title_values import (
    TitleCompletion,
    TitleSessionMetadata,
    TitleAttempt,
    TitlePresentation,
)

TITLE_ENGINE = TitleEngineIdentity.NAME


class TitleEngine(BaseEngine):
    def __init__(self, **kwargs):
        super().__init__(TitlePresentation.ENGINE_NAME, kind=StepKind.LLM, **kwargs)
        self.parameters = {}

    def describe_config(self):
        return TitleCompletion.schema(self.completion_adapter)

    def describe_agent(self):
        return AgentDefinition.describe(
            fields=(AgentDefinition.Field.ENGINE, AgentDefinition.Field.COMPLETION,
                    AgentDefinition.Field.PURPOSE, AgentDefinition.Field.DESCRIPTION,
                    AgentDefinition.Field.METADATA),
            properties={AgentDefinition.Field.COMPLETION: self.completion_adapter.describe_config()},
        )

    def for_agent(self, definition):
        worker = copy(self)
        worker.parameters = copy_model(definition.get(AgentDefinition.Field.COMPLETION, {}))
        self.completion_adapter.validate_request(worker.parameters)
        self.completion_adapter.validate_input_ownership(worker.parameters, auxiliary=TitlePresentation.AUXILIARY)
        return worker

    def resolve_config(self, config, name, *, session_config=TitlePresentation.ABSENT):
        from llm.core.configuration import resolve_engine_config

        return resolve_engine_config(
            config, name, session_config=session_config, schema=self.describe_config()
        )

    async def run(self, context):
        settings = ResolvedEngineConfiguration.read(context.resolve_config()).engine
        parameters = copy_model(self.parameters)
        self.completion_adapter.validate_input_ownership(
            parameters, auxiliary=TitlePresentation.AUXILIARY
        )
        request = ModelRequest(
            [
                ModelMessage(ModelRole.SYSTEM, TitlePresentation.INSTRUCTION),
                ModelMessage(ModelRole.USER, context.messages[-1].content),
            ],
            parameters,
        )
        if context.completion_policy is not TitlePresentation.ABSENT:
            request = await asyncio.to_thread(
                context.completion_policy.prepare, request
            )
        async with aclosing(
            self.stream_completion(
                request, provider=TitleCompletion(settings.policy).provider
            )
        ) as stream:
            async for item in stream:
                yield item


class SessionNamer:

    ATTEMPT_VERSION = TitleAttempt.VERSION

    def __init__(self, runtime):
        self.runtime = runtime
        self.tasks = {}
        self.errors = {}

    async def _completion(self, session, record, source):
        """Borrow provider settings, without borrowing the conversation's output contract."""
        definition = (
            await session.project.agents.aload(AgentBinding.from_metadata(source.metadata).agent_id)
            if source
            else await session.project.agents.aload(
                TitleSessionMetadata(record.metadata).agent(
                    self.runtime.config.agent_id
                )
            )
        )
        params = deepcopy(definition.get(AgentDefinition.Field.COMPLETION, {}))
        title = self.runtime.backend.engines.get(TITLE_ENGINE)
        params = title.completion_adapter.auxiliary_parameters(
            params,
            preserve_retries=TitlePresentation.PRESERVE_RETRIES,
            preserve_output_limits=TitlePresentation.PRESERVE_OUTPUT_LIMITS,
        )
        from .agents import TITLE_AGENT
        configured = await session.project.agents.aload(TITLE_AGENT)
        params.update(deepcopy(configured.get(AgentDefinition.Field.COMPLETION, {})))
        return params

    def is_active(self, session_id):
        return session_id in self.tasks

    def schedule(self, session, key):
        if self.runtime.config.auto_title and session.id not in self.tasks:
            task = asyncio.create_task(self._name(session, key))
            self.tasks[session.id] = task

            def finished(task):
                self.tasks.pop(session.id, TitlePresentation.ABSENT)
                if (
                    not task.cancelled()
                    and task.exception() is not TitlePresentation.ABSENT
                ):
                    from ...errors.adapters import from_exception

                    self.errors[session.id] = from_exception(
                        task.exception(), operation=TitlePresentation.OPERATION
                    )
                    self.runtime.dirty.set()

            task.add_done_callback(finished)

    async def _name(self, session, key):
        job = TitlePresentation.ABSENT
        try:
            record = await session.aget_data()
            if not TitleSessionMetadata(record.metadata).automatic:
                return
            history = await session.aconversation()
            if not any(
                str(m.role) == ModelRole.ASSISTANT and m.content for m in history
            ):
                return
            from llm.services.query import Query

            runs = await session.run.alist(
                query=Query(
                    descending=TitlePresentation.LATEST_FIRST,
                    limit=TitlePresentation.LATEST_COUNT,
                )
            )
            source = await runs[0].aget_data() if runs else TitlePresentation.ABSENT
            params = await self._completion(session, record, source)
            attempt = TitleAttempt.digest(source.id if source else key, params)
            if TitleSessionMetadata(record.metadata).attempt == attempt:
                return
            await session.asave(
                metadata=TitleSessionMetadata(record.metadata).with_attempt(attempt)
            )
            if not ModelParameters(params).optional_model:
                raise ValueError(self.runtime.t(MessageKey.TITLE_MODEL_REQUIRED))
            from .agents import TITLE_AGENT

            # One auxiliary Agent per source Session avoids concurrent naming jobs
            # overwriting each other's model selection before admission.
            title_id = TITLE_AGENT + "_" + session.id
            definition = {AgentDefinition.Field.ENGINE: TITLE_ENGINE,
                          AgentDefinition.Field.COMPLETION: params}
            from .agents import apply_agents
            await apply_agents(session.project, {title_id: definition})
            job = await session.project.sessions.acreate(
                TitlePresentation.JOB_TITLE,
                agent_ids=[title_id],
            )
            await job.asave(metadata=TitleSessionMetadata.job(session.id))
            text = TitlePresentation.context(history)
            request = await job.run.submit(text, recipients=[title_id])
            (handle,) = await request.wait()
            result = await handle.aresult()
            if str(result.status) != RunStatus.COMPLETED:
                raise RuntimeError(result.error or str(result.status))
            response = await handle.aresponse()
            title = TitlePresentation.response(response.content)
            if not title:
                raise ValueError(TitlePresentation.EMPTY_RESPONSE)
            current = await session.aget_data()
            if TitleSessionMetadata(current.metadata).automatic:
                await session.asave(
                    title=title,
                    metadata=TitleSessionMetadata(current.metadata).completed(),
                )
                self.errors.pop(session.id, TitlePresentation.ABSENT)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            from ...errors.adapters import from_exception

            self.errors[session.id] = from_exception(
                error, operation=TitlePresentation.OPERATION
            )
        finally:
            if job is not TitlePresentation.ABSENT:
                await job.run.shutdown()
                await job.adelete()
            self.runtime.dirty.set()

    async def close(self):
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(
            *tasks, return_exceptions=TitlePresentation.COLLECT_CANCELLATIONS
        )
