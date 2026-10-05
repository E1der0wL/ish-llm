"""Run from the checkout with: python -m examples.hub.preview."""

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import DynamicStyle
from prompt_toolkit.widgets import TextArea

from hub.ui.mockup import HubMockup
from hub.config.theme import HubTheme


def create_preview(*, input=None, output=None, theme=None):
    view = HubMockup(theme)
    shell = TextArea(text="", prompt="ish ~/workspace ❯ ", multiline=False)
    background = HSplit([
        Window(FormattedTextControl(
            "\n  ish · Hub UI preview\n\n"
            "  Ctrl+Q : Hub 열기 · ESC : 패널 / 최소화\n"
            "  Ctrl+C : 미리보기 종료 (Hub를 닫은 상태)\n\n"
            "  아래는 입력 보존을 확인하기 위한 모형 셸입니다.\n"
            "  실제 셸 명령이나 AI 요청은 실행되지 않습니다.\n"
        )), shell,
    ])
    root = FloatContainer(background, floats=[Float(
        view.container, top=1, bottom=1, left=2, right=2,
    )])
    keys = KeyBindings()

    @keys.add("c-q", filter=Condition(lambda: not view.visible))
    def toggle(event):
        view.toggle(event)

    @keys.add("c-c", filter=Condition(lambda: not view.visible))
    @keys.add("c-d", filter=Condition(lambda: not view.visible))
    def exit_preview(event):
        event.app.exit()

    app = Application(
        layout=Layout(root, focused_element=shell), key_bindings=keys,
        style=DynamicStyle(lambda: view.style),
        full_screen=True, mouse_support=True, input=input, output=output,
    )
    return app, view, shell


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme", choices=("terminal", "dark"), default="terminal")
    args = parser.parse_args()
    app, view, _ = create_preview(theme=HubTheme.dark() if args.theme == "dark" else HubTheme())
    app.run(pre_run=lambda: view.show(app))


if __name__ == "__main__":
    main()
