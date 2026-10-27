"""Localized guidance over public schema declarations; never validation."""

from llm.core.agents import AgentDefinition
from llm.core.models import ProjectConfig
from llm.core.configuration_targets import ParameterTargets
from llm.core.implementation_parameters import ImplementationParameters
from llm.core.policies import ContextPolicy, RunPolicy, UsagePolicy, ToolRetryPolicy, PolicySection
from llm.services.configuration_records import ProjectConfigForm
from llm.engines.loop.configuration import LoopParameters
from llm.engines.graph.configuration import GraphParameters, GraphAgentOptions
from llm.policies.completion import CompletionPolicy
from llm.providers.request_values import LiteLLMParameters
from llm.providers.options import ProviderOptions
from llm.components.review import ReviewPolicy
from llm.components.rag import RAGComponent
from llm.components.rag.configuration import RAGConfiguration
from llm.components.rag.prompts import ExtractionOptions
from llm.components.rag.search import SearchOptions
from llm.components.rag.job_policy import JobAdmission
from llm.components.memory import MemoryComponent
from llm.components.memory.configuration import MemoryConfiguration, MemoryProcessingConfig
from llm.components.goals import GoalComponent
from llm.components.goals.records import GoalSettings
from llm.components.refinement import RefinementComponent
from llm.components.refinement.records import RefinementPolicy
from llm.components.tools.selection import ToolSelection
from ..tools.builtin import HubBuiltinTools

from ...locales.keys import MessageKey


def _under(prefix, fields):
    return tuple(((*prefix, name), key) for name, key in fields)


