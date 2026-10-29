"""Automatic titles use an ordinary, isolated backend Session/Run."""


from hub.locales.keys import MessageKey

from llm.core.agents import AgentBinding, AgentDefinition
from llm.providers.request_values import ModelParameters
from llm.core.model_protocol import ModelRole

from llm.core.models import RunStatus
import asyncio
from collections import OrderedDict
from copy import deepcopy

from ..execution.auxiliary_engine import AuxiliaryEngine


from .title_contract import TitleEngineIdentity
from .title_values import (
    TitleSessionMetadata,
    TitleAttempt,
    TitlePresentation,
)

TITLE_ENGINE = TitleEngineIdentity.NAME


class TitleEngine(AuxiliaryEngine):
    name = TitlePresentation.ENGINE_NAME
    instruction = TitlePresentation.INSTRUCTION


class SessionNamer:

    ATTEMPT_VERSION = TitleAttempt.VERSION
    CHECK_LIMIT = 1024

    def __init__(self, runtime):
        self.runtime = runtime
        self.tasks = {}
        self.errors = {}
        self.checked = OrderedDict()
        self.configuration = None
        self.configuration_revision = 0

    def observe_configuration(self, project_id, definitions):
        """A changed public Agent catalog permits another title attempt.

        The catalog is periodically reconciled by SnapshotReader; this never
        supplies execution settings, which _completion reads afresh.
        """
        configuration = (project_id, definitions)
        if configuration != self.configuration:
            self.configuration = configuration
            self.configuration_revision += 1

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
        token = (key, self.configuration_revision)
        if (self.runtime.config.auto_title and session.id not in self.tasks
                and self.checked.get(session.id) != token):
            self.checked[session.id] = token
            self.checked.move_to_end(session.id)
            while len(self.checked) > self.CHECK_LIMIT:
                self.checked.popitem(last=False)
            task = asyncio.create_task(self._name(session, key))
            self.tasks[session.id] = task

            def finished(task):
                self.tasks.pop(session.id, TitlePresentation.ABSENT)
                if task.cancelled() and self.checked.get(session.id) == token:
                    self.checked.pop(session.id, None)
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
        changed = False
        try:
            record = await session.aget_data()
            if not TitleSessionMetadata(record.metadata).automatic:
                return
            history = await session.aconversation()
            from ..execution.completion_review import ConversationalInput
            requests = [m.content for m in history if str(m.role) == ModelRole.USER]
            if requests and all(ConversationalInput.matches(text) for text in requests):
                return
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
            changed = True
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
            changed = True
        finally:
            if job is not TitlePresentation.ABSENT:
                await job.run.shutdown()
                await job.adelete()
            if changed:
                self.runtime.snapshot_reader.invalidate(session.id)
                self.runtime.dirty.set()

    async def close(self):
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(
            *tasks, return_exceptions=TitlePresentation.COLLECT_CANCELLATIONS
        )
