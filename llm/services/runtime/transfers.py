"""Same-Project transport, with required two-owner receipts and ordinary admission.

The host owns this facade and its optional authorizer. Identifiers grant no model
capability. Neither transport nor recovery retries execution.
"""

from llm.core.continuations import RestartRecord, ResumeRecord

from datetime import datetime, timezone
import inspect

from llm.core.models import ConversationStorage, MessageRole, MessageStatus, SessionStatus
from llm.services.infrastructure.storage import (
    atomic_json, async_method, drain_on_cancel, read_json, reject_links,
)
from llm.services.runtime.requests import RequestAdmission

from llm.services.runtime.transfer_values import (
    TransferAction, TransferAuthorization, TransferContent, TransferDeadline, TransferDenied,
    TransferDirection, TransferExpired, TransferIdentity, TransferIntent, TransferInventory,
    TransferReceipt, TransferRequestView, TransferResultView, TransferRunView, TransferSessionView,
    TransferSnapshot, TransferSource,
)


class TransferLedger:
    """Required inventory storage; creation and CAS updates retain separate boundaries."""

    FILE = "transfers.json"
    ALREADY_EXISTS = "Transfer ledger already exists"

    @classmethod
    def path(cls, session):
        return reject_links(session.paths.state / cls.FILE)

    @classmethod
    def empty(cls, session):
        """Creation-only Repository document; never a reader fallback."""
        return TransferInventory.empty(project_id=session.project_id, session_id=session.id).to_dict()

    @classmethod
    def initialize(cls, session):
        if cls.path(session).exists():
            raise ValueError(cls.ALREADY_EXISTS)
        cls.write(session, cls.empty(session))

    @classmethod
    def write(cls, session, value):
        committed = TransferInventory.committed(value, project_id=session.project_id, session_id=session.id)
        atomic_json(cls.path(session), committed.to_dict())

    @classmethod
    def validate(cls, session, value):
        return TransferInventory.read(value, project_id=session.project_id, session_id=session.id)

    @classmethod
    def read(cls, session):
        return cls.validate(session, read_json(cls.path(session)))


