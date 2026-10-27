"""Hub-owned creation templates; existing projects are never reseeded on open."""

from hub.backend.execution.engine_selection import HubEngineIdentity

from copy import deepcopy
import asyncio

from llm.core.models import ProjectConfig
from llm.services.schema import ProjectConfiguration
from llm.components.tools.selection import ToolSelection

from ...asset.guides import STARTER_SKILLS
from .agents import DEFAULT_AGENT
from .title_contract import TitleEngineIdentity
from llm.core.implementation_parameters import ImplementationParameters
from llm.core.policies import ApprovalPolicy, ToolRetryPolicy
from llm.core.tool_contracts import ToolClassification
from llm.services.configuration_records import ProjectConfigForm
from llm.engines.loop.configuration import LoopParameters
from llm.components.rag import RAGComponent
from llm.components.skills import SkillComponent


from ...config.defaults import HubDefaults, CONVERSATION_STORAGE
from ..execution.environment import underlying_engine
from ..execution.retries import HubLoopEngine


def creation_values(backend, values):
    """Materialize Hub choices only for missing creation fields."""
    return ProjectConfigForm.creation_defaults(
        values,
        conversation_storage=CONVERSATION_STORAGE,
        components=list(backend.project_manager.components.names()),
    )


def provider_defaults():
    """Retry only pre-output transient failures through llm's provider policy."""
    return deepcopy(HubDefaults.PROVIDER)


def rag_defaults(*, extractor=None):
    """Model-independent defaults; graph extraction is an explicit later opt-in."""
    from llm.components.rag.configuration import RAGConfiguration
    from llm.components.rag.prompts import ExtractionOptions

    values = deepcopy(HubDefaults.RAG)
    # A host-supplied extractor owns its protocol. Only the public extraction
    # configuration capability admits JSON-mode/repair options; never force
    # model-client options into a custom extract(chunks) implementation.
    if extractor is not None and not callable(
        getattr(extractor, RAGConfiguration.CLIENT_EXTRACTION_METHOD, None)
    ):
        values[ImplementationParameters.CONFIG].pop(ExtractionOptions.KEY)
        values[ImplementationParameters.POLICY][ExtractionOptions.KEY].pop(ExtractionOptions.Field.REPAIR_ATTEMPTS)
    return values


async def create_project(
    backend,
    title,
    *,
    config,
    components,
    conversation_storage,
    agent_id=DEFAULT_AGENT,
    agent_definition=HubDefaults.UNSPECIFIED_AGENT,
    agent_completion=None,
):
    from ..progress import OperationProgress
    from ...locales.keys import MessageKey

    project_config = ProjectConfig.from_dict(deepcopy(config))
    # Application creation presets; explicit user policies always win.
    from ..tools.advice import MODEL_RISK_SCHEME

    project_config.policies.setdefault(
        ApprovalPolicy.SECTION,
        ApprovalPolicy.selected_threshold(
            MODEL_RISK_SCHEME, HubDefaults.APPROVAL_MAX_RISK,
            [ToolClassification().category],
        ),
    )
    presets = HubDefaults.components()
    for name, preset in presets.items():
        if name in components:
            current = project_config.parameter_targets.component(name).to_dict()
            project_config.set_component_parameters(
                name, ProjectConfig.merge(preset, current)
            )
    if RAGComponent.name in components:
        current = project_config.parameter_targets.component(
            RAGComponent.name
        ).to_dict()
        project_config.set_component_parameters(
            RAGComponent.name, ProjectConfig.merge(
                rag_defaults(extractor=backend.project_manager.components.get(RAGComponent.name).extractor), current)
        )
    ToolRetryPolicy.apply_defaults(
        project_config.policies,
        max_retries=HubDefaults.TOOL_RETRIES,
        delay_seconds=HubDefaults.TOOL_RETRY_DELAY,
    )
    from ..tools.builtin import HubBuiltinTools

    if HubBuiltinTools.NAME in components:
        builtin = backend.project_manager.components.get(HubBuiltinTools.NAME)
        if isinstance(builtin, HubBuiltinTools):
            parameters = project_config.parameter_targets.component(
                HubBuiltinTools.NAME
            )
            defaults = builtin.select_tools()
            selection = ToolSelection.read(parameters.config, scope=builtin.name)
            if not selection.configured:
                selection = ToolSelection.selected(defaults, scope=builtin.name)
            project_config.set_component_parameters(
                HubBuiltinTools.NAME,
                parameters.with_config(selection.to_dict()).to_dict(),
            )
    OperationProgress.report(MessageKey.PROGRESS_BACKEND_SCHEMA)
    engines = ProjectConfiguration.engine_parameter_schemas(
        backend.describe_project_config()
    )
    for name in (HubEngineIdentity.LOOP, TitleEngineIdentity.NAME):
        if ProjectConfiguration.engine_supports(
            engines, name, ImplementationParameters.POLICY, LoopParameters.PROVIDER
        ):
            parameters = project_config.parameter_targets.engine(name)
            values = parameters.policy
            LoopParameters.default_provider(values, provider_defaults())
            if (name == HubEngineIdentity.LOOP and HubBuiltinTools.NAME in components
                    and isinstance(underlying_engine(backend.engines.get(name)), HubLoopEngine)):
                values.setdefault(LoopParameters.FINAL_REVIEW, deepcopy(HubDefaults.FINAL_REVIEW))
            project_config.set_engine_parameters(
                name, parameters.with_policy(values).to_dict()
            )
    OperationProgress.report(MessageKey.PROGRESS_BACKEND_PROJECT)
    from .memory_setup import MemorySetup
    project_config, components = MemorySetup.prepare(project_config.to_dict(), components)
    project = await backend.projects.acreate(
        title,
        config=project_config,
        components=components,
        conversation_storage=conversation_storage,
    )
    try:
        await MemorySetup.ensure_sources(project)
        from .agents import initialize

        OperationProgress.report(MessageKey.PROGRESS_BACKEND_TOOLS)
        await initialize(
            backend, project, identifier=agent_id, definition=agent_definition,
            completion=agent_completion,
        )
        if SkillComponent.name in components:
            skills = await project.components.aget(SkillComponent.name)
            for index, skill in enumerate(STARTER_SKILLS):
                OperationProgress.report(MessageKey.PROGRESS_BACKEND_SKILLS, index, len(STARTER_SKILLS))
                await skills.aimport_directory(skill.directory)
    except BaseException:
        # Do not leave a partially seeded project as the next startup selection.
        await asyncio.shield(project.adelete())
        raise
    return project
