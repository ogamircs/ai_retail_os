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

    def integration_value(self, key: str) -> str | None:
        value = os.getenv(key)
        return value.strip() if value and value.strip() else None


settings = Settings()


# ----------------------------------------------------------------------
# Track 2 A6 — agent-mesh guardrails.
#
# All overridable via env vars. Defaults are conservative — the cockpit
# can run a multi-pass turn end-to-end without blowing past 60s.
# ----------------------------------------------------------------------

class MeshSettings:
    @property
    def enabled(self) -> bool:
        return os.getenv("MESH_ENABLED", "1").strip() not in ("0", "false", "False", "no", "")

    @property
    def max_revision_rounds(self) -> int:
        try:
            return int(os.getenv("MESH_MAX_REVISION_ROUNDS", "2"))
        except ValueError:
            return 2

    @property
    def max_critic_per_draft(self) -> int:
        try:
            return int(os.getenv("MESH_MAX_CRITIC_PER_DRAFT", "2"))
        except ValueError:
            return 2

    @property
    def turn_token_budget(self) -> int:
        """Soft cap; if a single specialist round produced this many words
        of artifact body, the Chief downgrades the rest of the turn to
        single-pass and logs a `mesh_downgrade` event. ~1.3 tokens/word."""
        try:
            return int(os.getenv("MESH_TURN_TOKEN_BUDGET", "12000"))
        except ValueError:
            return 12000

    @property
    def turn_wallclock_seconds(self) -> int:
        try:
            return int(os.getenv("MESH_TURN_WALLCLOCK_SECONDS", "60"))
        except ValueError:
            return 60


mesh = MeshSettings()
