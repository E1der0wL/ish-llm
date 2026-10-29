"""Compose Hub adapters and host resources; runtime owns their active lifetime."""

from hub.backend.execution.engine_selection import HubEngineIdentity

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import HubConfig


GRAPH_ENGINE_NAME = HubEngineIdentity.GRAPH


class BackendComposition:
    """Hub registration choices; Backend Owners retain execution and validation."""

    ABSENT = None
    ERROR_DETAILS = True
    INVALID_ENGINE = "Register the selected engine in HubConfig.engine_factories"
    TEST_WORKSPACES = "test-workspaces"
    TOOL_RUNTIME_REVISION = "hub-model-risk-tool-retry-v2"
    AGENT_HANDLER = "agent"
    TOOL_HANDLER = "tool"

    @classmethod
    def test_workspace_root(cls, workspace):
        from hub.config.store import WorkspaceJSONStore

        return WorkspaceJSONStore.directory(workspace) / cls.TEST_WORKSPACES


def create_backend(config: "HubConfig"):
    # Heavy imports and construction happen on the backend thread, not in .ishrc.
    from llm.llm import LargeLanguageModel, BackendServices, ProviderDiagnostics
    from llm.services.infrastructure.logging import DomainLogger
    from ..config.preferences import PreferencesStore
    from ..config.preference_records import PreferenceSnapshot
    from llm.services.runtime.tools import ToolRuntime
    from .execution.retries import HubLoopEngine, RetryMonitor, RetryObservationMixin
    from llm.engines.graph import GraphEngine
    from llm.engines.graph.configuration import GraphRegistration
    from llm.engines.graph.agent import AgentNode
    from llm.engines.graph.tool import ToolNode
    from llm.components.tools import ToolComponent
    from llm.components.data import DataComponent
    from .tools.resources import HubWorkflowComponent, HubRAGComponent, HubGoalComponent
    from llm.components.refinement import RefinementComponent
    from llm.components.memory import MemoryComponent
    from llm.components.prompts import PromptComponent
    from .tools.skills import HubSkillComponent
    from llm.components.mcp import MCPComponent
    from llm.components.vision import VisionComponent
    from .project.naming import TitleEngine, TITLE_ENGINE
    from .tools.builtin import HubBuiltinTools
    from llm.components.tools.builtin.file_values import FileReadArguments
    from .execution.context import HubContextBuilder
    from .tools.advice import classify_model_risk

    monitor = RetryMonitor()
    anchor = (
        config.workspace
        if config._workspace_anchor is BackendComposition.ABSENT
        else config._workspace_anchor
    )
    general = PreferenceSnapshot(PreferencesStore(anchor).load()).general()
    from .execution.provider_recovery import ProviderRecovery, CompletionRecoveryMixin
    from llm.engines.loop import LoopEngine

    language = general.resolved_language(config.language)
    recovery = ProviderRecovery(general, monitor, language)
    diagnostics = ProviderDiagnostics(
        error_details=BackendComposition.ERROR_DETAILS,
        request_summary=general.provider_request_summary,
        max_detail_chars=general.provider_max_detail_chars,
    )
    factories = {
        HubEngineIdentity.LOOP: HubLoopEngine,
        **(
            config.engine_factories
            if config.engine_factories is not BackendComposition.ABSENT
            else {}
        ),
    }
    if (
        config.initial_engine not in factories
        and config.initial_engine != GRAPH_ENGINE_NAME
    ):
        raise ValueError(BackendComposition.INVALID_ENGINE)
    engines = {name: factory() for name, factory in factories.items()}
    engines.setdefault(
        GRAPH_ENGINE_NAME,
        GraphEngine(
            handlers={
                BackendComposition.AGENT_HANDLER: AgentNode(
                    engines={HubEngineIdentity.LOOP: engines[HubEngineIdentity.LOOP]}
                ),
                BackendComposition.TOOL_HANDLER: ToolNode(),
            }
        ),
    )
    if config.auto_title:
        engines[TITLE_ENGINE] = TitleEngine()
    if config.postprocess:
        from .project.postprocessing import PostprocessingEngine
        from .project.postprocessing_values import Postprocessing
        engines[Postprocessing.ENGINE] = PostprocessingEngine()
    for engine in engines.values():
        if isinstance(engine, RetryObservationMixin):
            engine.retry_monitor = monitor
        if isinstance(engine, CompletionRecoveryMixin):
            engine.provider_recovery = recovery
            if (
                isinstance(engine, LoopEngine)
                and engine.recover_completion is BackendComposition.ABSENT
            ):
                engine.recover_completion = recovery.recover
    components = {
        component.name: component
        for component in (
            ToolComponent(),
            HubSkillComponent(),
            MCPComponent(),
            HubRAGComponent(),
            VisionComponent(),
            HubWorkflowComponent(
                handlers=GraphRegistration.handler_registry(engines[GRAPH_ENGINE_NAME])
            ),
            MemoryComponent(),
            DataComponent(),
            HubGoalComponent(),
            RefinementComponent(),
            PromptComponent(),
            HubBuiltinTools(
                config.file_root,
                test_root=BackendComposition.test_workspace_root(config.workspace),
                postprocess=bool(config.postprocess),
                tool_context_chars=general.tool_context_chars,
            ),
        )
    }
    for factory in (
        config.component_factories
        if config.component_factories is not BackendComposition.ABSENT
        else ()
    ):
        component = factory()
        components[component.name] = component
    from .execution.environment import EnvironmentEngine
    from .tools.retry import ToolRetrySelector, RetryAwareRunner
    from .tools.retry_evidence import file_candidates

    builtins = components.get(HubBuiltinTools.NAME)
    builtins = (
        builtins if isinstance(builtins, HubBuiltinTools) else BackendComposition.ABSENT
    )
    selector = ToolRetrySelector(
        chooser=config.tool_retry_chooser,
        evidence={
            **({FileReadArguments.NAME: file_candidates} if builtins else {}),
            **config.tool_retry_evidence,
        },
        builtins=builtins,
    )
    engines = {
        name: (
            EnvironmentEngine(
                engine,
                builtins,
                recovery=selector,
                retry_monitor=monitor,
                provider_recovery=recovery,
            )
            if not name.startswith(HubEngineIdentity.INTERNAL_PREFIX)
            else engine
        )
        for name, engine in engines.items()
    }
    return LargeLanguageModel(
        config.workspace,
        components=list(components.values()),
        engines=engines,
        services=BackendServices(
            context_builder=HubContextBuilder(),
            observability_sink=monitor,
            logger=DomainLogger(sink=config.log_sink),
            provider_diagnostics=diagnostics,
            tool_runtime=ToolRuntime(
                classify=(
                    config.tool_classifier
                    if config.tool_classifier is not BackendComposition.ABSENT
                    else classify_model_risk
                ),
                revision=BackendComposition.TOOL_RUNTIME_REVISION,
                decide_retry=selector,
                runner=RetryAwareRunner(selector),
            ),
        ),
    )
