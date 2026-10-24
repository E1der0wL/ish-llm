"""모델 입력용 대화와 복제용 대화의 정책을 관리한다. 전체/최근/완료/문자 예산 프리셋은 사용자-응답 쌍을 보존하고 현재 입력을 반드시 유지한다.

Build conversation context for Runs and clones, independent of storage."""

from llm.core.continuations import ResumeRecord
from llm.core.instruction_records import InstructionMessageRecord
from llm.core.steering_events import SteeringReason

from copy import deepcopy
from llm.core.policies import ContextMode, ContextPolicy

from llm.core.models import Message, MessageRole, MessageStatus
from llm.core.steering import SteeringScope, is_instruction, RunInstruction
from typing import Optional


# 저장된 대화에서 모델 입력에 필요한 대화를 선택한다.
class ConversationContextBuilder:
    UNSPECIFIED_POLICY = None
    MINIMUM_REMAINING_CHARS = 0
    FAILED_GROUP_STATES = (MessageStatus.FAILED, MessageStatus.INTERRUPTED)
    HISTORY_STATES = (
        MessageStatus.COMPLETED,
        MessageStatus.INTERRUPTED,
        MessageStatus.PAUSED,
        MessageStatus.FAILED,
    )
    CURRENT_BUDGET_EXCEEDED = "Current request exceeds context character budget"
    MISSING_INPUT = "Run input message is missing from the conversation"

    def __init__(self, policy: Optional[ContextPolicy] = UNSPECIFIED_POLICY):
        self.policy = policy

    def snapshot_for_run(self, messages, input_message_id):
        """Freeze selected history before Run-local budgets can reject execution."""
        return self.for_run(messages, input_message_id, policy=ContextPolicy(mode=ContextMode.FULL.value))

    def select_snapshot(self, history, *, policy=UNSPECIFIED_POLICY):
        """Apply the saved Run policy to its already ordered, frozen history."""
        return self._select(history, policy)

    def _select(self, history, policy=UNSPECIFIED_POLICY):
        policy = policy if policy is not self.UNSPECIFIED_POLICY else self.policy
        if policy is self.UNSPECIFIED_POLICY:
            return tuple(deepcopy(history))
        # 현재 입력은 항상 유지한다. 과거 대화는 사용자/응답 쌍 단위로 제거한다.
        current, previous = history[-1], history[:-1]
        groups = []
        for message in previous:
            if (message.role == MessageRole.USER and not is_instruction(message)) or not groups:
                groups.append([])
            groups[-1].append(message)
        if ContextPolicy.completed_only(policy):
            groups = [
                group
                for group in groups
                if any(
                    item.role == MessageRole.ASSISTANT and item.status == MessageStatus.COMPLETED
                    for item in group
                )
                and not any(item.status in self.FAILED_GROUP_STATES for item in group)
            ]
        if ContextPolicy.recent_only(policy):
            groups = groups[-policy.max_turns :]
        if ContextPolicy.has_character_limit(policy):
            budget = policy.max_chars - len(current.content)
            if budget < self.MINIMUM_REMAINING_CHARS:
                raise ValueError(self.CURRENT_BUDGET_EXCEEDED)
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
        from llm.services.runtime.requests import RequestAdmission

        inputs = {
            run_id: index
            for index, message in enumerate(messages)
            if message.role == MessageRole.USER and not is_instruction(message)
            for run_id in RequestAdmission.run_ids(message)
        }
        # 재개된 작업의 지시를 옛 실패 턴에만 남기면 recent 정책이 이를 버린다.
        # 각 지시는 자기 Run을 실제로 재개한 가장 최근 자손 턴에 연결한다.
        # 형제 재개 분기의 지시는 섞지 않으며 원본 Message/Run 연결은 수정하지 않는다.
        continuations = dict(inputs)
        for run_id in reversed(inputs):
            parent = ResumeRecord.source_id(messages[inputs[run_id]].metadata)
            if parent in continuations:
                continuations[parent] = max(continuations[parent], continuations[run_id])
        return [
            message
            for _, message in sorted(
                enumerate(messages),
                key=lambda item: (
                    (continuations if is_instruction(item[1]) else inputs).get(item[1].run_id, item[0]),
                    item[1].role == MessageRole.ASSISTANT,
                    is_instruction(item[1]),
                    item[0],
                ),
            )
        ]

    # 공개 API
    def for_run(
        self,
        messages: list[Message],
        input_message_id: str,
        *,
        policy: Optional[ContextPolicy] = UNSPECIFIED_POLICY,
    ) -> tuple[Message, ...]:
        messages = [m for m in messages if not m.conversation_deleted]
        for message in messages:
            if is_instruction(message):
                RunInstruction.validate_message(message)
        history = []
        from llm.services.runtime.requests import RequestAdmission

        committed = {
            run_id
            for message in messages
            if message.role == MessageRole.USER and message.status == MessageStatus.COMMITTED
            for run_id in RequestAdmission.run_ids(message)
        }
        for message in self._ordered(messages):
            # 노드 대상 지시는 그 실행의 체크포인트로만 복원한다. 다른 Agent/후속 Run에
            # 일반 사용자 지시처럼 흘러가지 않으며 대화 조회에는 원문이 그대로 남는다.
            if is_instruction(message) and any(
                t.scope is not SteeringScope.ROOT
                for t in InstructionMessageRecord.from_message(message).targets
            ):
                continue
            if message.id == input_message_id:
                history.append(message)
                return self._select(history, policy)
            if message.role == MessageRole.USER:
                if message.status == MessageStatus.COMMITTED:
                    history.append(message)
            elif message.status in self.HISTORY_STATES:
                if message.run_id is Message.UNBOUND_RUN or message.run_id in committed:
                    history.append(message)
        raise ValueError(self.MISSING_INPUT)

    def for_clone(self, messages: list[Message]) -> list[Message]:
        from llm.services.runtime.requests import RequestAdmission
        from llm.services.runtime.transfer_values import TransferReceipt

        messages = [m for m in messages if not m.conversation_deleted]
        for message in messages:
            if is_instruction(message):
                RunInstruction.validate_message(message)
        snapshot = deepcopy(self._ordered(messages))
        snapshot = [
            RequestAdmission.clone_message(message) if RequestAdmission.present(message) else message
            for message in snapshot
        ]
        for message in snapshot:
            message.detach_run()
            if TransferReceipt.present(message.metadata):
                message.metadata = TransferReceipt.clone_metadata(message.metadata)
            if message.status == MessageStatus.QUEUED:
                if is_instruction(message):
                    value = InstructionMessageRecord.from_message(message)
                    value = value.with_targets(
                        target.finish(SteeringReason.CLONED) if target.pending_for() else target
                        for target in value.targets
                    )
                    message.metadata.update(value.metadata())
                    message.status = value.message_status
                else:
                    message.status = MessageStatus.CANCELLED
            elif message.status == MessageStatus.STREAMING:
                message.status = MessageStatus.INTERRUPTED
        return snapshot
