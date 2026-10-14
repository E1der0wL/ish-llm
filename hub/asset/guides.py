"""Immutable manifest of editable defaults copied only into new projects."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StarterSkill:
    identifier: str
    title: str
    description: str
    filename: str

    def load(self) -> tuple[str, str, str, str]:
        instructions = Path(__file__).with_name("skills").joinpath(self.filename).read_text(encoding="utf-8")
        return self.identifier, self.title, self.description, instructions


SYSTEM_PROMPT = Path(__file__).with_name("prompts").joinpath("system.md").read_text(encoding="utf-8")
STARTER_SKILLS = (
    StarterSkill("hub-code-change", "코드 수정", "기존 코드의 동작과 경계를 확인하고 필요한 변경을 검증합니다.", "coding.md"),
    StarterSkill("hub-diagnose", "오류 진단", "오류를 재현하고 근거를 모아 원인과 해결책을 구분합니다.", "diagnose.md"),
    StarterSkill("hub-document", "문서 작성", "독자와 목적에 맞는 문서를 근거에 따라 작성하고 검토합니다.", "document.md"),
    StarterSkill("hub-create-rag", "RAG 자료 생성", "자료를 읽고 검색 가능한 RAG 문서로 등록합니다.", "create-rag.md"),
    StarterSkill("hub-create-workflow", "Workflow 생성", "검증 가능한 그래프 실행 흐름을 작성합니다.", "create-workflow.md"),
    StarterSkill("hub-create-skill", "Skill 생성", "재사용할 작업 지침을 작성하고 저장합니다.", "create-skill.md"),
    StarterSkill("hub-create-agent", "Agent 생성", "업무 범위와 도구가 명확한 Agent를 작성합니다.", "create-agent.md"),
)
SKILLS = tuple(skill.load() for skill in STARTER_SKILLS)