class TransferRecords:
    NO_AUTHORIZER = None
    LIVE_STORAGE = False
    INCLUDE_DELETED = True
    PORTABLE_HISTORY = True

    INVALID_AUTHORIZER = "Transfer authorizer must be callable"
    CROSS_PROJECT = "Transfer cannot cross Project ownership"
    DELETED_SESSION = "Transfer requires live Sessions"
    INVALID_AUTHORIZATION_RESULT = "Transfer authorizer must return a literal boolean synchronously"
    DENIED_ACTION = "Session transfer denied: "
    CONTINUATION_CYCLE = "Transfer continuation cycle"
    ORIGINAL_MESSAGE_MISMATCH = "Transfer original Message identity mismatch"
    LOST_PROVENANCE = "Transfer continuation lost its original provenance"
    DUPLICATE_RECEIPT = "Duplicate transfer receipt"
    INVENTORY_MISMATCH = "Transfer source and target inventories disagree"
    TARGET_MISMATCH = "Transfer target differs from the accepted request"
    SOURCE_MISMATCH = "Transfer source Run identity changed"
    ANCESTRY_MISMATCH = "Transfer ancestry differs from its stored source"
    MISSING_RECEIPT = "Transfer input has no matching receipt"
    RUN_EVIDENCE_MISMATCH = "Run lost its original transfer evidence"
    PROTECTED_HISTORY = "Session history is required by a retained transfer receipt"
    MISSING_ORIGIN = "Transfer origin is missing or belongs to another Session"
    INPUT_MISMATCH = "Run transport differs from its input"

    def __init__(self, sessions, authorize=NO_AUTHORIZER):
        if authorize is not self.NO_AUTHORIZER and not callable(authorize):
            raise TypeError(TransferRecords.INVALID_AUTHORIZER)
        self.sessions, self.authorize = sessions, authorize

    def ledger(self, session):
        return TransferLedger.validate(session, self.sessions.repository.read_transfers(session))

    def access(self, project, source, target, action, value=TransferAuthorization.ABSENT_RECEIPT):
        project = self.sessions.require_project(project)
        source, target = (self.sessions.load(project, s.id) for s in (source, target))
        if source.project_id != project.id or target.project_id != project.id:
            raise TransferDenied(TransferRecords.CROSS_PROJECT)
        if source.status == SessionStatus.DELETED or target.status == SessionStatus.DELETED:
            raise TransferDenied(TransferRecords.DELETED_SESSION)
        if self.authorize is not self.NO_AUTHORIZER:
            allowed = self.authorize(TransferAuthorization(action, project.id, source.id, target.id,
                value.to_dict() if value is not TransferReceipt.ABSENT else TransferAuthorization.ABSENT_RECEIPT).to_dict())
            if inspect.iscoroutine(allowed):
                allowed.close()
            if type(allowed) is not bool:
                raise TypeError(TransferRecords.INVALID_AUTHORIZATION_RESULT)
            if not allowed:
                raise TransferDenied(TransferRecords.DENIED_ACTION + action)
        return project, source, target

    @staticmethod
    def lineage(message, messages, runs):
        """Follow saved Resume/restart evidence; no guessed parent or hidden depth."""
        visited, descendants = set(), []
        while ResumeRecord.KEY in message.metadata or RestartRecord.KEY in message.metadata:
            if message.id in visited:
                raise ValueError(TransferRecords.CONTINUATION_CYCLE)
            visited.add(message.id)
            descendants.append(message)
            source_id = (ResumeRecord.require_source_id(message.metadata) if ResumeRecord.KEY in message.metadata
                         else RestartRecord.require_source_id(message.metadata))
            message = messages[runs[source_id].input_message_id]
        origin = TransferReceipt.from_metadata(message.metadata)
        if origin is not TransferReceipt.ABSENT:
            if origin.target_message_id != message.id:
                raise ValueError(TransferRecords.ORIGINAL_MESSAGE_MISMATCH)
        if any(TransferReceipt.from_metadata(m.metadata) != origin for m in descendants):
            raise ValueError(TransferRecords.LOST_PROVENANCE)
        return origin

    def validate_project(self, project, *, archive=LIVE_STORAGE):
        from llm.services.history.conversation import ConversationStore
        sessions = (self.sessions.repository.read_backup(project) if archive else
                    self.sessions.repository.list(project, include_deleted=self.INCLUDE_DELETED))
        owners = {s.id: s for s in sessions}
        ledgers = {s.id: TransferLedger.read(s) if archive else self.ledger(s) for s in sessions}
        values, incoming = {}, {}
        for ledger in ledgers.values():
            for direction, destination in ((TransferDirection.OUTGOING, values), (TransferDirection.INCOMING, incoming)):
                for identifier, value in ledger.entries(direction).items():
                    if identifier in destination:
                        raise ValueError(TransferRecords.DUPLICATE_RECEIPT)
                    destination[identifier] = value
        if values != incoming:
            raise ValueError(TransferRecords.INVENTORY_MISMATCH)
        stores = {s.id: ConversationStore(s.paths.conversation) if archive else self.sessions.conversations(s) for s in sessions}
        messages = {sid: {m.id: m for m in store.list()} for sid, store in stores.items()}
        involved = {sid for value in values.values() for sid in (value.source.session_id, value.target_session_id)}
        # A surviving input is also a witness when both inventory entries were lost.
        involved.update(sid for sid, rows in messages.items() if any(TransferReceipt.present(m.metadata) for m in rows.values()))
        runs = {}
        for sid in involved:
            records = (self.sessions.run_repository.read_backup(owners[sid]) if archive else self.sessions.run_repository.list(owners[sid]))
            RequestAdmission.history(list(messages[sid].values()), records, project_id=project.id, session_id=sid, persistent=self.PORTABLE_HISTORY)
            runs[sid] = {run.id: run for run in records}
        for value in values.values():
            source, target = value.source.session_id, value.target_session_id
            original = messages[target][value.target_message_id]
            admission = RequestAdmission.read(original, project_id=project.id, session_id=target)
            if (TransferReceipt.from_metadata(original.metadata) != value or original.conversation_deleted
                    or TransferContent.digest(original.content) != value.content_digest or tuple(admission.recipients) != value.recipients):
                raise ValueError(TransferRecords.TARGET_MISMATCH)
            chain = (source,)
            if value.source.run_id is not TransferSource.HOST_REFERENCE:
                run = runs[source][value.source.run_id]
                message = messages[source][run.input_message_id]
                if run.agent_id != value.source.agent_id or message.id != value.source.message_id or message.conversation_deleted:
                    raise ValueError(TransferRecords.SOURCE_MISMATCH)
                parent = self.lineage(message, messages[source], runs[source])
                if parent is not TransferReceipt.ABSENT:
                    chain = parent.chain
            if value.chain != (*chain, target):
                raise ValueError(TransferRecords.ANCESTRY_MISMATCH)
        for sid in involved:
            for message in messages[sid].values():
                if message.role != MessageRole.USER or not RequestAdmission.present(message):
                    continue
                value = self.lineage(message, messages[sid], runs[sid])
                if value is not TransferReceipt.ABSENT and (values.get(value.id) != value or value.target_session_id != sid):
                    raise ValueError(TransferRecords.MISSING_RECEIPT)
                for run in RequestAdmission.runs(message, list(runs[sid].values()), project_id=project.id, session_id=sid):
                    if TransferReceipt.from_metadata(run.metadata) != value:
                        raise ValueError(TransferRecords.RUN_EVIDENCE_MISMATCH)
        return dict(values)

    def validate_session(self, project, session, messages):
        ledger = self.ledger(session)
        if ledger.populated or any(TransferReceipt.present(m.metadata) for m in messages):
            self.validate_project(project)

    def protected(self, project):
        return {sid for value in self.validate_project(project).values()
                for sid in (value.source.session_id, value.target_session_id)}

    def require_unreferenced(self, project, session):
        if session.id in self.protected(project):
            raise ValueError(TransferRecords.PROTECTED_HISTORY)

    def origin(self, project, session, message):
        value = TransferReceipt.from_metadata(message.metadata)
        if value is not TransferReceipt.ABSENT:
            if self.validate_project(project).get(value.id) != value or value.target_session_id != session.id:
                raise ValueError(TransferRecords.MISSING_ORIGIN)
        return value

    def execution(self, project, session, run, *, action=TransferAction.EXECUTE):
        message = self.sessions.conversations(session).get(run.input_message_id)
        self.validate_session(project, session, [message])
        value = TransferReceipt.from_metadata(message.metadata)
        if TransferReceipt.from_metadata(run.metadata) != value:
            raise ValueError(TransferRecords.INPUT_MISMATCH)
        if value is TransferReceipt.ABSENT:
            return TransferDeadline.ABSENT
        source = self.sessions.load(project, value.source.session_id)
        self.access(project, source, session, action, value)
        return TransferDeadline.remaining(value.deadline)

    def continuation(self, project, session, run):
        self.execution(project, session, run, action=TransferAction.RESUME)
        return TransferReceipt.require(run.metadata).metadata() if TransferReceipt.present(run.metadata) else {}


