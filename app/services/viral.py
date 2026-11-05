"""Busca de vídeos virais por nicho: coleta -> métricas -> padrões -> (IA opcional) -> relatório."""

from __future__ import annotations

import logging
from datetime import date, datetime

from .. import ai
from ..analysis import hooks as hook_analysis
from ..collectors import apify, youtube
from ..collectors.models import Post
from ..config import settings
from .render import cell, link, num

log = logging.getLogger(__name__)

PERIOD_LABELS = {"day": "últimas 24h", "week": "última semana", "month": "último mês", "year": "último ano", "any": "qualquer data"}
PLATFORM_LABELS = {"youtube": "YouTube", "instagram": "Instagram", "twitter": "X/Twitter"}


def run(niche: str, extra_keywords: str = "", platforms: list[str] | None = None, period: str = "month",
        limit: int = 30, use_ai: bool = False, progress=None) -> dict:
    progress = progress or (lambda m: None)
    platforms = platforms or ["youtube"]
    query = f"{niche} {extra_keywords}".strip()
    warnings: list[str] = []
    posts: list[Post] = []

    collectors = {
        "youtube": lambda: youtube.search(query, period, limit, progress),
        "instagram": lambda: apify.instagram(niche, period, limit, progress),
        "twitter": lambda: apify.twitter(query, period, limit, progress),
    }
    for platform in platforms:
        if platform not in collectors:
            continue
        if platform in ("instagram", "twitter") and not settings.apify_enabled:
            warnings.append(f"{PLATFORM_LABELS[platform]} ignorado: configure APIFY_TOKEN no .env.")
            continue
        try:
            found = collectors[platform]()
            posts += found
            if not found:
                warnings.append(f"{PLATFORM_LABELS[platform]}: nenhum resultado.")
        except Exception as exc:
            log.exception("Coleta %s falhou", platform)
            warnings.append(f"{PLATFORM_LABELS[platform]} falhou: {exc}")

    progress(f"Analisando {len(posts)} posts")
    analysis = hook_analysis.analyze(posts, niche)
    posts.sort(key=lambda p: p.metrics.get("score", 0), reverse=True)

    ai_info = {"used": False}
    ai_text = ""
    if use_ai and posts:
        if not settings.ai_enabled:
            warnings.append("IA não usada: defina AI_API_KEY no .env.")
        else:
            progress("Gerando estudo com IA (dados já resumidos)")
            try:
                # mesma busca no mesmo dia reaproveita o estudo (0 tokens)
                key = f"viral|{niche.lower()}|{extra_keywords.lower()}|{sorted(platforms)}|{period}|{limit}|{date.today()}"
                res = ai.complete(SYSTEM_PROMPT, build_ai_prompt(niche, analysis, posts), cache_key=key)
                ai_text = res["text"]
                ai_info = {"used": True, "cached": res["cached"], "usage": res["usage"]}
            except ai.AIError as exc:
                warnings.append(str(exc))

    params = {"niche": niche, "keywords": extra_keywords, "platforms": platforms, "period": period, "limit": limit, "use_ai": use_ai}
    report = build_report(params, analysis, posts, ai_text, warnings)
    return {
        "params": params,
        "warnings": warnings,
        "analysis": analysis,
        "posts": [p.to_dict() for p in posts],
        "ai": ai_info,
        "ai_text": ai_text,
        "report_md": report,
    }


# ------------------------------------------------------------------ IA

SYSTEM_PROMPT = (
    "Você é um estrategista de conteúdo para vídeos curtos e YouTube. Responda em português do Brasil, "
    "em Markdown, de forma direta e prática. Baseie-se somente nos dados recebidos e cite números deles."
)


