"""Quiet, cancellable after-response records through public Component and Run APIs."""

import asyncio
import json
from copy import deepcopy
from collections import deque
from jsonschema import validate

from llm.core.agents import AgentBinding, AgentDefinition
from llm.core.editable_snapshot import EditableSnapshot
from llm.core.models import RunStatus
from llm.components.memory.records import MemoryRecord
from llm.components.memory.sources import MemorySource
from llm.components.memory.configuration import MemoryConfiguration
from llm.components.goals.records import GoalStatus, GoalScope, GoalDefinition, GoalRunReference
from llm.components.goals.records import GoalSettings
from llm.components.refinement.records import ProposalOperation, RefinementPolicy
from llm.providers.request_values import ModelParameters

from ..execution.auxiliary_engine import AuxiliaryEngine
from ..execution.completion_review import ConversationalInput
from ..execution.engine_selection import HubEngineIdentity
from ..tools.resource_contracts import GoalAuthoring
from .agents import apply_agents
from .title_values import TitleSessionMetadata
from .postprocessing_values import Postprocessing as P


class PostprocessingEngine(AuxiliaryEngine):
    name = P.NAME
    instruction = P.INSTRUCTION
    output_chars = P.OUTPUT_CHARS


class BackgroundRecords:
    def __init__(self, runtime):
        self.runtime = runtime
        self.tasks = {}
        self.seen = deque(maxlen=P.SEEN_RUNS)
        self.closed = False
        self.slots = asyncio.Semaphore(P.CONCURRENCY)

    async def _log(self, project, source, event, **values):
        await asyncio.to_thread(self.runtime.backend.services.logger.write,
            project.data.paths.logs, event, session_id=source.session_id,
            run_id=source.id, **values)

    async def _inputs(self, project, session, source):
        history = await session.aconversation()
        question = next((m.content for m in history if m.id == source.input_message_id), '')
        answer = next((m.content for m in history if m.id == source.assistant_message_id), '')
        if not answer.strip() or ConversationalInput.matches(question):
            return None
        agent_id = AgentBinding.from_metadata(source.metadata).agent_id
        agent = await project.agents.aload(agent_id)
        if not ModelParameters(agent.get(AgentDefinition.Field.COMPLETION, {})).optional_model:
            return None
        current = await project.aget_data()
        payload = {'request':question[:P.EXCERPT_CHARS], 'answer':answer[:P.EXCERPT_CHARS],
                   'excerpted':len(question)>P.EXCERPT_CHARS or len(answer)>P.EXCERPT_CHARS,
                   'memories':{}, 'goals':{}, 'refinement_target':None}
        owners = {}
        if 'memory' in current.components:
            policy = current.config.parameter_targets.component('memory').policy
            if policy.get(MemoryConfiguration.Field.TOOL_WRITE_STATUS) in MemoryRecord.STATUSES:
                data = await project.components.aget('memory')
                owners['memory'] = data
                records = await data.alist(session_id=session.id)
                payload['memories'] = dict(list(records.items())[-P.RECORDS:])
        if 'goals' in current.components:
            policy = current.config.parameter_targets.component('goals').policy
            if policy.get(GoalSettings.WRITE_TOOLS):
                data = await project.components.aget('goals')
                owners['goals'] = data
                records = await data.alist(session_id=session.id)
                payload['goals'] = {key:await data.asnapshot(key) for key in list(records)[:P.RECORDS]}
        if 'refinement' in current.components:
            policy = current.config.parameter_targets.component('refinement').policy
            if ProposalOperation.UPDATE.value in policy.get(RefinementPolicy.Field.PROPOSAL_OPERATIONS, ()):
                data = await project.components.aget('refinement')
                owners['refinement'] = data
                target = {'component':'agents','identifier':agent_id}
                captured = EditableSnapshot.read(await data.atarget_snapshot(target))
                payload['refinement_target'] = {'target':target, 'version':captured.version,
                    'guidance':{key:value for key,value in captured.data.items() if key in P.REFINEMENT_FIELDS}}
        # Drop optional reference records, never truncate definitions into valid-looking patches.
        if len(json.dumps(payload, ensure_ascii=False)) > P.INPUT_CHARS:
            payload['refinement_target'] = None
        for section in ('memories','goals'):
            while payload[section] and len(json.dumps(payload, ensure_ascii=False)) > P.INPUT_CHARS:
                payload[section].pop(next(iter(payload[section])))
        if not owners:
            return None
        return payload, owners, agent

    async def _apply(self, source, session, payload, owners, plan):
        validate(plan, P.schema())
        current = await session.project.aget_data()
        parameters = current.config.parameter_targets
        if ('memory' in owners and ('memory' not in current.components or
                parameters.component('memory').policy.get(MemoryConfiguration.Field.TOOL_WRITE_STATUS) not in MemoryRecord.STATUSES)):
            raise ValueError('Memory writing was disabled')
        if ('goals' in owners and ('goals' not in current.components or
                not parameters.component('goals').policy.get(GoalSettings.WRITE_TOOLS))):
            raise ValueError('Goal writing was disabled')
        if ('refinement' in owners and ('refinement' not in current.components or
                ProposalOperation.UPDATE.value not in parameters.component('refinement').policy.get(RefinementPolicy.Field.PROPOSAL_OPERATIONS, ()))):
            raise ValueError('Refinement proposals were disabled')
        # Validate every target before the first write; never expand scope from model text.
        for section, component in (('memories','memory'), ('goals','goals')):
            if plan[section] and component not in owners:
                raise ValueError('Background component writing is not selected')
            identifiers = [item['id'] for item in plan[section] if item['id'] is not None]
            if len(set(identifiers)) != len(identifiers):
                raise ValueError('Background plan contains duplicate targets')
            for item in plan[section]:
                action, identifier, data = item['action'], item['id'], item['data']
                if action == P.CREATE:
                    required = ('content',) if component == 'memory' else ('title','objective')
                    if identifier is not None or data is None or any(key not in data for key in required):
                        raise ValueError('Background creation requires data and a new identity')
                elif identifier not in payload[section]:
                    raise ValueError('Background record is outside the captured scope')
                if (action == P.DELETE) != (data is None):
                    raise ValueError('Only background deletion accepts null data')
        if plan['refinement'] is not None and payload['refinement_target'] is None:
            raise ValueError('Background refinement has no captured target')
        if plan['refinement'] is not None and not set(plan['refinement']['patch']).issubset(P.REFINEMENT_FIELDS):
            raise ValueError('Background refinement may only propose guidance changes')
        known = {MemoryRecord(m).content for m in payload['memories'].values()}
        provenance = {MemorySource.Field.KIND: MemorySource.API,
            MemorySource.Field.PROJECT_ID: session.project.id, MemorySource.Field.SESSION_ID: session.id,
            MemorySource.Field.RUN_ID: source.id, MemorySource.Field.INPUT_MESSAGE_ID: source.input_message_id}
        for item in plan['memories']:
            data = owners['memory']
            if item['action'] == P.CREATE:
                if item['data']['content'] in known:
                    continue
                status = parameters.component('memory').policy[MemoryConfiguration.Field.TOOL_WRITE_STATUS]
                await data.acreate({**item['data'], MemoryRecord.Field.STATUS:status,
                    MemoryRecord.Field.SCOPE:MemoryRecord.SESSION_SCOPE, MemoryRecord.Field.SESSION_ID:session.id}, source=provenance)
                known.add(item['data']['content'])
            else:
                old = MemoryRecord(payload['memories'][item['id']])
                version = dict(expected_revision=old.revision,expected_generation=old.generation,
                               session_id=session.id,source=provenance)
                if item['action'] == P.DELETE:
                    await data.adelete(item['id'], **version)
                else:
                    await data.aupdate(item['id'], item['data'], **version)
        for item in plan['goals']:
            data = owners['goals']
            if item['action'] == P.CREATE:
                await data.acreate({GoalDefinition.Field.STATUS:GoalStatus.ACTIVE.value, **item['data'],
                    GoalDefinition.Field.SCOPE:GoalScope.session(session.id),
                    GoalDefinition.Field.RUN_REFS:[GoalRunReference.create(session.id,source.id,GoalAuthoring.RELATION)]})
            else:
                version = EditableSnapshot.read(payload['goals'][item['id']]).version
                if item['action'] == P.DELETE:
                    await data.adelete(item['id'],expected_version=version)
                else:
                    await data.aupdate(item['id'],item['data'],expected_version=version)
        if plan['refinement'] is not None:
            captured = payload['refinement_target']
            await owners['refinement'].acreate({
                'target':captured['target'], 'operation':ProposalOperation.UPDATE.value,
                'expected_version':captured['version'], **plan['refinement'],
                'evidence':[{'session_id':session.id,'run_id':source.id}],
            })  # Proposal only. Approval/apply stays in the foreground.

    async def _process(self, source):
        project = job = None
        try:
            async with self.slots, asyncio.timeout(P.TIMEOUT):
                project = await self.runtime.backend.projects.aload(AgentBinding.from_run(source).project_id)
                session = await project.sessions.aload(source.session_id)
                inputs = await self._inputs(project, session, source)
                if inputs is None:
                    return
                payload, owners, agent = inputs
                engine = self.runtime.backend.engines.get(P.ENGINE)
                parameters = engine.completion_adapter.auxiliary_parameters(
                    agent.get(AgentDefinition.Field.COMPLETION, {}), preserve_output_limits=False)
                agent_id = P.AGENT_PREFIX + source.session_id
                await apply_agents(project, {agent_id:{AgentDefinition.Field.ENGINE:P.ENGINE,
                    AgentDefinition.Field.COMPLETION:parameters}})
                job = await project.sessions.acreate(P.NAME, agent_ids=[agent_id])
                await job.asave(metadata={TitleSessionMetadata.INTERNAL:P.JOB,
                    TitleSessionMetadata.SOURCE_SESSION:session.id})
                run, = await (await job.run.submit(json.dumps(payload, ensure_ascii=False),
                                                  recipients=[agent_id])).wait()
                result = await run.aget_data()
                if result.status != RunStatus.COMPLETED:
                    raise ValueError(result.error or str(result.status))
                plan = json.loads((await run.aresponse()).content)
                # A newer foreground Run cancels this task through observe().
                await self._apply(source, session, payload, owners, plan)
                await self._log(project, source, 'hub.background.completed', job_run_id=run.id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if project is not None:
                await self._log(project, source, 'hub.background.failed', error=str(error))
        finally:
            if job is not None:
                await job.run.shutdown()
                await job.adelete()

    def observe(self, run):
        if self.runtime.config.postprocess is not True:
            return
        if self.closed or HubEngineIdentity.internal(run.engine):
            return
        key = (AgentBinding.from_run(run).project_id, run.session_id)
        previous = self.tasks.get(key)
        if run.status in (RunStatus.PENDING, RunStatus.RUNNING):
            if previous:
                previous.cancel()
            return
        if run.status != RunStatus.COMPLETED or run.id in self.seen:
            return
        self.seen.append(run.id)
        if previous:
            previous.cancel()
        task = asyncio.create_task(self._process(deepcopy(run)))
        self.tasks[key] = task
        def finished(done):
            if self.tasks.get(key) is done:
                self.tasks.pop(key, None)
            if not done.cancelled():
                done.exception()  # Errors are recorded, never surfaced as a conversation failure.
        task.add_done_callback(finished)

    async def close(self):
        self.closed = True
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
