"""Same-Project transport receipts. Target User admission owns all execution.

The trusted host facade is the caller's authority, optionally narrowed by a host
authorizer. This module grants no implicit model Tool or cross-Project access.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect

from llm.core.models import MessageRole, MessageStatus, SessionStatus
from llm.services.infrastructure.storage import (
    async_method, atomic_json, child, read_json, reject_links, revision_token,
    drain_on_cancel,
)
from llm.services.infrastructure.logging import log_event
from llm.services.runtime.requests import RequestAdmission
from llm.errors import CodedError


class TransferExpired(CodedError, ValueError):
    code = "transfer_expired"


class TransferDenied(CodedError, PermissionError):
    code = "transfer_denied"


def clock():
    return datetime.now(timezone.utc)


def deadline_value(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("Transfer deadline must be an ISO timezone-aware string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Transfer deadline requires a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def remaining(value):
    if value is None:
        return None
    seconds = (datetime.fromisoformat(value) - clock()).total_seconds()
    if seconds <= 0:
        raise TransferExpired("Session transfer deadline expired")
    return seconds


def key_value(key):
    if not isinstance(key, str) or not key.strip() or len(key) > 256:
        raise ValueError("Transfer key must contain 1 to 256 characters")
    return key


def content_digest(content):
    if not isinstance(content, str):
        raise TypeError("Transfer input must be text")
    return hashlib.sha256(content.encode()).hexdigest()


def receipt_id(project_id, session_id, key):
    return revision_token({"project": project_id, "session": session_id, "key": key_value(key)})


class TransferRecords:
    """One source-owned idempotency receipt, atomically written with target User."""

    def __init__(self, sessions, authorize=None):
        self.sessions, self.authorize = sessions, authorize

    @staticmethod
    def path(source, identifier):
        if (not isinstance(identifier, str) or len(identifier) != 64
                or any(c not in "0123456789abcdef" for c in identifier)):
            raise ValueError("Invalid transfer ID")
        return reject_links(source.paths.state / "transfers" / (identifier + ".json"))

    @staticmethod
    def validate(value):
        fields = {"storage_version", "id", "source", "target_session_id", "recipients",
                  "content_digest", "key", "deadline", "chain", "accepted_at",
                  "target_message_id", "digest"}
        if (not isinstance(value, dict) or set(value) != fields
                or type(value["storage_version"]) is not int or value["storage_version"] != 1):
            raise ValueError("Invalid transfer receipt version or fields")
        source = value["source"]
        if not isinstance(source, dict) or set(source) != {
                "project_id", "session_id", "run_id", "agent_id", "message_id"}:
            raise ValueError("Invalid transfer source")
        # Reuse domain ID validation without interpreting IDs as paths/authority.
        from pathlib import Path
        for identifier in (source["project_id"], source["session_id"], value["target_session_id"],
                           value["target_message_id"]):
            child(Path("."), identifier)
        for name in ("run_id", "message_id"):
            if source[name] is not None:
                child(Path("."), source[name])
        if source["run_id"] is None and any(source[n] is not None for n in ("agent_id", "message_id")):
            raise ValueError("Host-origin transfer cannot claim Agent or Message identity")
        if source["run_id"] is not None and source["message_id"] is None:
            raise ValueError("Run-origin transfer requires its input Message")
        if source["agent_id"] is not None:
            RequestAdmission.identifiers([source["agent_id"]])
        if value["recipients"] != RequestAdmission.identifiers(value["recipients"]):
            raise ValueError("Transfer recipients must be canonical")
        chain = value["chain"]
        if (not isinstance(chain, list) or not 2 <= len(chain) <= 16
                or any(not isinstance(v, str) for v in chain) or len(set(chain)) != len(chain)
                or chain[-2:] != [source["session_id"], value["target_session_id"]]):
            raise ValueError("Transfer cycle or invalid ancestor chain")
        for identifier in chain:
            child(Path("."), identifier)
        if value["deadline"] != deadline_value(value["deadline"]):
            raise ValueError("Transfer deadline must be canonical UTC")
        deadline_value(value["accepted_at"])
        if value["accepted_at"] is None or value["id"] != receipt_id(source["project_id"], source["session_id"], value["key"]):
            raise ValueError("Transfer receipt identity mismatch")
        digest = value["content_digest"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid transfer input digest")
        if value["digest"] != revision_token({k: v for k, v in value.items() if k != "digest"}):
            raise ValueError("Transfer receipt digest mismatch")
        return deepcopy(value)

    def check_access(self, project, source, target, action, *, origin=None):
        project = self.sessions.require_project(project)
        source = self.sessions.load(project, source.id)
        target = self.sessions.load(project, target.id)
        if source.status == SessionStatus.DELETED or target.status == SessionStatus.DELETED:
            raise ValueError("Session transfer requires live owned Sessions")
        if source.project_id != project.id or target.project_id != project.id:
            raise TransferDenied("Session transfer cannot cross Project ownership")
        if self.authorize is not None:
            request = {"action": action, "project_id": project.id,
                       "source_session_id": source.id, "target_session_id": target.id,
                       "source": deepcopy(origin["source"]) if origin else None,
                       "recipients": list(origin["recipients"]) if origin else []}
            allowed = self.authorize(request)
            if inspect.iscoroutine(allowed):
                allowed.close()
            if type(allowed) is not bool:
                raise TypeError("Session authorizer must return a literal boolean synchronously")
            if not allowed:
                raise TransferDenied("Session exchange denied: " + action)
        return project, source, target

    def load(self, project, source, identifier):
        value = self.validate(read_json(self.path(source, identifier)))
        if value["id"] != identifier or value["source"]["project_id"] != project.id or value["source"]["session_id"] != source.id:
            raise ValueError("Transfer receipt ownership mismatch")
        target = self.sessions.load(project, value["target_session_id"])
        self.validate_links(value, source, target,
            self.sessions.conversations(source), self.sessions.conversations(target), self.sessions.run_repository)
        return value, target

    @staticmethod
    def validate_links(value, source, target, source_store, target_store, runs):
        original = target_store.get(value["target_message_id"])
        admission = RequestAdmission.read(original)
        if (original.role != MessageRole.USER or original.metadata.get("transfer") != value
                or content_digest(original.content) != value["content_digest"] or admission is None
                or admission["recipients"] != value["recipients"] or original.metadata.get("conversation_deleted")):
            raise ValueError("Transfer target admission differs from its receipt")
        if source.id != value["source"]["session_id"] or target.id != value["target_session_id"]:
            raise ValueError("Transfer Session ownership mismatch")
        if value["source"]["run_id"] is not None:
            run = runs.load(source, value["source"]["run_id"])
            if run.agent_id != value["source"]["agent_id"] or run.input_message_id != value["source"]["message_id"]:
                raise ValueError("Transfer source Run identity mismatch")
            message = source_store.get(run.input_message_id)
            parent = message.metadata.get("transfer")
            if parent is not None:
                TransferRecords.validate(parent)
                if parent["target_session_id"] != source.id:
                    raise ValueError("Source transfer belongs to another Session")
            expected = (parent["chain"] if parent else [source.id]) + [target.id]
            if expected != value["chain"] or message.metadata.get("conversation_deleted"):
                raise ValueError("Transfer ancestor chain differs from its source")
        elif value["chain"] != [source.id, target.id]:
            raise ValueError("Host-origin transfer cannot claim an ancestor chain")
        return original

    def message_origin(self, project, session, message):
        value = message.metadata.get("transfer")
        if value is None:
            return None
        value = self.validate(value)
        if value["target_session_id"] != session.id or value["source"]["project_id"] != project.id:
            raise ValueError("Transfer input belongs to another Session or Project")
        source = self.sessions.load(project, value["source"]["session_id"])
        saved, _ = self.load(project, source, value["id"])
        if saved != value:
            raise ValueError("Transfer origin differs from its source receipt")
        self.validate_lineage(value, session, message, self.sessions.conversations(session), self.sessions.run_repository)
        return value

    @staticmethod
    def validate_lineage(value, session, message, store, runs):
        visited = set()
        while message.id != value["target_message_id"]:
            if message.id in visited or len(visited) >= 64 or message.metadata.get("transfer") != value:
                raise ValueError("Invalid transfer continuation lineage")
            visited.add(message.id)
            parent = message.metadata.get("resume", message.metadata.get("restart", {})).get("run_id")
            if not parent:
                raise ValueError("Transfer continuation requires an original Run")
            run = runs.load(session, parent)
            message = store.get(run.input_message_id)
        if message.metadata.get("transfer") != value:
            raise ValueError("Transfer original input is missing")

    def execution(self, project, session, run, *, action="execute"):
        message = self.sessions.conversations(session).get(run.input_message_id)
        value = self.message_origin(project, session, message)
        if value is None:
            return None
        if run.metadata.get("transfer") != value:
            raise ValueError("Run transport provenance differs from its User")
        source = self.sessions.load(project, value["source"]["session_id"])
        self.check_access(project, source, session, action, origin=value)
        return remaining(value["deadline"])

    def continuation(self, project, session, run):
        original = self.sessions.conversations(session).get(run.input_message_id)
        if "transfer" not in run.metadata and "transfer" not in original.metadata:
            return {}
        self.execution(project, session, run, action="resume")
        return {"transfer": deepcopy(run.metadata["transfer"])}

    def receipts(self, project):
        result = []
        for source in self.sessions.repository.list(project, include_deleted=True):
            folder = reject_links(source.paths.state / "transfers")
            for path in sorted(folder.glob("*.json")):
                value, _ = self.load(project, source, path.stem)
                result.append(value)
        return result

    def validate_session(self, project, session, messages):
        for message in messages:
            if "transfer" in message.metadata:
                self.message_origin(project, session, message)
        for path in sorted(reject_links(session.paths.state / "transfers").glob("*.json")):
            self.load(project, session, path.stem)

    def protected_sessions(self, project):
        return {identifier for value in self.receipts(project)
                for identifier in (value["source"]["session_id"], value["target_session_id"])}

    def require_unreferenced(self, project, session):
        if session.id in self.protected_sessions(project):
            raise ValueError("Session history is required by a retained transfer receipt")

    def validate_project(self, project, *, archive=False):
        # Archive inspection must use archive-owned paths and never log/recover.
        sessions = (self.sessions.repository.read_backup(project) if archive else
                    self.sessions.repository.list(project, include_deleted=True))
        by_id = {s.id: s for s in sessions}
        from llm.services.history.conversation import ConversationStore
        stores = {s.id: ConversationStore(s.paths.conversation) if archive else self.sessions.conversations(s)
                  for s in sessions}
        runs = self.sessions.run_repository
        if archive:
            # The original archive readers validate records without logging.
            from types import SimpleNamespace
            saved = {(s.id, run.id): run for s in sessions
                     for run in runs.read_backup(s, allow_pending=True)}
            runs = SimpleNamespace(load=lambda session, identifier: saved[(session.id, identifier)])
        values = {}
        for source in sessions:
            for path in sorted(reject_links(source.paths.state / "transfers").glob("*.json")):
                value = self.validate(read_json(reject_links(path)))
                if (path.stem != value["id"] or value["source"]["session_id"] != source.id
                        or value["source"]["project_id"] != project.id):
                    raise ValueError("Transfer archive ownership mismatch")
                target = by_id[value["target_session_id"]]
                self.validate_links(value, source, target, stores[source.id], stores[target.id], runs)
                values[value["id"]] = value
        for session in sessions:
            for message in stores[session.id].list():
                origin = message.metadata.get("transfer")
                if origin is not None:
                    origin = self.validate(origin)
                    if origin != values.get(origin["id"]) or origin["target_session_id"] != session.id:
                        raise ValueError("Transfer input has no matching source receipt")
                    self.validate_lineage(origin, session, message, stores[session.id], runs)
        return list(values.values())


class TransferAdmission:
    """Private extension of RunManager's existing atomic User admission."""

    def __init__(self, exchange, target_id, content, recipients, key, source_run_id, deadline):
        self.exchange, self.target_id, self.content = exchange, target_id, content
        self.recipients, self.key = RequestAdmission.identifiers(recipients), key_value(key)
        self.source_run_id, self.deadline = source_run_id, deadline_value(deadline)
        content_digest(content)

    def prepare(self):
        project, source, target = self.exchange._owned(self.target_id)
        records = self.exchange.records
        if project.conversation_storage != "file":
            raise ValueError("Durable Session exchange requires file conversation storage")
        # A custom factory declaring file ownership must actually provide the
        # portable journal contract; never persist a receipt to an ephemeral queue.
        from llm.services.history.conversation import ConversationStore
        if any(not isinstance(records.sessions.conversations(s), ConversationStore) for s in (source, target)):
            raise ValueError("Durable Session exchange requires portable ConversationStore journals")
        identity = {"project_id": project.id, "session_id": source.id,
                    "run_id": self.source_run_id, "agent_id": None, "message_id": None}
        chain = [source.id]
        if self.source_run_id is not None:
            run = records.sessions.run_repository.load(source, self.source_run_id)
            identity.update(agent_id=run.agent_id, message_id=run.input_message_id)
            message = records.sessions.conversations(source).get(run.input_message_id)
            origin = records.message_origin(project, source, message)
            if origin:
                chain = origin["chain"]
        if target.id in chain or len(chain) >= 16:
            raise ValueError("Session transfer cycle or depth limit")
        intent = {"storage_version": 1, "id": receipt_id(project.id, source.id, self.key),
                  "source": identity, "target_session_id": target.id, "recipients": self.recipients,
                  "content_digest": content_digest(self.content), "key": self.key,
                  "deadline": self.deadline, "chain": [*chain, target.id]}
        records.check_access(project, source, target, "send", origin=intent)
        return project, source, target, intent

    def existing(self):
        project, source, target, intent = self.prepare()
        records = self.exchange.records
        if records.path(source, intent["id"]).exists():
            value, _ = records.load(project, source, intent["id"])
            if any(value[k] != v for k, v in intent.items()):
                raise ValueError("transfer_conflict: idempotency key has a different payload")
            return records.sessions.conversations(target).get(value["target_message_id"])
        if any(m.metadata.get("transfer", {}).get("id") == intent["id"]
               for m in records.sessions.conversations(target).list()):
            raise ValueError("Transfer target exists without its receipt; recover integrity first")
        return None

    def persist(self, message):
        project, source, target, intent = self.prepare()
        remaining(intent["deadline"])
        value = {**intent, "accepted_at": clock().isoformat(), "target_message_id": message.id}
        value["digest"] = revision_token(value)
        value = self.exchange.records.validate(value)
        store = self.exchange.records.sessions.conversations(target)
        store.update_metadata(message.id, {"transfer": value})
        atomic_json(self.exchange.records.path(source, value["id"]), value)
        log_event(source.paths.logs, "transfer.accepted", entity_id=value["id"], related_id=message.id)
        log_event(target.paths.logs, "transfer.received", entity_id=message.id, related_id=value["id"])
        return store.get(message.id)


