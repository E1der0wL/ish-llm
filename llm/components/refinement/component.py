"""제안은 실행 기록의 참조 데이터다. 자동 모델 호출이나 Run 수명은 소유하지 않는다."""

from llm.components.definitions import DefinitionComponent
from llm.core.schema import object_schema, implementation_schema, field
from .data import RefinementData


IDENTIFIER = field("string", pattern="^[A-Za-z0-9_-]{1,64}$")
TARGET = object_schema({"component": field("string", enum=["skills", "prompts", "agents", "memory"]),
    "identifier": IDENTIFIER, "session_id": IDENTIFIER}, required=["component", "identifier"], additionalProperties=False)
EVIDENCE = object_schema({"session_id": IDENTIFIER, "run_id": IDENTIFIER, "step_id": IDENTIFIER,
    "tool_call_id": field("string", minLength=1), "error_code": field("string", minLength=1),
    "note": field("string", minLength=1)}, required=["session_id", "run_id"], additionalProperties=False)


class RefinementComponent(DefinitionComponent):
    name = directory = "refinement"
    capabilities = ("refinement", "tools")
    data_class = RefinementData
    schema = object_schema({
        "target": TARGET, "operation": {"const": "update"}, "expected_version": field("string", minLength=1),
        "reason": field("string", minLength=1),
        "evidence": {"type": "array", "minItems": 1, "items": EVIDENCE},
        "patch": object_schema(minProperties=1),
        "status": field("string", enum=["proposed", "approved", "rejected", "applied", "rolled_back", "failed"]),
        "before": object_schema(), "source": object_schema(), "history": {"type": "array", "items": object_schema()},
    }, required=["target", "operation", "expected_version", "reason", "evidence", "patch", "status", "before", "source", "history"])

    def configuration_schema(self):
        return implementation_schema(config=object_schema(additionalProperties=False),
            policy=object_schema({"apply_tools": field("boolean")}, additionalProperties=False))

    def clone(self, source, destination):
        # 제안은 원본 Project의 실행/버전/승인에 묶여 있다. 새 Project에서 재승인으로 위장하지 않는다.
        self.initialize(destination)

    def history_references(self, project):
        records = self.list(project).values()
        return {"message_ids": [], "session_ids": [], "run_ids": sorted({ref["run_id"]
            for record in records for ref in [*record["evidence"], record.get("source", {})] if "run_id" in ref})}

    def resolve(self, project, capability):
        raise ValueError("Refinement capabilities require a lifecycle-bound component data factory")

    def resolve_runtime(self, project, capability, *, data_factory):
        data = data_factory(self.name)
        if capability == "refinement":
            return data
        if capability == "tools":
            from .tools import refinement_tools
            return refinement_tools(data, apply=self.configuration(project).get("policy", {}).get("apply_tools") is True)
        return super().resolve_runtime(project, capability, data_factory=data_factory)
