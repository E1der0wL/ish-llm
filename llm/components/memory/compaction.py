"""명시적 압축 요청과 파생 문맥. 원본 대화·체크포인트·Tool 영수증은 수정하지 않는다."""

from llm.core.model_protocol import ModelRole

from copy import deepcopy
from contextlib import aclosing
import asyncio
import json


from llm.core.models import new_id, RunStatus
from .compaction_values import CompactionMode, CompactionOptions, SummaryCheckpoint
from .compaction_records import CompactionState, CompactionRequest, CompactionSource, CompactionApplied, CompactionView, CompactionSourceRead
from .compaction_runtime import CompactionExecution, CompactionModelInput, CompactionOutcome, CompactionObservation
from .compaction_storage import CompactionManifest, CompactionInventoryEntry, CompactionInventory
from .configuration import MemoryProcessingConfig
from llm.components.summarization import SummaryResult, summarize_fragments
from llm.components.base import validate_name
from llm.services.infrastructure.storage import atomic_json, make_directory, revision_token, record, unlink_file


def compaction_schema(*, completion_adapter=CompactionOptions.ABSENT):
    return CompactionOptions.schema(completion_adapter=completion_adapter)


def validate_compaction_options(options, *, completion_adapter=CompactionOptions.ABSENT):
    CompactionOptions.validate(options, completion_adapter=completion_adapter)


class CompactionStopped(RuntimeError):
    """A saved cancellation/changed request prevents further work or publication."""


def turns(request, current_id):
    """현재 입력·system/developer와 현재 Tool 교환은 압축 대상에서 제외한다."""
    groups = []
    for message in request.messages:
        if message.source_id == current_id:
            break
        if message.source_id is CompactionExecution.NO_SOURCE_ID or message.message.role in (ModelRole.SYSTEM, ModelRole.DEVELOPER):
            continue
        if message.message.role == ModelRole.USER and not message.continuation or not groups:
            groups.append([])
        groups[-1].append(message)
    return groups


def signatures(messages):
    return CompactionSource.signatures(messages)


def validate_checkpoint(value):
    """A cumulative cursor is meaningful only with its bound source and summary."""
    SummaryCheckpoint.validate(value)


