"""Creation defaults for dok-owned Hub Agents; provider semantics remain in llm."""
from dataclasses import dataclass

from ..core.records import Nullable, Record, Text, setting


@dataclass(frozen=True, slots=True)
class HubConfig(Record):
    model: str | None = setting(Nullable(Text(nonblank=True, single_line=True)), default=None,
        description="Initial provider/model for newly created dok Agents. null leaves it unset. Existing Hub Agent settings are preserved; edit those in Hub.")
    api_key: str | None = setting(Nullable(Text()), default=None,
        description="Initial API key for newly created dok Agents. null omits the override; an explicit empty string is passed through. Saved with the new Agent's completion settings.")
    api_base: str | None = setting(Nullable(Text(nonblank=True, single_line=True)), default=None,
        description="Initial API endpoint for newly created dok Agents. null omits the override. The llm provider adapter validates HTTP/HTTPS URLs at registration.")

    def completion(self, *, reasoning_effort: str | None = None) -> dict:
        """Map explicit connection defaults and the Agent owner's fixed effort."""
        from llm.providers.request_values import LiteLLMParameters
        from llm.providers.schema import validate_model_params

        values = {name: value for name, value in (
            (LiteLLMParameters.MODEL, self.model),
            (LiteLLMParameters.API_KEY, self.api_key),
            (LiteLLMParameters.API_BASE, self.api_base),
            (LiteLLMParameters.REASONING_EFFORT, reasoning_effort),
        ) if value is not None}
        validate_model_params(values)
        return values
