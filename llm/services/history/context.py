"""모델 입력용 대화와 복제용 대화의 정책을 관리한다. 전체/최근/완료/문자 예산 프리셋은 사용자-응답 쌍을 보존하고 현재 입력을 반드시 유지한다.

Build conversation context for Runs and clones, independent of storage."""

from copy import deepcopy

from llm.core.models import Message, MessageRole, MessageStatus
from llm.core.steering import is_instruction, RunInstruction
from dataclasses import dataclass
from typing import Optional
@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """문맥 선택 프리셋. budget은 토큰 추정 대신 정확한 문자 수 상한을 사용한다."""

    mode: Optional[str] = None
    max_turns: Optional[int] = None
    max_chars: Optional[int] = None

    def __post_init__(self):
        if self.mode not in (None, "full", "recent", "completed", "recent_completed", "budget"):
            raise ValueError("Unknown context policy")
        if self.max_turns is not None and (type(self.max_turns) is not int or self.max_turns < 1):
            raise ValueError("max_turns must be positive")
        if self.max_chars is not None and (type(self.max_chars) is not int or self.max_chars < 1):
            raise ValueError("max_chars must be positive")
        if self.mode in ("recent", "recent_completed") and self.max_turns is None:
            raise ValueError("Recent context requires explicit max_turns")
        if self.mode == "budget" and self.max_chars is None:
            raise ValueError("budget policy requires max_chars")


# 저장된 대화에서 모델 입력에 필요한 대화를 선택한다.
class ConversationContextBuilder:
    def __init__(self, policy: Optional[ContextPolicy] = None):
        self.policy = policy

    def _select(self, history, policy=None):
        policy = policy if policy is not None else self.policy
        if policy is None:
            return tuple(deepcopy(history))
        # 현재 입력은 항상 유지한다. 과거 대화는 사용자/응답 쌍 단위로 제거한다.
        current, previous = history[-1], history[:-1]
        groups = []
        for message in previous:
            if (message.role == MessageRole.USER and not is_instruction(message)) or not groups:
                groups.append([])
            groups[-1].append(message)
        if policy.mode in ("completed", "recent_completed"):
            groups = [group for group in groups if any(
                item.role == MessageRole.ASSISTANT and item.status == MessageStatus.COMPLETED for item in group)
                and not any(item.status in (MessageStatus.FAILED, MessageStatus.INTERRUPTED) for item in group)]
        if policy.mode in ("recent", "recent_completed"):
            groups = groups[-policy.max_turns:]
        if policy.max_chars is not None:
            budget = policy.max_chars - len(current.content)
            if budget < 0:
                raise ValueError("Current request exceeds context character budget")
            kept = []
            for group in reversed(groups):
                size = sum(len(item.content) for item in group)
                if size > budget:
                    break
                budget -= size
                kept.append(group)
            groups = list(reversed(kept))
        return tuple(deepcopy([item for group in groups for item in group] + [current]))

    def _ordered(self, messages: list[Message]) -> list[Message]:
        inputs = {message.run_id: index for index, message in enumerate(messages)
                  if message.role == MessageRole.USER and message.run_id and not is_instruction(message)}
        # 재개된 작업의 지시를 옛 실패 턴에만 남기면 recent 정책이 이를 버린다.
        # 각 지시는 자기 Run을 실제로 재개한 가장 최근 자손 턴에 연결한다.
        # 형제 재개 분기의 지시는 섞지 않으며 원본 Message/Run 연결은 수정하지 않는다.
        continuations = dict(inputs)
        for run_id in reversed(inputs):
            parent = messages[inputs[run_id]].metadata.get("resume", {}).get("run_id")
            if parent in continuations:
                continuations[parent] = max(continuations[parent], continuations[run_id])
        return [message for _, message in sorted(enumerate(messages), key=lambda item: (
            (continuations if is_instruction(item[1]) else inputs).get(item[1].run_id, item[0]),
            item[1].role == MessageRole.ASSISTANT, is_instruction(item[1]), item[0],
        ))]

    # 공개 API
    def for_run(self, messages: list[Message], input_message_id: str, *,
                 policy: Optional[ContextPolicy] = None) -> tuple[Message, ...]:
        messages = [m for m in messages if not m.metadata.get("conversation_deleted")]
        for message in messages:
            if is_instruction(message):
                RunInstruction.validate_message(message)
        history = []
        committed = {message.run_id for message in messages
                     if message.role == MessageRole.USER and message.status == MessageStatus.COMMITTED}
        for message in self._ordered(messages):
            # 노드 대상 지시는 그 실행의 체크포인트로만 복원한다. 다른 Agent/후속 Run에
            # 일반 사용자 지시처럼 흘러가지 않으며 대화 조회에는 원문이 그대로 남는다.
            if is_instruction(message) and any(t["scope"] is not None for t in message.metadata["steering"].get("targets", [])):
                continue
            if message.id == input_message_id:
                history.append(message)
                return self._select(history, policy)
            if message.role == MessageRole.USER:
                if message.status == MessageStatus.COMMITTED:
                    history.append(message)
            elif message.status in (MessageStatus.COMPLETED, MessageStatus.INTERRUPTED, MessageStatus.PAUSED,
                                    MessageStatus.FAILED):
                if message.run_id is None or message.run_id in committed:
                    history.append(message)
        raise ValueError("Run input message is missing from the conversation")

    def for_clone(self, messages: list[Message]) -> list[Message]:
        messages = [m for m in messages if not m.metadata.get("conversation_deleted")]
        for message in messages:
            if is_instruction(message):
                RunInstruction.validate_message(message)
        snapshot = deepcopy(self._ordered(messages))
        for message in snapshot:
            message.run_id = None
            if message.status == MessageStatus.QUEUED:
                message.status = MessageStatus.CANCELLED
                if is_instruction(message):
                    message.metadata["steering"].update(status="unapplied", reason="cloned")
                    for target in message.metadata["steering"].get("targets", []):
                        if target["status"] == "pending":
                            target.update(status="unapplied", reason="cloned")
            elif message.status == MessageStatus.STREAMING:
                message.status = MessageStatus.INTERRUPTED
        return snapshot
