"""Agent 프롬프트의 버전 확인과 변경을 같은 Project 잠금 안에서 처리한다."""

from typing import Optional
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import async_method


class AgentData(ComponentData):
    """일반 CRUD에 프롬프트 편의 API를 추가한다. 이미 시작한 Run 스냅샷은 수정하지 않는다."""

    @workspace_locked
    def snapshot(self, identifier: str) -> dict:
        """UI 편집과 실행 이력 비교에 사용할 정의와 버전을 반환한다."""
        project, component = self._current()
        data = component.load(project, identifier)
        return {"agent_id": identifier, "definition": data, "revision": component.revision(data)}

    @workspace_locked
    def revise(self, identifier: str, definition: dict, *, expected_revision: str) -> dict:
        """동시 UI 편집을 덮어쓰지 않고 전체 정의를 교체한다."""
        project, component = self._current()
        if component.revision(component.load(project, identifier)) != expected_revision:
            raise ValueError("Agent changed; reload its definition before updating")
        component.save(project, identifier, definition)
        log_event(project.paths.logs, "agent.updated", entity_id=project.id)
        return {"agent_id": identifier, "revision": component.revision(definition), "applies_to": "subsequent_runs"}

    @workspace_locked
    def prompt(self, identifier: str) -> dict:
        project, component = self._current()
        data = component.load(project, identifier)
        # UI가 저장하지 않은 빈 문자열을 다시 저장해 상속을 끊지 않게 한다.
        return {"agent_id": identifier, **({"system_prompt": data["system_prompt"]} if "system_prompt" in data else {}),
                "revision": component.revision(data)}

    @workspace_locked
    def update_prompt(self, identifier: str, system_prompt: Optional[str], *, expected_revision: str) -> dict:
        if system_prompt is not None and not isinstance(system_prompt, str):
            raise TypeError("system_prompt must be a string or None")
        project, component = self._current()
        data = component.load(project, identifier)
        if component.revision(data) != expected_revision:
            raise ValueError("Agent changed; reload its prompt before updating")
        data["system_prompt"] = system_prompt
        component.save(project, identifier, data)
        log_event(project.paths.logs, "agent.prompt_updated", entity_id=project.id)
        return {"agent_id": identifier, "revision": component.revision(data), "applies_to": "subsequent_runs"}

    aprompt = async_method(prompt)
    aupdate_prompt = async_method(update_prompt)
    asnapshot = async_method(snapshot)
    arevise = async_method(revise)
