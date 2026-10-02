"""개발 checkout을 import할 수 있는 ish/worker에서 사용하는 공개 테스트 설정."""
from functools import partial

if plugin.get("llm") is not None:
    from examples.llm.ish_loop import main as loop_test

    prompt.set_tool("llm-test", function=partial(
        loop_test,
        completion={"model": "openai/your-model"},
        api_key_env="ISH_LLM_API_KEY",
    ))
