"""Compose Hub adapters and host resources; runtime owns their active lifetime."""

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import HubConfig


def create_backend(config: "HubConfig"):
    # Heavy imports and construction happen on the backend thread, not in .ishrc.
    from llm.llm import LargeLanguageModel, BackendServices, ProviderDiagnostics
    from llm.services.infrastructure.logging import DomainLogger
    from ..config.preferences import PreferencesStore
    from ..config.general import GeneralSettings
    from llm.services.runtime.tools import ToolRuntime
    from .execution.retries import HubLoopEngine, RetryMonitor, RetryObservationMixin
    from llm.engines.graph import GraphEngine
    from llm.engines.graph.agent import AgentNode
    from llm.engines.graph.tool import ToolNode
    from llm.components.tools import ToolComponent
    from .tools.resources import HubAgentComponent, HubWorkflowComponent, HubRAGComponent, HubGoalComponent
    from llm.components.refinement import RefinementComponent
    from llm.components.memory import MemoryComponent
    from llm.components.prompts import PromptComponent
    from .tools.skills import HubSkillComponent
    from llm.components.mcp import MCPComponent
    from llm.components.vision import VisionComponent
    from .project.naming import TitleEngine, TITLE_ENGINE
    from .tools.builtin import HubBuiltinTools
    from .execution.context import HubContextBuilder
    from .tools.advice import classify_model_risk
    monitor = RetryMonitor()
    general = GeneralSettings(**PreferencesStore(config._workspace_anchor or config.workspace).load().get("general", {}))
    from .execution.provider_recovery import ProviderRecovery, CompletionRecoveryMixin
    from llm.engines.loop import LoopEngine
    recovery = ProviderRecovery(general, monitor, general.language or config.language)
    diagnostics = ProviderDiagnostics(error_details=True,
        request_summary=general.provider_request_summary, max_detail_chars=general.provider_max_detail_chars)
    factories = {"loop": HubLoopEngine, **(config.engine_factories if config.engine_factories is not None else {})}
    if config.engine not in factories and config.engine != "graph":
        raise ValueError("Register the selected engine in HubConfig.engine_factories")
    engines = {name: factory() for name, factory in factories.items()}
    engines.setdefault("graph", GraphEngine(handlers={
        "agent": AgentNode(engines={"loop": engines["loop"]}), "tool": ToolNode()}))
    if config.auto_title:
        engines[TITLE_ENGINE] = TitleEngine()
    for engine in engines.values():
        if isinstance(engine, RetryObservationMixin):
            engine.retry_monitor = monitor
        if isinstance(engine, CompletionRecoveryMixin):
            engine.provider_recovery = recovery
            if isinstance(engine, LoopEngine) and engine.recover_completion is None:
                engine.recover_completion = recovery.recover
    components = {component.name: component for component in
                  (ToolComponent(), HubSkillComponent(), MCPComponent(), HubRAGComponent(), VisionComponent(),
                   HubAgentComponent(engines=engines),
                   HubWorkflowComponent(handlers=getattr(engines["graph"], "handlers", {})), MemoryComponent(),
                   HubGoalComponent(), RefinementComponent(), PromptComponent(),
                   HubBuiltinTools(config.file_root, test_root=Path(config.workspace).expanduser() / ".hub" / "test-workspaces"))}
    for factory in config.component_factories if config.component_factories is not None else ():
        component = factory()
        components[component.name] = component
    from .execution.environment import EnvironmentEngine
    from .tools.retry import ToolRetrySelector, RetryAwareRunner
    from .tools.retry_evidence import file_candidates
    builtins = components.get("builtin_tools")
    builtins = builtins if isinstance(builtins, HubBuiltinTools) else None
    selector = ToolRetrySelector(chooser=config.tool_retry_chooser,
        evidence={**({"file_read": file_candidates} if builtins else {}), **config.tool_retry_evidence},
        fallback_engine=engines["loop"], builtins=builtins)
    engines = {name: EnvironmentEngine(engine, builtins, recovery=selector, retry_monitor=monitor,
                                      provider_recovery=recovery)
               if not name.startswith("_hub_") else engine
               for name, engine in engines.items()}
    return LargeLanguageModel(config.workspace, components=list(components.values()), engines=engines,
                              services=BackendServices(context_builder=HubContextBuilder(), observability_sink=monitor,
                                  logger=DomainLogger(sink=config.log_sink),
                                  provider_diagnostics=diagnostics,
                                  tool_runtime=ToolRuntime(
                                      classify=config.tool_classifier if config.tool_classifier is not None else classify_model_risk,
                                      revision="hub-model-risk-tool-retry-v2", decide_retry=selector,
                                      runner=RetryAwareRunner(selector))))
