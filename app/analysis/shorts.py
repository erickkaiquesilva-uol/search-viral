"""Análise específica de Shorts, por regras (sem IA).

Num Short, quem decide se o vídeo continua sendo assistido são os primeiros
segundos e o ritmo. Por isso, além das métricas e ganchos de título, medimos
a fala segundo a segundo a partir da legenda:

- quando a fala começa e o que é dito de 0 a 3s (gancho) e de 3 a 15s;
- ritmo (palavras por segundo) e quanto do vídeo tem fala;
- como termina: CTA, pergunta, ou "loop" (o fim emenda no começo);
- duração, de 0 a 180s (limite atual dos Shorts).
"""

from __future__ import annotations

import re
from collections import Counter
from statistics import mean, median

from ..collectors.models import Post
from . import metrics
from .hooks import HOOKS_BY_KEY, classify_format, detect_hooks, title_features, title_mold
from .text import content_words

DURATION_BUCKETS = [(15, "Até 15s"), (30, "16 a 30s"), (60, "31 a 60s"), (90, "61 a 90s"), (180, "91 a 180s")]
TAG_RE = re.compile(r"^\[.*\]$")
MUSIC_RE = re.compile(r"m[úu]sica|music", re.IGNORECASE)
CTA_RE = re.compile(
    r"\b(segue|siga|me segue|inscrev\w*|ativa o sininho|comenta|comente|curte|curta|deixa (o|seu) like|compartilha\w*|"
    r"manda (pra|para)|salva|salve|link (na|da) bio|parte \d|parte dois|follow|subscribe|comment|share|like)\b",
    re.IGNORECASE,
)
QUESTION_END_RE = re.compile(r"\?|\b(qual|quais|o que voc[êe]s?|voc[êe]s? (acham|j[áa]|faria|fariam)|concorda|e a[íi])\b", re.IGNORECASE)


def _duration_bucket(d: float | None) -> str:
    if not d:
        return "Desconhecida"
    for limit, label in DURATION_BUCKETS:
        if d <= limit:
            return label
    return "Mais de 180s"


def _phases(duration: float) -> list[tuple[str, float, float]]:
    """Fases do roteiro, proporcionais à duração (um Short de 10s não tem 'contexto de 12s')."""
    d = max(duration or 0, 1)
    hook_end = min(3.0, d * 0.2)
    final_start = max(hook_end, d - min(5.0, d * 0.2))
    context_end = min(15.0, hook_end + (final_start - hook_end) * 0.35) if d > 20 else hook_end
    phases = [("Gancho", 0.0, hook_end)]
    if context_end > hook_end:
        phases.append(("Contexto", hook_end, context_end))
    if final_start > context_end:
        phases.append(("Desenvolvimento", context_end, final_start))
    phases.append(("Final", final_start, d + 1))
    return phases


def short_features(p: Post) -> dict:
    words = p.transcript or []
    spoken = [(t, w) for t, w in words if not TAG_RE.match(w)]
    duration = p.duration or (words[-1][0] + 1 if words else 0)
    f: dict = {
        "duration_bucket": _duration_bucket(p.duration),
        "has_transcript": bool(words),
        "transcript_status": p.metrics.get("transcript_status", "ok" if words else "sem_legenda"),
        "music": any(MUSIC_RE.search(w) for _, w in words if TAG_RE.match(w)),
        "has_speech": len(spoken) >= 3,
        "n_words": len(spoken),
    }
    if not f["has_speech"]:
        f.update({"speech_start": None, "wps": None, "coverage": None, "hook_text": "", "context_text": "",
                  "ending_text": "", "opening_hooks": [], "cta": False, "question_end": False, "loop": False,
                  "first_words": ""})
        return f

    first_t, last_t = spoken[0][0], spoken[-1][0]
    span = max(last_t - first_t + 0.6, 1.0)
    phases = {name: (a, b) for name, a, b in _phases(duration)}
    text_in = lambda a, b: " ".join(w for t, w in spoken if a <= t < b)  # noqa: E731

    hook_text = text_in(0, 3)
    # Se a fala começa depois de 3s, o "gancho falado" são as primeiras palavras
    hook_for_detection = hook_text or " ".join(w for _, w in spoken[:10])
    ending_a = phases["Final"][0]
    ending_text = text_in(ending_a, duration + 1)
    head = set(content_words(" ".join(w for _, w in spoken[:10])))
    tail = set(content_words(" ".join(w for _, w in spoken[-10:])))

    f.update({
        "speech_start": round(first_t, 1),
        "wps": round(len(spoken) / span, 2),
        "coverage": round(min(1.0, len({int(t) for t, _ in spoken}) / max(duration, 1)), 2),
        "hook_text": hook_text,
        "context_text": text_in(3, 15),
        "ending_text": ending_text,
        "opening_hooks": detect_hooks(hook_for_detection),
        "cta": bool(CTA_RE.search(ending_text)),
        "question_end": bool(QUESTION_END_RE.search(ending_text)),
        # "loop": o fim repete termos do começo, o que costuma fazer a pessoa assistir de novo
        "loop": len(head & tail) >= 2 and (len(head & tail) / max(1, len(head | tail))) >= 0.2,
        "first_words": " ".join(w.lower().strip(",.!?") for _, w in spoken[:2]),
    })
    return f


