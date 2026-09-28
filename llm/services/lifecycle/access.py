"""Project의 최신 상태와 소유 경로를 검증한다. 오래된 핸들이 삭제된 Project에 쓰지 못하도록 서비스 간 검사를 통일한다.

Authoritative Project lifecycle checks shared by application services."""

from typing import Protocol

from llm.services.infrastructure.locking import WorkspaceOwnership
from llm.core.models import Project
from llm.core.paths import ProjectPaths


class ProjectReader(Protocol):
    ownership: WorkspaceOwnership
    def paths(self, project_id: str) -> ProjectPaths: ...
    def load(self, project_id: str) -> Project: ...


class ProjectAccess:
    def __init__(self, repository: ProjectReader) -> None:
        self.repository = repository

    def load(self, project_id: str, *, allow_deleted: bool = False) -> Project:
        current = self.repository.load(project_id)
        if current.deleted and not allow_deleted:
            raise ValueError("Project is deleted")
        return current

    def require(self, project: Project, *, allow_deleted: bool = False) -> Project:
        expected = self.repository.paths(project.id)
        if project.paths.root.absolute() != expected.root.absolute():
            raise ValueError("Project path ownership mismatch")
        return self.load(project.id, allow_deleted=allow_deleted)
