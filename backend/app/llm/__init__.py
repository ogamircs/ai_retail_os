from app.config import settings
from app.llm.base import LLMProvider
from app.llm.tracing import wrap_provider


def get_provider() -> LLMProvider:
    p = settings.provider
    key = settings.api_key
    model = settings.model
    if not key:
        raise RuntimeError(
            f"missing API key for provider '{p}' — set the corresponding env var in .env"
        )
    if p == "anthropic":
        from app.llm.anthropic_p import AnthropicProvider

        inner = AnthropicProvider(api_key=key, model=model)
    elif p == "openai":
        from app.llm.openai_p import OpenAIProvider

        inner = OpenAIProvider(api_key=key, model=model)
    elif p == "google":
        from app.llm.google_p import GoogleProvider

        inner = GoogleProvider(api_key=key, model=model)
    else:
        raise ValueError(f"unknown provider: {p}")
    # Track 4 M2: wrap with the MLflow tracing decorator if enabled.
    # When MLFLOW_TRACE_ENABLED is unset (or mlflow isn't installed),
    # `wrap_provider` returns the bare provider — no overhead.
    return wrap_provider(inner)
