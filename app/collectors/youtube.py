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
from itertools import zip_longest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote_plus

import time

import httpx
import yt_dlp

from .. import db
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


class CaptionsBlocked(RuntimeError):
    """O YouTube respondeu 429: limite de requisições de legenda por IP."""


def fetch_transcript(info: dict, client: httpx.Client) -> tuple[list[list], str]:
    """Legenda com cache. Retorna (palavras, status), status = ok | sem_legenda | bloqueado."""
    vid = info.get("id", "")
    cached = db.transcript_get(vid) if vid else None
    if cached is not None:
        return cached, "ok" if cached else "sem_legenda"
    url = _pick_caption_url(info)
    if not url:
        db.transcript_set(vid, [])
        return [], "sem_legenda"
    for attempt in range(2):
        try:
            resp = client.get(url, timeout=15)
            if resp.status_code == 429:
                raise CaptionsBlocked()
            resp.raise_for_status()
            words = _parse_json3(resp.json())
            db.transcript_set(vid, words)
            return words, "ok" if words else "sem_legenda"
        except CaptionsBlocked:
            if attempt == 0:
                time.sleep(3)
                continue
            return [], "bloqueado"  # não grava no cache: tentar de novo mais tarde
        except Exception as exc:
            log.info("Legenda indisponível para %s: %s", vid, exc)
            return [], "sem_legenda"
    return [], "bloqueado"


def _parse_json3(data: dict) -> list[list]:
    """Legenda palavra a palavra: [[segundo, palavra], ...].

    Legendas automáticas trazem o tempo de cada palavra (tOffsetMs). Nas manuais
    vem uma linha inteira por evento, então as palavras são distribuídas pela duração.
    """
    words: list[list] = []
    for ev in data.get("events", []):
        start = ev.get("tStartMs", 0)
        segs = [s for s in (ev.get("segs") or []) if (s.get("utf8") or "").strip()]
        if not segs:
            continue
        if len(segs) == 1 and segs[0].get("tOffsetMs") is None:
            parts = segs[0]["utf8"].split()
            step = (ev.get("dDurationMs") or 1000) / max(1, len(parts))
            words += [[round((start + i * step) / 1000, 2), w] for i, w in enumerate(parts)]
        else:
            for s in segs:
                for w in s["utf8"].split():
                    words.append([round((start + (s.get("tOffsetMs") or 0)) / 1000, 2), w])
    return words


def words_between(words: list[list], start: float, end: float) -> str:
    return " ".join(w for t, w in words if start <= t < end)


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
    width, height = src.get("width"), src.get("height")
    post.metrics["vertical"] = bool(width and height and height > width)
    is_short = is_short or post.metrics["vertical"]
    # idioma original: campo "language" ou a legenda automática "<lang>-orig"
    orig = next((k[:-5] for k in (src.get("automatic_captions") or {}) if k.endswith("-orig")), "")
    post.metrics["language"] = (src.get("language") or orig or "").lower()
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
        by_id = {p.id: p for p in posts}
        load_transcripts([by_id[i] for i in top_ids], full_by_id, progress)
        for i in top_ids:
            by_id[i].transcript = []  # no modo geral só a abertura interessa; evita JSON enorme
    return posts


# ------------------------------------------------------------------ Shorts

SHORTS_MAX_SECONDS = 180  # limite atual do YouTube para Shorts
PERIOD_DAYS = {"day": 1, "week": 7, "month": 31, "year": 366}
CHANNEL_RE = re.compile(r"youtube\.com/(@[\w.\-]+|channel/[\w\-]+|c/[\w\-]+|user/[\w\-]+)", re.IGNORECASE)
VIDEO_RE = re.compile(r"(?:youtube\.com/(?:shorts/|watch\?v=|live/)|youtu\.be/)([\w\-]{11})", re.IGNORECASE)


