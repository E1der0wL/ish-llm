"""Opt-in question handler using the existing Engine event and interaction contracts."""

from copy import deepcopy
import inspect
from llm.core.checkpoint_records import CheckpointRecords, CheckpointWrite, CheckpointPause, CheckpointStep
from llm.core.checkpoints import CheckpointStatus, CheckpointEnvelope
from llm.core.continuations import ResumeRecord
from llm.core.interactions import InteractionRequest, InteractionView, same_interaction_value
from llm.core.questions import QuestionMode, validate_question
from llm.core.tool_event_result import ToolEventResult


class QuestionHandler:
    """Host selects the factory, public Tool schema and blocking mode explicitly.

    No Tool is installed automatically. This handler has no filesystem access,
    schedules no Run and cannot make an approval decision.
    """

    CHECKPOINT_NAME = 'question'
    STATE_KEY = 'state'
    resumable_container = True

    def __init__(self, factory, *, mode, revision):
        if not callable(factory) or inspect.iscoroutinefunction(factory) or mode not in QuestionMode or not isinstance(revision, str) or not revision:
            raise ValueError('Question handler requires a factory, explicit mode and revision')
        self.factory, self.mode = factory, mode
        self.execution_binding = {'question_mode': mode, 'revision': revision}

    async def __call__(self, arguments):
        raise ValueError('Question handler requires the Engine event execution contract')

    async def execute_events(self, context, arguments, *, request_key, checkpoint_name, step_id, result):
        from llm.engines.events import EngineEvent, EngineEventType
        if context is None or not request_key or not checkpoint_name or not step_id:
            raise ValueError('Question handler requires a durable invocation and originating Step')
        key = f'{request_key}/{self.CHECKPOINT_NAME}'
        saved = CheckpointRecords.records_or_empty(context.checkpoint).get(key)
        if saved is not None:
            envelope, saved = CheckpointEnvelope.read(saved)
            if (envelope.engine_scope, envelope.checkpoint_name, envelope.local_key) != (request_key, self.CHECKPOINT_NAME, self.STATE_KEY):
                raise ValueError('Question checkpoint owner changed')

        def checkpoint(value, interaction=None):
            # A handler's private record must use the common nested envelope.
            # Parent strategies own scheduling, not this question payload.
            envelope = CheckpointEnvelope.for_lifecycle(engine_scope=request_key, checkpoint_name=self.CHECKPOINT_NAME,
                                                        local_key=self.STATE_KEY, status=value['status'], container=True)
            stored = CheckpointEnvelope.attach(envelope.to_dict(), value)
            if interaction is not None:
                stored = CheckpointStep.attach_interaction(stored, interaction)
            return EngineEvent(EngineEventType.CHECKPOINT, interaction=interaction,
                               metadata=CheckpointWrite.record_event(checkpoint_name, key, stored).to_dict())

        if saved is not None:
            if saved['question_mode'] != self.mode or not same_interaction_value(saved['arguments'], arguments):
                raise ValueError('Question invocation changed')
            if saved['status'] == CheckpointStatus.COMPLETED:
                ToolEventResult(result).yielded(deepcopy(saved['result']))
                return
            if saved['status'] != CheckpointStatus.WAITING or self.mode != QuestionMode.BLOCKING:
                raise ValueError('Invalid question checkpoint lifecycle')
            request = InteractionRequest.from_dict(saved['interaction'])
            decision = ResumeRecord.require(context.run.metadata).decisions[key]
            option, value = request.select_decision(decision)
            output = {'request_id': request.id, 'option_id': option.id, 'value': value}
            yield checkpoint({'status': CheckpointStatus.COMPLETED, 'question_mode': self.mode,
                              'arguments': arguments, 'result': output})
        else:
            request = self.factory(deepcopy(arguments))
            validate_question(request)
            if request.binding:
                raise ValueError('Question factory cannot supply an execution binding')
            if self.mode == QuestionMode.BLOCKING:
                request = request.bind(checkpoint_name, key, selection=True)
                yield checkpoint({'status': CheckpointStatus.WAITING, 'question_mode': self.mode,
                                  'arguments': arguments, 'interaction': request.to_dict()}, request)
                yield EngineEvent(EngineEventType.PAUSED, metadata=CheckpointPause(checkpoint_name).to_dict())
                return
            output = {'request_id': request.id, 'source_run_id': context.run.id, 'status': InteractionView.PENDING}
            receipt = checkpoint({'status': CheckpointStatus.COMPLETED, 'question_mode': self.mode,
                                  'arguments': arguments, 'result': output})
            yield EngineEvent(EngineEventType.QUESTION_POSTED, step_id=step_id,
                              interaction=request, checkpoint=receipt)
        ToolEventResult(result).yielded(output)