class SessionExchange:
    def __init__(self, session):
        self.session, self.app = session, session.app

    @property
    def records(self):
        return self.app.project_manager.sessions.transfers

    async def _async_call(self, operation, *args, **kwargs):
        return await self.app._storage_call(operation, *args, **kwargs)

    def _owned(self, target_id):
        self.app._check_open()
        project, source = self.session.project.data, self.session.data
        target = self.app.project_manager.sessions.load(project, target_id)
        return project, source, target

    def inspect(self, target_session_id, *, conversation_limit=0):
        if type(conversation_limit) is not int or not 0 <= conversation_limit <= 100:
            raise ValueError("conversation_limit must be between 0 and 100")
        with self.app.project_manager.ownership.scope():
            project, source, target = self._owned(target_session_id)
            self.records.check_access(project, source, target, "inspect")
            result = {"project_id": project.id, "session_id": target.id, "title": target.title,
                      "status": str(target.status), "agent_ids": list(target.agent_ids),
                      "current_run_ids": sorted(target.active_run_ids)}
            if conversation_limit:
                self.records.check_access(project, source, target, "conversation")
                from llm.core.steering import is_instruction
                values = [m for m in self.records.sessions.conversations(target).list()
                          if m.role in (MessageRole.USER, MessageRole.ASSISTANT) and not is_instruction(m)
                          and not m.metadata.get("conversation_deleted")
                          and m.status not in (MessageStatus.QUEUED, MessageStatus.STREAMING)]
                result["conversation"] = [{"id": m.id, "role": str(m.role), "text": m.content,
                                           "status": str(m.status)} for m in values[-conversation_limit:]]
            return result

    ainspect = async_method(inspect)

    def get(self, key):
        handle = TransferHandle(self, key_value(key))
        handle.snapshot()
        return handle

    aget = async_method(get)

    async def send(self, target_session_id, content, *, agent_ids, key, source_run_id=None, deadline=None):
        admission = TransferAdmission(self, target_session_id, content, agent_ids, key, source_run_id, deadline)
        _, _, target = await self._async_call(self._owned, target_session_id)
        manager = self.app._manager(target)
        from llm.core.configuration import UNSET
        await drain_on_cancel(manager._submit(content, UNSET, {}, UNSET, admission.recipients, UNSET,
                                              transfer=admission))
        return TransferHandle(self, key)