def build_ai_prompt(niche: str, a: dict, posts: list[Post], n_examples: int = 10) -> str:
    """Prompt compacto: só estatísticas e os melhores exemplos (aprox. 1.000 a 1.500 tokens)."""
    lines = [f"Nicho: {niche}", f"Posts analisados: {a['summary']['total']} (grupo viral: {a['summary']['viral_group']})", "", "Ganchos (% nos virais / % no restante / lift):"]
    for h in a["hooks"][:8]:
        lines.append(f"- {h['label']}: {h['share_top']:.0%} / {h['share_rest']:.0%} / {h['lift']}")
    lines.append("Formatos (score médio):")
    for f in a["formats"][:5]:
        lines.append(f"- {f['format']}: {f['avg_score']} ({f['count']} posts)")
    t = a["titles"]
    lines.append(f"Título viral médio: {t['avg_len_top']} caracteres; caixa alta {t['caps_top']:.0%}; parênteses {t['brackets_top']:.0%}")
    if a["top_terms"]:
        lines.append("Termos recorrentes: " + ", ".join(x for x, _ in a["top_terms"][:10]))
    lines.append("")
    lines.append("Top exemplos (título | plataforma | views | views/dia | views/seguidores | duração | abertura falada):")
    for p in posts[:n_examples]:
        m = p.metrics
        opening = (p.opening[:160] + "...") if p.opening else "-"
        dur = f"{int(p.duration)}s" if p.duration else "-"
        lines.append(f"- {p.headline[:120]} | {p.platform} | {num(p.views)} | {num(m.get('views_per_day'))} | {m.get('outlier') or '-'} | {dur} | {opening}")
    lines += [
        "",
        "Entregue exatamente estas seções:",
        "## Por que viraliza (4 a 6 bullets ligando padrões aos dados)",
        "## Padrão para copiar (estrutura: gancho 0-3s, desenvolvimento, retenção, CTA, duração ideal)",
        "## 10 ganchos prontos (título + primeira frase falada, adaptados ao nicho)",
        "## 2 roteiros modelo (vídeo curto de até 60s, em tópicos)",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ relatório


def build_report(params: dict, a: dict, posts: list[Post], ai_text: str, warnings: list[str]) -> str:
    plats = ", ".join(PLATFORM_LABELS.get(p, p) for p in params["platforms"])
    out = [
        f"# Estudo de virais: {cell(params['niche'])}",
        "",
        f"Gerado em {datetime.now():%d/%m/%Y %H:%M} · Plataformas: {plats} · Período: {PERIOD_LABELS.get(params['period'], params['period'])} · "
        f"{a.get('summary', {}).get('total', 0)} posts analisados",
        "",
    ]
    if warnings:
        out += ["> " + w for w in warnings] + [""]
    if not posts:
        out.append("Nenhum post encontrado. Tente outro termo ou um período maior.")
        return "\n".join(out)

    out += ["## Siga este padrão", ""]
    out += [f"- {i}" for i in a["insights"]] + [""]

    if ai_text:
        out += ["## Estudo da IA", "", ai_text.strip(), ""]

    out += ["## Ganchos encontrados", "", "| Gancho | % nos virais | % no restante | Lift | Melhor exemplo |", "|---|---|---|---|---|"]
    for h in a["hooks"]:
        ex = h["examples"][0] if h["examples"] else None
        lift = "só nos virais" if h["lift"] == 99.0 else (h["lift"] if h["lift"] is not None else "-")
        out.append(f"| {h['label']} | {h['share_top']:.0%} | {h['share_rest']:.0%} | {lift} | {link(ex['headline'][:80], ex['url']) if ex else '-'} |")
    out.append("")
    out += ["### Por que cada gancho funciona e como usar", ""]
    for h in a["hooks"][:6]:
        out += [f"**{h['label']}**: {h['why']}", ""]
        out += [f"- Modelo: `{t}`" for t in h["templates"][:2]]
        out.append("")

    out += ["## Formatos", "", "| Formato | Posts | % nos virais | Score médio | Mediana views/dia |", "|---|---|---|---|---|"]
    for f in a["formats"]:
        out.append(f"| {f['format']} | {f['count']} | {f['share_top']:.0%} | {f['avg_score']} | {num(f['median_views_per_day'])} |")
    out.append("")

    if a["molds"]:
        out += ["## Moldes de título (a partir dos virais)", ""]
        out += [f"- `{cell(m['mold'])}` ← {link(m['from'][:90], m['url'])}" for m in a["molds"]]
        out.append("")

    if a["openings"]:
        out += ["## Como os virais abrem o vídeo (primeiros 15s falados)", ""]
        for o in a["openings"]:
            tags = f" _({', '.join(o['hooks'])})_" if o["hooks"] else ""
            out.append(f"- {link(o['headline'][:80], o['url'])}: “{cell(o['opening'][:220])}”{tags}")
        out.append("")

    if a["top_terms"] or a["top_hashtags"]:
        out += ["## Termos e hashtags dos virais", ""]
        if a["top_terms"]:
            out.append("**Termos:** " + ", ".join(f"{cell(t)} ({n})" for t, n in a["top_terms"]))
        if a["top_hashtags"]:
            out.append("")
            out.append("**Hashtags:** " + ", ".join(f"#{cell(t)} ({n})" for t, n in a["top_hashtags"]))
        out.append("")

    out += ["## Referências (ordenadas por score de viralização)", "",
            "| # | Rede | Título | Autor | Views | Views/dia | Views/seguidores | Engaj. | Score |", "|---|---|---|---|---|---|---|---|---|"]
    for i, p in enumerate(posts[:50], 1):
        m = p.metrics
        eng = f"{m['engagement']:.1%}" if m.get("engagement") is not None else "-"
        outlier = f"{m['outlier']}x" if m.get("outlier") else "-"
        out.append(
            f"| {i} | {PLATFORM_LABELS.get(p.platform, p.platform)} | {link(p.headline[:90], p.url)} | {link(p.author, p.author_url)} | "
            f"{num(p.views)} | {num(m.get('views_per_day'))} | {outlier} | {eng} | {m.get('score')} |"
        )
    out += ["", "_Score = percentil de views/dia (45%), views/seguidores (30%) e engajamento (25%) dentro da mesma rede._"]
    return "\n".join(out)
