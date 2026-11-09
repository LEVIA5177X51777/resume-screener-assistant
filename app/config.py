"""Runtime settings, read once from environment variables (.env supported)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    llm_provider: str = os.getenv("LLM_PROVIDER", "mock").strip().lower()

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

    openai_compat_base_url: str = os.getenv("OPENAI_COMPAT_BASE_URL", "https://api.groq.com/openai/v1")
    openai_compat_api_key: str = os.getenv("OPENAI_COMPAT_API_KEY", "")
    openai_compat_model: str = os.getenv("OPENAI_COMPAT_MODEL", "llama-3.3-70b-versatile")

    max_input_chars: int = int(os.getenv("MAX_INPUT_CHARS", "12000"))
    min_input_chars: int = 50
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "3"))
    redact_pii: bool = _bool("REDACT_PII", True)
    store_raw_text: bool = _bool("STORE_RAW_TEXT", False)
    retention_days: int = int(os.getenv("RETENTION_DAYS", "90"))

    database_path: str = os.getenv("DATABASE_PATH", "data/screener.db")

    def __repr__(self) -> str:  # never leak secrets via logs / tracebacks
        return f"Settings(provider={self.llm_provider!r}, model={self.model_name!r})"

    @property
    def model_name(self) -> str:
        return {
            "gemini": self.gemini_model,
            "openai_compat": self.openai_compat_model,
        }.get(self.llm_provider, "mock")


settings = Settings()
