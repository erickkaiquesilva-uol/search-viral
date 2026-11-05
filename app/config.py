import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    ai_api_key: str = os.getenv("AI_API_KEY", "").strip()
    ai_base_url: str = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    ai_model: str = os.getenv("AI_MODEL", "gpt-4o-mini")
    ai_max_tokens: int = _int("AI_MAX_TOKENS", 1000)

    apify_token: str = os.getenv("APIFY_TOKEN", "").strip()
    apify_instagram_actor: str = os.getenv("APIFY_INSTAGRAM_ACTOR", "apify/instagram-hashtag-scraper")
    apify_twitter_actor: str = os.getenv("APIFY_TWITTER_ACTOR", "apidojo/tweet-scraper")

    yt_lang: str = os.getenv("YT_LANG", "pt")
    yt_region: str = os.getenv("YT_REGION", "BR")
    yt_enrich_top: int = _int("YT_ENRICH_TOP", 25)
    yt_transcript_top: int = _int("YT_TRANSCRIPT_TOP", 8)
    # Pausa (segundos) entre downloads de legenda; o YouTube bloqueia rajadas com HTTP 429
    yt_caption_delay: float = float(os.getenv("YT_CAPTION_DELAY", "0.8") or 0.8)
    shorts_limit_max: int = _int("SHORTS_LIMIT_MAX", 60)

    # Relativo à raiz do projeto, ou absoluto se você quiser guardar fora dele.
    db_path: Path = ROOT / os.path.expanduser(os.getenv("DB_PATH", "data/app.db"))

    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = _int("PORT", 8000)
    # Fuso usado para "melhor dia/horário de publicação"
    app_timezone: str = os.getenv("APP_TIMEZONE", "America/Sao_Paulo")

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_api_key)

    @property
    def apify_enabled(self) -> bool:
        return bool(self.apify_token)


settings = Settings()
