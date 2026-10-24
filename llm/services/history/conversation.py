"""Session 대화의 공통 API와 파일/메모리 저장소. 기본 JSONL은 fsync로 복구하고 메모리는 명시적으로 선택한다."""

from llm.core.results import OutputOperation

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

from llm.core.models import (
    ConversationStorage,
    ConversationStorageInput,
    Message,
    MessageRole,
    MessageStatus,
    Session,
    new_id,
)
from llm.core.paths import SessionPaths
from llm.services.infrastructure.storage import sync_directory, append_bytes, prepare_replace, temporary_file
from llm.services.infrastructure.transactions import current_transaction, NO_TRANSACTION
from .conversation_records import ConversationEvent, ConversationJournal, ConversationFileStamp
from llm.services.query import Query, select


def _serialized(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._mutex:
            return method(self, *args, **kwargs)

    return guarded


class Conversation(ABC):
    """파일/메모리 저장에 공통인 메시지 상태 전이와 분리된 조회 스냅샷을 제공한다."""

    ABSENT = None
    EMPTY_TEXT = ""
    COUNT_INCREMENT = 1
    DUPLICATE_ID = "Duplicate message ID"
    INVALID_OPERATION = ConversationEvent.INVALID_OPERATION
    STREAMING_REQUIRED = "Deltas require a streaming message"
    RECONCILIATION_OWNER = "Output reconciliation ownership mismatch"
    INVALID_RECONCILIATION = "Reconciled message content must be text"
    INVALID_DELTA = "Invalid message delta"

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

    def _validate(self, event):
        if event.kind == ConversationEvent.CREATE:
            data = event.creation
            if data.id in self._messages:
                raise ValueError(self.DUPLICATE_ID)
            data.message()
        else:
            event.validate_change(self._messages[event.id])

    def _record(self, event):
        # Also protect injected stores before invoking their write hook.
        self._validate(ConversationEvent(event))
        self._append(event)

    def _apply(self, event: dict) -> None:
        event = ConversationEvent(event)
        self._validate(event)
        kind = event.kind
        if kind == ConversationEvent.CREATE:
            data = event.creation
            self._messages[data.id] = data.message()
            self._counts[data.count_key] += self.COUNT_INCREMENT
        else:
            message = self._messages[event.id]
            if kind == ConversationEvent.DELTA:
                operation = event.operation
                buffer = self._text_buffers.get(message.id)
                if buffer is self.ABSENT or operation == OutputOperation.REPLACE:
                    buffer = StringIO()
                    if operation != OutputOperation.REPLACE:
                        buffer.write(message.content)
                    self._text_buffers[message.id] = buffer
                    message.content = self.EMPTY_TEXT
                buffer.write(event.text)
            elif kind == ConversationEvent.CHANGE_STATUS:
                status = MessageStatus(event.status)
                if status != MessageStatus.STREAMING and message.id in self._text_buffers:
                    message.content = self._text_buffers.pop(message.id).getvalue()
                self._counts[(message.role, message.status)] -= self.COUNT_INCREMENT
                message.status = status
                self._counts[(message.role, message.status)] += self.COUNT_INCREMENT
            elif kind == ConversationEvent.CHANGE_METADATA:
                message.metadata.update(event.metadata)
            elif kind == ConversationEvent.BIND_RUN:
                message.run_id = event.run_id

    # 공개 API
    @_serialized
    def count(self, *, status: Optional[MessageStatus] = ABSENT, role: Optional[MessageRole] = ABSENT) -> int:
        """내용/메타데이터 복사 없이 상태별 개수를 센다. 대기열 admission에 사용한다."""
        self._refresh()
        return sum(
            count
            for (item_role, item_status), count in self._counts.items()
            if (status is self.ABSENT or item_status == status) and (role is self.ABSENT or item_role == role)
        )

    @_serialized
    def list(self, *, query: Optional[Query] = Query.UNSPECIFIED) -> list[Message]:
        self._refresh()
        items = list(self._messages.values()) if query is Query.UNSPECIFIED else self._messages.values()
        return [self._snapshot(message) for message in select(items, query)]

    @_serialized
    def get(self, message_id: str) -> Message:
        self._refresh()
        return self._snapshot(self._messages[message_id])

    @_serialized
    def create(
        self,
        role: MessageRole,
        content: str,
        status: MessageStatus,
        *,
        message_id: Optional[str] = ABSENT,
        run_id: Optional[str] = Message.UNBOUND_RUN,
        metadata: Optional[dict] = ABSENT,
    ) -> Message:
        message = Message(
            message_id or new_id(), role, content, status, run_id=run_id, metadata=deepcopy(metadata or {})
        )
        self._refresh()
        if message.id in self._messages:
            raise ValueError(self.DUPLICATE_ID)
        self._record(ConversationEvent.create(message))
        return message

    @_serialized
    def delta(self, message_id: str, text: str, *, operation: str = OutputOperation.APPEND.value) -> None:
        self._refresh()
        if operation not in tuple(OutputOperation):
            raise ValueError(self.INVALID_OPERATION)
        if self._messages[message_id].status != MessageStatus.STREAMING:
            raise ValueError(self.STREAMING_REQUIRED)
        self._record(ConversationEvent.delta(message_id, text, operation))

    @_serialized
    def reconcile(self, message_id: str, text: str, *, run_id: str) -> None:
        """소유 Run의 출력 저널로 본문만 복구한다. 기존 메시지 상태는 변경하지 않는다."""
        self._refresh()
        message = self._messages[message_id]
        if message.role != MessageRole.ASSISTANT or message.run_id != run_id:
            raise ValueError(self.RECONCILIATION_OWNER)
        if not isinstance(text, str):
            raise TypeError(self.INVALID_RECONCILIATION)
        self._record(ConversationEvent.reconcile(message_id, text))

    @_serialized
    def deltas(self, message_id: str, values) -> None:
        """하나의 저장 경계로 여러 델타를 확정한다. 모든 입력을 쓰기 전에 검증한다."""
        self._refresh()
        if self._messages[message_id].status != MessageStatus.STREAMING:
            raise ValueError(self.STREAMING_REQUIRED)
        events = []
        for text, operation in values:
            if not isinstance(text, str) or operation not in tuple(OutputOperation):
                raise ValueError(self.INVALID_DELTA)
            events.append(ConversationEvent.delta(message_id, text, operation))
        if type(self).delta is not Conversation.delta or Conversation.delta.__name__ in self.__dict__:
            for event in events:
                event = ConversationEvent(event)
                if event.explicit_operation == OutputOperation.APPEND:
                    self.delta(message_id, event.text)
                else:
                    self.delta(message_id, event.text, operation=event.explicit_operation)
            return
        self._append_many(events)

    @_serialized
    def set_status(self, message_id: str, status: MessageStatus) -> None:
        self._refresh()
        self._messages[message_id]
        self._record(ConversationEvent.change_status(message_id, status))

    @_serialized
    def bind_run(self, message_id: str, run_id: str) -> None:
        self._refresh()
        self._messages[message_id]
        self._record(ConversationEvent.bind_run(message_id, run_id))

    @_serialized
    def update_metadata(self, message_id: str, metadata: dict) -> None:
        self._refresh()
        self._messages[message_id]
        self._record(ConversationEvent.change_metadata(message_id, metadata))


# JSONL 이벤트를 증분 반영하여 최신 메시지 상태를 복원한다.
class ConversationStore(Conversation):
    """Incremental JSONL projection; caller owns workspace write coordination."""

    INITIAL_OFFSET = 0
    NO_SIGNATURE = None
    ROLLBACK_KEY = "conversation"
    PRUNE_PROTECTED_STATES = Message.PENDING_STATES
    PENDING_PRUNE = "Cannot prune pending messages"
    INCOMPLETE_EVENT = "Incomplete conversation event; preserve the original journal"

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self._offset = self.INITIAL_OFFSET
        self._signature = self.NO_SIGNATURE

    def _enlist(self):
        transaction = current_transaction()
        if transaction is not NO_TRANSACTION and transaction.owns(self.path):

            def invalidate():
                with self._mutex:
                    self._clear()
                    self._offset, self._signature = self.INITIAL_OFFSET, self.NO_SIGNATURE

            transaction.on_rollback((self.ROLLBACK_KEY, id(self)), invalidate)

    @_serialized
    def prune(self, message_ids):
        """비활성 Session의 명시적 정리만 사용한다. 생존 메시지를 원자 교체하고 캐시를 무효화한다."""
        from llm.services.infrastructure.storage import reject_links

        reject_links(self.path)
        self._refresh()
        removed = set(message_ids)
        if any(m.id in removed and m.status in self.PRUNE_PROTECTED_STATES for m in self._messages.values()):
            raise ValueError(self.PENDING_PRUNE)
        self._enlist()
        prepare_replace(self.path)
        self.path.parent.mkdir(
            parents=ConversationJournal.CREATE_PARENTS, exist_ok=ConversationJournal.ALLOW_EXISTING_DIRECTORY
        )
        fd, temporary = temporary_file(self.path)
        try:
            with os.fdopen(
                fd, ConversationJournal.REPLACE_MODE, encoding=ConversationJournal.ENCODING
            ) as stream:
                for message in self._messages.values():
                    if message.id not in removed:
                        stream.write(
                            ConversationJournal.line(ConversationEvent.create(self._snapshot(message)))
                        )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            sync_directory(self.path.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
            self._signature = self.NO_SIGNATURE
            self._offset = self.INITIAL_OFFSET
            self._clear()

    def _append(self, event: dict) -> None:
        data = ConversationJournal.encode(event)
        snapshot = json.loads(data)
        self._validate(ConversationEvent(snapshot))
        self._enlist()
        append_bytes(self.path, data)
        # Apply the serialized snapshot only after fsync succeeds.
        self._apply(snapshot)
        stat = self.path.stat()
        self._offset = stat.st_size
        self._signature = self._stamp(stat)
        # Text already has a durable JSONL record. Avoid a second file open and
        # rotation check for every token; lifecycle events remain operational logs.
        if ConversationEvent(event).kind != ConversationEvent.DELTA:
            ConversationEvent(event).log(SessionPaths(self.path.parent).logs)

    def _append_many(self, events):
        if type(self)._append is not ConversationStore._append:
            return super()._append_many(events)
        if not events:
            return
        data = ConversationJournal.encode_many(events)
        self._enlist()
        append_bytes(self.path, data)
        for line in data.splitlines():
            self._apply(json.loads(line))
        stat = self.path.stat()
        self._offset, self._signature = stat.st_size, self._stamp(stat)

    @staticmethod
    def _stamp(stat):
        return ConversationFileStamp.capture(stat)

    def _refresh(self) -> None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self._clear()
            self._offset, self._signature = self.INITIAL_OFFSET, self.NO_SIGNATURE
            return
        stamp = self._stamp(stat)
        if stamp == self._signature:
            return
        previous = self._signature
        if (
            previous is self.NO_SIGNATURE
            or stamp.file_identity != previous.file_identity
            or stat.st_size < self._offset
            or (stat.st_size <= previous.size and stamp != previous)
        ):
            self._clear()
            self._offset = self.INITIAL_OFFSET
        try:
            with self.path.open(ConversationJournal.READ_MODE) as stream:
                stream.seek(self._offset)
                for line in stream:
                    if not line.endswith(ConversationJournal.RECORD_END):
                        raise ValueError(self.INCOMPLETE_EVENT)
                    self._apply(json.loads(line))
                    self._offset += len(line)
            self._signature = stamp
        except BaseException:
            # A failed replay must fail again on the next read, not return a
            # partially projected cache as if the corrupt record were valid.
            self._clear()
            self._offset, self._signature = self.INITIAL_OFFSET, self.NO_SIGNATURE
            raise


def conversation_store(session: Session) -> ConversationStore:
    """Default injectable factory; consumers do not choose storage paths."""
    return ConversationStore(session.paths.conversation)


class MemoryConversationStore(Conversation):
    """한 Session의 휘발성 메시지 저장소. 파일이나 전역 캐시에 접근하지 않는다."""

    ROLLBACK_KEY = "memory_message"
    EMPTY_BUFFER_LENGTH = 0

    def _refresh(self) -> None:
        pass

    def _append(self, event: dict) -> None:
        # 파일 모드와 동일한 JSON 값 검증/스냅샷 계약을 지킨다.
        snapshot = ConversationJournal.snapshot(event)
        self._validate(ConversationEvent(snapshot))
        transaction = current_transaction()
        if transaction is not NO_TRANSACTION:
            transaction.changed()
            identifier = ConversationEvent(snapshot).observation_id
            key = (self.ROLLBACK_KEY, id(self), identifier)
            previous = deepcopy(self._messages.get(identifier))
            # append 취소에는 이전 길이만 필요하다. replace는 새 버퍼를 만들어 원본을 보존한다.
            previous_buffer = self._text_buffers.get(identifier)
            previous_length = (
                previous_buffer.tell() if previous_buffer is not self.ABSENT else self.EMPTY_BUFFER_LENGTH
            )

            def restore():
                with self._mutex:
                    if previous is self.ABSENT:
                        self._messages.pop(identifier, self.ABSENT)
                    else:
                        self._messages[identifier] = previous
                    if previous_buffer is self.ABSENT:
                        self._text_buffers.pop(identifier, self.ABSENT)
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

    ABSENT = None

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
            store = self._stores.pop(self._key(session), self.ABSENT)
            if store is not self.ABSENT:
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

    DEFAULT_FILE_CACHE_SIZE = 32
    MINIMUM_CACHE_SIZE = 0
    ABSENT = None
    EVICT_OLDEST = False
    DISCARD_METHOD = MemoryConversations.discard.__name__
    INVALID_CACHE_SIZE = "file_cache_size must be nonnegative"
    INVALID_FACTORY = "Conversation storage must be 'file', 'memory', or a factory"
    FACTORY_CONFLICT = "Project conversation_storage conflicts with the custom conversation factory"

    def __init__(
        self,
        selection: Callable[[Session], Optional[str]],
        *,
        default: Union[str, Callable[[Session], Conversation]] = conversation_store,
        file_cache_size: int = DEFAULT_FILE_CACHE_SIZE,
    ) -> None:
        if type(file_cache_size) is not int or file_cache_size < self.MINIMUM_CACHE_SIZE:
            raise ValueError(self.INVALID_CACHE_SIZE)
        if default is conversation_store:
            default = ConversationStorage.FILE.value
        if isinstance(default, str):
            ConversationStorage.validate(default, nullable=ConversationStorageInput.EXPLICIT)
            self.default_storage: Optional[str] = default
            self._custom = self.ABSENT
        elif callable(default):
            self.default_storage = ConversationStorageInput.INJECTED
            self._custom = default
        else:
            raise TypeError(self.INVALID_FACTORY)
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
                self._files.popitem(last=self.EVICT_OLDEST)
            return self._files[key]

    def resolve(self, storage: Optional[str]) -> Optional[str]:
        """저장소를 열기 전에 선택과 주입된 팩토리의 충돌을 검증한다."""
        ConversationStorage.validate(storage)
        if storage is not ConversationStorageInput.INJECTED and self._custom is not self.ABSENT:
            raise ValueError(self.FACTORY_CONFLICT)
        return storage if storage is not ConversationStorageInput.INJECTED else self.default_storage

    def __call__(self, session: Session) -> Conversation:
        storage = self.resolve(self._selection(session))
        if storage == ConversationStorage.MEMORY:
            return self._memory(session)
        if storage == ConversationStorage.FILE:
            return self._file(session)
        return self._custom(session)

    def discard(self, session: Session) -> None:
        # Project가 이미 영구 삭제되었을 수 있으므로 여기서는 설정을 다시 읽지 않는다.
        self._memory.discard(session)
        with self._mutex:
            self._files.pop(session.paths.conversation.absolute(), self.ABSENT)
        discard = getattr(self._custom, self.DISCARD_METHOD, self.ABSENT)
        if callable(discard):
            discard(session)

    def clear_owned(self) -> None:
        """백엔드가 소유한 메모리/파일 읽기 캐시만 정리한다. 파일은 삭제하지 않는다."""
        self._memory.clear()
        with self._mutex:
            self._files.clear()
