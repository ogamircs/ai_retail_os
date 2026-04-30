import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND / "data"
ARTIFACTS_DIR = ROOT / "artifacts"
DB_PATH = DATA_DIR / "spine.db"

# Load .env from project root first (where the user keeps real keys), then backend
load_dotenv(ROOT / ".env")
load_dotenv(BACKEND / ".env", override=False)

DATA_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


class Settings:
    @property
    def provider(self) -> str:
        explicit = os.getenv("LLM_PROVIDER")
        if explicit:
            return explicit.lower()
        # Auto-pick first provider with a key
        if os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
            return "google"
        return "anthropic"

    @property
    def model(self) -> str:
        p = self.provider
        if p == "anthropic":
            return os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")
        if p == "openai":
            return os.getenv("OPENAI_MODEL", "gpt-4o")
        if p == "google":
            return os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")
        raise ValueError(f"unknown provider: {p}")

    @property
    def api_key(self) -> str | None:
        p = self.provider
        if p == "anthropic":
            return os.getenv("ANTHROPIC_API_KEY")
        if p == "openai":
            return os.getenv("OPENAI_API_KEY")
        if p == "google":
            return os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        return None

    def set_provider(self, provider: str) -> None:
        provider = provider.lower()
        if provider not in ("anthropic", "openai", "google"):
            raise ValueError(f"unknown provider: {provider}")
        os.environ["LLM_PROVIDER"] = provider


settings = Settings()