class TransferAdmission:
    """Validated intent used only inside RunManager's ordinary admission transaction."""

    PORTABLE_STORAGE_REQUIRED = "Session transfer requires portable file conversation storage"
    CYCLE = "Session transfer cycle"
    CONCURRENT_ADMISSION = "Transfer was admitted concurrently"
    NOT_ACCEPTED = None

    def __init__(self, exchange, target_id, content, recipients, key, source_run_id, deadline):
        self.exchange, self.target_id, self.content = exchange, target_id, content
        self.recipients, self.key = RequestAdmission.identifiers(recipients), key
        self.source_run_id, self.deadline = source_run_id, TransferDeadline.read(deadline)
        TransferContent.digest(content)

    def prepare(self):
        from llm.services.history.conversation import ConversationStore
        records, session = self.exchange.records, self.exchange.session
        project, source = session.project.data, session.data
        target = records.sessions.load(project, self.target_id)
        if project.conversation_storage != ConversationStorage.FILE or any(not isinstance(records.sessions.conversations(s), ConversationStore) for s in (source, target)):
            raise ValueError(TransferAdmission.PORTABLE_STORAGE_REQUIRED)
        values = records.validate_project(project)
        identity = TransferSource.host(project.id, source.id)
        chain = (source.id,)
        if self.source_run_id is not TransferSource.HOST_REFERENCE:
            run = records.sessions.run_repository.load(source, self.source_run_id)
            message = records.sessions.conversations(source).get(run.input_message_id)
            RequestAdmission.runs(message, records.sessions.run_repository.list(source), project_id=project.id, session_id=source.id)
            identity = TransferSource.from_run(project.id, source.id, run)
            origin = records.origin(project, source, message)
            if origin is not TransferReceipt.ABSENT:
                chain = origin.chain
        if target.id in chain:
            raise ValueError(TransferAdmission.CYCLE)
        intent = TransferIntent(TransferIntent.SCHEMA_VERSION,
            TransferIdentity.create(project.id, source.id, self.key), identity, target.id,
            tuple(self.recipients), self.key, TransferContent.digest(self.content), self.deadline, (*chain, target.id))
        records.access(project, source, target, TransferAction.SEND, intent)
        return project, source, target, intent, values

    def existing(self):
        _, _, target, intent, values = self.prepare()
        existing = values.get(intent.id)
        if existing is self.NOT_ACCEPTED:
            return self.NOT_ACCEPTED
        intent.require_same_payload(existing)
        return self.exchange.records.sessions.conversations(target).get(existing.target_message_id)

    def persist(self, message):
        _, source, target, intent, values = self.prepare()
        if intent.id in values:
            raise ValueError(TransferAdmission.CONCURRENT_ADMISSION)
        TransferDeadline.remaining(intent.deadline)
        value = TransferReceipt.accept(intent, message.id, datetime.now(timezone.utc).isoformat())
        store = self.exchange.records.sessions.conversations(target)
        store.update_metadata(message.id, value.metadata())
        for owner, direction in ((source, TransferDirection.OUTGOING), (target, TransferDirection.INCOMING)):
            ledger = self.exchange.records.ledger(owner)
            self.exchange.records.sessions.repository.write_transfers(owner, ledger.with_receipt(direction, value))
        return store.get(message.id)


