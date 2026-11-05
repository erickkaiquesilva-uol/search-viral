"""Instagram e X/Twitter via atores do Apify.

As duas redes exigem login e bloqueiam scraping direto, então usamos o Apify
(scraping gerenciado, com plano gratuito limitado). Os nomes dos atores ficam no
.env, e o mapeamento de campos é tolerante a variações de formato.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from ..config import settings
from .models import Post

log = logging.getLogger(__name__)

API = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
PERIOD_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


class ApifyError(RuntimeError):
    pass


def _run_actor(actor: str, payload: dict, timeout: int = 240) -> list[dict]:
    if not settings.apify_enabled:
        raise ApifyError("APIFY_TOKEN não configurado no .env")
    url = API.format(actor=actor.replace("/", "~"))
    resp = httpx.post(
        url,
        params={"timeout": timeout},
        json=payload,
        headers={"Authorization": f"Bearer {settings.apify_token}"},
        timeout=timeout + 30,
    )
    if resp.status_code >= 400:
        raise ApifyError(f"Apify {actor} respondeu {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    return data if isinstance(data, list) else []


def _first(d: dict, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e12 else value, tz=timezone.utc)
    s = str(value)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(s)  # formato do X: "Wed Oct 05 20:00:00 +0000 2026"
    except (TypeError, ValueError):
        pass
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None


def niche_to_hashtag(niche: str) -> str:
    plain = unicodedata.normalize("NFKD", niche).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]", "", plain).lower()


# ---------------------------------------------------------------- Instagram


def instagram(niche: str, period: str = "month", limit: int = 40, progress=None) -> list[Post]:
    progress = progress or (lambda m: None)
    tag = niche_to_hashtag(niche)
    progress(f"Instagram: buscando #{tag} (Apify)")
    items = _run_actor(settings.apify_instagram_actor, {"hashtags": [tag], "resultsLimit": limit})

    cutoff = None
    if period in PERIOD_DAYS:
        cutoff = datetime.now(timezone.utc) - timedelta(days=PERIOD_DAYS[period])

    posts: list[Post] = []
    for it in items:
        if it.get("error"):
            continue
        kind = str(_first(it, "type", "productType", default="")).lower()
        is_video = "video" in kind or "clip" in kind or bool(it.get("videoUrl"))
        published = _parse_date(_first(it, "timestamp", "takenAt"))
        if cutoff and published and published < cutoff:
            continue
        owner = _first(it, "ownerUsername", default="")
        caption = _first(it, "caption", default="") or ""
        posts.append(
            Post(
                platform="instagram",
                id=str(_first(it, "id", "shortCode", default=it.get("url", ""))),
                url=_first(it, "url", default=f"https://www.instagram.com/p/{it.get('shortCode', '')}/"),
                text=caption[:1500],
                author=owner,
                author_url=f"https://www.instagram.com/{owner}/" if owner else "",
                author_followers=_first(it, "ownerFollowersCount", "followersCount"),
                views=_first(it, "videoPlayCount", "videoViewCount", "playCount"),
                likes=_first(it, "likesCount", "likes"),
                comments=_first(it, "commentsCount", "comments"),
                shares=_first(it, "sharesCount", "reshareCount"),
                duration=_first(it, "videoDuration"),
                published_at=published,
                thumbnail=_first(it, "displayUrl", "thumbnailUrl", default=""),
                hashtags=[h.lower() for h in (it.get("hashtags") or [])],
                is_video=is_video,
            )
        )
    return posts


# ---------------------------------------------------------------- X / Twitter


def twitter(niche: str, period: str = "month", limit: int = 50, progress=None) -> list[Post]:
    progress = progress or (lambda m: None)
    query = f"{niche} filter:videos -filter:retweets lang:{settings.yt_lang}"
    if period in PERIOD_DAYS:
        since = (datetime.now(timezone.utc) - timedelta(days=PERIOD_DAYS[period])).strftime("%Y-%m-%d")
        query += f" since:{since}"
    progress(f"X/Twitter: buscando '{query}' (Apify)")
    # O ator exige no mínimo 50 itens por consulta
    items = _run_actor(
        settings.apify_twitter_actor, {"searchTerms": [query], "sort": "Top", "maxItems": max(50, limit)}
    )

    posts: list[Post] = []
    for it in items:
        if it.get("noResults") or not it.get("id"):
            continue
        author = it.get("author") or {}
        user = _first(author, "userName", "screen_name", default="")
        media = ((it.get("extendedEntities") or {}).get("media")) or []
        video = next((m for m in media if m.get("type") in ("video", "animated_gif")), None)
        duration = None
        if video:
            ms = (video.get("video_info") or {}).get("duration_millis")
            duration = ms / 1000 if ms else None
        tags = [h.get("text", "").lower() for h in ((it.get("entities") or {}).get("hashtags") or [])]
        posts.append(
            Post(
                platform="twitter",
                id=str(it["id"]),
                url=_first(it, "url", "twitterUrl", default=f"https://x.com/{user}/status/{it['id']}"),
                text=(_first(it, "fullText", "text", default="") or "")[:1500],
                author=user,
                author_url=f"https://x.com/{user}" if user else "",
                author_followers=_first(author, "followers", "followers_count"),
                views=_first(it, "viewCount"),
                likes=_first(it, "likeCount"),
                comments=_first(it, "replyCount"),
                shares=(it.get("retweetCount") or 0) + (it.get("quoteCount") or 0),
                duration=duration,
                published_at=_parse_date(it.get("createdAt")),
                thumbnail=(video or {}).get("media_url_https", ""),
                hashtags=tags,
                is_video=video is not None,
            )
        )
    return posts[:limit]