class TransferHandle:
    """Bound source authority; snapshots are detached JSON projections, not handles."""

    def __init__(self, exchange, key):
        self.exchange, self.key, self.app = exchange, key, exchange.app

    async def _async_call(self, operation, *args, **kwargs):
        return await self.app._storage_call(operation, *args, **kwargs)

    def _current(self, action="query"):
        self.app._check_open()
        project, source = self.exchange.session.project.data, self.exchange.session.data
        identifier = receipt_id(project.id, source.id, self.key)
        value, target = self.exchange.records.load(project, source, identifier)
        self.exchange.records.check_access(project, source, target, action, origin=value)
        store = self.exchange.records.sessions.conversations(target)
        messages = [m for m in store.list() if m.metadata.get("transfer", {}).get("id") == identifier]
        for message in messages:
            self.exchange.records.validate_lineage(value, target, message, store, self.app.run_repository)
        return value, target, messages

    def _snapshot(self, action):
        with self.app.project_manager.ownership.scope():
            value, target, messages = self._current(action)
            rows, requests = [], []
            for message in messages:
                runs = self.app.run_repository.for_request(target, message)
                requests.append({"message_id": message.id, "status": str(message.status),
                                 "run_ids": [run.id for run in runs]})
                for run in runs:
                    response = self.exchange.records.sessions.conversations(target).get(run.assistant_message_id)
                    rows.append({"run_id": run.id, "agent_id": run.agent_id, "status": str(run.status),
                                 "input_message_id": run.input_message_id, "assistant_message_id": response.id,
                                 "error_code": run.error_code,
                                 "result": {"message_id": response.id, "text": response.content, "status": str(response.status)},
                                 "previous_run_id": run.metadata.get("resume", run.metadata.get("restart", {})).get("run_id")})
            parents = {m.metadata.get("resume", m.metadata.get("restart", {})).get("run_id") for m in messages}
            states = {r["status"] for r in rows if r["run_id"] not in parents}
            states.update("accepted" if m.status == MessageStatus.QUEUED else "cancelled"
                          for m in messages if not any(r["input_message_id"] == m.id for r in rows))
            status = ("running" if "running" in states else "accepted" if states & {"accepted", "pending"}
                      else next(iter(states)) if len(states) == 1 else "partial_failure")
            return {"id": value["id"], "status": status, "source": deepcopy(value["source"]),
                    "target_session_id": target.id, "recipients": list(value["recipients"]),
                    "message_id": value["target_message_id"], "key": self.key,
                    "deadline": value["deadline"], "accepted_at": value["accepted_at"],
                    "requests": requests, "runs": rows}

    def snapshot(self):
        return self._snapshot("query")

    asnapshot = async_method(snapshot)

    async def wait(self, *, timeout=None):
        import asyncio
        async def wait_all():
            _, target, messages = await self._async_call(self._current)
            for message in messages:
                if message.status == MessageStatus.CANCELLED:
                    continue
                await self.app._manager(target).wait_requests(message.id)
            return await self.asnapshot()
        return await asyncio.wait_for(wait_all(), timeout=timeout)

    async def cancel(self, *, run_id=None):
        value, target, messages = await self._async_call(self._current, "cancel")
        manager = self.app._manager(target)
        rows = (await self._async_call(self._snapshot, "cancel"))["runs"]
        if run_id is not None:
            if run_id not in {r["run_id"] for r in rows}:
                raise ValueError("Run does not belong to this transfer")
            return await manager.cancel_run(run_id)
        changed = False
        for message in messages:
            changed = await manager.cancel_request(message.id) or changed
        # Re-read after cancellation: admission may have won the concurrent race.
        for run in (await self._async_call(self._snapshot, "cancel"))["runs"]:
            if run["status"] in ("pending", "running"):
                changed = await manager.cancel_run(run["run_id"]) or changed
        return changed

    async def resume(self, run_id, **options):
        _, target, _ = await self._async_call(self._current, "resume")
        if run_id not in {r["run_id"] for r in (await self._async_call(self._snapshot, "resume"))["runs"]}:
            raise ValueError("Run does not belong to this transfer")
        await self.app._manager(target).resume(run_id, **options)
        return self