def _flat_list(url: str, limit: int) -> list[dict]:
    opts = _base_opts() | {"extract_flat": "in_playlist", "playlistend": limit}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        log.warning("Listagem falhou (%s): %s", url, exc)
        return []
    return [e for e in (info.get("entries") or []) if e and e.get("id")]


REEL_RE = re.compile(r'"reelWatchEndpoint":\{"videoId":"([\w-]{11})"')
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def _search_page_shorts(query: str) -> list[dict]:
    """IDs do bloco de Shorts da página de busca.

    O yt-dlp ignora esse bloco (itens "reel"), então lemos o HTML direto. Ele traz
    cerca de 25 Shorts por consulta, ordenados por relevância/popularidade. Não
    aceita filtro de data: com filtro, o YouTube some com o bloco.
    """
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&hl={settings.yt_lang}&gl={settings.yt_region}"
    headers = {"User-Agent": BROWSER_UA, "Accept-Language": f"{settings.yt_lang}-{settings.yt_region},{settings.yt_lang};q=0.9"}
    try:
        html = httpx.get(url, headers=headers, timeout=20, follow_redirects=True).text
    except httpx.HTTPError as exc:
        log.warning("Busca de Shorts falhou (%s): %s", query, exc)
        return []
    ids = list(dict.fromkeys(REEL_RE.findall(html)))
    return [{"id": i, "url": f"https://www.youtube.com/shorts/{i}"} for i in ids]


def _is_short_info(full: dict) -> bool:
    duration = full.get("duration") or 0
    if not duration or duration > SHORTS_MAX_SECONDS:
        return False
    w, h = full.get("width"), full.get("height")
    if w and h:
        return h > w  # Shorts são verticais
    return "/shorts/" in (full.get("webpage_url") or "")


def _enrich_shorts(entries: list[dict], progress, with_transcript: bool = True) -> list[Post]:
    """Detalha cada candidato, mantém só os Shorts e baixa a legenda completa."""
    progress(f"YouTube Shorts: detalhando {len(entries)} candidatos")
    with ThreadPoolExecutor(max_workers=8) as pool:
        fulls = list(pool.map(lambda e: _full_info(e.get("url") or f"https://www.youtube.com/shorts/{e['id']}"), entries))
    pairs = [(e, f) for e, f in zip(entries, fulls) if f and _is_short_info(f)]
    posts = []
    for e, f in pairs:
        p = _to_post(e, f)
        p.url = f"https://www.youtube.com/shorts/{p.id}"
        p.metrics["is_short"] = True
        if not with_transcript:
            p.metrics["_info"] = f  # temporário: quem chamou baixa a legenda depois de filtrar
        posts.append(p)

    if with_transcript and posts:
        load_transcripts(posts, {f["id"]: f for _, f in pairs}, progress)
    return posts


def load_transcripts(posts: list[Post], infos: dict[str, dict], progress) -> None:
    """Baixa as legendas uma a uma (o YouTube bloqueia rajadas com 429).

    Ao primeiro bloqueio, para de pedir: insistir só prolonga o bloqueio. As já
    baixadas ficam em cache no SQLite e não são pedidas de novo.
    """
    progress(f"YouTube Shorts: lendo a fala de {len(posts)} vídeos")
    blocked = False
    with httpx.Client(follow_redirects=True) as client:
        for p in posts:
            if blocked:
                cached = db.transcript_get(p.id)
                words, status = (cached, "ok" if cached else "sem_legenda") if cached is not None else ([], "bloqueado")
            else:
                was_cached = db.transcript_get(p.id) is not None
                words, status = fetch_transcript(infos[p.id], client)
                blocked = status == "bloqueado"
                if not was_cached and not blocked:
                    time.sleep(settings.yt_caption_delay)  # espaça os pedidos para não ser bloqueado
            p.transcript = words
            p.opening = words_between(words, 0, OPENING_SECONDS)
            p.metrics["transcript_status"] = status


