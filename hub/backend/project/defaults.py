"""Hub-owned creation templates; existing projects are never reseeded on open."""

from hub.backend.execution.engine_selection import HubEngineIdentity

from copy import deepcopy
import asyncio

from llm.core.models import ConversationStorage, ProjectConfig
from llm.services.schema import ProjectConfiguration
from llm.components.tools.selection import ToolSelection
from llm.components.rag.prompts import ExtractionFailurePolicy
from llm.components.rag.search import SearchMethod, SearchExpansion, RelationRanking

from ...asset.guides import STARTER_SKILLS, SYSTEM_PROMPT, TEMPLATE_VERSION
from .agents import DEFAULT_AGENT
from .title_contract import TitleEngineIdentity
from llm.core.implementation_parameters import ImplementationParameters
from llm.core.policies import ToolRetryPolicy
from llm.services.configuration_records import ProjectConfigForm
from llm.engines.loop.configuration import LoopParameters
from llm.providers.options import ProviderOptions
from llm.components.rag import RAGComponent
from llm.components.rag.configuration import RAGConfiguration
from llm.components.rag.search import SearchOptions
from llm.components.rag.prompts import ExtractionOptions
from llm.components.rag.job_policy import JobAdmission
from llm.components.vision import VisionComponent
from llm.components.vision.configuration import OCRSettings
from llm.components.vision.backends import TesseractBackend
from llm.components.memory import MemoryComponent
from llm.components.memory.configuration import (
    MemoryConfiguration,
    MemoryProcessingConfig,
)
from llm.components.memory.records import MemoryRecord
from llm.components.goals import GoalComponent
from llm.components.goals.records import GoalSettings
from llm.components.refinement import RefinementComponent
from llm.components.refinement.records import ProposalOperation, RefinementPolicy
from llm.components.skills import SkillComponent


CONVERSATION_STORAGE = ConversationStorage.FILE.value


class ProjectCreationDefaults:
    """Application choices for new Projects; selected Backend Owners validate them."""

    UNSPECIFIED_AGENT = None
    PROVIDER = {
        ProviderOptions.Field.MAX_ATTEMPTS: 4,
        ProviderOptions.Field.DELAY_SECONDS: 10,
        ProviderOptions.Field.MAX_DELAY_SECONDS: 60,
    }
    TOOL_RETRIES = 2
    TOOL_RETRY_DELAY = 0
    RAG = {
        ImplementationParameters.CONFIG: {
            RAGConfiguration.Field.CHUNK_SIZE: 1200,
            RAGConfiguration.Field.EMBEDDING_CACHE_MAX_BYTES: 16 * 1024 * 1024,
            RAGConfiguration.Field.EXTRACTION_BATCH_SIZE: 8,
            RAGConfiguration.Field.SEARCH_CACHE_CHARS: 1_000_000,
            RAGConfiguration.Field.INDEX_BATCH_SIZE: 64,
            SearchOptions.CONFIG_KEY: {
                SearchOptions.Field.METHOD: SearchMethod.HYBRID.value,
                SearchOptions.Field.EXPAND: SearchExpansion.CHUNK.value,
                SearchOptions.Field.LIMIT: 5,
                SearchOptions.Field.RERANK: True,
                SearchOptions.Field.CANDIDATE_COUNT: 20,
                SearchOptions.Field.RRF_CONSTANT: 60,
                SearchOptions.Field.MAX_HOPS: 2,
                SearchOptions.Field.RELATION_LIMIT: 20,
                SearchOptions.Field.RELATION_RANKING: RelationRanking.SOURCE.value,
            },
            RAGConfiguration.Field.GRAPH: {
                RAGConfiguration.Field.BUFFER_POOL_SIZE: 64 * 1024 * 1024,
                RAGConfiguration.Field.MAX_NUM_THREADS: 2,
            },
        },
        ImplementationParameters.POLICY: {
            RAGConfiguration.Field.EMBEDDING_CONCURRENCY: 2,
            JobAdmission.KEY: {JobAdmission.MAX_ACTIVE: 1},
            ExtractionOptions.KEY: {
                ExtractionOptions.Field.FAILURE_POLICY: ExtractionFailurePolicy.DISABLED.value
            },
        },
    }
    COMPONENTS = {
        VisionComponent.name: {
            ImplementationParameters.CONFIG: {
                OCRSettings.NAME: {OCRSettings.BACKEND: TesseractBackend.NAME}
            },
            ImplementationParameters.POLICY: {
                OCRSettings.NAME: {OCRSettings.TIMEOUT: 60}
            },
        },
        MemoryComponent.name: {
            ImplementationParameters.CONFIG: {
                MemoryConfiguration.Field.SEARCH_STRATEGY: MemoryConfiguration.KEYWORD,
                MemoryConfiguration.Field.SEARCH_STATUS: MemoryRecord.ALL,
                MemoryConfiguration.Field.SEARCH_LIMIT: 5,
                MemoryProcessingConfig.KEY: {
                    MemoryProcessingConfig.Field.PRIORITY: 10,
                    MemoryProcessingConfig.Field.RECALL_LIMIT: 5,
                    MemoryProcessingConfig.Field.RECALL_QUERY_CHARS: 2000,
                },
            },
            ImplementationParameters.POLICY: {
                MemoryConfiguration.Field.TOOL_WRITE_STATUS: MemoryRecord.CANDIDATE,
                MemoryConfiguration.Field.MAX_SEARCH_RESULTS: 20,
                MemoryProcessingConfig.KEY: {
                    MemoryProcessingConfig.Field.RECALL: True,
                    MemoryProcessingConfig.Field.CONTEXT_CHARS: 4000,
                    MemoryProcessingConfig.Field.FAILURE_MODE: MemoryProcessingConfig.CONTINUE,
                },
            },
        },
        GoalComponent.name: {
            ImplementationParameters.CONFIG: {GoalSettings.PRIORITY: 20},
            ImplementationParameters.POLICY: {
                GoalSettings.INJECT: True,
                GoalSettings.WRITE_TOOLS: True,
            },
        },
        RefinementComponent.name: {
            ImplementationParameters.POLICY: {
                RefinementPolicy.Field.PROPOSAL_OPERATIONS: [
                    ProposalOperation.UPDATE.value,
                    ProposalOperation.CREATE.value,
                    ProposalOperation.FORK.value,
                    ProposalOperation.BIND_SKILLS.value,
                ],
                RefinementPolicy.Field.APPLY_TOOLS: True,
            }
        },
    }

    @classmethod
    def components(cls):
        return deepcopy(cls.COMPONENTS)


