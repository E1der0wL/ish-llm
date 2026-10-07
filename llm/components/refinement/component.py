"""제안은 실행 기록의 참조 데이터다. 자동 모델 호출이나 Run 수명은 소유하지 않는다."""

from llm.components.definitions import DefinitionComponent
from llm.core.schema import object_schema, implementation_schema, field, open_schema, metadata_schema
from .data import RefinementData


IDENTIFIER = field("string", pattern="^[A-Za-z0-9_-]{1,64}$")
TARGET = object_schema({"component": field("string", enum=["skills", "prompts", "agents", "memory"]),
    "identifier": IDENTIFIER, "session_id": IDENTIFIER}, required=["component", "identifier"], additionalProperties=False)
EVIDENCE = object_schema({"session_id": IDENTIFIER, "run_id": IDENTIFIER, "step_id": IDENTIFIER,
    "tool_call_id": field("string", minLength=1), "error_code": field("string", minLength=1),
    "note": field("string", minLength=1)}, required=["session_id", "run_id"], additionalProperties=False)
PARENT = object_schema({"identifier": IDENTIFIER, "version": field("string", pattern="^[a-f0-9]{64}$")},
    required=["identifier", "version"], additionalProperties=False)
EVALUATION = object_schema({
    "evaluator": field("string", minLength=1), "baseline_version": field(["string", "null"]),
    "candidate_version": field("string", pattern="^[a-f0-9]{64}$"),
    "results": open_schema("external evaluator", category="result"),
    "regressions": {"type": "array", "x-schema-owner": "external evaluator"},
    "improvements": {"type": "array", "x-schema-owner": "external evaluator"},
    "evidence": {"type": "array", "items": EVIDENCE}},
    required=["evaluator", "baseline_version", "candidate_version", "results", "regressions", "improvements"],
    additionalProperties=False)

SOURCE = object_schema({"kind": field("string", minLength=1), "session_id": IDENTIFIER,
    "run_id": IDENTIFIER, "step_id": IDENTIFIER, "proposal_id": IDENTIFIER,
    "reason": field("string"), "metadata": metadata_schema()}, required=["kind"])
HISTORY = object_schema({"status": field("string"), "at": field("string"), "source": SOURCE},
                        required=["status", "at", "source"])
STORED_EVALUATION = object_schema({**EVALUATION["properties"], "source": SOURCE, "at": field("string")},
    required=[*EVALUATION["required"], "source", "at"])


class RefinementComponent(DefinitionComponent):
    name = directory = "refinement"
    capabilities = ("refinement", "tools")
    data_class = RefinementData
    schema = object_schema({
        "target": TARGET, "operation": {"enum": ["update", "create", "fork", "bind_skills"]},
        "expected_version": field(["string", "null"], minLength=1), "parent": PARENT,
        "reason": field("string", minLength=1),
        "evidence": {"type": "array", "minItems": 1, "items": EVIDENCE},
        "patch": open_schema("Refinement target EDITABLE and selected target validator", category="implementation", minProperties=1),
        "status": field("string", enum=["proposed", "approved", "rejected", "applied", "rolled_back", "failed"]),
        "before": open_schema("selected target record snapshot", category="implementation"),
        "metadata": metadata_schema(), "source": SOURCE, "history": {"type": "array", "items": HISTORY},
        "evaluations": {"type": "array", "items": STORED_EVALUATION},
        "applied_target": object_schema({"identifier": IDENTIFIER, "version": field("string")}, required=["identifier", "version"]),
        "skill_versions": object_schema(additionalProperties=field("string", pattern="^[a-f0-9]{64}$")),
    }, required=["target", "operation", "expected_version", "reason", "evidence", "patch", "status", "before", "source", "history"])

    def describe_config(self):
        return implementation_schema(config=object_schema(additionalProperties=False),
            policy=object_schema({"apply_tools": field("boolean"), "require_evaluation": field("boolean"),
                "proposal_operations": {"type": "array", "uniqueItems": True,
                    "items": {"enum": ["update", "create", "fork", "bind_skills"]}}}, additionalProperties=False))

    def clone(self, source, destination):
        # 제안은 원본 Project의 실행/버전/승인에 묶여 있다. 새 Project에서 재승인으로 위장하지 않는다.
        self.initialize(destination)

    def history_references(self, project):
        records = self.list(project).values()
        return {"message_ids": [], "session_ids": [], "run_ids": sorted({ref["run_id"]
            for record in records for ref in [*record["evidence"], record.get("source", {}),
                *(ref for evaluation in record.get("evaluations", []) for ref in evaluation.get("evidence", [])),
                *(evaluation.get("source", {}) for evaluation in record.get("evaluations", []))] if "run_id" in ref})}

    def resolve(self, project, capability):
        raise ValueError("Refinement capabilities require a lifecycle-bound component data factory")

    def resolve_runtime(self, project, capability, *, data_factory):
        data = data_factory(self.name)
        if capability == "refinement":
            return data
        if capability == "tools":
            from .tools import refinement_tools
            policy = self.get_config(project).get("policy", {})
            return refinement_tools(data, apply=policy.get("apply_tools") is True,
                                    operations=policy.get("proposal_operations", ()))
        return super().resolve_runtime(project, capability, data_factory=data_factory)
