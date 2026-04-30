from app.config import settings
from app.llm.base import LLMProvider


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

        return AnthropicProvider(api_key=key, model=model)
    if p == "openai":
        from app.llm.openai_p import OpenAIProvider

        return OpenAIProvider(api_key=key, model=model)
    if p == "google":
        from app.llm.google_p import GoogleProvider

        return GoogleProvider(api_key=key, model=model)
    raise ValueError(f"unknown provider: {p}")