def creation_values(backend, values):
    """Materialize Hub choices only for missing creation fields."""
    return ProjectConfigForm.creation_defaults(
        values,
        conversation_storage=CONVERSATION_STORAGE,
        components=list(backend.project_manager.components.names()),
    )


def provider_defaults():
    """Retry only pre-output transient failures through llm's provider policy."""
    return deepcopy(ProjectCreationDefaults.PROVIDER)


def rag_defaults():
    """Model-independent defaults; graph extraction is an explicit later opt-in."""
    return deepcopy(ProjectCreationDefaults.RAG)


async def create_project(
    backend,
    title,
    *,
    config,
    components,
    conversation_storage,
    agent_id=DEFAULT_AGENT,
    agent_definition=ProjectCreationDefaults.UNSPECIFIED_AGENT,
):
    project_config = ProjectConfig.from_dict(deepcopy(config))
    # Application creation presets; explicit user policies always win.
    presets = ProjectCreationDefaults.components()
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
            RAGComponent.name, ProjectConfig.merge(rag_defaults(), current)
        )
    ToolRetryPolicy.apply_defaults(
        project_config.policies,
        max_retries=ProjectCreationDefaults.TOOL_RETRIES,
        delay_seconds=ProjectCreationDefaults.TOOL_RETRY_DELAY,
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
    engines = ProjectConfiguration.engine_parameter_schemas(
        backend.describe_project_config()
    )
    if ProjectConfiguration.engine_supports(
        engines,
        HubEngineIdentity.LOOP,
        ImplementationParameters.CONFIG,
        LoopParameters.SYSTEM_PROMPT,
    ):
        parameters = project_config.parameter_targets.engine(HubEngineIdentity.LOOP)
        values = parameters.config
        LoopParameters(values).default_system_prompt(SYSTEM_PROMPT)
        project_config.set_engine_parameters(
            HubEngineIdentity.LOOP, parameters.with_config(values).to_dict()
        )
    for name in (HubEngineIdentity.LOOP, TitleEngineIdentity.NAME):
        if ProjectConfiguration.engine_supports(
            engines, name, ImplementationParameters.POLICY, LoopParameters.PROVIDER
        ):
            parameters = project_config.parameter_targets.engine(name)
            values = parameters.policy
            LoopParameters.default_provider(values, provider_defaults())
            project_config.set_engine_parameters(
                name, parameters.with_policy(values).to_dict()
            )
    project = await backend.projects.acreate(
        title,
        config=project_config,
        components=components,
        conversation_storage=conversation_storage,
    )
    try:
        from .agents import initialize

        await initialize(
            backend, project, identifier=agent_id, definition=agent_definition
        )
        if SkillComponent.name in components:
            skills = await project.components.aget(SkillComponent.name)
            for skill in STARTER_SKILLS:
                await skills.aimport_directory(skill.directory)
    except BaseException:
        # Do not leave a partially seeded project as the next startup selection.
        await asyncio.shield(project.adelete())
        raise
    return project