class Compactions:
    """ComponentData의 잠금 경계에서만 호출하는 Memory 소유 파생 자료 저장소."""

    SCHEMA_VERSION = CompactionManifest.SCHEMA_VERSION
    MANIFEST = "compactions.manifest.json"
    CONTEXTS = "compactions"
    REFERENCE, SUMMARY = CompactionMode.REFERENCE, CompactionMode.SUMMARY
    MODES = CompactionMode.VALUES
    TOOLS = "tool_summaries"
    PENDING, RUNNING, PARTIAL, APPLIED = "pending", "running", "partial", "applied"
    FAILED, INTERRUPTED, CANCELLED, STALE = "failed", "interrupted", "cancelled", "stale"
    STATUSES = frozenset((PENDING, RUNNING, PARTIAL, FAILED, INTERRUPTED, CANCELLED, STALE, APPLIED))
    RESUMABLE_STATES = frozenset((PARTIAL, FAILED, INTERRUPTED))
    ACTIVE_STATES = frozenset((PENDING, RUNNING))
    UNFINISHED_STATES = ACTIVE_STATES | RESUMABLE_STATES
    RESUME, CANCEL = "resume", "cancel"
    KEY_SEPARATOR = "/"
    SUFFIX = ".json"
    KEY_FORMAT = "{directory}/{session}/{identifier}" + SUFFIX


    NO_TOOL = None
    NO_SCOPE = None
    NO_SESSION_RESOURCES = None
    NO_SESSION = None
    NO_RUN = None
    NO_ERROR = None
    TEXT_ENCODING = "utf-8"
    SOURCE_PROBE_OFFSET = CompactionSourceRead.PROBE_OFFSET
    SOURCE_PROBE_LENGTH = CompactionSourceRead.VERIFY_ONLY
    INVALID_SCOPE = 'Compaction scope must be a nonempty checkpoint scope or null'
    SCOPED_TOOL = 'Tool summary storage has no context scope'
    EXISTING_STORAGE = 'Cannot adopt existing Memory compaction storage'
    INVALID_FILE = 'Memory compaction evidence must be a regular file'
    INVALID_JSON = 'Invalid Memory compaction JSON'
    MISSING_DIRECTORY = 'Memory compaction directory is missing'
    INVALID_SESSION_DIRECTORY = 'Invalid compaction Session directory'
    INVALID_ENTRY = 'Invalid compaction entry'
    MISSING_OR_UNDECLARED = 'Memory compaction records are missing or undeclared'
    CHANGED_SCOPE = 'Compaction scope identity changed'
    CHANGED_INTEGRITY = 'Memory compaction integrity changed'
    UNBOUND_SESSIONS = 'Session resources are not bound'
    RUN_ENDED = 'Owning Run ended; explicitly resume summary'
    MISSING_SESSION = 'Summary source Session was removed; cancel and release its references'
    MISSING_SOURCE = 'Compaction source was removed'
    CHANGED_SOURCE = 'Compaction source changed'
    NOT_RUNNING = 'Summary request is no longer running'
    CHANGED_CHECKPOINT = 'Summary checkpoint changed'
    INVALID_CHECKPOINT = 'Invalid summary checkpoint'
    CHANGED = 'Compaction changed; reload before changing it'
    NOT_RESUMABLE = 'Only an unfinished inactive summary can resume'
    NOT_CANCELLABLE = 'Summary request cannot be cancelled'
    UNKNOWN_ACTION = 'Unknown summary action'
    UNFINISHED_DELETE = 'Finish or cancel summary work before deleting its derived state'
    CHANGED_REQUEST = 'Summary request changed'
    NOT_FOUND = 'Compaction does not exist'
    CHANGED_CONTEXT = 'Compaction context changed; reload before requesting'
    UNOBSERVED_CONTEXT = 'No observed model context; execute a Loop request first'
    ALREADY_PENDING = 'A compaction request is already pending'
    EMPTY_SELECTION = 'No complete prior turns selected for compaction'

    @classmethod
    def key(cls, session_id, scope, tool_id=NO_TOOL):
        validate_name(session_id)
        if scope is not cls.NO_SCOPE and (not isinstance(scope, str) or not scope):
            raise ValueError(cls.INVALID_SCOPE)
        if tool_id is not cls.NO_TOOL:
            if scope is not cls.NO_SCOPE:
                raise ValueError(cls.SCOPED_TOOL)
            return cls.KEY_FORMAT.format(directory=cls.TOOLS, session=session_id, identifier=validate_name(tool_id))
        return cls.KEY_FORMAT.format(directory=cls.CONTEXTS, session=session_id,
                                     identifier=revision_token(CompactionState.scope_identity(scope)))

    @classmethod
    def collect_history_references(cls, project, component, messages):
        for key, state in cls.inventory(project, component).records.items():
            if key.startswith(cls.CONTEXTS + cls.KEY_SEPARATOR):
                CompactionState(state).collect_history_references(messages, cls.UNFINISHED_STATES)

    @classmethod
    def resource_references(cls, project, component):
        from llm.core.resource_references import StoredResourceReference
        refs = []
        for key, state in cls.inventory(project, component).records.items():
            if key.startswith(cls.TOOLS + cls.KEY_SEPARATOR):
                request = CompactionState(state).request
                refs.append(StoredResourceReference.from_value(request.reference_record, request.identifier).to_dict())
        return refs

    @classmethod
    def tool_summary_ids(cls, project, component, session_id):
        validate_name(session_id)
        prefix = cls.TOOLS + cls.KEY_SEPARATOR + session_id + cls.KEY_SEPARATOR
        return [key.removeprefix(prefix).removesuffix(cls.SUFFIX)
                for key in sorted(cls.inventory(project, component).records) if key.startswith(prefix)]

    @classmethod
    def initialize(cls, project, component):
        root = component.root(project)
        paths = [component._checked(root / name) for name in (cls.MANIFEST, cls.CONTEXTS, cls.TOOLS)]
        if any(path.exists() for path in paths):
            raise ValueError(cls.EXISTING_STORAGE)
        for path in paths[1:]:
            make_directory(path)
        atomic_json(paths[0], CompactionManifest.create(schema_version=cls.SCHEMA_VERSION,
            project_id=project.id, component=component.name))

    @staticmethod
    def _sources(value, hash_field):
        return CompactionSource.validate(value, hash_field)

    @classmethod
    def validate_state(cls, state, *, scope, tool_id, completion_adapter=CompactionOptions.ABSENT):
        CompactionState.validate_state(state, scope=scope, tool_id=tool_id,
            completion_adapter=completion_adapter, declaration=cls, validate_sources=cls._sources,
            validate_options=validate_compaction_options, validate_cursor=validate_checkpoint)

    @classmethod
    def inventory(cls, project, component):
        root = component.root(project)
        def read(path):
            path = component._checked(path)
            if not path.is_file():
                raise ValueError(cls.INVALID_FILE)
            try:
                return component.deserialize(path.read_text(encoding=cls.TEXT_ENCODING))
            except TypeError as error:
                raise ValueError(cls.INVALID_JSON) from error
        manifest = read(root / cls.MANIFEST)
        CompactionManifest.validate(manifest, schema_version=cls.SCHEMA_VERSION,
            project_id=project.id, component=component.name)
        inventory = CompactionManifest(manifest)
        paths = {}
        for kind in (cls.CONTEXTS, cls.TOOLS):
            directory = component._checked(root / kind)
            if not directory.is_dir():
                raise ValueError(cls.MISSING_DIRECTORY)
            for session in directory.iterdir():
                component._checked(session)
                validate_name(session.name)
                if not session.is_dir():
                    raise ValueError(cls.INVALID_SESSION_DIRECTORY)
                for path in session.iterdir():
                    component._checked(path)
                    if path.suffix != cls.SUFFIX or not path.is_file():
                        raise ValueError(cls.INVALID_ENTRY)
                    validate_name(path.stem)
                    paths[path.relative_to(root).as_posix()] = path
        if paths.keys() != inventory.keys:
            raise ValueError(cls.MISSING_OR_UNDECLARED)
        records = {}
        for key, entry in inventory.items:
            selected = CompactionInventoryEntry.read(entry)
            path = paths[key]
            tool_id = path.stem if path.parent.parent.name == cls.TOOLS else cls.NO_TOOL
            if cls.key(path.parent.name, selected.scope, tool_id) != key:
                raise ValueError(cls.CHANGED_SCOPE)
            state = read(path)
            cls.validate_state(state, scope=selected.scope, tool_id=tool_id, completion_adapter=component.completion_adapter)
            if selected.revision != revision_token(state):
                raise ValueError(cls.CHANGED_INTEGRITY)
            records[key] = state
        return CompactionInventory(manifest, records)

    def __init__(self, data, session_id, scope, *, tool_id=NO_TOOL):
        self.data, self.session_id = data, session_id
        project, component = data._current()
        self.project, self.component = project, component
        self.scope, self.tool_id = scope, tool_id
        self.record_key = self.key(session_id, scope, tool_id)
        if data._sessions is self.NO_SESSION_RESOURCES:
            raise RuntimeError(self.UNBOUND_SESSIONS)
        # Project-owned derived records outlive a purged Session. They remain
        # inspectable/cancellable/deletable, but cannot resume without their owner.
        try:
            self.session = data._sessions.load(project, session_id)
        except FileNotFoundError:
            self.session = self.NO_SESSION
        self.path = component._checked(component.root(project) / self.record_key)

    @staticmethod
    def _view(state):
        return CompactionState.describe(state)

    def read(self):
        return self.inventory(self.project, self.component).records.get(self.record_key, {})

    def write(self, value):
        manifest, _ = self.inventory(self.project, self.component)
        self.validate_state(value, scope=self.scope, tool_id=self.tool_id, completion_adapter=self.component.completion_adapter)
        CompactionManifest(manifest).declare(self.record_key, self.scope, value)
        atomic_json(self.path, value)
        atomic_json(self.component.root(self.project) / self.MANIFEST, manifest)

    def recover(self):
        """Read once and mark orphaned work inactive; never replay it automatically."""
        state = self.read()
        request = CompactionState(state).optional_request
        if request.optional_status == self.RUNNING:
            try:
                run = self.data._sessions.run_repository.load(self.session, request.run_id) if self.session else self.NO_RUN
            except FileNotFoundError:
                run = self.NO_RUN
            if run is self.NO_RUN or run.status != RunStatus.RUNNING:
                request.finish(self.INTERRUPTED, self.RUN_ENDED)
                self.write(state)
        return state

    def check_sources(self, request):
        request = CompactionRequest(request)
        if self.session is self.NO_SESSION:
            raise ValueError(self.MISSING_SESSION)
        store = self.data._sessions.conversations(self.session)
        for source in request.stored_sources:
            try:
                current = store.get(CompactionSource(source).identifier)
            except KeyError as error:
                raise ValueError(self.MISSING_SOURCE) from error
            if revision_token(record(current)) != CompactionSource(source).revision:
                raise ValueError(self.CHANGED_SOURCE)
        if request.has_reference:
            self.data._read_source(request.reference_record, offset=self.SOURCE_PROBE_OFFSET, limit=self.SOURCE_PROBE_LENGTH)

    def require_running(self, state, identifier, run_id):
        request = CompactionState(state).optional_request
        if request.optional_identifier != identifier or request.optional_status != self.RUNNING or request.optional_run_id != run_id:
            raise CompactionStopped(self.NOT_RUNNING)
        return request.to_record()

    def checkpoint(self, identifier, run_id, fingerprint, total, progress, previous_offset):
        state = self.read()
        request = CompactionRequest(self.require_running(state, identifier, run_id))
        saved = request.checkpoint_record
        cursor = SummaryCheckpoint(saved)
        if saved is not SummaryCheckpoint.ABSENT and (cursor.fingerprint != fingerprint or cursor.offset != previous_offset):
            raise ValueError(self.CHANGED_CHECKPOINT)
        if (type(progress.offset) is not int or not previous_offset <= progress.offset <= total
                or not isinstance(progress.text, str)
                or progress.offset > SummaryCheckpoint.INITIAL_OFFSET and len(progress.text) > request.options.summary_chars):
            raise ValueError(self.INVALID_CHECKPOINT)
        # At offset zero, text may be an older applied summary with a different
        # target size. Only newly generated fragments must meet this request's cap.
        checkpoint = SummaryCheckpoint.create(fingerprint=fingerprint, offset=progress.offset,
            summary=progress.text, total_chars=total,
            completed_chunks=(cursor.completed_chunks if saved else SummaryCheckpoint.INITIAL_CHUNKS)
                + (progress.offset > previous_offset))
        validate_checkpoint(checkpoint)
        request.checkpoint(checkpoint)
        self.write(state)

    def change(self, expected_revision, action):
        state = self.recover()
        if revision_token(state) != expected_revision:
            raise ValueError(self.CHANGED)
        request = CompactionState(state).optional_request
        status = request.optional_status
        if action == self.RESUME:
            if status not in self.RESUMABLE_STATES:
                raise ValueError(self.NOT_RESUMABLE)
            self.check_sources(request.to_record())
            request.finish(self.PENDING, self.NO_ERROR)
        elif action == self.CANCEL:
            if status not in self.UNFINISHED_STATES:
                raise ValueError(self.NOT_CANCELLABLE)
            request.finish(self.CANCELLED, self.NO_ERROR)
        else:
            raise ValueError(self.UNKNOWN_ACTION)
        self.write(state)
        return self._view(state)

    def view(self):
        return self._view(self.recover())

    def delete(self, expected_revision):
        state = self.recover()
        if (self.session is not self.NO_SESSION and self.session.active_run_ids
                or CompactionState(state).optional_request.optional_status in self.UNFINISHED_STATES):
            raise ValueError(self.UNFINISHED_DELETE)
        if revision_token(state) != expected_revision:
            raise ValueError(self.CHANGED_REQUEST)
        manifest, _ = self.inventory(self.project, self.component)
        if self.record_key not in CompactionManifest(manifest).keys:
            raise FileNotFoundError(self.NOT_FOUND)
        CompactionManifest(manifest).remove(self.record_key)
        unlink_file(self.path)
        atomic_json(self.component.root(self.project) / self.MANIFEST, manifest)

    def request(self, options, expected_revision):
        validate_compaction_options(options, completion_adapter=self.component.completion_adapter)
        state = self.recover()
        if expected_revision != revision_token(state):
            raise ValueError(self.CHANGED_CONTEXT)
        if not CompactionState(state).has_observed_view:
            raise ValueError(self.UNOBSERVED_CONTEXT)
        if CompactionState(state).optional_request.optional_status in self.ACTIVE_STATES:
            raise ValueError(self.ALREADY_PENDING)
        groups = CompactionState(state).view.turns
        selected = groups[:-CompactionOptions(options).keep_turns] if CompactionOptions(options).keep_turns else groups
        if not selected:
            raise ValueError(self.EMPTY_SELECTION)
        CompactionState(state).request_context(new_id(), self.PENDING, options, selected)
        if self.session is self.NO_SESSION:
            raise ValueError(self.MISSING_SESSION)
        store = self.data._sessions.conversations(self.session)
        stored_sources = CompactionSource.stored(CompactionState(state).request.sources,
            lambda identifier: revision_token(record(store.get(identifier))))
        CompactionState(state).request.set_stored_sources(stored_sources)
        self.write(state)
        return deepcopy(CompactionState(state).request.to_record())