class SessionExchange:
    def __init__(self, session):
        self.session, self.app = session, session.app

    @property
    def records(self):
        return self.app.project_manager.sessions.transfers

    async def _async_call(self, operation, *args, **kwargs):
        return await self.app._storage_call(operation, *args, **kwargs)

    def inspect(self, target_session_id):
        with self.app.project_manager.ownership.scope():
            self.app._check_open()
            project, source = self.session.project.data, self.session.data
            target = self.records.sessions.load(project, target_session_id)
            _, _, target = self.records.access(project, source, target, TransferAction.INSPECT)
            self.records.validate_project(project)
            return TransferSessionView(target.id, target.title, tuple(target.agent_ids)).to_dict()

    ainspect = async_method(inspect)

    def get(self, key):
        handle = TransferHandle(self, key)
        handle.snapshot()
        return handle

    aget = async_method(get)

    async def send(self, target_session_id, content, *, recipients, key, source_run_id=TransferSource.HOST_REFERENCE, deadline=TransferDeadline.ABSENT):
        admission = TransferAdmission(self, target_session_id, content, recipients, key, source_run_id, deadline)
        _, _, target, _, _ = await self._async_call(admission.prepare)
        await self.app._manager(target).submit_transfer(admission)
        return TransferHandle(self, key)


