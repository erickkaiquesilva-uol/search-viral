"""Coletor do YouTube via yt-dlp (sem API key).

Fluxo:
1. Busca "rasa" (rápida) na página de resultados, com filtro de período.
2. Coleta detalhada dos vídeos com mais views (likes, comentários, data, inscritos).
3. Para os melhores, baixa a legenda e guarda os primeiros segundos falados (o gancho).
"""

from __future__ import annotations

import base64
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote_plus

import httpx
import yt_dlp

from ..config import settings
from .models import Post

log = logging.getLogger(__name__)

# O parâmetro "sp" da busca do YouTube é um protobuf em base64:
#   campo 2 (filtros) { 1: data de envio, 2: tipo, 3: duração }
UPLOAD_DATE = {"day": 2, "week": 3, "month": 4, "year": 5}
TYPE_VIDEO = 1
DURATION_SHORT = 1  # menos de 4 minutos


def _sp(period: str, short: bool) -> str:
    filters = bytearray()
    if period in UPLOAD_DATE:
        filters += bytes([0x08, UPLOAD_DATE[period]])
    filters += bytes([0x10, TYPE_VIDEO])
    if short:
        filters += bytes([0x18, DURATION_SHORT])
    raw = bytes([0x12, len(filters)]) + bytes(filters)
    return quote_plus(base64.b64encode(raw).decode())

OPENING_SECONDS = 15
HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)


def _base_opts() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
        # usa o SSL do sistema (truststore) em vez do bundle do certifi
        "compat_opts": ["no-certifi"],
        "http_headers": {"Accept-Language": f"{settings.yt_lang}-{settings.yt_region},{settings.yt_lang};q=0.9"},
        "extractor_args": {"youtube": {"lang": [settings.yt_lang]}},
    }


def _search_url(query: str, period: str, short: bool) -> str:
    return (
        f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        f"&gl={settings.yt_region}&hl={settings.yt_lang}&sp={_sp(period, short)}"
    )


def _flat_search(query: str, period: str, limit: int, short: bool) -> list[dict]:
    opts = _base_opts() | {"extract_flat": "in_playlist", "playlistend": limit}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(_search_url(query, period, short), download=False) or {}
    except Exception as exc:
        log.warning("Busca YouTube falhou (%s, short=%s): %s", query, short, exc)
        return []
    return [e for e in (info.get("entries") or []) if e and e.get("id")]


def _full_info(url: str) -> dict | None:
    try:
        with yt_dlp.YoutubeDL(_base_opts()) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as exc:  # vídeo removido, restrito por idade etc.
        log.info("Falha ao detalhar %s: %s", url, exc)
        return None


def _pick_caption_url(info: dict) -> str | None:
    lang = settings.yt_lang
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    # Prioridade: legenda manual no idioma > automática original no idioma >
    # automática traduzida para o idioma > qualquer original.
    candidates: list[list[dict]] = []
    candidates += [v for k, v in manual.items() if k.split("-")[0] == lang]
    candidates += [v for k, v in auto.items() if k == f"{lang}-orig"]
    candidates += [v for k, v in auto.items() if k == lang]
    candidates += [v for k, v in auto.items() if k.endswith("-orig")]
    candidates += list(manual.values())[:1]
    for formats in candidates:
        for f in formats or []:
            if f.get("ext") == "json3" and f.get("url"):
                return f["url"]
    return None


def _opening_text(info: dict, client: httpx.Client) -> str:
    """Texto falado nos primeiros segundos do vídeo, a partir da legenda."""
    url = _pick_caption_url(info)
    if not url:
        return ""
    try:
        data = client.get(url, timeout=15).json()
    except Exception as exc:
        log.info("Legenda indisponível para %s: %s", info.get("id"), exc)
        return ""
    words: list[str] = []
    for ev in data.get("events", []):
        if ev.get("tStartMs", 0) > OPENING_SECONDS * 1000:
            break
        for seg in ev.get("segs") or []:
            words.append(seg.get("utf8", ""))
    return re.sub(r"\s+", " ", "".join(words)).strip()


