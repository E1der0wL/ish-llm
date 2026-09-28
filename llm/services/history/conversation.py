"""Session 대화의 공통 API와 파일/메모리 저장소. 기본 JSONL은 fsync로 복구하고 메모리는 명시적으로 선택한다."""

from abc import ABC, abstractmethod
from typing import Callable, Optional, Union
import json
import os
import threading
from functools import wraps
from copy import deepcopy
from collections import Counter, OrderedDict
from pathlib import Path
from io import StringIO

from llm.core.models import Message, MessageRole, MessageStatus, Session, new_id
from llm.core.paths import SessionPaths
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import record, sync_directory, append_bytes, prepare_replace, temporary_file
from llm.services.infrastructure.transactions import current_transaction
from llm.services.query import Query, select


def _serialized(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._mutex:
            return method(self, *args, **kwargs)
    return guarded


class Conversation(ABC):
    """파일/메모리 저장에 공통인 메시지 상태 전이와 분리된 조회 스냅샷을 제공한다."""

    def __init__(self) -> None:
        self._mutex = threading.RLock()
        self._messages: dict[str, Message] = {}
        self._text_buffers: dict[str, StringIO] = {}
        self._counts = Counter()

    def _clear(self):
        self._messages.clear()
        self._text_buffers.clear()
        self._counts.clear()

    @abstractmethod
    def _refresh(self) -> None:
        """선택한 저장소의 최신 상태를 반영한다."""

    @abstractmethod
    def _append(self, event: dict) -> None:
        """이벤트를 저장한 뒤 상태에 반영한다. 저장 실패 시 상태를 변경하지 않는다."""

    def _append_many(self, events):
        # 사용자 저장소는 단일 이벤트 계약만 구현해도 묶음 API를 사용할 수 있다.
        for event in events:
            self._append(event)

    def _snapshot(self, message: Message) -> Message:
        """조회할 메시지만 본문을 합친다. 내부 버퍼는 공개 모델/저장 형식에 노출하지 않는다."""
        snapshot = deepcopy(message)
        if message.id in self._text_buffers:
            snapshot.content = self._text_buffers[message.id].getvalue()
        return snapshot

    def _apply(self, event: dict) -> None:
        kind = event["type"]
        if kind == "message.create":
            data = event["message"]
            if data["id"] in self._messages:
                raise ValueError("Duplicate message ID")
            self._messages[data["id"]] = Message(
                **{**data, "role": MessageRole(data["role"]),
                   "status": MessageStatus(data["status"])})
            self._counts[(data["role"], data["status"])] += 1
        else:
            message = self._messages[event["id"]]
            if kind == "message.delta":
                if not isinstance(event["text"], str):
                    raise TypeError("Message delta text must be a string")
                operation = event.get("operation", "append")
                buffer = self._text_buffers.get(message.id)
                if buffer is None or operation == "replace":
                    buffer = StringIO()
                    if operation != "replace":
                        buffer.write(message.content)
                    self._text_buffers[message.id] = buffer
                    message.content = ""
                buffer.write(event["text"])
            elif kind == "message.status":
                status = MessageStatus(event["status"])
                if status != MessageStatus.STREAMING and message.id in self._text_buffers:
                    message.content = self._text_buffers.pop(message.id).getvalue()
                self._counts[(message.role, message.status)] -= 1
                message.status = status
                self._counts[(message.role, message.status)] += 1
            elif kind == "message.metadata":
                message.metadata.update(event["metadata"])
            elif kind == "message.run":
                message.run_id = event["run_id"]
            else:
                raise ValueError(f"Unknown conversation event: {kind}")

    # 공개 API
    @_serialized
    def count(self, *, status: Optional[MessageStatus] = None, role: Optional[MessageRole] = None) -> int:
        """내용/메타데이터 복사 없이 상태별 개수를 센다. 대기열 admission에 사용한다."""
        self._refresh()
        return sum(count for (item_role, item_status), count in self._counts.items()
                   if (status is None or item_status == status) and (role is None or item_role == role))

    @_serialized
    def list(self, *, query: Optional[Query] = None) -> list[Message]:
        self._refresh()
        items = list(self._messages.values()) if query is None else self._messages.values()
        return [self._snapshot(message) for message in select(items, query)]

    @_serialized
    def get(self, message_id: str) -> Message:
        self._refresh()
        return self._snapshot(self._messages[message_id])

    @_serialized
    def create(self, role: MessageRole, content: str, status: MessageStatus,
               *, message_id: Optional[str] = None, run_id: Optional[str] = None,
               metadata: Optional[dict] = None) -> Message:
        message = Message(message_id or new_id(), role, content, status,
                          run_id=run_id, metadata=deepcopy(metadata or {}))
        self._refresh()
        if message.id in self._messages:
            raise ValueError("Duplicate message ID")
        self._append({"type": "message.create", "message": record(message)})
        return message

    @_serialized
    def delta(self, message_id: str, text: str, *, operation: str = "append") -> None:
        self._refresh()
        if operation not in ("append", "replace"):
            raise ValueError("Message delta operation must be append or replace")
        if self._messages[message_id].status != MessageStatus.STREAMING:
            raise ValueError("Deltas require a streaming message")
        self._append({"type": "message.delta", "id": message_id, "text": text, "operation": operation})

    @_serialized
    def reconcile(self, message_id: str, text: str, *, run_id: str) -> None:
        """소유 Run의 출력 저널로 본문만 복구한다. 기존 메시지 상태는 변경하지 않는다."""
        self._refresh()
        message = self._messages[message_id]
        if message.role != MessageRole.ASSISTANT or message.run_id != run_id:
            raise ValueError("Output reconciliation ownership mismatch")
        if not isinstance(text, str):
            raise TypeError("Reconciled message content must be text")
        self._append({"type": "message.delta", "id": message_id, "text": text,
                      "operation": "replace", "reason": "output_reconciliation"})

    @_serialized
    def deltas(self, message_id: str, values) -> None:
        """하나의 저장 경계로 여러 델타를 확정한다. 모든 입력을 쓰기 전에 검증한다."""
        self._refresh()
        if self._messages[message_id].status != MessageStatus.STREAMING:
            raise ValueError("Deltas require a streaming message")
        events = []
        for text, operation in values:
            if not isinstance(text, str) or operation not in ("append", "replace"):
                raise ValueError("Invalid message delta")
            events.append({"type": "message.delta", "id": message_id, "text": text, "operation": operation})
        if type(self).delta is not Conversation.delta or "delta" in self.__dict__:
            for event in events:
                if event["operation"] == "append":
                    self.delta(message_id, event["text"])
                else:
                    self.delta(message_id, event["text"], operation=event["operation"])
            return
        self._append_many(events)

    @_serialized
    def set_status(self, message_id: str, status: MessageStatus) -> None:
        self._refresh()
        self._messages[message_id]
        self._append({"type": "message.status", "id": message_id, "status": status})

    @_serialized
    def bind_run(self, message_id: str, run_id: str) -> None:
        self._refresh()
        self._messages[message_id]
        self._append({"type": "message.run", "id": message_id, "run_id": run_id})

    @_serialized
    def update_metadata(self, message_id: str, metadata: dict) -> None:
        self._refresh()
        self._messages[message_id]
        self._append({"type": "message.metadata", "id": message_id, "metadata": metadata})


# JSONL 이벤트를 증분 반영하여 최신 메시지 상태를 복원한다.
class ConversationStore(Conversation):
    """Incremental JSONL projection; caller owns workspace write coordination."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self._offset = 0
        self._signature = None

    def _enlist(self):
        transaction = current_transaction()
        if transaction is not None and transaction.owns(self.path):
            def invalidate():
                with self._mutex:
                    self._clear()
                    self._offset, self._signature = 0, None
            transaction.on_rollback(("conversation", id(self)), invalidate)

    @_serialized
    def prune(self, message_ids):
        """비활성 Session의 명시적 정리만 사용한다. 생존 메시지를 원자 교체하고 캐시를 무효화한다."""
        from llm.services.infrastructure.storage import reject_links
        reject_links(self.path)
        self._refresh()
        removed = set(message_ids)
        if any(m.id in removed and m.status in (MessageStatus.QUEUED, MessageStatus.STREAMING)
               for m in self._messages.values()):
            raise ValueError("Cannot prune pending messages")
        self._enlist()
        prepare_replace(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = temporary_file(self.path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                for message in self._messages.values():
                    if message.id not in removed:
                        stream.write(json.dumps({"type": "message.create", "message": record(self._snapshot(message))},
                                                ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            sync_directory(self.path.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
            self._signature = None
            self._offset = 0
            self._clear()

    def _repair_tail(self) -> None:
        # A crash can leave an incomplete last append. Never discard a full line.
        if not self.path.exists():
            return
        with self.path.open("r+b") as stream:
            stream.seek(0, os.SEEK_END)
            end = stream.tell()
            if not end:
                return
            stream.seek(end - 1)
            if stream.read(1) == b"\n":
                return
            position = end
            while position:
                start = max(0, position - 8192)
                stream.seek(start)
                block = stream.read(position - start)
                newline = block.rfind(b"\n")
                if newline != -1:
                    transaction = current_transaction()
                    if transaction is not None and transaction.owns(self.path):
                        transaction.before_truncate(self.path, start + newline + 1)
                    stream.truncate(start + newline + 1)
                    break
                position = start
            else:
                transaction = current_transaction()
                if transaction is not None and transaction.owns(self.path):
                    transaction.before_truncate(self.path, 0)
                stream.truncate(0)
            stream.flush()
            os.fsync(stream.fileno())

    def _append(self, event: dict) -> None:
        data = (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        self._enlist()
        if self._signature is not None and self._offset < self._signature[2]:
            self._repair_tail()
        append_bytes(self.path, data)
        # Apply the serialized snapshot only after fsync succeeds.
        self._apply(json.loads(data))
        stat = self.path.stat()
        self._offset = stat.st_size
        self._signature = self._stamp(stat)
        # Text already has a durable JSONL record. Avoid a second file open and
        # rotation check for every token; lifecycle events remain operational logs.
        if event["type"] != "message.delta":
            message = event.get("message", {})
            log_event(SessionPaths(self.path.parent).logs, event["type"],
                      entity_id=event.get("id", message.get("id")),
                      status=event.get("status", message.get("status")))

    def _append_many(self, events):
        if type(self)._append is not ConversationStore._append:
            return super()._append_many(events)
        if not events:
            return
        data = b"".join((json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                        for event in events)
        self._enlist()
        if self._signature is not None and self._offset < self._signature[2]:
            self._repair_tail()
        append_bytes(self.path, data)
        for line in data.splitlines():
            self._apply(json.loads(line))
        stat = self.path.stat()
        self._offset, self._signature = stat.st_size, self._stamp(stat)

    @staticmethod
    def _stamp(stat):
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

    def _refresh(self) -> None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self._clear()
            self._offset, self._signature = 0, None
            return
        stamp = self._stamp(stat)
        if stamp == self._signature:
            return
        previous = self._signature
        if (previous is None or stamp[:2] != previous[:2]
                or stat.st_size < self._offset
                or (stat.st_size <= previous[2] and stamp != previous)):
            self._clear()
            self._offset = 0
        try:
            with self.path.open("rb") as stream:
                stream.seek(self._offset)
                for line in stream:
                    if not line.endswith(b"\n"):
                        break
                    self._apply(json.loads(line))
                    self._offset += len(line)
            self._signature = stamp
        except BaseException:
            # A failed replay must fail again on the next read, not return a
            # partially projected cache as if the corrupt record were valid.
            self._clear()
            self._offset, self._signature = 0, None
            raise


def conversation_store(session: Session) -> ConversationStore:
    """Default injectable factory; consumers do not choose storage paths."""
    return ConversationStore(session.paths.conversation)


class MemoryConversationStore(Conversation):
    """한 Session의 휘발성 메시지 저장소. 파일이나 전역 캐시에 접근하지 않는다."""

    def _refresh(self) -> None:
        pass

    def _append(self, event: dict) -> None:
        # 파일 모드와 동일한 JSON 값 검증/스냅샷 계약을 지킨다.
        snapshot = json.loads(json.dumps(event, ensure_ascii=False, allow_nan=False))
        transaction = current_transaction()
        if transaction is not None:
            transaction.changed()
            identifier = snapshot.get("id", snapshot.get("message", {}).get("id"))
            key = ("memory_message", id(self), identifier)
            previous = deepcopy(self._messages.get(identifier))
            # append 취소에는 이전 길이만 필요하다. replace는 새 버퍼를 만들어 원본을 보존한다.
            previous_buffer = self._text_buffers.get(identifier)
            previous_length = previous_buffer.tell() if previous_buffer is not None else 0
            def restore():
                with self._mutex:
                    if previous is None:
                        self._messages.pop(identifier, None)
                    else:
                        self._messages[identifier] = previous
                    if previous_buffer is None:
                        self._text_buffers.pop(identifier, None)
                    else:
                        previous_buffer.seek(previous_length)
                        previous_buffer.truncate()
                        self._text_buffers[identifier] = previous_buffer
                    self._counts = Counter((m.role, m.status) for m in self._messages.values())
            transaction.on_rollback(key, restore)
        self._apply(snapshot)

    # 공개 API
    @_serialized
    def clear(self) -> None:
        """저장소를 소유한 서비스가 수명 종료 시 메시지를 해제한다."""
        self._clear()


class MemoryConversations:
    """Session별 메모리 저장소를 공유하는 주입용 팩토리. 서로 다른 workspace도 구분한다.

    직접 주입하면 팩토리 수명은 호출자가 관리한다. 문자열 'memory' 선택은 백엔드가
    새 팩토리를 소유하고 shutdown 시 정리하므로 다음 백엔드와 대화를 공유하지 않는다.
    """

    def __init__(self) -> None:
        self._mutex = threading.RLock()
        self._stores: dict[Path, MemoryConversationStore] = {}

    @staticmethod
    def _key(session: Session) -> Path:
        return session.paths.root.resolve()

    # 공개 API
    def __call__(self, session: Session) -> MemoryConversationStore:
        with self._mutex:
            key = self._key(session)
            if key not in self._stores:
                self._stores[key] = MemoryConversationStore()
            return self._stores[key]

    def discard(self, session: Session) -> None:
        """영구 삭제된 Session의 대화를 해제한다. 소프트 삭제에는 호출하지 않는다."""
        with self._mutex:
            store = self._stores.pop(self._key(session), None)
            if store is not None:
                store.clear()

    def clear(self) -> None:
        """팩토리 소유자가 모든 런타임 종료 후 호출한다."""
        with self._mutex:
            for store in self._stores.values():
                store.clear()
            self._stores.clear()


class ProjectConversations:
    """프로젝트의 선택을 적용하며 실행·조회·복제에 동일한 메모리 저장소를 제공한다.

    새 프로젝트 생성 시 미지정 선택은 백엔드 기본값을 따른다. 사용자 팩토리는 그 계약을
    임의의 파일/메모리 구현으로 대체하지 않으며, 수명도 호출자가 관리한다.
    """

    def __init__(self, selection: Callable[[Session], Optional[str]], *,
                 default: Union[str, Callable[[Session], Conversation]] = conversation_store,
                 file_cache_size: int = 32) -> None:
        if type(file_cache_size) is not int or file_cache_size < 0:
            raise ValueError("file_cache_size must be nonnegative")
        if default is conversation_store:
            default = "file"
        if isinstance(default, str):
            if default not in ("file", "memory"):
                raise ValueError("Conversation storage must be 'file', 'memory', or a factory")
            self.default_storage: Optional[str] = default
            self._custom = None
        elif callable(default):
            self.default_storage = None
            self._custom = default
        else:
            raise TypeError("Conversation storage must be 'file', 'memory', or a factory")
        self._selection = selection
        self._memory = MemoryConversations()
        self._file_cache_size = file_cache_size
        self._files = OrderedDict()
        self._mutex = threading.RLock()

    def _file(self, session):
        if not self._file_cache_size:
            return conversation_store(session)
        key = session.paths.conversation.absolute()
        with self._mutex:
            if key not in self._files:
                self._files[key] = conversation_store(session)
            self._files.move_to_end(key)
            while len(self._files) > self._file_cache_size:
                self._files.popitem(last=False)
            return self._files[key]

    def resolve(self, storage: Optional[str]) -> Optional[str]:
        """저장소를 열기 전에 선택과 주입된 팩토리의 충돌을 검증한다."""
        if storage not in (None, "file", "memory"):
            raise ValueError("Project conversation_storage must be 'file' or 'memory'")
        if storage is not None and self._custom is not None:
            raise ValueError("Project conversation_storage conflicts with the custom conversation factory")
        return storage if storage is not None else self.default_storage

    def __call__(self, session: Session) -> Conversation:
        storage = self.resolve(self._selection(session))
        if storage == "memory":
            return self._memory(session)
        if storage == "file":
            return self._file(session)
        return self._custom(session)

    def discard(self, session: Session) -> None:
        # Project가 이미 영구 삭제되었을 수 있으므로 여기서는 설정을 다시 읽지 않는다.
        self._memory.discard(session)
        with self._mutex:
            self._files.pop(session.paths.conversation.absolute(), None)
        discard = getattr(self._custom, "discard", None)
        if callable(discard):
            discard(session)

    def clear_owned(self) -> None:
        """백엔드가 소유한 메모리/파일 읽기 캐시만 정리한다. 파일은 삭제하지 않는다."""
        self._memory.clear()
        with self._mutex:
            self._files.clear()
