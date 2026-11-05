"""Shorts do YouTube: estudo por nicho/canal e raio-X de um Short."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from statistics import median

from .. import ai, db
from ..analysis import metrics
from ..analysis import shorts as sa
from ..analysis.hooks import HOOKS_BY_KEY, detect_hooks, title_features
from ..analysis.text import niche_to_hashtag
from ..collectors import youtube
from ..collectors.models import Post
from ..config import settings
from .render import cell, link, num
from .viral import PERIOD_LABELS

log = logging.getLogger(__name__)


def _fmt_s(x) -> str:
    return "-" if x is None else f"{x}s"


def _pct(x) -> str:
    return f"{(x or 0) * 100:.0f}%"


# ================================================================== estudo (nicho ou canal)


def run_niche(niche: str, keywords: str = "", period: str = "month", limit: int = 30,
              use_ai: bool = False, progress=None) -> dict:
    progress = progress or (lambda m: None)
    query = f"{niche} {keywords}".strip()
    posts = youtube.search_shorts(query, niche_to_hashtag(niche), period, limit, progress)
    params = {"mode": "niche", "niche": niche, "keywords": keywords, "period": period, "limit": limit, "use_ai": use_ai}
    return _study(posts, niche, params, f"Shorts do nicho: {niche}", use_ai, progress,
                  cache_key=f"shorts|{niche.lower()}|{keywords.lower()}|{period}|{limit}|{date.today()}")


def run_channel(channel_url: str, limit: int = 20, use_ai: bool = False, progress=None) -> dict:
    progress = progress or (lambda m: None)
    posts = youtube.channel_shorts(channel_url, limit, progress)
    name = next((p.author for p in posts if p.author), channel_url)
    params = {"mode": "channel", "channel_url": channel_url, "niche": name, "limit": limit, "use_ai": use_ai, "period": "any"}
    return _study(posts, name, params, f"Shorts do canal: {name}", use_ai, progress,
                  cache_key=f"shorts-channel|{channel_url.lower()}|{limit}|{date.today()}")


def _study(posts: list[Post], niche: str, params: dict, title: str, use_ai: bool, progress, cache_key: str) -> dict:
    warnings: list[str] = []
    if not posts:
        warnings.append("Nenhum Short encontrado. Tente outro termo, um período maior ou confira o link do canal.")
    progress(f"Analisando {len(posts)} Shorts")
    analysis = sa.analyze(posts, niche)
    posts.sort(key=lambda p: p.metrics.get("score", 0), reverse=True)

    ai_text, ai_info = "", {"used": False}
    if use_ai and posts:
        if not settings.ai_enabled:
            warnings.append("IA não usada: defina AI_API_KEY no .env.")
        else:
            progress("Gerando estudo com IA (dados já resumidos)")
            try:
                res = ai.complete(SYSTEM_PROMPT, build_study_prompt(niche, analysis), cache_key=cache_key)
                ai_text, ai_info = res["text"], {"used": True, "cached": res["cached"], "usage": res["usage"]}
            except ai.AIError as exc:
                warnings.append(str(exc))

    report = build_study_report(title, params, analysis, posts, ai_text, warnings)
    return {"params": params, "warnings": warnings, "analysis": analysis, "posts": [p.to_dict() for p in posts],
            "ai": ai_info, "ai_text": ai_text, "report_md": report}


SYSTEM_PROMPT = (
    "Você é um estrategista de YouTube Shorts. Responda em português do Brasil, em Markdown, de forma "
    "direta e prática. Baseie-se somente nos dados recebidos e cite números deles."
)


def build_study_prompt(niche: str, a: dict) -> str:
    s, b = a["speech"], a["benchmark"]
    lines = [
        f"Nicho: {niche}. Shorts analisados: {a['summary']['total']} (grupo viral: {a['summary']['viral_group']}).",
        f"Virais: fala começa em {s['start_top']}s; {s['wps_top']} palavras/s; fala em {_pct(s['coverage_top'])} do tempo; "
        f"música em {_pct(s['share_music_top'])}; duração mediana {b['duration']}s; faixa que mais performa: {b['best_bucket']}.",
        f"Restante: fala começa em {s['start_rest']}s; {s['wps_rest']} palavras/s.",
        "Final (virais/restante): " + "; ".join(f"{k} {_pct(v['top'])}/{_pct(v['rest'])}" for k, v in a["ending"].items()),
        "Ganchos dos 0-3s (% virais / % restante): " + "; ".join(f"{h['label']} {_pct(h['share_top'])}/{_pct(h['share_rest'])}" for h in a["opening_hooks"][:6]),
        "",
        "Top Shorts (título | views | duração | 0-3s falado | final):",
    ]
    for ex in a["examples"][:8]:
        lines.append(f"- {ex['headline'][:90]} | {num(ex['views'])} | {_fmt_s(int(ex['duration'] or 0))} | "
                     f"{(ex['hook_text'] or '-')[:110]} | {(ex['ending_text'] or '-')[:90]}")
    lines += [
        "",
        "Entregue exatamente estas seções:",
        "## Por que esses Shorts viralizam (4 a 6 bullets ligando padrões aos dados)",
        "## Roteiro modelo segundo a segundo (0-3s, 3-15s, desenvolvimento, final)",
        "## 10 ganchos prontos para os 3 primeiros segundos (frase falada + o que aparece na tela)",
        "## 2 roteiros completos de Short para o nicho",
    ]
    return "\n".join(lines)


def build_study_report(title: str, params: dict, a: dict, posts: list[Post], ai_text: str, warnings: list[str]) -> str:
    summ = a.get("summary", {})
    period = PERIOD_LABELS.get(params.get("period", "any"), "-")
    out = [f"# {cell(title)}", "",
           f"Gerado em {datetime.now():%d/%m/%Y %H:%M} · Período: {period} · {summ.get('total', 0)} Shorts analisados "
           f"(até 180s, verticais) · fala lida em {summ.get('with_transcript', 0)}", ""]
    out += ["> " + w for w in warnings] + ([""] if warnings else [])
    if not posts:
        return "\n".join(out)

    out += ["## Receita do Short viral neste nicho", ""] + [f"- {s}" for s in a["recipe"]] + [""]
    out += ["## Siga este padrão", ""] + [f"- {i}" for i in a["insights"]] + [""]
    if ai_text:
        out += ["## Estudo da IA", "", ai_text.strip(), ""]

    s = a["speech"]
    if not summ.get("with_transcript"):
        out += ["## Fala e ritmo", "",
                "_A fala não pôde ser lida neste estudo (legendas bloqueadas pelo YouTube ou ausentes), então ritmo, "
                "gancho falado e final ficaram de fora. Use “Tentar ler a fala de novo” em alguns minutos._", ""]
    else:
        out += _speech_table(s, a["ending"])
    return _study_report_rest(out, a, posts)


def _speech_table(s: dict, ending: dict) -> list[str]:
    out = ["## Fala e ritmo", "", "| Métrica | Virais | Restante |", "|---|---|---|",
            f"| Têm fala | {_pct(s['share_speech_top'])} | {_pct(s['share_speech_rest'])} |",
            f"| Fala começa em (mediana) | {_fmt_s(s['start_top'])} | {_fmt_s(s['start_rest'])} |",
            f"| Palavras por segundo | {s['wps_top'] or '-'} | {s['wps_rest'] or '-'} |",
            f"| Tempo com fala | {_pct(s['coverage_top'])} | {_pct(s['coverage_rest'])} |",
            f"| Música marcada na legenda | {_pct(s['share_music_top'])} | {_pct(s['share_music_rest'])} |"]
    labels = {"cta": "CTA no final", "question_end": "Pergunta no final", "loop": "Final em loop"}
    out += [f"| {labels[k]} | {_pct(v['top'])} | {_pct(v['rest'])} |" for k, v in ending.items()] + [""]
    return out


def _study_report_rest(out: list[str], a: dict, posts: list[Post]) -> str:
    out += ["## Duração", "", "| Faixa | Shorts | % nos virais | Score médio | Mediana views/dia |", "|---|---|---|---|---|"]
    out += [f"| {b['bucket']} | {b['count']} | {_pct(b['share_top'])} | {b['avg_score']} | {num(b['median_views_per_day'])} |"
            for b in a["durations"]] + [""]

    for key, heading in (("opening_hooks", "Ganchos falados nos primeiros 3s"), ("title_hooks", "Ganchos no título")):
        if not a[key]:
            continue
        out += [f"## {heading}", "", "| Gancho | % nos virais | % no restante | Lift | Exemplo |", "|---|---|---|---|---|"]
        for h in a[key]:
            lift = "só nos virais" if h["lift"] == 99.0 else (h["lift"] if h["lift"] is not None else "-")
            ex = h["example"]
            label = (f"“{ex['hook_text'][:70]}”" if key == "opening_hooks" and ex["hook_text"] else ex["headline"][:70])
            out.append(f"| {h['label']} | {_pct(h['share_top'])} | {_pct(h['share_rest'])} | {lift} | {link(label, ex['url'])} |")
        out.append("")

    out += ["## Como os melhores Shorts são falados", ""]
    for ex in a["examples"][:8]:
        hooks = f" _({', '.join(ex['opening_hooks'])})_" if ex["opening_hooks"] else ""
        out.append(f"**{link(ex['headline'][:90], ex['url'])}** · {num(ex['views'])} views · {_fmt_s(int(ex['duration'] or 0))} · "
                   f"fala em {_fmt_s(ex['speech_start'])} · {ex['wps'] or '-'} palavras/s{hooks}")
        out.append("")
        if ex["hook_text"] or ex["context_text"] or ex["ending_text"]:
            out.append(f"- 0 a 3s: “{cell(ex['hook_text']) or '-'}”")
            out.append(f"- 3 a 15s: “{cell(ex['context_text']) or '-'}”")
            out.append(f"- Final: “{cell(ex['ending_text']) or '-'}”")
        elif ex.get("transcript_status") == "bloqueado":
            out.append("- Fala não lida: legenda bloqueada temporariamente pelo YouTube.")
        else:
            out.append("- Sem fala na legenda (vídeo só com imagem/música, ou sem legenda automática).")
        out.append("")

    if a["molds"]:
        out += ["## Moldes de título", ""] + [f"- `{cell(m['mold'])}` ← {link(m['from'][:80], m['url'])}" for m in a["molds"]] + [""]

    out += ["## Referências", "", "| # | Short | Canal | Views | Views/dia | Views/inscritos | Duração | Score |", "|---|---|---|---|---|---|---|---|"]
    for i, p in enumerate(posts[:60], 1):
        m = p.metrics
        out.append(f"| {i} | {link(p.headline[:80], p.url)} | {link(p.author, p.author_url)} | {num(p.views)} | "
                   f"{num(m.get('views_per_day'))} | {str(m['outlier']) + 'x' if m.get('outlier') else '-'} | "
                   f"{_fmt_s(int(p.duration or 0))} | {m.get('score')} |")
    out += ["", "_Score = percentil de views/dia (45%), views/inscritos (30%) e engajamento (25%) entre os Shorts coletados._"]
    return "\n".join(out)


def retry_speech(search_id: int, progress=None) -> int:
    """Baixa de novo só as legendas que o YouTube bloqueou e refaz a análise, sem nova coleta."""
    progress = progress or (lambda m: None)
    saved = db.get_search(search_id)
    if not saved or saved["kind"] not in ("shorts", "short_xray"):
        raise ValueError("Estudo de Shorts não encontrado.")
    r, params = saved["result"], saved["params"]

    if saved["kind"] == "short_xray":
        fresh = run_xray(params["url"], params.get("benchmark_id"), False, progress=progress)
        fresh["ai"], fresh["ai_text"] = r.get("ai", {"used": False}), r.get("ai_text", "")
        db.update_search(search_id, fresh)
        return search_id

    posts = [Post.from_dict(d) for d in r["posts"]]
    pending = [p for p in posts if p.metrics.get("transcript_status") == "bloqueado"]
    if pending:
        # as URLs de legenda expiram, então o detalhe do vídeo é lido de novo
        progress(f"Relendo {len(pending)} Shorts sem fala")
        with ThreadPoolExecutor(max_workers=8) as pool:
            infos = list(pool.map(lambda p: youtube._full_info(f"https://www.youtube.com/watch?v={p.id}"), pending))
        ok = [(p, i) for p, i in zip(pending, infos) if i]
        youtube.load_transcripts([p for p, _ in ok], {p.id: i for p, i in ok}, progress)

    niche = params.get("niche", saved["query"])
    title = f"Shorts do canal: {niche}" if params.get("mode") == "channel" else f"Shorts do nicho: {niche}"
    progress(f"Analisando {len(posts)} Shorts")
    analysis = sa.analyze(posts, niche)
    posts.sort(key=lambda p: p.metrics.get("score", 0), reverse=True)
    warnings = [w for w in r.get("warnings", []) if "legenda" not in w.lower()]
    r.update(analysis=analysis, posts=[p.to_dict() for p in posts], warnings=warnings,
             report_md=build_study_report(title, params, analysis, posts, r.get("ai_text", ""), warnings))
    db.update_search(search_id, r)
    return search_id


# ================================================================== raio-X


def run_xray(url: str, benchmark_id: int | None = None, use_ai: bool = False, limit: int = 20, progress=None) -> dict:
    """Link de Short/vídeo: raio-X. Link de canal: estudo dos Shorts do canal."""
    progress = progress or (lambda m: None)
    if not youtube.VIDEO_RE.search(url):
        if youtube.CHANNEL_RE.search(url):
            return run_channel(url, limit, use_ai, progress)
        raise ValueError("Link não reconhecido. Use o link de um Short/vídeo ou de um canal do YouTube.")

    warnings: list[str] = []
    post, _ = youtube.single_short(url, progress)
    if not post:
        raise ValueError("Não foi possível ler esse vídeo (removido, privado ou com restrição de idade).")
    if not post.metrics.get("is_short"):
        warnings.append("Este vídeo não é um Short (é horizontal ou tem mais de 180s). A análise segue mesmo assim.")
    if post.metrics.get("transcript_status") == "bloqueado":
        warnings.append("O YouTube limitou temporariamente as legendas. A análise de fala ficou incompleta; tente de novo em alguns minutos.")

    metrics.compute([post])
    post.metrics.pop("score", None)  # score é relativo a um grupo; com 1 vídeo não faz sentido
    post.hooks = detect_hooks(post.headline)
    post.metrics["title"] = title_features(post.headline)
    f = sa.short_features(post)
    post.metrics["short"] = f
    phases = sa.timeline(post)

    channel = _channel_benchmark(post, progress)
    bench, bench_label = None, ""
    if benchmark_id:
        saved = db.get_search(int(benchmark_id))
        if saved and saved["kind"] == "shorts":
            bench = saved["result"]["analysis"].get("benchmark")
            bench_label = saved["query"]
        else:
            warnings.append("Estudo de comparação não encontrado ou não é um estudo de Shorts.")
    comparison = sa.compare_to_benchmark(f, post, bench)

    ai_text, ai_info = "", {"used": False}
    if use_ai:
        if not settings.ai_enabled:
            warnings.append("IA não usada: defina AI_API_KEY no .env.")
        else:
            progress("Gerando análise com IA (transcrição + métricas)")
            try:
                res = ai.complete(SYSTEM_PROMPT, build_xray_prompt(post, f, phases, channel, bench),
                                  cache_key=f"xray|{post.id}|{benchmark_id}|{date.today()}")
                ai_text, ai_info = res["text"], {"used": True, "cached": res["cached"], "usage": res["usage"]}
            except ai.AIError as exc:
                warnings.append(str(exc))

    params = {"mode": "single", "url": url, "benchmark_id": benchmark_id, "use_ai": use_ai}
    report = build_xray_report(post, f, phases, channel, comparison, bench_label, ai_text, warnings)
    return {"params": params, "warnings": warnings, "post": post.to_dict(), "features": f, "timeline": phases,
            "channel": channel, "comparison": comparison, "ai": ai_info, "ai_text": ai_text, "report_md": report,
            "title": post.headline}


def _channel_benchmark(post: Post, progress) -> dict | None:
    """Compara o Short com os Shorts recentes do mesmo canal."""
    if not post.author_url:
        return None
    try:
        others = [p for p in youtube.channel_shorts(post.author_url, 15, progress, with_transcript=False) if p.id != post.id]
    except Exception as exc:
        log.info("Benchmark do canal falhou: %s", exc)
        return None
    if not others:
        return None
    metrics.compute(others)
    views = [p.views for p in others if p.views is not None]
    vpd = [p.metrics["views_per_day"] for p in others if p.metrics.get("views_per_day")]
    med_views = median(views) if views else None
    med_vpd = median(vpd) if vpd else None
    best = max(others, key=lambda p: p.views or 0)
    return {
        "n": len(others),
        "median_views": med_views,
        "median_views_per_day": med_vpd,
        "views_ratio": round(post.views / med_views, 2) if post.views and med_views else None,
        "vpd_ratio": round(post.metrics["views_per_day"] / med_vpd, 2) if post.metrics.get("views_per_day") and med_vpd else None,
        "best": {"headline": best.headline, "url": best.url, "views": best.views},
    }


def build_xray_prompt(p: Post, f: dict, phases: list[dict], channel: dict | None, bench: dict | None) -> str:
    m = p.metrics
    lines = [
        f"Short: {p.headline} | canal {p.author} ({num(p.author_followers)} inscritos) | {num(p.views)} views | "
        f"{num(m.get('views_per_day'))} views/dia | duração {int(p.duration or 0)}s",
        f"Fala começa em {f.get('speech_start')}s; {f.get('wps')} palavras/s; CTA={f.get('cta')}; loop={f.get('loop')}; música={f.get('music')}",
    ]
    if channel and channel.get("views_ratio"):
        lines.append(f"Views = {channel['views_ratio']}x a mediana dos últimos {channel['n']} Shorts do canal.")
    if bench:
        lines.append(f"Virais do nicho: fala em {bench.get('speech_start')}s, {bench.get('wps')} palavras/s, duração {bench.get('duration')}s, "
                     f"ganchos: {', '.join(bench.get('top_opening_hooks') or [])}.")
    lines.append("Roteiro falado por fase:")
    budget = 700  # palavras: limita o custo mesmo em Shorts de 3 minutos
    for ph in phases:
        words = ph["text"].split()[:budget]
        budget -= len(words)
        lines.append(f"[{ph['phase']} {ph['start']}-{ph['end']}s] {' '.join(words) or '(sem fala)'}")
    lines += ["", "Entregue: ## Por que funciona (ou não) · ## Estrutura usada · ## Como replicar em outro nicho · ## 3 variações do gancho de 0-3s"]
    return "\n".join(lines)


def build_xray_report(p: Post, f: dict, phases, channel, comparison, bench_label, ai_text, warnings) -> str:
    m = p.metrics
    out = [f"# Raio-X do Short: {cell(p.headline)}", "",
           f"{link('Abrir no YouTube', p.url)} · Canal: {link(p.author, p.author_url)} ({num(p.author_followers)} inscritos) · "
           f"Publicado: {p.published_at:%d/%m/%Y}" if p.published_at else f"{link('Abrir no YouTube', p.url)}", ""]
    out += ["> " + w for w in warnings] + ([""] if warnings else [])

    eng = f"{m['engagement']:.1%}" if m.get("engagement") is not None else "-"
    out += ["## Números", "", "| Views | Views/dia | Views/inscritos | Likes | Comentários | Engajamento | Duração |", "|---|---|---|---|---|---|---|",
            f"| {num(p.views)} | {num(m.get('views_per_day'))} | {str(m['outlier']) + 'x' if m.get('outlier') else '-'} | "
            f"{num(p.likes)} | {num(p.comments)} | {eng} | {_fmt_s(int(p.duration or 0))} |", ""]

    if channel:
        lines = []
        if channel.get("views_ratio") is not None:
            lines.append(f"Este Short teve **{channel['views_ratio']}x** as views da mediana dos últimos {channel['n']} Shorts do canal ({num(channel['median_views'])}).")
        if channel.get("vpd_ratio") is not None:
            lines.append(f"Em views por dia, foi **{channel['vpd_ratio']}x** a mediana do canal.")
        lines.append(f"Short mais visto do canal na amostra: {link(channel['best']['headline'][:80], channel['best']['url'])} ({num(channel['best']['views'])}).")
        out += ["## Comparado com o próprio canal", ""] + [f"- {l}" for l in lines] + [""]

    out += ["## Fala e estrutura", ""]
    if f.get("has_speech"):
        opening = ", ".join(HOOKS_BY_KEY[k].label for k in f["opening_hooks"]) or "nenhum gancho conhecido detectado"
        out += [f"- Fala começa em **{f['speech_start']}s** · **{f['wps']} palavras/s** · fala em {_pct(f['coverage'])} do tempo · {f['n_words']} palavras",
                f"- Gancho de abertura: {opening}",
                f"- Final: {'CTA falado' if f['cta'] else 'sem CTA'} · {'pergunta' if f['question_end'] else 'sem pergunta'} · {'emenda no começo (loop)' if f['loop'] else 'sem loop'}",
                f"- Música marcada na legenda: {'sim' if f['music'] else 'não'}", ""]
        out += ["| Fase | Tempo | O que é dito |", "|---|---|---|"]
        out += [f"| {ph['phase']} | {ph['start']} a {ph['end']}s | {cell(ph['text'])[:400] or '-'} |" for ph in phases] + [""]
    elif f.get("transcript_status") == "bloqueado":
        out += ["A fala não foi lida: o YouTube bloqueou as legendas temporariamente. Use “Tentar ler a fala de novo” em alguns minutos.", ""]
    else:
        out += ["Sem fala na legenda: o Short depende de imagem, texto na tela ou música. A análise visual está planejada para uma próxima etapa.", ""]

    if p.hooks:
        out += ["## Título", "", "Ganchos no título: " + ", ".join(HOOKS_BY_KEY[k].label for k in p.hooks), ""]
    if comparison:
        out += [f"## Comparado com os virais do estudo “{cell(bench_label)}”", ""] + [f"- {c}" for c in comparison] + [""]
    if ai_text:
        out += ["## Análise da IA", "", ai_text.strip(), ""]
    if p.transcript:
        spoken = " ".join(w for _, w in p.transcript)
        out += ["## Transcrição completa", "", cell(spoken)[:6000], ""]
    return "\n".join(out)
