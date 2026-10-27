"""Run a live Hub from a checkout: python -m hub --help."""

from hub.locales.codes import LanguageCode

import argparse
from .backend.project.agents import DEFAULT_AGENT
from pathlib import Path

from .ui.application import create_application
from .backend.runtime import HubConfig
from .config.workspace import WorkspaceSelection


class HubCommand:
    DESCRIPTION = "Hub — LargeLanguageModel terminal chat"
    WORKSPACE = "--workspace"
    AGENT = "--agent"
    AGENT_HELP = "Stored Agent ID; also names the Agent created for a new Project"
    MODEL = "--model"
    MODEL_HELP = "LiteLLM model name; used when creating the default Project"
    API_BASE = "--api-base"
    API_BASE_HELP = "OpenAI-compatible endpoint for a new Project"
    PROJECT = "--project"
    PROJECT_HELP = "Existing Project ID; retains its persisted model/settings"
    SESSION = "--session"
    SESSION_HELP = "Existing Session ID inside --project"
    LANGUAGE = "--language"
    FILE_ROOT = "--file-root"
    FILE_ROOT_HELP = "Root for @path completion"
    PROJECT_REQUIRED = (
        "provide --model for the default Project, or --project to reopen a Project"
    )
    SESSION_REQUIRES_PROJECT = "--session requires --project"
    ENTRY_POINT = "__main__"


def main() -> None:
    parser = argparse.ArgumentParser(description=HubCommand.DESCRIPTION)
    parser.add_argument(
        HubCommand.WORKSPACE, type=Path, default=WorkspaceSelection.default_workspace()
    )
    parser.add_argument(
        HubCommand.AGENT, default=DEFAULT_AGENT, help=HubCommand.AGENT_HELP
    )
    parser.add_argument(HubCommand.MODEL, help=HubCommand.MODEL_HELP)
    parser.add_argument(HubCommand.API_BASE, help=HubCommand.API_BASE_HELP)
    parser.add_argument(HubCommand.PROJECT, help=HubCommand.PROJECT_HELP)
    parser.add_argument(HubCommand.SESSION, help=HubCommand.SESSION_HELP)
    parser.add_argument(
        HubCommand.LANGUAGE, choices=LanguageCode.BUILTIN, default=LanguageCode.DEFAULT
    )
    parser.add_argument(HubCommand.FILE_ROOT, type=Path, help=HubCommand.FILE_ROOT_HELP)
    args = parser.parse_args()
    if not args.project and not args.model:
        parser.error(HubCommand.PROJECT_REQUIRED)
    if args.session and not args.project:
        parser.error(HubCommand.SESSION_REQUIRES_PROJECT)
    config = HubConfig(
        args.workspace,
        agent_id=args.agent,
        model=args.model,
        api_base=args.api_base,
        project_id=args.project,
        session_id=args.session,
        language=args.language,
        file_root=args.file_root,
    )
    from .config.restart import restart_config
    from .ui.restart import RESTART

    launch_config = config
    while True:
        app, controller = create_application(config)
        try:
            result = app.run(pre_run=lambda: controller.view.show(app))
        finally:
            controller.close()
        if result != RESTART:
            break
        config = restart_config(launch_config, controller.config)


if __name__ == HubCommand.ENTRY_POINT:
    main()
