"""Compose Hub adapters and host resources; runtime owns their active lifetime."""

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import HubConfig


def create_backend(config: "HubConfig"):
    # Heavy imports and construction happen on the backend thread, not in .ishrc.
    from llm.llm import LargeLanguageModel, BackendServices
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
    from .project.naming import TitleEngine, TITLE_ENGINE
    from .tools.builtin import HubBuiltinTools
    from .execution.context import HubContextBuilder
    from .tools.advice import classify_model_risk
    monitor = RetryMonitor()
    factories = {"loop": HubLoopEngine, **(config.engine_factories or {})}
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
    components = {component.name: component for component in
                  (ToolComponent(), HubSkillComponent(), MCPComponent(), HubRAGComponent(),
                   HubAgentComponent(engines=engines),
                   HubWorkflowComponent(handlers=getattr(engines["graph"], "handlers", {})), MemoryComponent(),
                   HubGoalComponent(), RefinementComponent(), PromptComponent(),
                   HubBuiltinTools(config.file_root, test_root=Path(config.workspace).expanduser() / ".hub" / "test-workspaces"))}
    for factory in config.component_factories or ():
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
    engines = {name: EnvironmentEngine(engine, builtins, recovery=selector, retry_monitor=monitor)
               if not name.startswith("_hub_") else engine
               for name, engine in engines.items()}
    return LargeLanguageModel(config.workspace, components=list(components.values()), engines=engines,
                              services=BackendServices(context_builder=HubContextBuilder(), observability_sink=monitor,
                                  tool_runtime=ToolRuntime(
                                      classify=config.tool_classifier if config.tool_classifier is not None else classify_model_risk,
                                      revision="hub-model-risk-tool-retry-v2", decide_retry=selector,
                                      runner=RetryAwareRunner(selector))))