def timeline(p: Post) -> list[dict]:
    """Roteiro do Short dividido em fases, com o texto falado em cada uma."""
    words = [(t, w) for t, w in (p.transcript or []) if not TAG_RE.match(w)]
    out = []
    for name, a, b in _phases(p.duration or (words[-1][0] + 1 if words else 1)):
        out.append({"phase": name, "start": round(a, 1), "end": round(min(b, p.duration or b), 1),
                    "text": " ".join(w for t, w in words if a <= t < b)})
    return out


def _share(group, pred) -> float:
    return round(sum(1 for p in group if pred(p)) / len(group), 3) if group else 0.0


def _med(values) -> float | None:
    vals = [v for v in values if v is not None]
    return round(median(vals), 2) if vals else None


def analyze(posts: list[Post], niche: str) -> dict:
    if not posts:
        return {"summary": {"total": 0}, "insights": []}

    metrics.compute(posts)
    for p in posts:
        p.hooks = detect_hooks(p.headline)
        p.format = classify_format(p)
        p.metrics["title"] = title_features(p.headline)
        p.metrics["short"] = short_features(p)

    top, rest = metrics.top_share(posts)
    rest = rest or top
    S = lambda p: p.metrics["short"]  # noqa: E731
    with_speech_top = [p for p in top if S(p)["has_speech"]]
    with_speech_rest = [p for p in rest if S(p)["has_speech"]]

    buckets = []
    for _, label in DURATION_BUCKETS + [(0, "Desconhecida")]:
        group = [p for p in posts if S(p)["duration_bucket"] == label]
        if not group:
            continue
        buckets.append({
            "bucket": label, "count": len(group),
            "share_top": _share(top, lambda p, l=label: S(p)["duration_bucket"] == l),
            "avg_score": round(mean(p.metrics["score"] for p in group), 1),
            "median_views_per_day": _med(p.metrics.get("views_per_day") for p in group) or 0,
        })

    def hook_table(get_keys):
        rows = []
        for key, h in HOOKS_BY_KEY.items():
            st = _share(top, lambda p: key in get_keys(p))
            sr = _share(rest, lambda p: key in get_keys(p))
            n = sum(1 for p in posts if key in get_keys(p))
            if n:
                best = max((p for p in posts if key in get_keys(p)), key=lambda p: p.metrics["score"])
                rows.append({"key": key, "label": h.label, "why": h.why, "count": n, "share_top": st, "share_rest": sr,
                             "lift": round(st / sr, 2) if sr else (None if st == 0 else 99.0),
                             "example": {"headline": best.headline, "url": best.url, "hook_text": S(best).get("hook_text", "")},
                             "templates": h.templates})
        rows.sort(key=lambda r: (r["share_top"] * min(r["lift"] or 0, 5), r["count"]), reverse=True)
        return rows

    speech = {
        "share_speech_top": _share(top, lambda p: S(p)["has_speech"]),
        "share_speech_rest": _share(rest, lambda p: S(p)["has_speech"]),
        "share_music_top": _share(top, lambda p: S(p)["music"]),
        "share_music_rest": _share(rest, lambda p: S(p)["music"]),
        "wps_top": _med(S(p)["wps"] for p in with_speech_top),
        "wps_rest": _med(S(p)["wps"] for p in with_speech_rest),
        "start_top": _med(S(p)["speech_start"] for p in with_speech_top),
        "start_rest": _med(S(p)["speech_start"] for p in with_speech_rest),
        "coverage_top": _med(S(p)["coverage"] for p in with_speech_top),
        "coverage_rest": _med(S(p)["coverage"] for p in with_speech_rest),
    }
    ending = {
        k: {"top": _share(with_speech_top, lambda p, k=k: S(p)[k]), "rest": _share(with_speech_rest, lambda p, k=k: S(p)[k])}
        for k in ("cta", "question_end", "loop")
    }
    first_words = Counter(S(p)["first_words"] for p in with_speech_top if S(p)["first_words"]).most_common(6)

    status = Counter(S(p)["transcript_status"] for p in posts)
    examples = [
        {"headline": p.headline, "url": p.url, "score": p.metrics["score"], "views": p.views, "duration": p.duration,
         "speech_start": S(p)["speech_start"], "wps": S(p)["wps"], "hook_text": S(p)["hook_text"],
         "context_text": S(p)["context_text"][:220], "ending_text": S(p)["ending_text"][:160],
         "transcript_status": S(p)["transcript_status"],
         "opening_hooks": [HOOKS_BY_KEY[k].label for k in S(p)["opening_hooks"]]}
        for p in sorted(posts, key=lambda p: p.metrics["score"], reverse=True)[:10]
    ]
    molds = [{"mold": m, "from": p.headline, "url": p.url} for p in top[:10]
             if p.headline and (m := title_mold(p.headline, niche)) != p.headline]

    result = {
        "summary": {
            "total": len(posts), "viral_group": len(top),
            "with_transcript": status.get("ok", 0), "transcript_blocked": status.get("bloqueado", 0),
            "no_captions": status.get("sem_legenda", 0),
            "median_views_top": _med(p.views for p in top) or 0,
            "median_duration_top": _med(p.duration for p in top),
            "median_outlier_top": _med(p.metrics.get("outlier") for p in top),
        },
        "durations": buckets,
        "speech": speech,
        "ending": ending,
        "opening_hooks": hook_table(lambda p: S(p)["opening_hooks"]),
        "title_hooks": hook_table(lambda p: p.hooks),
        "first_words": first_words,
        "examples": examples,
        "molds": molds,
    }
    result["benchmark"] = benchmark_from(result)
    result["recipe"] = build_recipe(result)
    result["insights"] = build_insights(result)
    return result


