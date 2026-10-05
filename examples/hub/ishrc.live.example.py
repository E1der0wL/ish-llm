"""Optional explicit Hub settings. The minimal setup is hub_plugin.install(prompt).

Default installs open settings without a model and register every builtin component,
Loop and Graph. Export provider credentials before starting ish.
"""

from pathlib import Path
from llm.engines.loop import LoopEngine

hub_plugin = plugin.get("hub")
if hub_plugin is not None:
    hub = hub_plugin.install(
        prompt,
        config=hub_plugin.HubConfig(
            workspace=Path.home() / ".ish/hub-workspace",
            engine="loop",
            model="openai/YOUR_MODEL",
            language="ko",  # ko / en
            # user_profile=hub_plugin.UserProfile(display_name="My name"),  # Defaults to the OS account.
            file_root=Path.cwd(),
            engine_factories={
                "loop": LoopEngine,
                "review": lambda: LoopEngine(system_prompt="Review the user's work and explain concrete improvements."),
            },
            # For a Gemini model that exposes reasoning, use e.g.
            # "loop": lambda: LoopEngine(completion_kwargs={"reasoning_effort": "low"})
            # auto_title=False,  # Disable the extra title-generation model call.
            # api_base="https://YOUR_SERVER/v1",
            # project_id="PROJECT_ID",
            # session_id="SESSION_ID",
        ),
        # theme=hub_plugin.HubTheme.dark(),
    )