def search_shorts(query: str, hashtag: str, period: str = "month", limit: int = 30, progress=None) -> list[Post]:
    """Shorts de um nicho, de duas fontes:

    - bloco de Shorts da página de busca (3 consultas): os mais populares;
    - página da hashtag (/hashtag/<tag>/shorts): mais recentes. A contagem de views
      dessa listagem vem errada (o YouTube abrevia "7,3 mi" e chega "73"), então não
      serve para ordenar; usamos os números do detalhamento.

    A busca comum com filtro "curto" foi descartada: em testes, 100% dos resultados
    eram vídeos horizontais.
    """
    progress = progress or (lambda m: None)
    progress(f"YouTube Shorts: buscando '{query}' e #{hashtag}")
    tag_url = f"https://www.youtube.com/hashtag/{hashtag}/shorts"
    queries = [query, f"{query} shorts", f"#{hashtag}"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        tag_f = pool.submit(_flat_list, tag_url, limit * 2)
        page_results = list(pool.map(_search_page_shorts, queries))
        tag_entries = tag_f.result()
    popular = [e for group in zip_longest(*page_results) for e in group if e]

    # intercala populares (busca) e recentes (hashtag)
    entries: dict[str, dict] = {}
    for pair in zip_longest(popular, tag_entries):
        for e in pair:
            if e:
                entries.setdefault(e["id"], e)
    # folga: parte dos candidatos fica fora do período/idioma e será descartada
    candidates = list(entries.values())[: min(limit * 3, 100)]

    # Filtra antes de baixar legendas, para não gastar requisições com o que será descartado
    posts = _enrich_shorts(candidates, progress, with_transcript=False)
    found = len(posts)
    if period in PERIOD_DAYS:  # nenhuma das fontes aceita filtro de data
        cutoff = datetime.now(timezone.utc).timestamp() - PERIOD_DAYS[period] * 86400
        posts = [p for p in posts if not p.published_at or p.published_at.timestamp() >= cutoff]
    in_period = len(posts)
    # hashtags como #receitasfit atraem vídeos em espanhol (#recetasfit)
    lang = settings.yt_lang.lower()
    posts = [p for p in posts if not p.metrics.get("language") or p.metrics["language"].startswith(lang)]
    progress(f"YouTube Shorts: {found} Shorts encontrados, {in_period} no período, {len(posts)} no idioma '{lang}'")
    posts = sorted(posts, key=lambda p: p.views or 0, reverse=True)[:limit]
    if posts:
        load_transcripts(posts, {p.id: p.metrics.pop("_info") for p in posts}, progress)
    for p in posts:
        p.metrics.pop("_info", None)
    return posts


def channel_shorts(channel_url: str, limit: int = 20, progress=None, with_transcript: bool = True) -> list[Post]:
    progress = progress or (lambda m: None)
    base = CHANNEL_RE.search(channel_url)
    url = f"https://www.youtube.com/{base.group(1)}/shorts" if base else channel_url
    progress(f"YouTube Shorts: listando Shorts do canal {base.group(1) if base else url}")
    posts = _enrich_shorts(_flat_list(url, limit), progress, with_transcript)
    for p in posts:
        p.metrics.pop("_info", None)
    return posts


def single_short(url: str, progress=None) -> tuple[Post | None, dict | None]:
    """Um Short (ou vídeo) com legenda completa. Retorna (post, info bruta)."""
    progress = progress or (lambda m: None)
    m = VIDEO_RE.search(url)
    if not m:
        return None, None
    progress("YouTube: lendo o vídeo")
    full = _full_info(f"https://www.youtube.com/watch?v={m.group(1)}")
    if not full:
        return None, None
    post = _to_post({"id": full["id"], "url": url}, full)
    if _is_short_info(full):
        post.url = f"https://www.youtube.com/shorts/{post.id}"
        post.metrics["is_short"] = True
    load_transcripts([post], {post.id: full}, progress)
    return post, full
