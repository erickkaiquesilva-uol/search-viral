"""Relatório sobre um tema: busca web -> extração de texto -> resumo extrativo -> (IA opcional)."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlparse

import httpx
import trafilatura
from ddgs import DDGS

from .. import ai
from ..analysis.hooks import HOOKS_BY_KEY
from ..analysis.text import content_words, extractive_summary, keywords
from ..collectors import youtube
from ..config import settings
from .render import cell, link, num

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
SKIP_DOMAINS = ("youtube.com", "youtu.be", "instagram.com", "facebook.com", "tiktok.com", "x.com", "twitter.com")
MAX_DOC_CHARS = 20000


def _region() -> str:
    return f"{settings.yt_region.lower()}-{settings.yt_lang}"


def _web_search(topic: str, n: int) -> list[dict]:
    results: list[dict] = []
    with DDGS() as ddgs:
        try:
            for r in ddgs.text(topic, region=_region(), max_results=n) or []:
                results.append({"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", ""), "kind": "web"})
        except Exception as exc:
            log.warning("Busca web falhou: %s", exc)
        try:
            for r in ddgs.news(topic, region=_region(), max_results=max(3, n // 2)) or []:
                results.append({"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("body", ""),
                                "kind": "notícia", "date": r.get("date", ""), "source": r.get("source", "")})
        except Exception as exc:
            log.info("Busca de notícias falhou: %s", exc)

    seen, unique = set(), []
    for r in results:
        url = r["url"]
        if not url or url in seen or any(d in urlparse(url).netloc for d in SKIP_DOMAINS):
            continue
        seen.add(url)
        unique.append(r)
    return unique


def _fetch_text(client: httpx.Client, url: str) -> str:
    try:
        resp = client.get(url, timeout=15)
        if resp.status_code >= 400 or "html" not in resp.headers.get("content-type", "html"):
            return ""
        text = trafilatura.extract(resp.text, url=url, include_comments=False, include_tables=False, favor_precision=True)
        return (text or "")[:MAX_DOC_CHARS]
    except Exception as exc:
        log.info("Falha ao ler %s: %s", url, exc)
        return ""


IDEA_TEMPLATES = [
    ("lista", "5 erros que quase todo mundo comete com {t}"),
    ("segredo", "O que ninguém te conta sobre {t}"),
    ("pergunta", "{T}: vale a pena ou é exagero?"),
    ("contrario", "Tudo que te falaram sobre {t} está errado"),
    ("alerta", "Pare de ignorar {t} (isso vai te custar caro)"),
    ("experimento", "Testei {t} por 30 dias: o que aconteceu"),
    ("tutorial", "{T} explicado em 60 segundos"),
]


_PT_MARKERS = re.compile(r"\b(não|são|você|uma|também|então|isso|pelo|pela|muito|mais|com)\b|ções?\b", re.I)
_ES_MARKERS = re.compile(r"\b(el|los|las|del|es|una|también|pero|muy|usted|hay|con|más)\b|ci[óo]n(es)?\b", re.I)


def _looks_spanish(text: str) -> bool:
    """Termos como 'marketing de afiliados' trazem muitos resultados em espanhol."""
    sample = text[:4000]
    return len(_ES_MARKERS.findall(sample)) > 1.5 * max(1, len(_PT_MARKERS.findall(sample)))


def _content_ideas(topic: str, terms: list[str]) -> list[str]:
    """Ideias de vídeo sem IA: moldes de ganchos fortes preenchidos com o tema e seus termos-chave."""
    # bigramas são assuntos mais concretos que palavras soltas
    related = [t for t in terms if " " in t and t.lower() not in topic.lower()][:3]
    subjects = [topic] + related
    ideas = []
    for i, (key, template) in enumerate(IDEA_TEMPLATES):
        s = subjects[i % len(subjects)]
        text = template.replace("{t}", s).replace("{T}", s[:1].upper() + s[1:])
        ideas.append(f"**{HOOKS_BY_KEY[key].label}**: {text}")
    return ideas


def run(topic: str, depth: int = 8, use_ai: bool = False, include_videos: bool = True, progress=None) -> dict:
    progress = progress or (lambda m: None)
    warnings: list[str] = []

    progress(f"Buscando fontes na web sobre '{topic}'")
    results = _web_search(topic, depth)
    if not results:
        warnings.append("Nenhuma fonte encontrada na busca web.")

    progress(f"Lendo {len(results)} páginas")
    with httpx.Client(follow_redirects=True, headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"}) as client, \
            ThreadPoolExecutor(max_workers=8) as pool:
        texts = list(pool.map(lambda r: _fetch_text(client, r["url"]), results))

    sources, skipped_lang = [], 0
    for r, text in zip(results, texts):
        content = text or r["snippet"]
        if settings.yt_lang == "pt" and _looks_spanish(f"{r['title']} {content}"):
            skipped_lang += 1
            continue
        sources.append(dict(r, id=len(sources) + 1, chars=len(text), text=content))
    if skipped_lang:
        warnings.append(f"{skipped_lang} fonte(s) em espanhol foram descartadas.")
    docs = [{"id": s["id"], "text": s["text"]} for s in sources if s["text"]]
    read_ok = sum(1 for s in sources if s["chars"] > 300)

    progress("Extraindo pontos principais (local, sem IA)")
    exclude = set(content_words(topic))
    terms = keywords([d["text"] for d in docs], top=20, exclude=exclude)
    highlights = extractive_summary(docs, topic, max_sentences=15, per_doc=3)

    videos = []
    if include_videos:
        progress("Buscando vídeos populares sobre o tema no YouTube")
        try:
            entries = youtube._flat_search(topic, "year", 20, short=False)
            entries.sort(key=lambda e: e.get("view_count") or 0, reverse=True)
            videos = [{"title": e.get("title", ""), "url": e.get("url", ""), "channel": e.get("channel", ""),
                       "views": e.get("view_count")} for e in entries[:8]]
        except Exception as exc:
            warnings.append(f"Vídeos do YouTube indisponíveis: {exc}")

    ideas = _content_ideas(topic, [t for t, _ in terms])

    ai_text, ai_info = "", {"used": False}
    if use_ai and highlights:
        if not settings.ai_enabled:
            warnings.append("IA não usada: defina AI_API_KEY no .env.")
        else:
            progress("Gerando relatório com IA (a partir dos trechos selecionados)")
            try:
                key = f"research|{topic.lower()}|{depth}|{datetime.now():%Y-%m-%d}"
                res = ai.complete(SYSTEM_PROMPT, build_ai_prompt(topic, terms, highlights), cache_key=key)
                ai_text = res["text"]
                ai_info = {"used": True, "cached": res["cached"], "usage": res["usage"]}
            except ai.AIError as exc:
                warnings.append(str(exc))

    params = {"topic": topic, "depth": depth, "use_ai": use_ai, "include_videos": include_videos}
    public_sources = [{k: v for k, v in s.items() if k != "text"} for s in sources]
    report = build_report(topic, terms, highlights, sources, videos, ideas, ai_text, warnings, read_ok)
    return {
        "params": params,
        "warnings": warnings,
        "terms": terms,
        "highlights": highlights,
        "sources": public_sources,
        "videos": videos,
        "ideas": ideas,
        "ai": ai_info,
        "ai_text": ai_text,
        "report_md": report,
    }


SYSTEM_PROMPT = (
    "Você é um pesquisador que escreve relatórios objetivos em português do Brasil, em Markdown. "
    "Use apenas os trechos fornecidos e cite as fontes no formato [n]. Não invente dados."
)


def build_ai_prompt(topic: str, terms: list[tuple[str, int]], highlights: list[dict]) -> str:
    lines = [f"Tema: {topic}", "Termos-chave: " + ", ".join(t for t, _ in terms[:12]), "", "Trechos:"]
    for h in highlights:
        lines.append(f"[{h['doc_id']}] {h['text'][:320]}")
    lines += [
        "",
        "Escreva as seções:",
        "## Resumo executivo (1 parágrafo)",
        "## Pontos principais (bullets com [n])",
        "## Dados e números citados",
        "## Divergências ou pontos de atenção",
        "## 5 ideias de vídeo sobre o tema (título com gancho + ângulo)",
    ]
    return "\n".join(lines)


def build_report(topic, terms, highlights, sources, videos, ideas, ai_text, warnings, read_ok) -> str:
    out = [
        f"# Relatório: {cell(topic)}",
        "",
        f"Gerado em {datetime.now():%d/%m/%Y %H:%M} · {len(sources)} fontes encontradas, {read_ok} lidas por completo",
        "",
    ]
    if warnings:
        out += ["> " + w for w in warnings] + [""]

    if ai_text:
        out += [ai_text.strip(), ""]
    else:
        out += ["## Pontos principais", "", "_Trechos selecionados automaticamente das fontes (sem IA)._", ""]
        out += [f"- {cell(h['text'])} [{h['doc_id']}]" for h in highlights] or ["- Nada relevante extraído."]
        out.append("")

    if terms:
        out += ["## Termos-chave", "", ", ".join(f"{cell(t)} ({n})" for t, n in terms), ""]

    if not ai_text:
        out += ["## Ideias de vídeo", ""] + [f"- {i}" for i in ideas] + [""]

    if videos:
        out += ["## Vídeos populares sobre o tema (YouTube, último ano)", "", "| Vídeo | Canal | Views |", "|---|---|---|"]
        out += [f"| {link(v['title'][:90], v['url'])} | {cell(v['channel'])} | {num(v['views'])} |" for v in videos]
        out.append("")

    out += ["## Fontes", ""]
    for s in sources:
        extra = f" · {cell(s.get('source'))} {cell(s.get('date', ''))[:10]}" if s["kind"] == "notícia" else ""
        out.append(f"{s['id']}. {link(s['title'] or s['url'], s['url'])} ({s['kind']}{extra})")
    return "\n".join(out)