def benchmark_from(a: dict) -> dict:
    """Números de referência do grupo viral, usados para comparar um Short no raio-X."""
    best_bucket = next((b for b in sorted(a["durations"], key=lambda b: b["avg_score"], reverse=True) if b["count"] >= 3), None)
    return {
        "wps": a["speech"]["wps_top"], "speech_start": a["speech"]["start_top"], "coverage": a["speech"]["coverage_top"],
        "duration": a["summary"]["median_duration_top"], "best_bucket": best_bucket["bucket"] if best_bucket else None,
        "top_opening_hooks": [h["label"] for h in a["opening_hooks"][:3] if h["share_top"] > 0],
        "cta_top": a["ending"]["cta"]["top"], "loop_top": a["ending"]["loop"]["top"],
    }


def build_recipe(a: dict) -> list[str]:
    """A 'receita' do Short viral no nicho, passo a passo pelo tempo do vídeo."""
    b, s = a["benchmark"], a["speech"]
    steps = []
    if a["summary"].get("with_transcript"):
        hooks = ", ".join(b["top_opening_hooks"]) or "frase direta sobre o benefício"
        start = f"começando a falar em até {b['speech_start']}s" if b["speech_start"] is not None else "sem fala (imagem e música carregam o vídeo)"
        steps.append(f"**0 a 3s (gancho):** {start}, usando {hooks}.")
    else:
        steps.append("**0 a 3s (gancho):** sem dados de fala nesta coleta; veja os ganchos de título abaixo.")
    if b["wps"]:
        steps.append(f"**Ritmo:** cerca de {b['wps']} palavras por segundo, com fala em {int((b['coverage'] or 0) * 100)}% do tempo.")
    if b["best_bucket"] or b["duration"]:
        parts = [b["best_bucket"]] if b["best_bucket"] else []
        if b["duration"]:
            parts.append(f"mediana dos virais: {int(b['duration'])}s")
        steps.append("**Duração:** " + "; ".join(parts) + ".")
    if not a["summary"].get("with_transcript"):
        return steps
    end = []
    if b["cta_top"] >= 0.3:
        end.append("CTA falado (seguir/comentar/salvar)")
    if b["loop_top"] >= 0.2:
        end.append("final que emenda no começo (loop)")
    if a["ending"]["question_end"]["top"] >= 0.25:
        end.append("pergunta para gerar comentário")
    steps.append("**Final:** " + (", ".join(end) if end else "termina logo após entregar o resultado, sem enrolar") + ".")
    if s["share_music_top"] >= 0.3:
        steps.append(f"**Áudio:** {int(s['share_music_top'] * 100)}% dos virais têm música marcada na legenda.")
    return steps


def _pct(x) -> str:
    return f"{(x or 0) * 100:.0f}%"


