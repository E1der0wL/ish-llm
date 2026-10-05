"""Run a live Hub from a checkout: python -m hub --help."""

import argparse
from pathlib import Path

from .ui.application import create_application
from .backend.runtime import HubConfig
from .config.theme import HubTheme


def main() -> None:
    parser = argparse.ArgumentParser(description="Hub — LargeLanguageModel terminal chat")
    parser.add_argument("--workspace", type=Path, default=Path.home() / ".ish/hub-workspace")
    parser.add_argument("--engine", required=True, choices=("loop",))
    parser.add_argument("--model", help="LiteLLM model name; used when creating the default Project")
    parser.add_argument("--api-base", help="OpenAI-compatible endpoint for a new Project")
    parser.add_argument("--project", help="Existing Project ID; retains its persisted model/settings")
    parser.add_argument("--session", help="Existing Session ID inside --project")
    parser.add_argument("--theme", choices=("terminal", "dark"), default="terminal")
    parser.add_argument("--language", choices=("ko", "en"), default="ko")
    parser.add_argument("--file-root", type=Path, help="Root for @path completion")
    args = parser.parse_args()
    if not args.project and not args.model:
        parser.error("provide --model for the default Project, or --project to reopen a Project")
    if args.session and not args.project:
        parser.error("--session requires --project")
    config = HubConfig(args.workspace, args.engine, args.model, args.api_base, args.project, args.session,
                       language=args.language, file_root=args.file_root)
    app, controller = create_application(config, theme=HubTheme.dark() if args.theme == "dark" else None)
    try:
        app.run(pre_run=lambda: controller.view.show(app))
    finally:
        controller.close()


if __name__ == "__main__":
    main()
