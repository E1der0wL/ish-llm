"""목표와 진행 상태를 저장한다. Run은 소유하지 않고 참조만 한다."""

from llm.core.history_references import HistoryReferenceRecord

from llm.components.processing import COMPLETION_PROCESSORS
from llm.components.tools.registry import ToolRegistry

from llm.components.definitions import DefinitionComponent
from .data import GoalData
from .records import GoalDefinition, GoalRecord, GoalRunReference, GoalSettings


class GoalComponent(DefinitionComponent):
    name = directory = GoalDefinition.COMPONENT
    capabilities = (GoalDefinition.COMPONENT, ToolRegistry.CAPABILITY, COMPLETION_PROCESSORS)
    data_class = GoalData
    schema = GoalDefinition.schema()
    LIFECYCLE_REQUIRED = "Goal capabilities require a lifecycle-bound component data factory"

    def describe_config(self):
        return GoalSettings.schema()

    def validate_config(self, data):
        super().validate_config(data)
        GoalSettings(data).validate_injection()

    def history_references(self, project):
        records = self.list(project).values()
        return HistoryReferenceRecord.project(
            run_ids=sorted({GoalRunReference(r).run_id for value in records for r in GoalRecord(value).run_refs}),
            message_ids=[], session_ids=sorted({GoalRecord(v).scope.session_id for v in self.list(project).values()
                                               if GoalRecord(v).scope.is_session}))

    def clone(self, source, destination):
        # Session와 Run은 복제되지 않는다. 잘못된 목적지 참조를 만들지 않는다.
        for identifier, value in self.list(source).items():
            selected = GoalRecord(value)
            if selected.scope.is_project:
                self.create(destination, selected.cloned(source.id, identifier), identifier=identifier)

    def resolve(self, project, capability):
        raise ValueError(self.LIFECYCLE_REQUIRED)

    def resolve_runtime(self, project, capability, *, data_factory):
        data = data_factory(self.name)
        if capability == GoalDefinition.COMPONENT:
            return data
        if capability == ToolRegistry.CAPABILITY:
            from .tools import goal_tools
            return goal_tools(data, write=GoalSettings(self.get_config(project)).write_tools)
        if capability == COMPLETION_PROCESSORS:
            from .processing import GoalProcessor
            return GoalProcessor(data, self.get_config(project))
        return super().resolve_runtime(project, capability, data_factory=data_factory)
