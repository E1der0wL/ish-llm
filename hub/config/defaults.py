"""Hub creation profile: non-secret product choices, validated by their owners."""

from copy import deepcopy
from llm.core.models import ConversationStorage
from llm.components.rag.prompts import ExtractionFailurePolicy
from llm.components.rag.search import SearchMethod, SearchExpansion, RelationRanking

from llm.core.implementation_parameters import ImplementationParameters
from llm.providers.options import ProviderOptions
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
from llm.components.review import ReviewPolicy


CONVERSATION_STORAGE = ConversationStorage.FILE.value


class HubDefaults:
    """Application choices for new Projects; selected Backend Owners validate them."""

    UNSPECIFIED_AGENT = None
    PROVIDER = {
        ProviderOptions.Field.MAX_ATTEMPTS: 4,
        ProviderOptions.Field.DELAY_SECONDS: 10,
        ProviderOptions.Field.MAX_DELAY_SECONDS: 60,
    }
    TOOL_RETRIES = 2
    TOOL_RETRY_DELAY = 0
    FINAL_REVIEW = {
        ReviewPolicy.Field.MAX_CONTINUATIONS: 2,
        ReviewPolicy.Field.MAX_ATTEMPTS: 3,
        ReviewPolicy.Field.MAX_FEEDBACK_CHARS: 2000,
        ReviewPolicy.Field.HOOK_TIMEOUT: 5,
    }
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


    @staticmethod
    def globals(profile=None):
        from dataclasses import asdict
        from .profile import UserProfile
        from .theme import HubTheme
        from .general import GeneralSettings
        from .sections import PreferenceSection
        return {PreferenceSection.PROFILE.value: asdict(UserProfile() if profile is None else profile),
                PreferenceSection.APPEARANCE.value: asdict(HubTheme()),
                PreferenceSection.GENERAL.value: asdict(GeneralSettings())}