def build_insights(a: dict) -> list[str]:
    out: list[str] = []
    s, e, summ = a["speech"], a["ending"], a["summary"]

    if summ["transcript_blocked"]:
        out.append(f"⚠️ {summ['transcript_blocked']} Shorts ficaram sem análise de fala porque o YouTube limitou temporariamente as legendas. Rode de novo em alguns minutos (as legendas já baixadas ficam em cache).")

    for h in [h for h in a["opening_hooks"] if h["share_top"] >= 0.25 and (h["lift"] or 0) >= 1.2][:3]:
        lift = "só aparece nos virais" if h["lift"] == 99.0 else f"{h['lift']:.1f}x mais que no restante"
        out.append(f"Nos primeiros 3s, use **{h['label']}**: {_pct(h['share_top'])} dos virais abrem assim ({lift}).")

    if s["start_top"] is not None and s["start_rest"] is not None and s["start_top"] < s["start_rest"]:
        out.append(f"Os virais começam a falar em **{s['start_top']}s** (mediana), contra {s['start_rest']}s no restante. Não abra com silêncio.")
    if s["wps_top"] and s["wps_rest"]:
        faster = "mais rápido" if s["wps_top"] > s["wps_rest"] else "mais devagar"
        out.append(f"Ritmo dos virais: **{s['wps_top']} palavras/s** ({faster} que o restante, {s['wps_rest']}).")
    if s["share_speech_top"] - s["share_speech_rest"] >= 0.15:
        out.append(f"Shorts com fala dominam: {_pct(s['share_speech_top'])} dos virais têm narração contra {_pct(s['share_speech_rest'])} do restante.")
    elif s["share_speech_rest"] - s["share_speech_top"] >= 0.15:
        out.append(f"Neste nicho, vídeos **sem fala** (só imagem/música) performam bem: {_pct(1 - s['share_speech_top'])} dos virais não têm narração.")

    best = [b for b in a["durations"] if b["count"] >= 3]
    if best:
        b = max(best, key=lambda b: b["avg_score"])
        out.append(f"Duração com melhor desempenho: **{b['bucket']}** (score médio {b['avg_score']}, {b['count']} Shorts).")
    for key, label in (("cta", "CTA no final"), ("loop", "final em loop"), ("question_end", "pergunta no final")):
        t, r = e[key]["top"], e[key]["rest"]
        if t - r >= 0.15:
            out.append(f"**{label}** aparece em {_pct(t)} dos virais contra {_pct(r)} do restante.")
    if a["first_words"]:
        out.append("Primeiras palavras mais comuns nos virais: " + ", ".join(f"“{w}” ({n})" for w, n in a["first_words"][:4]) + ".")
    th = [h for h in a["title_hooks"] if h["share_top"] >= 0.25][:2]
    if th:
        out.append("No título, os ganchos mais presentes nos virais são: " + ", ".join(f"{h['label']} ({_pct(h['share_top'])})" for h in th) + ".")
    if summ.get("median_outlier_top"):
        out.append(f"Os Shorts virais tiveram mediana de **{summ['median_outlier_top']:.1f}x** mais views do que inscritos no canal.")
    return out


def compare_to_benchmark(f: dict, p: Post, bench: dict | None) -> list[str]:
    """Frases do raio-X: o que este Short faz igual ou diferente dos virais do nicho."""
    if not bench:
        return []
    out = []
    if f.get("speech_start") is not None and bench.get("speech_start") is not None:
        diff = f["speech_start"] - bench["speech_start"]
        out.append(f"Começa a falar em {f['speech_start']}s (virais do nicho: {bench['speech_start']}s)" + (" ✅" if diff <= 0.5 else ". Comece mais cedo."))
    if f.get("wps") and bench.get("wps"):
        ratio = f["wps"] / bench["wps"]
        verdict = "✅" if 0.85 <= ratio <= 1.15 else ("mais rápido que os virais" if ratio > 1 else "mais lento que os virais")
        out.append(f"Ritmo de {f['wps']} palavras/s (virais: {bench['wps']}): {verdict}.")
    if p.duration and bench.get("duration"):
        out.append(f"Duração de {int(p.duration)}s (mediana dos virais: {int(bench['duration'])}s; faixa que mais performa: {bench.get('best_bucket') or '-'}).")
    if bench.get("top_opening_hooks"):
        mine = {HOOKS_BY_KEY[k].label for k in f.get("opening_hooks", [])}
        common = mine & set(bench["top_opening_hooks"])
        out.append("Gancho de abertura alinhado com os virais ✅ (" + ", ".join(common) + ")." if common
                   else "Os virais do nicho abrem com: " + ", ".join(bench["top_opening_hooks"]) + ". Este Short não usa nenhum deles.")
    if bench.get("cta_top", 0) >= 0.3 and not f.get("cta"):
        out.append(f"{_pct(bench['cta_top'])} dos virais fecham com CTA falado; este não tem.")
    return out
