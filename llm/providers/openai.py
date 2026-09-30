"""OpenAI-compatible 임베딩용 선택 어댑터. SDK 캐시/재시도 없이 네이티브 응답을 반환한다."""


async def embedding(**request):
    from openai import AsyncOpenAI
    params = dict(request)
    # LiteLLM 전용 제어 인자는 서버 payload에 넣지 않는다.
    for key in ("num_retries", "max_retries", "caching", "cache"):
        params.pop(key, None)
    client_options = {"max_retries": 0, "timeout": params.pop("timeout", 60)}
    for source, target in (("api_key", "api_key"), ("api_base", "base_url"),
                           ("organization", "organization"), ("default_headers", "default_headers")):
        if source in params:
            client_options[target] = params.pop(source)
    if params["model"].startswith("openai/"):
        params["model"] = params["model"][7:]
    async with AsyncOpenAI(**client_options) as client:
        return await client.embeddings.create(**params)