class SettingsHelp:
    PROJECT = "project"
    AGENT = "agent"
    ANY_NAME = "*"
    COMPONENT = (PROJECT, ProjectConfigForm.config.__name__, ProjectConfig.PARAMETERS, ParameterTargets.COMPONENTS)
    RAG = (*COMPONENT, RAGComponent.name)
    MEMORY = (*COMPONENT, MemoryComponent.name)
    GOALS = (*COMPONENT, GoalComponent.name)
    REFINEMENT = (*COMPONENT, RefinementComponent.name)
    BUILTINS = (*COMPONENT, HubBuiltinTools.NAME)
    ENGINE = (PROJECT, ProjectConfigForm.config.__name__, ProjectConfig.PARAMETERS, ParameterTargets.ENGINES, ANY_NAME)
    AGENT_ENGINE = (AGENT, AgentDefinition.Field.ENGINE_OPTIONS)
    RULES = (
        *_under((AGENT, AgentDefinition.Field.COMPLETION,), (
            (LiteLLMParameters.MODEL, MessageKey.OPTION_HELP_MODEL),
            (LiteLLMParameters.API_KEY, MessageKey.OPTION_HELP_API_KEY),
            (LiteLLMParameters.API_BASE, MessageKey.OPTION_HELP_API_BASE),
            (LiteLLMParameters.TEMPERATURE, MessageKey.OPTION_HELP_TEMPERATURE),
            (LiteLLMParameters.MAX_TOKENS, MessageKey.OPTION_HELP_MAX_TOKENS),
            (LiteLLMParameters.MAX_COMPLETION_TOKENS, MessageKey.OPTION_HELP_MAX_COMPLETION_TOKENS),
            (LiteLLMParameters.REASONING_EFFORT, MessageKey.OPTION_HELP_REASONING_EFFORT),
            (LiteLLMParameters.TIMEOUT, MessageKey.OPTION_HELP_TIMEOUT),
            (LiteLLMParameters.TOP_P, MessageKey.OPTION_HELP_TOP_P),
            (LiteLLMParameters.SEED, MessageKey.OPTION_HELP_SEED),
            (LiteLLMParameters.RESPONSE_FORMAT, MessageKey.OPTION_HELP_RESPONSE_FORMAT),
            (LiteLLMParameters.NUM_RETRIES, MessageKey.OPTION_HELP_NUM_RETRIES),
            (LiteLLMParameters.MAX_RETRIES, MessageKey.OPTION_HELP_MAX_RETRIES),
        )),
        *_under((*RAG, ImplementationParameters.CONFIG, RAGConfiguration.Field.EMBEDDING_PARAMS,), (
            (LiteLLMParameters.MODEL, MessageKey.OPTION_HELP_MODEL),
            (LiteLLMParameters.API_KEY, MessageKey.OPTION_HELP_API_KEY),
            (LiteLLMParameters.API_BASE, MessageKey.OPTION_HELP_API_BASE),
            (LiteLLMParameters.TIMEOUT, MessageKey.OPTION_HELP_TIMEOUT),
            (LiteLLMParameters.NUM_RETRIES, MessageKey.OPTION_HELP_NUM_RETRIES),
            (LiteLLMParameters.MAX_RETRIES, MessageKey.OPTION_HELP_MAX_RETRIES),
        )),
        *_under((*RAG, ImplementationParameters.CONFIG, RAGConfiguration.Field.EXTRACTION_PARAMS,), (
            (LiteLLMParameters.MODEL, MessageKey.OPTION_HELP_MODEL),
            (LiteLLMParameters.API_KEY, MessageKey.OPTION_HELP_API_KEY),
            (LiteLLMParameters.API_BASE, MessageKey.OPTION_HELP_API_BASE),
            (LiteLLMParameters.TEMPERATURE, MessageKey.OPTION_HELP_TEMPERATURE),
            (LiteLLMParameters.MAX_TOKENS, MessageKey.OPTION_HELP_MAX_TOKENS),
            (LiteLLMParameters.MAX_COMPLETION_TOKENS, MessageKey.OPTION_HELP_MAX_COMPLETION_TOKENS),
            (LiteLLMParameters.REASONING_EFFORT, MessageKey.OPTION_HELP_REASONING_EFFORT),
            (LiteLLMParameters.TIMEOUT, MessageKey.OPTION_HELP_TIMEOUT),
            (LiteLLMParameters.TOP_P, MessageKey.OPTION_HELP_TOP_P),
            (LiteLLMParameters.SEED, MessageKey.OPTION_HELP_SEED),
            (LiteLLMParameters.RESPONSE_FORMAT, MessageKey.OPTION_HELP_RESPONSE_FORMAT),
            (LiteLLMParameters.NUM_RETRIES, MessageKey.OPTION_HELP_NUM_RETRIES),
            (LiteLLMParameters.MAX_RETRIES, MessageKey.OPTION_HELP_MAX_RETRIES),
        )),
        *_under((*RAG, ImplementationParameters.CONFIG, RAGConfiguration.Field.RERANK_PARAMS,), (
            (LiteLLMParameters.MODEL, MessageKey.OPTION_HELP_MODEL),
            (LiteLLMParameters.API_KEY, MessageKey.OPTION_HELP_API_KEY),
            (LiteLLMParameters.API_BASE, MessageKey.OPTION_HELP_API_BASE),
            (LiteLLMParameters.TIMEOUT, MessageKey.OPTION_HELP_TIMEOUT),
            (LiteLLMParameters.NUM_RETRIES, MessageKey.OPTION_HELP_NUM_RETRIES),
            (LiteLLMParameters.MAX_RETRIES, MessageKey.OPTION_HELP_MAX_RETRIES),
        )),
        *_under((*MEMORY, ImplementationParameters.CONFIG, MemoryProcessingConfig.KEY, MemoryProcessingConfig.Field.COMPLETION,), (
            (LiteLLMParameters.MODEL, MessageKey.OPTION_HELP_MEMORY_SUMMARY_MODEL),
            (LiteLLMParameters.API_KEY, MessageKey.OPTION_HELP_API_KEY),
            (LiteLLMParameters.API_BASE, MessageKey.OPTION_HELP_API_BASE),
            (LiteLLMParameters.TEMPERATURE, MessageKey.OPTION_HELP_TEMPERATURE),
            (LiteLLMParameters.MAX_TOKENS, MessageKey.OPTION_HELP_MAX_TOKENS),
            (LiteLLMParameters.MAX_COMPLETION_TOKENS, MessageKey.OPTION_HELP_MAX_COMPLETION_TOKENS),
            (LiteLLMParameters.REASONING_EFFORT, MessageKey.OPTION_HELP_REASONING_EFFORT),
            (LiteLLMParameters.TIMEOUT, MessageKey.OPTION_HELP_TIMEOUT),
            (LiteLLMParameters.TOP_P, MessageKey.OPTION_HELP_TOP_P),
            (LiteLLMParameters.SEED, MessageKey.OPTION_HELP_SEED),
            (LiteLLMParameters.RESPONSE_FORMAT, MessageKey.OPTION_HELP_RESPONSE_FORMAT),
            (LiteLLMParameters.NUM_RETRIES, MessageKey.OPTION_HELP_NUM_RETRIES),
            (LiteLLMParameters.MAX_RETRIES, MessageKey.OPTION_HELP_MAX_RETRIES),
        )),
        *_under((AGENT,), (
            (AgentDefinition.Field.PURPOSE, MessageKey.OPTION_HELP_AGENT_PURPOSE),
            (AgentDefinition.Field.DESCRIPTION, MessageKey.OPTION_HELP_AGENT_PURPOSE),
            (AgentDefinition.Field.ENGINE, MessageKey.OPTION_HELP_AGENT_ENGINE),
            (AgentDefinition.Field.SYSTEM_PROMPT, MessageKey.OPTION_HELP_AGENT_PROMPT),
            (AgentDefinition.Field.TOOLS, MessageKey.OPTION_HELP_AGENT_TOOLS),
            (AgentDefinition.Field.INPUT_SCHEMA, MessageKey.OPTION_HELP_AGENT_SCHEMA),
            (AgentDefinition.Field.OUTPUT_SCHEMA, MessageKey.OPTION_HELP_AGENT_SCHEMA),
            (AgentDefinition.Field.OUTPUT_FORMAT, MessageKey.OPTION_HELP_AGENT_FORMAT),
            (AgentDefinition.Field.METADATA, MessageKey.OPTION_HELP_AGENT_METADATA),
        )),
        *_under((AGENT, AgentDefinition.Field.RESOURCES,), (
            (AgentDefinition.ResourceField.SKILLS, MessageKey.OPTION_HELP_AGENT_RESOURCES),
            (AgentDefinition.ResourceField.RAG, MessageKey.OPTION_HELP_AGENT_RAG),
            (AgentDefinition.ResourceField.MCP, MessageKey.OPTION_HELP_AGENT_MCP),
            (AgentDefinition.ResourceField.PROMPT, MessageKey.OPTION_HELP_AGENT_RESOURCE_PROMPT),
        )),
        *_under((AGENT, AgentDefinition.Field.POLICY,), (
            (AgentDefinition.PolicyField.REQUIRE_TOOL, MessageKey.OPTION_HELP_REQUIRE_TOOL),
            (AgentDefinition.PolicyField.MAX_TOOL_CALLS, MessageKey.OPTION_HELP_MAX_TOOL_CALLS),
            (AgentDefinition.PolicyField.TIMEOUT_SECONDS, MessageKey.OPTION_HELP_TIMEOUT_SECONDS),
        )),
        *_under((*AGENT_ENGINE,), (
            (GraphAgentOptions.WORKFLOW, MessageKey.OPTION_HELP_WORKFLOW),
        )),
        *_under((*ENGINE, ImplementationParameters.POLICY,), (
            (LoopParameters.MAX_ITERATIONS, MessageKey.OPTION_HELP_MAX_ITERATIONS),
            (LoopParameters.REQUEST_TIMEOUT, MessageKey.OPTION_HELP_REQUEST_TIMEOUT),
            (LoopParameters.TOOL_TIMEOUT, MessageKey.OPTION_HELP_TOOL_TIMEOUT),
            (LoopParameters.MAX_TOOL_CALLS, MessageKey.OPTION_HELP_MAX_TOOL_CALLS),
            (LoopParameters.MAX_ARGUMENT_CHARS, MessageKey.OPTION_HELP_MAX_ARGUMENT_CHARS),
            (LoopParameters.MAX_OUTPUT_CHARS, MessageKey.OPTION_HELP_MAX_OUTPUT_CHARS),
            (GraphParameters.MAX_STEPS, MessageKey.OPTION_HELP_MAX_STEPS),
            (GraphParameters.MAX_PARALLELISM, MessageKey.OPTION_HELP_MAX_PARALLELISM),
            (GraphParameters.MAX_NESTED_DEPTH, MessageKey.OPTION_HELP_MAX_NESTED_DEPTH),
            (GraphParameters.TIMEOUT_SECONDS, MessageKey.OPTION_HELP_TIMEOUT_SECONDS),
        )),
        *_under((*AGENT_ENGINE, ImplementationParameters.POLICY,), (
            (LoopParameters.MAX_ITERATIONS, MessageKey.OPTION_HELP_MAX_ITERATIONS),
            (LoopParameters.REQUEST_TIMEOUT, MessageKey.OPTION_HELP_REQUEST_TIMEOUT),
            (LoopParameters.TOOL_TIMEOUT, MessageKey.OPTION_HELP_TOOL_TIMEOUT),
            (LoopParameters.MAX_TOOL_CALLS, MessageKey.OPTION_HELP_MAX_TOOL_CALLS),
            (LoopParameters.MAX_ARGUMENT_CHARS, MessageKey.OPTION_HELP_MAX_ARGUMENT_CHARS),
            (LoopParameters.MAX_OUTPUT_CHARS, MessageKey.OPTION_HELP_MAX_OUTPUT_CHARS),
            (GraphParameters.MAX_STEPS, MessageKey.OPTION_HELP_MAX_STEPS),
            (GraphParameters.MAX_PARALLELISM, MessageKey.OPTION_HELP_MAX_PARALLELISM),
            (GraphParameters.MAX_NESTED_DEPTH, MessageKey.OPTION_HELP_MAX_NESTED_DEPTH),
            (GraphParameters.TIMEOUT_SECONDS, MessageKey.OPTION_HELP_TIMEOUT_SECONDS),
        )),
        *_under((*ENGINE, ImplementationParameters.CONFIG,), (
            (LoopParameters.BUFFER_SIZE, MessageKey.OPTION_HELP_BUFFER_SIZE),
            (GraphParameters.CLEANUP_TIMEOUT, MessageKey.OPTION_HELP_CLEANUP_TIMEOUT),
        )),
        *_under((*AGENT_ENGINE, ImplementationParameters.CONFIG,), (
            (LoopParameters.BUFFER_SIZE, MessageKey.OPTION_HELP_BUFFER_SIZE),
            (GraphParameters.CLEANUP_TIMEOUT, MessageKey.OPTION_HELP_CLEANUP_TIMEOUT),
        )),
        *_under((*ENGINE, ImplementationParameters.POLICY, LoopParameters.PROVIDER,), (
            (ProviderOptions.Field.MAX_ATTEMPTS, MessageKey.OPTION_HELP_PROVIDER_MAX_ATTEMPTS),
            (ProviderOptions.Field.DELAY_SECONDS, MessageKey.OPTION_HELP_PROVIDER_DELAY_SECONDS),
            (ProviderOptions.Field.MAX_DELAY_SECONDS, MessageKey.OPTION_HELP_PROVIDER_MAX_DELAY_SECONDS),
            (ProviderOptions.Field.WALL_TIMEOUT, MessageKey.OPTION_HELP_PROVIDER_WALL_TIMEOUT),
            (ProviderOptions.Field.RESPECT_RETRY_AFTER, MessageKey.OPTION_HELP_PROVIDER_RESPECT_RETRY_AFTER),
            (ProviderOptions.Field.JITTER, MessageKey.OPTION_HELP_PROVIDER_JITTER),
        )),
        *_under((*AGENT_ENGINE, ImplementationParameters.POLICY, LoopParameters.PROVIDER,), (
            (ProviderOptions.Field.MAX_ATTEMPTS, MessageKey.OPTION_HELP_PROVIDER_MAX_ATTEMPTS),
            (ProviderOptions.Field.DELAY_SECONDS, MessageKey.OPTION_HELP_PROVIDER_DELAY_SECONDS),
            (ProviderOptions.Field.MAX_DELAY_SECONDS, MessageKey.OPTION_HELP_PROVIDER_MAX_DELAY_SECONDS),
            (ProviderOptions.Field.WALL_TIMEOUT, MessageKey.OPTION_HELP_PROVIDER_WALL_TIMEOUT),
            (ProviderOptions.Field.RESPECT_RETRY_AFTER, MessageKey.OPTION_HELP_PROVIDER_RESPECT_RETRY_AFTER),
            (ProviderOptions.Field.JITTER, MessageKey.OPTION_HELP_PROVIDER_JITTER),
        )),
        *_under((*RAG, ImplementationParameters.POLICY, RAGConfiguration.Field.PROVIDER,), (
            (ProviderOptions.Field.MAX_ATTEMPTS, MessageKey.OPTION_HELP_PROVIDER_MAX_ATTEMPTS),
            (ProviderOptions.Field.DELAY_SECONDS, MessageKey.OPTION_HELP_PROVIDER_DELAY_SECONDS),
            (ProviderOptions.Field.MAX_DELAY_SECONDS, MessageKey.OPTION_HELP_PROVIDER_MAX_DELAY_SECONDS),
            (ProviderOptions.Field.WALL_TIMEOUT, MessageKey.OPTION_HELP_PROVIDER_WALL_TIMEOUT),
            (ProviderOptions.Field.RESPECT_RETRY_AFTER, MessageKey.OPTION_HELP_PROVIDER_RESPECT_RETRY_AFTER),
            (ProviderOptions.Field.JITTER, MessageKey.OPTION_HELP_PROVIDER_JITTER),
        )),
        *_under((*MEMORY, ImplementationParameters.POLICY, MemoryProcessingConfig.KEY, MemoryProcessingConfig.Field.PROVIDER,), (
            (ProviderOptions.Field.MAX_ATTEMPTS, MessageKey.OPTION_HELP_PROVIDER_MAX_ATTEMPTS),
            (ProviderOptions.Field.DELAY_SECONDS, MessageKey.OPTION_HELP_PROVIDER_DELAY_SECONDS),
            (ProviderOptions.Field.MAX_DELAY_SECONDS, MessageKey.OPTION_HELP_PROVIDER_MAX_DELAY_SECONDS),
            (ProviderOptions.Field.WALL_TIMEOUT, MessageKey.OPTION_HELP_PROVIDER_WALL_TIMEOUT),
            (ProviderOptions.Field.RESPECT_RETRY_AFTER, MessageKey.OPTION_HELP_PROVIDER_RESPECT_RETRY_AFTER),
            (ProviderOptions.Field.JITTER, MessageKey.OPTION_HELP_PROVIDER_JITTER),
        )),
        *_under((*ENGINE, ImplementationParameters.POLICY, LoopParameters.FINAL_REVIEW,), (
            (ReviewPolicy.Field.MAX_CONTINUATIONS, MessageKey.OPTION_HELP_REVIEW_MAX_CONTINUATIONS),
            (ReviewPolicy.Field.MAX_ATTEMPTS, MessageKey.OPTION_HELP_REVIEW_MAX_ATTEMPTS),
            (ReviewPolicy.Field.MAX_FEEDBACK_CHARS, MessageKey.OPTION_HELP_REVIEW_MAX_FEEDBACK_CHARS),
            (ReviewPolicy.Field.HOOK_TIMEOUT, MessageKey.OPTION_HELP_REVIEW_HOOK_TIMEOUT),
        )),
        *_under((*AGENT_ENGINE, ImplementationParameters.POLICY, LoopParameters.FINAL_REVIEW,), (
            (ReviewPolicy.Field.MAX_CONTINUATIONS, MessageKey.OPTION_HELP_REVIEW_MAX_CONTINUATIONS),
            (ReviewPolicy.Field.MAX_ATTEMPTS, MessageKey.OPTION_HELP_REVIEW_MAX_ATTEMPTS),
            (ReviewPolicy.Field.MAX_FEEDBACK_CHARS, MessageKey.OPTION_HELP_REVIEW_MAX_FEEDBACK_CHARS),
            (ReviewPolicy.Field.HOOK_TIMEOUT, MessageKey.OPTION_HELP_REVIEW_HOOK_TIMEOUT),
        )),
        *_under((*ENGINE, ImplementationParameters.POLICY, CompletionPolicy.NAME,), (
            (CompletionPolicy.Field.MAX_TOKENS, MessageKey.OPTION_HELP_INPUT_TOKENS),
            (CompletionPolicy.Field.RESERVE_TOKENS, MessageKey.OPTION_HELP_RESERVE_TOKENS),
            (CompletionPolicy.Field.COUNTER, MessageKey.OPTION_HELP_COUNTER),
        )),
        *_under((*AGENT_ENGINE, ImplementationParameters.POLICY, CompletionPolicy.NAME,), (
            (CompletionPolicy.Field.MAX_TOKENS, MessageKey.OPTION_HELP_INPUT_TOKENS),
            (CompletionPolicy.Field.RESERVE_TOKENS, MessageKey.OPTION_HELP_RESERVE_TOKENS),
            (CompletionPolicy.Field.COUNTER, MessageKey.OPTION_HELP_COUNTER),
        )),
        *_under((PROJECT, ProjectConfigForm.config.__name__, ProjectConfig.POLICIES, PolicySection.USAGE,), (
            (UsagePolicy.counter.__name__, MessageKey.OPTION_HELP_COUNTER),
        )),
        *_under((*RAG, ImplementationParameters.CONFIG,), (
            (RAGConfiguration.Field.CHUNK_SIZE, MessageKey.OPTION_HELP_RAG_CHUNK_SIZE),
            (RAGConfiguration.Field.EXTRACTION_BATCH_SIZE, MessageKey.OPTION_HELP_RAG_EXTRACTION_BATCH_SIZE),
            (RAGConfiguration.Field.EMBEDDING_PARAMS, MessageKey.OPTION_HELP_RAG_EMBEDDING_PARAMS),
            (RAGConfiguration.Field.RERANK_PARAMS, MessageKey.OPTION_HELP_RAG_RERANK_PARAMS),
            (RAGConfiguration.Field.EXTRACTION_PARAMS, MessageKey.OPTION_HELP_RAG_EXTRACTION_PARAMS),
            (RAGConfiguration.Field.INDEX_BATCH_SIZE, MessageKey.OPTION_HELP_RAG_INDEX_BATCH_SIZE),
        )),
        *_under((*RAG, ImplementationParameters.POLICY, ExtractionOptions.KEY,), (
            (ExtractionOptions.Field.FAILURE_POLICY, MessageKey.OPTION_HELP_RAG_FAILURE_POLICY),
            (ExtractionOptions.Field.REPAIR_ATTEMPTS, MessageKey.OPTION_HELP_RAG_REPAIR_ATTEMPTS),
        )),
        *_under((*RAG, ImplementationParameters.CONFIG, ExtractionOptions.KEY,), (
            (ExtractionOptions.Field.JSON_MODE, MessageKey.OPTION_HELP_RAG_JSON_MODE),
            (ExtractionOptions.Field.RELATION_TYPES, MessageKey.OPTION_HELP_RAG_RELATIONS),
        )),
        *_under((*RAG, ImplementationParameters.CONFIG, SearchOptions.CONFIG_KEY,), (
            (SearchOptions.Field.METHOD, MessageKey.OPTION_HELP_RAG_METHOD),
            (SearchOptions.Field.RERANK, MessageKey.OPTION_HELP_RAG_RERANK),
            (SearchOptions.Field.LIMIT, MessageKey.OPTION_HELP_RAG_LIMIT),
            (SearchOptions.Field.CANDIDATE_COUNT, MessageKey.OPTION_HELP_RAG_CANDIDATES),
        )),
        *_under((*RAG, ImplementationParameters.POLICY,), (
            (RAGConfiguration.Field.EMBEDDING_CONCURRENCY, MessageKey.OPTION_HELP_RAG_CONCURRENCY),
        )),
        *_under((*RAG, ImplementationParameters.POLICY, JobAdmission.KEY,), (
            (JobAdmission.MAX_ACTIVE, MessageKey.OPTION_HELP_RAG_ACTIVE),
        )),
        *_under((*MEMORY, ImplementationParameters.POLICY, MemoryProcessingConfig.KEY,), (
            (MemoryProcessingConfig.Field.RECALL, MessageKey.OPTION_HELP_MEMORY_RECALL),
            (MemoryProcessingConfig.Field.SUMMARIZE, MessageKey.OPTION_HELP_MEMORY_SUMMARIZE),
            (MemoryProcessingConfig.Field.EXTRACT, MessageKey.OPTION_HELP_MEMORY_EXTRACT),
            (MemoryProcessingConfig.Field.FAILURE_MODE, MessageKey.OPTION_HELP_MEMORY_FAILURE),
        )),
        *_under((*MEMORY, ImplementationParameters.CONFIG,), (
            (MemoryConfiguration.Field.SEARCH_STATUS, MessageKey.OPTION_HELP_MEMORY_STATUS),
        )),
        *_under((*MEMORY, ImplementationParameters.POLICY,), (
            (MemoryConfiguration.Field.TOOL_WRITE_STATUS, MessageKey.OPTION_HELP_MEMORY_WRITE),
        )),
        *_under((*GOALS, ImplementationParameters.POLICY,), (
            (GoalSettings.INJECT, MessageKey.OPTION_HELP_GOAL_INJECT),
            (GoalSettings.WRITE_TOOLS, MessageKey.OPTION_HELP_GOAL_WRITE),
        )),
        *_under((*GOALS, ImplementationParameters.CONFIG,), (
            (GoalSettings.IDENTIFIERS, MessageKey.OPTION_HELP_GOAL_IDS),
            (GoalSettings.NESTED_AGENT_IDS, MessageKey.OPTION_HELP_GOAL_NESTED),
            (GoalSettings.PRIORITY, MessageKey.OPTION_HELP_PRIORITY),
        )),
        *_under((*MEMORY, ImplementationParameters.CONFIG, MemoryProcessingConfig.KEY,), (
            (MemoryProcessingConfig.Field.PRIORITY, MessageKey.OPTION_HELP_PRIORITY),
        )),
        *_under((*REFINEMENT, ImplementationParameters.POLICY,), (
            (RefinementPolicy.Field.APPLY_TOOLS, MessageKey.OPTION_HELP_REFINEMENT_APPLY),
            (RefinementPolicy.Field.REQUIRE_EVALUATION, MessageKey.OPTION_HELP_REFINEMENT_EVALUATION),
            (RefinementPolicy.Field.PROPOSAL_OPERATIONS, MessageKey.OPTION_HELP_REFINEMENT_OPERATIONS),
        )),
        *_under((*BUILTINS, ImplementationParameters.CONFIG,), (
            (ToolSelection.enabled.__name__, MessageKey.OPTION_HELP_BUILTIN_ENABLED),
        )),
        *_under((PROJECT, ProjectConfigForm.config.__name__, ProjectConfig.POLICIES, PolicySection.TOOL_RETRY,), (
            (ToolRetryPolicy.max_retries.__name__, MessageKey.OPTION_HELP_TOOL_RETRIES),
        )),
        *_under((PROJECT, ProjectConfigForm.config.__name__, ProjectConfig.POLICIES, PolicySection.CONTEXT,), (
            (ContextPolicy.mode.__name__, MessageKey.OPTION_HELP_CONTEXT_MODE),
            (ContextPolicy.max_turns.__name__, MessageKey.OPTION_HELP_CONTEXT_TURNS),
        )),
        *_under((PROJECT, ProjectConfigForm.config.__name__, ProjectConfig.POLICIES, PolicySection.RUN,), (
            (RunPolicy.max_queued.__name__, MessageKey.OPTION_HELP_RUN_QUEUE),
        )),
    )

    @classmethod
    def message(cls, scope, path):
        target = (scope, *path)
        for pattern, key in cls.RULES:
            if len(pattern) == len(target) and all(
                expected == actual or expected == cls.ANY_NAME
                for expected, actual in zip(pattern, target)
            ):
                return key
        return None