class TransferHandle:
    UNLIMITED_WAIT = None
    ALL_RUNS = None

    QUERY_CHANGED = "Transfer changed during query"
    UNRELATED_RUN = "Run does not belong to this transfer"
    def __init__(self, exchange, key):
        self.exchange, self.key, self.app = exchange, key, exchange.app

    async def _async_call(self, operation, *args, **kwargs):
        return await self.app._storage_call(operation, *args, **kwargs)

    def _current(self, action):
        self.app._check_open()
        project, source = self.exchange.session.project.data, self.exchange.session.data
        identifier = TransferIdentity.create(project.id, source.id, self.key)
        # Authorize using source-owned identity before reading target contents.
        value = self.exchange.records.ledger(source).entries(TransferDirection.OUTGOING)[identifier]
        target = self.exchange.records.sessions.load(project, value.target_session_id)
        self.exchange.records.access(project, source, target, action, value)
        values = self.exchange.records.validate_project(project)
        if values[identifier] != value:
            raise ValueError(TransferHandle.QUERY_CHANGED)
        messages = [m for m in self.exchange.records.sessions.conversations(target).list()
                    if TransferReceipt.from_metadata(m.metadata) == value]
        return value, target, messages

    def _snapshot(self, action):
        with self.app.project_manager.ownership.scope():
            value, target, messages = self._current(action)
            records = self.app.run_repository.list(target)
            rows, requests = [], []
            store = self.exchange.records.sessions.conversations(target)
            for message in messages:
                runs = RequestAdmission.runs(message, records, project_id=target.project_id, session_id=target.id)
                requests.append(TransferRequestView(message.id, str(message.status), tuple(r.id for r in runs)))
                for run in runs:
                    response = store.get(run.assistant_message_id)
                    rows.append(TransferRunView(run.id, run.agent_id, str(run.status), run.input_message_id,
                        run.error_code, TransferResultView(response.id, response.content, str(response.status))))
            return TransferSnapshot(value, tuple(requests), tuple(rows))

    def snapshot(self):
        return self._snapshot(TransferAction.QUERY).to_dict()

    asnapshot = async_method(snapshot)

    async def wait(self, *, timeout=UNLIMITED_WAIT):
        import asyncio
        async def complete():
            _, target, messages = await self._async_call(self._current, TransferAction.QUERY)
            for message in messages:
                if message.status != MessageStatus.CANCELLED:
                    await self.app._manager(target).wait_request(message.id)
            return await self.asnapshot()
        return await asyncio.wait_for(complete(), timeout=timeout)

    async def cancel(self, *, run_id=ALL_RUNS):
        async def apply():
            _, target, messages = await self._async_call(self._current, TransferAction.CANCEL)
            manager = self.app._manager(target)
            rows = (await self._async_call(self._snapshot, TransferAction.CANCEL)).runs
            if run_id is not self.ALL_RUNS:
                if run_id not in {r.run_id for r in rows}:
                    raise ValueError(TransferHandle.UNRELATED_RUN)
                return await manager.cancel_run(run_id)
            changed = False
            for message in messages:
                changed = await manager.cancel_request(message.id) or changed
            for row in (await self._async_call(self._snapshot, TransferAction.CANCEL)).runs:
                changed = await manager.cancel_run(row.run_id) or changed
            return changed
        return await drain_on_cancel(apply())

    async def resume(self, run_id, **options):
        _, target, _ = await self._async_call(self._current, TransferAction.RESUME)
        if run_id not in {r.run_id for r in (await self._async_call(self._snapshot, TransferAction.RESUME)).runs}:
            raise ValueError(TransferHandle.UNRELATED_RUN)
        await self.app._manager(target).resume(run_id, **options)
        return self
