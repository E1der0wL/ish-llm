"""Same-Project transport, with required two-owner receipts and ordinary admission.

The host owns this facade and its optional authorizer. Identifiers grant no model
capability. Neither transport nor recovery retries execution.
"""

from llm.core.continuations import RestartRecord, ResumeRecord

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
from pathlib import Path

from llm.core.models import ConversationStorage, MessageRole, MessageStatus, ProjectConfig, RunStatus, SessionStatus
from llm.errors import CodedError
from llm.services.infrastructure.storage import (
    atomic_json, async_method, child, drain_on_cancel, read_json, reject_links, revision_token,
)
from llm.services.runtime.requests import RequestAdmission


class TransferExpired(CodedError, ValueError):
    code = "transfer_expired"


class TransferDenied(CodedError, PermissionError):
    code = "transfer_denied"


def deadline_value(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("Transfer deadline must be timezone-aware ISO text")
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("Transfer deadline requires a timezone")
    return stamp.astimezone(timezone.utc).isoformat()


def remaining(value):
    if value is None:
        return None
    seconds = (datetime.fromisoformat(value) - datetime.now(timezone.utc)).total_seconds()
    if seconds <= 0:
        raise TransferExpired("Session transfer deadline expired")
    return seconds


def transfer_id(project_id, session_id, key):
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Transfer idempotency key must be nonempty text")
    return revision_token([project_id, session_id, key])


def content_digest(content):
    if not isinstance(content, str):
        raise TypeError("Transfer content must be text")
    return hashlib.sha256(content.encode()).hexdigest()


class TransferReceipt:
    SCHEMA_VERSION = 1
    FIELDS = frozenset(("schema_version", "id", "source", "target_session_id", "recipients",
        "key", "content_digest", "deadline", "chain", "target_message_id", "accepted_at", "revision"))

    @classmethod
    def read(cls, value):
        if (not isinstance(value, dict) or value.keys() != cls.FIELDS
                or type(value["schema_version"]) is not int or value["schema_version"] != cls.SCHEMA_VERSION):
            raise ValueError("Transfer requires every current receipt field")
        ProjectConfig.validate_json(value)
        source = value["source"]
        if not isinstance(source, dict) or source.keys() != {"project_id", "session_id", "run_id", "agent_id", "message_id"}:
            raise ValueError("Transfer source requires complete identity")
        for identifier in (source["project_id"], source["session_id"], value["target_session_id"], value["target_message_id"]):
            child(Path("."), identifier)
        if source["run_id"] is None:
            if source["agent_id"] is not None or source["message_id"] is not None:
                raise ValueError("Host source cannot claim a Run identity")
        else:
            child(Path("."), source["run_id"])
            child(Path("."), source["message_id"])
            RequestAdmission.identifiers([source["agent_id"]])
        if value["recipients"] != RequestAdmission.identifiers(value["recipients"]):
            raise ValueError("Transfer recipients must be canonical")
        chain = value["chain"]
        if (not isinstance(chain, list) or len(chain) < 2 or any(not isinstance(v, str) for v in chain)
                or len(set(chain)) != len(chain) or chain[-2:] != [source["session_id"], value["target_session_id"]]):
            raise ValueError("Transfer cycle or inconsistent lineage")
        for identifier in chain:
            child(Path("."), identifier)
        for name in ("deadline", "accepted_at"):
            if value[name] != deadline_value(value[name]):
                raise ValueError("Transfer timestamps must be canonical UTC")
        if value["accepted_at"] is None or value["id"] != transfer_id(source["project_id"], source["session_id"], value["key"]):
            raise ValueError("Transfer identity mismatch")
        digest = value["content_digest"]
        if not isinstance(digest, str) or len(digest) != 64 or any(v not in "0123456789abcdef" for v in digest):
            raise ValueError("Invalid transfer content digest")
        if value["revision"] != revision_token({k: v for k, v in value.items() if k != "revision"}):
            raise ValueError("Transfer receipt revision mismatch")
        return deepcopy(value)


class TransferLedger:
    """Required inventory, initialized only by creation; reads never repair it."""
    SCHEMA_VERSION = 1
    FILE = "transfers.json"

    @classmethod
    def path(cls, session):
        return reject_links(session.paths.state / cls.FILE)

    @classmethod
    def empty(cls, session):
        """Creation-only document for any Repository; never a reader fallback."""
        value = {"schema_version": cls.SCHEMA_VERSION, "project_id": session.project_id,
                 "session_id": session.id, "outgoing": {}, "incoming": {}}
        value["revision"] = revision_token(value)
        return cls.validate(session, value)

    @classmethod
    def initialize(cls, session):
        if cls.path(session).exists():
            raise ValueError("Transfer ledger already exists")
        cls.write(session, cls.empty(session))

    @classmethod
    def write(cls, session, value):
        value = deepcopy(value)
        value.pop("revision", None)
        value["revision"] = revision_token(value)
        cls.validate(session, value)
        atomic_json(cls.path(session), value)

    @classmethod
    def validate(cls, session, value):
        if (not isinstance(value, dict) or value.keys() != {"schema_version", "project_id", "session_id", "outgoing", "incoming", "revision"}
                or type(value["schema_version"]) is not int or value["schema_version"] != cls.SCHEMA_VERSION
                or (value["project_id"], value["session_id"]) != (session.project_id, session.id)
                or value["revision"] != revision_token({k: v for k, v in value.items() if k != "revision"})):
            raise ValueError("Transfer ledger ownership, fields or revision mismatch")
        for direction in ("outgoing", "incoming"):
            if not isinstance(value[direction], dict):
                raise ValueError("Transfer inventory must be an object")
            for identifier, raw in value[direction].items():
                receipt = TransferReceipt.read(raw)
                owner = receipt["source"]["session_id"] if direction == "outgoing" else receipt["target_session_id"]
                if identifier != receipt["id"] or owner != session.id or receipt["source"]["project_id"] != session.project_id:
                    raise ValueError("Transfer inventory entry belongs to another owner")
        return deepcopy(value)

    @classmethod
    def read(cls, session):
        return cls.validate(session, read_json(cls.path(session)))


class TransferRecords:
    def __init__(self, sessions, authorize=None):
        if authorize is not None and not callable(authorize):
            raise TypeError("Transfer authorizer must be callable")
        self.sessions, self.authorize = sessions, authorize

    def ledger(self, session):
        return TransferLedger.validate(session, self.sessions.repository.read_transfers(session))

    def access(self, project, source, target, action, value=None):
        project = self.sessions.require_project(project)
        source, target = (self.sessions.load(project, s.id) for s in (source, target))
        if source.project_id != project.id or target.project_id != project.id:
            raise TransferDenied("Transfer cannot cross Project ownership")
        if source.status == SessionStatus.DELETED or target.status == SessionStatus.DELETED:
            raise TransferDenied("Transfer requires live Sessions")
        if self.authorize is not None:
            allowed = self.authorize({"action": action, "project_id": project.id,
                "source_session_id": source.id, "target_session_id": target.id,
                "receipt": deepcopy(value)})
            if inspect.iscoroutine(allowed):
                allowed.close()
            if type(allowed) is not bool:
                raise TypeError("Transfer authorizer must return a literal boolean synchronously")
            if not allowed:
                raise TransferDenied("Session transfer denied: " + action)
        return project, source, target

    @staticmethod
    def lineage(message, messages, runs):
        """Follow saved Resume/restart evidence; no guessed parent or hidden depth."""
        visited, descendants = set(), []
        while ResumeRecord.KEY in message.metadata or RestartRecord.KEY in message.metadata:
            if message.id in visited:
                raise ValueError("Transfer continuation cycle")
            visited.add(message.id)
            descendants.append(message)
            link = message.metadata[ResumeRecord.KEY] if ResumeRecord.KEY in message.metadata else message.metadata[RestartRecord.KEY]
            message = messages[runs[link["run_id"]].input_message_id]
        origin = message.metadata.get("transfer")
        if origin is not None:
            origin = TransferReceipt.read(origin)
            if origin["target_message_id"] != message.id:
                raise ValueError("Transfer original Message identity mismatch")
        if any(m.metadata.get("transfer") != origin for m in descendants):
            raise ValueError("Transfer continuation lost its original provenance")
        return origin

    def validate_project(self, project, *, archive=False):
        from llm.services.history.conversation import ConversationStore
        sessions = (self.sessions.repository.read_backup(project) if archive else
                    self.sessions.repository.list(project, include_deleted=True))
        owners = {s.id: s for s in sessions}
        ledgers = {s.id: TransferLedger.read(s) if archive else self.ledger(s) for s in sessions}
        values, incoming = {}, {}
        for ledger in ledgers.values():
            for direction, destination in (("outgoing", values), ("incoming", incoming)):
                for identifier, value in ledger[direction].items():
                    if identifier in destination:
                        raise ValueError("Duplicate transfer receipt")
                    destination[identifier] = value
        if values != incoming:
            raise ValueError("Transfer source and target inventories disagree")
        stores = {s.id: ConversationStore(s.paths.conversation) if archive else self.sessions.conversations(s) for s in sessions}
        messages = {sid: {m.id: m for m in store.list()} for sid, store in stores.items()}
        involved = {sid for value in values.values() for sid in (value["source"]["session_id"], value["target_session_id"])}
        # A surviving input is also a witness when both inventory entries were lost.
        involved.update(sid for sid, rows in messages.items() if any("transfer" in m.metadata for m in rows.values()))
        runs = {}
        for sid in involved:
            records = (self.sessions.run_repository.read_backup(owners[sid]) if archive else self.sessions.run_repository.list(owners[sid]))
            RequestAdmission.history(list(messages[sid].values()), records, project_id=project.id, session_id=sid, persistent=True)
            runs[sid] = {run.id: run for run in records}
        for value in values.values():
            source, target = value["source"]["session_id"], value["target_session_id"]
            original = messages[target][value["target_message_id"]]
            admission = RequestAdmission.read(original, project_id=project.id, session_id=target)
            if (original.metadata.get("transfer") != value or original.conversation_deleted
                    or content_digest(original.content) != value["content_digest"] or admission.recipients != value["recipients"]):
                raise ValueError("Transfer target differs from the accepted request")
            chain = [source]
            if value["source"]["run_id"] is not None:
                run = runs[source][value["source"]["run_id"]]
                message = messages[source][run.input_message_id]
                if run.agent_id != value["source"]["agent_id"] or message.id != value["source"]["message_id"] or message.conversation_deleted:
                    raise ValueError("Transfer source Run identity changed")
                parent = self.lineage(message, messages[source], runs[source])
                if parent is not None:
                    chain = parent["chain"]
            if value["chain"] != [*chain, target]:
                raise ValueError("Transfer ancestry differs from its stored source")
        for sid in involved:
            for message in messages[sid].values():
                if message.role != MessageRole.USER or not RequestAdmission.present(message):
                    continue
                value = self.lineage(message, messages[sid], runs[sid])
                if value is not None and (values.get(value["id"]) != value or value["target_session_id"] != sid):
                    raise ValueError("Transfer input has no matching receipt")
                for run in RequestAdmission.runs(message, list(runs[sid].values()), project_id=project.id, session_id=sid):
                    if run.metadata.get("transfer") != value:
                        raise ValueError("Run lost its original transfer evidence")
        return deepcopy(values)

    def validate_session(self, project, session, messages):
        ledger = self.ledger(session)
        if ledger["incoming"] or ledger["outgoing"] or any("transfer" in m.metadata for m in messages):
            self.validate_project(project)

    def protected(self, project):
        return {sid for value in self.validate_project(project).values()
                for sid in (value["source"]["session_id"], value["target_session_id"])}

    def require_unreferenced(self, project, session):
        if session.id in self.protected(project):
            raise ValueError("Session history is required by a retained transfer receipt")

    def origin(self, project, session, message):
        value = message.metadata.get("transfer")
        if value is not None:
            value = TransferReceipt.read(value)
            if self.validate_project(project).get(value["id"]) != value or value["target_session_id"] != session.id:
                raise ValueError("Transfer origin is missing or belongs to another Session")
        return value

    def execution(self, project, session, run, *, action="execute"):
        message = self.sessions.conversations(session).get(run.input_message_id)
        self.validate_session(project, session, [message])
        value = message.metadata.get("transfer")
        if run.metadata.get("transfer") != value:
            raise ValueError("Run transport differs from its input")
        if value is None:
            return None
        source = self.sessions.load(project, value["source"]["session_id"])
        self.access(project, source, session, action, value)
        return remaining(value["deadline"])

    def continuation(self, project, session, run):
        self.execution(project, session, run, action="resume")
        return {"transfer": deepcopy(run.metadata["transfer"])} if "transfer" in run.metadata else {}


class TransferAdmission:
    """Validated intent used only inside RunManager's ordinary admission transaction."""
    def __init__(self, exchange, target_id, content, recipients, key, source_run_id, deadline):
        self.exchange, self.target_id, self.content = exchange, target_id, content
        self.recipients, self.key = RequestAdmission.identifiers(recipients), key
        self.source_run_id, self.deadline = source_run_id, deadline_value(deadline)
        content_digest(content)

    def prepare(self):
        from llm.services.history.conversation import ConversationStore
        records, session = self.exchange.records, self.exchange.session
        project, source = session.project.data, session.data
        target = records.sessions.load(project, self.target_id)
        if project.conversation_storage != ConversationStorage.FILE or any(not isinstance(records.sessions.conversations(s), ConversationStore) for s in (source, target)):
            raise ValueError("Session transfer requires portable file conversation storage")
        values = records.validate_project(project)
        identity = {"project_id": project.id, "session_id": source.id, "run_id": self.source_run_id,
                    "agent_id": None, "message_id": None}
        chain = [source.id]
        if self.source_run_id is not None:
            run = records.sessions.run_repository.load(source, self.source_run_id)
            message = records.sessions.conversations(source).get(run.input_message_id)
            RequestAdmission.runs(message, records.sessions.run_repository.list(source), project_id=project.id, session_id=source.id)
            identity.update(agent_id=run.agent_id, message_id=message.id)
            origin = records.origin(project, source, message)
            if origin is not None:
                chain = origin["chain"]
        if target.id in chain:
            raise ValueError("Session transfer cycle")
        intent = {"schema_version": TransferReceipt.SCHEMA_VERSION, "id": transfer_id(project.id, source.id, self.key),
            "source": identity, "target_session_id": target.id, "recipients": self.recipients, "key": self.key,
            "content_digest": content_digest(self.content), "deadline": self.deadline, "chain": [*chain, target.id]}
        records.access(project, source, target, "send", intent)
        return project, source, target, intent, values

    def existing(self):
        _, _, target, intent, values = self.prepare()
        existing = values.get(intent["id"])
        if existing is None:
            return None
        if any(existing[key] != value for key, value in intent.items()):
            raise ValueError("Transfer idempotency key conflicts with its original payload")
        return self.exchange.records.sessions.conversations(target).get(existing["target_message_id"])

    def persist(self, message):
        _, source, target, intent, values = self.prepare()
        if intent["id"] in values:
            raise ValueError("Transfer was admitted concurrently")
        remaining(intent["deadline"])
        value = {**intent, "target_message_id": message.id, "accepted_at": datetime.now(timezone.utc).isoformat()}
        value["revision"] = revision_token(value)
        value = TransferReceipt.read(value)
        store = self.exchange.records.sessions.conversations(target)
        store.update_metadata(message.id, {"transfer": value})
        for owner, direction in ((source, "outgoing"), (target, "incoming")):
            ledger = self.exchange.records.ledger(owner)
            ledger[direction][value["id"]] = value
            self.exchange.records.sessions.repository.write_transfers(owner, ledger)
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
            _, _, target = self.records.access(project, source, target, "inspect")
            self.records.validate_project(project)
            return {"session_id": target.id, "title": target.title, "agent_ids": list(target.agent_ids)}

    ainspect = async_method(inspect)

    def get(self, key):
        handle = TransferHandle(self, key)
        handle.snapshot()
        return handle

    aget = async_method(get)

    async def send(self, target_session_id, content, *, recipients, key, source_run_id=None, deadline=None):
        admission = TransferAdmission(self, target_session_id, content, recipients, key, source_run_id, deadline)
        _, _, target, _, _ = await self._async_call(admission.prepare)
        await self.app._manager(target).submit_transfer(admission)
        return TransferHandle(self, key)


class TransferHandle:
    def __init__(self, exchange, key):
        self.exchange, self.key, self.app = exchange, key, exchange.app

    async def _async_call(self, operation, *args, **kwargs):
        return await self.app._storage_call(operation, *args, **kwargs)

    def _current(self, action):
        self.app._check_open()
        project, source = self.exchange.session.project.data, self.exchange.session.data
        identifier = transfer_id(project.id, source.id, self.key)
        # Authorize using source-owned identity before reading target contents.
        value = self.exchange.records.ledger(source)["outgoing"][identifier]
        target = self.exchange.records.sessions.load(project, value["target_session_id"])
        self.exchange.records.access(project, source, target, action, value)
        values = self.exchange.records.validate_project(project)
        if values[identifier] != value:
            raise ValueError("Transfer changed during query")
        messages = [m for m in self.exchange.records.sessions.conversations(target).list()
                    if m.metadata.get("transfer") == value]
        return value, target, messages

    def _snapshot(self, action):
        with self.app.project_manager.ownership.scope():
            value, target, messages = self._current(action)
            records = self.app.run_repository.list(target)
            rows, requests = [], []
            store = self.exchange.records.sessions.conversations(target)
            for message in messages:
                runs = RequestAdmission.runs(message, records, project_id=target.project_id, session_id=target.id)
                requests.append({"message_id": message.id, "status": str(message.status), "run_ids": [r.id for r in runs]})
                for run in runs:
                    response = store.get(run.assistant_message_id)
                    rows.append({"run_id": run.id, "agent_id": run.agent_id, "status": str(run.status),
                        "input_message_id": run.input_message_id, "error_code": run.error_code,
                        "result": {"message_id": response.id, "text": response.content, "status": str(response.status)}})
            return {"receipt": deepcopy(value), "requests": requests, "runs": rows}

    def snapshot(self):
        return self._snapshot("query")

    asnapshot = async_method(snapshot)

    async def wait(self, *, timeout=None):
        import asyncio
        async def complete():
            _, target, messages = await self._async_call(self._current, "query")
            for message in messages:
                if message.status != MessageStatus.CANCELLED:
                    await self.app._manager(target).wait_request(message.id)
            return await self.asnapshot()
        return await asyncio.wait_for(complete(), timeout=timeout)

    async def cancel(self, *, run_id=None):
        async def apply():
            _, target, messages = await self._async_call(self._current, "cancel")
            manager = self.app._manager(target)
            rows = (await self._async_call(self._snapshot, "cancel"))["runs"]
            if run_id is not None:
                if run_id not in {r["run_id"] for r in rows}:
                    raise ValueError("Run does not belong to this transfer")
                return await manager.cancel_run(run_id)
            changed = False
            for message in messages:
                changed = await manager.cancel_request(message.id) or changed
            for row in (await self._async_call(self._snapshot, "cancel"))["runs"]:
                changed = await manager.cancel_run(row["run_id"]) or changed
            return changed
        return await drain_on_cancel(apply())

    async def resume(self, run_id, **options):
        _, target, _ = await self._async_call(self._current, "resume")
        if run_id not in {r["run_id"] for r in (await self._async_call(self._snapshot, "resume"))["runs"]}:
            raise ValueError("Run does not belong to this transfer")
        await self.app._manager(target).resume(run_id, **options)
        return self