def _to_post(entry: dict, full: dict | None) -> Post:
    src = full or entry
    published = None
    if src.get("timestamp"):
        published = datetime.fromtimestamp(src["timestamp"], tz=timezone.utc)
    elif src.get("upload_date"):
        published = datetime.strptime(src["upload_date"], "%Y%m%d").replace(tzinfo=timezone.utc)

    description = src.get("description") or ""
    tags = [t.lower() for t in (src.get("tags") or [])]
    hashtags = sorted({h.lower() for h in HASHTAG_RE.findall(f"{src.get('title', '')} {description}")})

    url = src.get("webpage_url") or entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}"
    duration = src.get("duration")
    is_short = "/shorts/" in url or (duration is not None and duration <= 60)

    thumbs = src.get("thumbnails") or entry.get("thumbnails") or []
    thumbnail = src.get("thumbnail") or (thumbs[-1]["url"] if thumbs else "")

    post = Post(
        platform="youtube",
        id=entry["id"],
        url=url,
        title=src.get("title") or "",
        text=description[:1500],
        author=src.get("channel") or src.get("uploader") or "",
        author_url=src.get("channel_url") or src.get("uploader_url") or "",
        author_followers=src.get("channel_follower_count"),
        views=src.get("view_count"),
        likes=src.get("like_count"),
        comments=src.get("comment_count"),
        duration=duration,
        published_at=published,
        thumbnail=thumbnail,
        hashtags=hashtags or tags[:10],
    )
    post.metrics["is_short"] = is_short
    post.metrics["has_time"] = bool(src.get("timestamp"))
    post.metrics["tags"] = tags[:20]
    return post


def search(query: str, period: str = "month", limit: int = 40, progress=None) -> list[Post]:
    """Busca vídeos do YouTube para o nicho/consulta: uma busca geral e outra só de vídeos curtos."""
    progress = progress or (lambda msg: None)
    per_query = max(10, limit)

    progress(f"YouTube: buscando '{query}' (vídeos longos e curtos)")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda s: _flat_search(query, period, per_query, s), (False, True)))
    # Metade das vagas para cada grupo; vídeos curtos costumam ter menos views
    # absolutas e sumiriam se ordenássemos tudo junto.
    by_views = lambda e: e.get("view_count") or 0  # noqa: E731
    long_list, short_list = (sorted(b, key=by_views, reverse=True) for b in results)
    entries: dict[str, dict] = {}
    for e in long_list[: limit - limit // 2] + short_list[: limit // 2] + long_list + short_list:
        if len(entries) >= limit:
            break
        entries.setdefault(e["id"], e)

    ranked = sorted(entries.values(), key=by_views, reverse=True)
    short_ids = {e["id"] for e in short_list}
    longs = [e for e in ranked if e["id"] not in short_ids]
    shorts = [e for e in ranked if e["id"] in short_ids]
    interleaved = [e for pair in zip(longs, shorts) for e in pair]
    interleaved += longs[len(shorts):] + shorts[len(longs):]
    to_enrich = interleaved[: settings.yt_enrich_top]
    progress(f"YouTube: detalhando {len(to_enrich)} de {len(ranked)} vídeos")

    with ThreadPoolExecutor(max_workers=8) as pool:
        fulls = list(pool.map(lambda e: _full_info(e["url"]), to_enrich))
    full_by_id = {e["id"]: f for e, f in zip(to_enrich, fulls) if f}

    posts = [_to_post(e, full_by_id.get(e["id"])) for e in ranked]

    # Gancho falado: legenda dos primeiros segundos dos mais vistos
    top_ids = [e["id"] for e in to_enrich if e["id"] in full_by_id][: settings.yt_transcript_top]
    if top_ids:
        progress(f"YouTube: lendo a abertura falada de {len(top_ids)} vídeos")
        with httpx.Client(follow_redirects=True) as client, ThreadPoolExecutor(max_workers=4) as pool:
            openings = dict(zip(top_ids, pool.map(lambda i: _opening_text(full_by_id[i], client), top_ids)))
        for p in posts:
            p.opening = openings.get(p.id, "")
    return posts
