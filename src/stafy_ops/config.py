from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str
    telegram_allowed_user_ids: str
    telegram_webhook_secret: str = ""

    github_token: str
    github_org: str = "stafy-app"
    github_project_number: int = 1

    llm_base_url: str
    llm_api_key: str
    llm_model: str

    max_question_rounds: int = 6

    # Upstash Redis REST creds — the Vercel Marketplace integration injects either naming.
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""
    kv_rest_api_url: str = ""
    kv_rest_api_token: str = ""

    @property
    def redis_rest(self) -> tuple[str, str] | None:
        url = self.upstash_redis_rest_url or self.kv_rest_api_url
        token = self.upstash_redis_rest_token or self.kv_rest_api_token
        return (url, token) if url and token else None

    @property
    def allowed_user_ids(self) -> set[int]:
        return {int(p) for p in self.telegram_allowed_user_ids.split(",") if p.strip()}

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        for name in ("telegram_bot_token", "github_token", "llm_base_url", "llm_api_key", "llm_model"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name.upper()} must not be empty")
        if not self.llm_base_url.startswith(("http://", "https://")):
            raise ValueError("LLM_BASE_URL must start with http:// or https://")
        try:
            ids = self.allowed_user_ids
        except ValueError as exc:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS must be comma-separated integers") from exc
        if not ids:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS must contain at least one user ID")
        if not 1 <= self.max_question_rounds <= 10:
            raise ValueError("MAX_QUESTION_ROUNDS must be between 1 and 10")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
