"""Refinement target policy and effects through the selected resource's public API."""

from copy import deepcopy
from llm.core.agents import AgentDefinition, AgentResourceSelection
from llm.core.editable_snapshot import EditableSnapshot
from llm.components.skills.records import SkillRecord
from llm.components.prompts.records import PromptDefinition
from llm.components.memory.identity import MemoryIdentity
from llm.components.memory.records import MemoryRecord
from llm.services.infrastructure.storage import check_revision, revision_token
from .records import ProposalOperation, RefinementTarget, RefinementRevision


class RefinementTargets:
    ABSENT = None
    PRESENT = False
    CREATING = (ProposalOperation.CREATE, ProposalOperation.FORK)
    EDITABLE = {
        SkillRecord.CAPABILITY: SkillRecord.EDITABLE,
        PromptDefinition.CAPABILITY: {PromptDefinition.Field.MESSAGES, PromptDefinition.Field.DESCRIPTION},
        AgentDefinition.REFERENCE_COMPONENT: {
            AgentDefinition.Field.PURPOSE,
            AgentDefinition.Field.DESCRIPTION,
        },
        MemoryIdentity.COMPONENT: {
            MemoryRecord.Field.CONTENT,
            MemoryRecord.Field.KIND,
            MemoryRecord.Field.TAGS,
        },
    }
    MEMORY_CONTENT = (
        MemoryRecord.Field.CONTENT,
        MemoryRecord.Field.KIND,
        MemoryRecord.Field.TAGS,
        MemoryRecord.Field.SCOPE,
    )
    PROPOSAL_METADATA = "refinement_proposal"
    INVALID_SCOPE = "Only scoped Memory targets accept session_id"
    ALREADY_EXISTS = "Refinement creation target already exists"
    INVALID_CREATION = "Skill creation requires an absent target and expected_version=null"
    REQUIRED_VERSION = "expected_version is required"
    UNEXPECTED_PARENT = "Only a fork accepts parent"
    INVALID_BINDING = "Skill binding requires unique Skill IDs"
    INVALID_BINDING_PATCH = "Binding may change only Agent resources.skills"
    CHANGED_SKILL = "Bound Skill changed; create a new binding proposal"
    UNCHANGED_BINDING = "Binding does not change the target"
    INVALID_PATCH = "Refinement patch contains unsupported or authority-changing fields"
    UNCHANGED_PATCH = "Refinement patch does not change the target"
    INVALID_CONTENT = "Memory candidate requires content"
    INVALID_KIND = "Memory candidate requires kind"
    INVALID_TAGS = "Memory candidate requires text tags"

    def __init__(self, data):
        self.data = data

    def handle(self, target):
        return (
            self.data.agents()
            if target.component == AgentDefinition.REFERENCE_COMPONENT
            else self.data.related(target.component)
        )

    def target(self, raw, *, absent=PRESENT):
        target = RefinementTarget.read(raw)
        if target.has_session and target.component != MemoryIdentity.COMPONENT:
            raise ValueError(self.INVALID_SCOPE)
        handle = self.handle(target)
        kwargs = target.session_arguments()
        if absent:
            try:
                handle.load(target.identifier, **kwargs)
            except FileNotFoundError:
                return handle, {}, kwargs
            raise FileExistsError(self.ALREADY_EXISTS)
        value = handle.load(target.identifier, **kwargs)
        return handle, value, kwargs

    def baseline(self, proposal):
        operation = proposal.operation
        creating = operation in self.CREATING
        handle, current, kwargs = self.data._target(proposal.target.to_dict(), absent=creating)
        if creating:
            if (
                proposal.target.component != SkillRecord.CAPABILITY
                or proposal.expected_version is not self.ABSENT
            ):
                raise ValueError(self.INVALID_CREATION)
        else:
            if not proposal.expected_version:
                raise ValueError(self.REQUIRED_VERSION)
            check_revision(current, proposal.expected_version)
        if operation == ProposalOperation.FORK:
            parent = RefinementRevision.read_parent(proposal.optional_parent)
            current = handle.load(parent.identifier)
            check_revision(current, parent.version)
        elif proposal.has_parent:
            raise ValueError(self.UNEXPECTED_PARENT)
        return handle, current, kwargs

    def bindings(self, skills):
        if (
            not isinstance(skills, list)
            or any(not isinstance(s, str) for s in skills)
            or len(set(skills)) != len(skills)
        ):
            raise ValueError(self.INVALID_BINDING)
        handle = self.data.related(SkillRecord.CAPABILITY)
        return {key: EditableSnapshot.read(handle.snapshot(key)).version for key in skills}

    def replacement(self, proposal, current):
        target, patch = proposal.target, proposal.patch
        if proposal.operation == ProposalOperation.BIND_SKILLS:
            if target.component != AgentDefinition.REFERENCE_COMPONENT or not proposal.has_only_skill_binding:
                raise ValueError(self.INVALID_BINDING_PATCH)
            versions = self.data._bindings(proposal.requested_skills)
            if proposal.skill_versions != versions:
                raise ValueError(self.CHANGED_SKILL)
            result = AgentResourceSelection.replace_skills(current, proposal.requested_skills)
            if result == current:
                raise ValueError(self.UNCHANGED_BINDING)
            AgentDefinition.validate(target.identifier, result)
            return result
        if not isinstance(patch, dict) or not patch or set(patch) - self.EDITABLE[target.component]:
            raise ValueError(self.INVALID_PATCH)
        result = {**deepcopy(current), **deepcopy(patch)}
        if proposal.operation == ProposalOperation.FORK:
            result = SkillRecord.renamed(result, target.identifier)
            result = SkillRecord.read(result).with_lineage(
                proposal.parent.identifier, proposal.parent.version
            )
        if result == current:
            raise ValueError(self.UNCHANGED_PATCH)
        if target.component == MemoryIdentity.COMPONENT:
            self.validate_memory_candidate(result)
        elif target.component == AgentDefinition.REFERENCE_COMPONENT:
            AgentDefinition.validate(target.identifier, result)
        else:
            self.data.registry.get(target.component).validate_record(target.identifier, result)
        return result

    @classmethod
    def validate_memory_candidate(cls, result):
        if (
            not isinstance(result.get(MemoryRecord.Field.CONTENT), str)
            or not result[MemoryRecord.Field.CONTENT].strip()
        ):
            raise ValueError(cls.INVALID_CONTENT)
        if (
            not isinstance(result.get(MemoryRecord.Field.KIND), str)
            or not result[MemoryRecord.Field.KIND].strip()
        ):
            raise ValueError(cls.INVALID_KIND)
        if not isinstance(result.get(MemoryRecord.Field.TAGS), list) or any(
            not isinstance(t, str) or not t.strip() for t in result[MemoryRecord.Field.TAGS]
        ):
            raise ValueError(cls.INVALID_TAGS)

    @classmethod
    def memory_candidate(cls, replacement, current, target_id, identifier):
        candidate = {key: deepcopy(replacement[key]) for key in cls.MEMORY_CONTENT}
        if MemoryRecord.Field.SESSION_ID in replacement:
            candidate[MemoryRecord.Field.SESSION_ID] = replacement[MemoryRecord.Field.SESSION_ID]
        original = MemoryRecord(current)
        candidate.update(
            {
                MemoryRecord.Field.STATUS: MemoryRecord.CANDIDATE,
                MemoryRecord.Field.REPLACES: [
                    MemoryRecord.reference(target_id, original.generation, original.revision)
                ],
                MemoryRecord.Field.METADATA: {cls.PROPOSAL_METADATA: identifier},
            }
        )
        return candidate

    def apply(self, identifier, proposal, handle, current, kwargs, replacement, source):
        component, target_id = proposal.target.component, proposal.target.identifier
        if proposal.operation == ProposalOperation.FORK:
            applied_id = handle.fork(
                proposal.parent.identifier,
                replacement,
                identifier=target_id,
                expected_revision=proposal.parent.version,
            )
        elif proposal.operation == ProposalOperation.CREATE:
            applied_id = handle.create(replacement, identifier=target_id)
        elif component == MemoryIdentity.COMPONENT:
            candidate = self.memory_candidate(replacement, current, target_id, identifier)
            applied_id = handle.create(candidate, source=source)
        elif component == AgentDefinition.REFERENCE_COMPONENT:
            handle.revise(target_id, replacement, expected_revision=proposal.expected_version)
            applied_id = target_id
        else:
            handle.save(target_id, replacement, expected_version=proposal.expected_version)
            applied_id = target_id
        after = handle.load(applied_id, **kwargs)
        return RefinementRevision.create(applied_id, revision_token(after)).to_dict()

    def rollback_target(self, proposal):
        target = proposal.target
        handle = self.handle(target)
        kwargs = target.session_arguments()
        applied = proposal.applied_target
        current = handle.load(applied.identifier, **kwargs)
        check_revision(current, applied.version)
        return handle, current, kwargs

    def rollback(self, proposal, handle, current, kwargs, source):
        target, applied = proposal.target, proposal.applied_target
        if proposal.operation in self.CREATING:
            # SkillData.delete checks Agent and child-lineage references under the same lock.
            handle.delete(applied.identifier, expected_version=applied.version)
        elif target.component == MemoryIdentity.COMPONENT:
            memory = MemoryRecord(current)
            handle.delete(
                applied.identifier,
                expected_revision=memory.revision,
                expected_generation=memory.generation,
                source=source,
                **kwargs,
            )
        elif target.component == AgentDefinition.REFERENCE_COMPONENT:
            handle.revise(target.identifier, proposal.before, expected_revision=applied.version)
        else:
            handle.save(target.identifier, proposal.before, expected_version=applied.version)