async def summarize_request(session, pending, text, previous, result, *, scope, tool_id=CompactionExecution.NO_TOOL):
    """One request, a bounded slice of work, and a durable cumulative cursor per chunk.

    No model call runs under a storage lock. The owner checks the request on each
    save; only an explicit resume requeues partial/interrupted/failed requests.
    """
    pending = CompactionRequest(pending)
    options = pending.options_record
    parameters = CompactionOptions(options)
    fingerprint = revision_token(CompactionModelInput.identity(text, previous, options))
    saved = pending.checkpoint_record
    cursor = SummaryCheckpoint(saved)
    if saved is not SummaryCheckpoint.ABSENT and (cursor.fingerprint != fingerprint or cursor.total_chars != len(text)):
        raise ValueError(CompactionExecution.SOURCE_OPTIONS_CHANGED)
    offset, summary = (cursor.offset, cursor.summary) if saved else (SummaryCheckpoint.INITIAL_OFFSET, previous)
    previous_offset = offset
    async def progress(value):
        nonlocal previous_offset
        await session.data._async_call(session.data._save_compaction, session.context.session.id, scope,
            pending.identifier, session.context.run.id, fingerprint, len(text), value, previous_offset, tool_id=tool_id)
        previous_offset = value.offset
    # Persist identity before the first request as well as after every validated chunk.
    await progress(SummaryResult(text=summary, offset=offset))
    async with aclosing(summarize_fragments(text, offset=offset, previous_summary=summary,
            prompt=parameters.prompt + CompactionModelInput.INSTRUCTION,
            max_input_size=parameters.model_input_chars, max_summary_chars=parameters.summary_chars,
            max_calls=parameters.max_summary_calls,
            build_payload=lambda value, start, total, previous: CompactionModelInput.payload(
                value, start, total, previous, parameters.summary_chars),
            measure=lambda payload: len(json.dumps(payload, ensure_ascii=CompactionExecution.ENSURE_ASCII)), invoke=session._model,
            result=result, progress=progress)) as events:
        async for event in events:
            yield event


