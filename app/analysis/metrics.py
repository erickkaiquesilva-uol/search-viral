"""Métricas de viralização, calculadas localmente.

- views_per_day: velocidade de crescimento (views / dias desde a publicação)
- engagement: (likes + comentários + compartilhamentos) / views
- outlier: views / seguidores do autor. Acima de 1 indica que o vídeo foi além da
  própria audiência, o sinal mais forte de que o algoritmo distribuiu o conteúdo.
- score (0 a 100): média ponderada dos percentis dessas métricas dentro da mesma
  plataforma, para comparar vídeos de canais grandes e pequenos de forma justa.
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, timezone

from ..collectors.models import Post

WEIGHTS = {"views_per_day": 0.45, "outlier": 0.30, "engagement": 0.25}


def _percentile_fn(values: list[float]):
    ordered = sorted(values)
    n = len(ordered)

    def pct(v: float) -> float:
        if n <= 1:
            return 0.5
        return bisect_left(ordered, v) / (n - 1)

    return pct


def compute(posts: list[Post]) -> None:
    now = datetime.now(timezone.utc)
    for p in posts:
        m = p.metrics
        days = None
        if p.published_at:
            days = max((now - p.published_at).total_seconds() / 86400, 0.5)
        m["age_days"] = round(days, 1) if days else None

        # No X a métrica principal de alcance pode faltar; usamos likes como aproximação
        reach = p.views if p.views else None
        interactions = sum(x or 0 for x in (p.likes, p.comments, p.shares))
        m["views_per_day"] = round(reach / days, 1) if reach and days else None
        m["engagement"] = round(interactions / reach, 4) if reach else None
        m["outlier"] = round(reach / p.author_followers, 2) if reach and p.author_followers else None
        m["interactions"] = interactions

    for platform in {p.platform for p in posts}:
        group = [p for p in posts if p.platform == platform]
        pct_fns = {}
        for key in WEIGHTS:
            vals = [p.metrics[key] for p in group if p.metrics.get(key) is not None]
            pct_fns[key] = _percentile_fn(vals) if vals else None
        # Sem views (comum em posts de imagem), cai para interações absolutas
        inter_pct = _percentile_fn([p.metrics["interactions"] for p in group])

        for p in group:
            total, weight = 0.0, 0.0
            for key, w in WEIGHTS.items():
                v = p.metrics.get(key)
                if v is not None and pct_fns[key]:
                    total += pct_fns[key](v) * w
                    weight += w
            if weight == 0:
                total, weight = inter_pct(p.metrics["interactions"]), 1.0
            p.metrics["score"] = round(100 * total / weight, 1)


def top_share(posts: list[Post], fraction: float = 0.25) -> tuple[list[Post], list[Post]]:
    """Separa o grupo 'viral' (melhor quartil por score) do restante."""
    ordered = sorted(posts, key=lambda p: p.metrics.get("score", 0), reverse=True)
    k = max(1, round(len(ordered) * fraction))
    return ordered[:k], ordered[k:]