async def prepare_compaction(session, request):
    """명시적 요청만 안전 경계에서 소비하고 적용한다. 자동 시점·한도를 정하지 않는다."""
    data, context = session.data, session.context
    scope = context.checkpoint_scope
    state = await data._async_call(data._compaction_state, context.session.id, scope)
    raw = deepcopy(request.messages)
    available = {m.source_id: revision_token(m.message.to_dict()) for m in raw if m.source_id is not CompactionExecution.NO_SOURCE_ID}

    def matches(sources):
        return all(available.get(CompactionSource(source).identifier) == CompactionSource(source).digest for source in sources)

    def apply(value):
        if not value or not matches(value.sources):
            return False
        ids = {CompactionSource(source).identifier for source in value.sources}
        # 승인/진행 중 호출과 현재 사용자 지시는 제거할 수 없다.
        allowed = {m.source_id for group in turns(request, context.run.input_message_id) for m in group}
        if not ids.issubset(allowed):
            return False
        request.messages = [m for m in request.messages if m.source_id not in ids]
        position = next(i for i, m in enumerate(request.messages) if m.source_id == context.run.input_message_id)
        item = request.messages[position]
        value_text = CompactionExecution.CONTEXT_PREFIX + value.summary
        request.messages[position] = item.with_text(item.message.text + value_text)
        return True

    prior = CompactionState(state).previous_applied
    prior_applied = apply(prior)
    pending = CompactionState(state).optional_request
    if pending.optional_status == Compactions.PENDING:
        options = pending.options_record
        sources = pending.sources
        if not matches(sources):
            await data._async_call(data._finish_compaction, context.session.id, scope, pending.identifier, Compactions.STALE, CompactionExecution.NO_APPLIED,
                                   CompactionExecution.SOURCES_UNAVAILABLE)
        else:
            claim = await data._async_call(data._claim_compaction, context.session.id, scope, pending.identifier, context.run.id)
            if claim:
                before = deepcopy(request.messages)
                config = session.config
                from .processing import MemoryProcessor
                session.config = MemoryProcessingConfig.for_compaction(options, MemoryProcessor.CONTINUE)
                outcome = CompactionOutcome()
                async def compact(result, source):
                    ids = {CompactionSource(item).identifier for item in sources}
                    text = json.dumps([m.message.to_dict() for m in raw if m.source_id in ids], ensure_ascii=CompactionExecution.ENSURE_ASCII)
                    summary = SummaryResult()
                    try:
                        async with aclosing(summarize_request(session, pending.to_record(), text,
                                prior.summary if prior_applied else CompactionExecution.EMPTY_SUMMARY, summary, scope=scope)) as events:
                            async for event in events:
                                yield event
                    except CompactionStopped:
                        result.update(CompactionObservation.cancelled())
                        return
                    if not summary.complete:
                        outcome.mark_partial()
                        result.update(CompactionObservation.partial(summary))
                        return
                    text = summary.text
                    combined = [*(prior.sources if prior_applied else []), *sources]
                    candidate = CompactionApplied.context(text, combined, source)
                    request.messages = deepcopy(raw)
                    if not apply(candidate):
                        raise ValueError(CompactionExecution.UNSAFE_TURNS)
                    policy = context.completion_policy
                    if CompactionOptions(options).has_target_tokens and policy is CompactionExecution.NO_POLICY:
                        raise ValueError(CompactionExecution.MISSING_COUNTER)
                    after_tokens = await asyncio.to_thread(policy.counter, request.to_model()) if policy else CompactionExecution.NO_POLICY
                    if policy:
                        if type(after_tokens) is not int or after_tokens < CompactionExecution.MINIMUM_TOKEN_COUNT:
                            raise ValueError(CompactionExecution.INVALID_COUNTER)
                        budget = policy.max_tokens - policy.reserve_tokens
                        if after_tokens > min(CompactionOptions(options).target_tokens_or(budget), budget):
                            raise ValueError(CompactionExecution.EXCEEDED_BUDGET)
                    result.update(CompactionObservation.completed(text, len(combined), after_tokens))
                    outcome.record(candidate, after_tokens)
                try:
                    async with aclosing(session._step(CompactionExecution.STEP, compact)) as events:
                        async for event in events:
                            yield event
                    if outcome.has_candidate:
                        await data._async_call(data._finish_compaction, context.session.id, scope, pending.identifier, Compactions.APPLIED, outcome.candidate.to_record(), CompactionExecution.NO_ERROR,
                                               run_id=context.run.id)
                    else:
                        request.messages = before
                        await data._async_call(data._finish_compaction, context.session.id, scope, pending.identifier,
                            Compactions.PARTIAL if outcome.partial else Compactions.FAILED, CompactionExecution.NO_APPLIED,
                            CompactionExecution.INCOMPLETE, run_id=context.run.id)
                except CompactionStopped:
                    request.messages = before
                except BaseException:
                    request.messages = before
                    await data._async_call(data._finish_compaction, context.session.id, scope, pending.identifier, Compactions.INTERRUPTED, CompactionExecution.NO_APPLIED, CompactionExecution.INTERRUPTED, run_id=context.run.id)
                    raise
                finally:
                    session.config = config
    policy = context.completion_policy
    count = await asyncio.to_thread(policy.counter, request.to_model()) if policy else CompactionExecution.NO_POLICY
    if count is not CompactionExecution.NO_POLICY and (type(count) is not int or count < CompactionExecution.MINIMUM_TOKEN_COUNT):
        raise ValueError(CompactionExecution.NONNEGATIVE_COUNTER)
    view = CompactionView.create(run_id=context.run.id, scope=scope, iteration=request.iteration,
        tokens=count, max_tokens=policy.max_tokens if policy else CompactionExecution.NO_POLICY,
        reserve_tokens=policy.reserve_tokens if policy else CompactionExecution.NO_POLICY,
        turns=[signatures(group) for group in turns(request, context.run.input_message_id)])
    await data._async_call(data._observe_context, context.session.id, scope, view)
