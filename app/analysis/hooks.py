"""Detecção de ganchos e formatos, e o estudo de padrões, tudo por regras (sem IA).

A ideia: comparar o grupo "viral" (melhor quartil por score) com o restante.
Um gancho que aparece muito mais nos virais do que no resto (lift alto) é um
padrão que vale copiar naquele nicho.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from statistics import mean, median
from zoneinfo import ZoneInfo

from ..collectors.models import Post
from ..config import settings
from . import metrics
from .text import content_words, keywords, strip_accents

try:
    LOCAL_TZ = ZoneInfo(settings.app_timezone)
except Exception:  # fuso inválido no .env
    LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
WEEKDAYS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


@dataclass
class Hook:
    key: str
    label: str
    why: str
    patterns: list[str]
    templates: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.regex = re.compile("|".join(f"(?:{p})" for p in self.patterns), re.IGNORECASE | re.UNICODE)

    def match(self, text: str) -> bool:
        return bool(self.regex.search(text or ""))


HOOKS: list[Hook] = [
    Hook(
        "lista", "Lista numerada",
        "Promete um conteúdo com começo, meio e fim definidos. O número cria expectativa de completude e retém até o último item.",
        [r"\b\d+\s+(coisas|dicas|formas|maneiras|erros|passos|motivos|raz[õo]es|n[íi]veis|h[áa]bitos|segredos|sinais|regras|ideias|truques|li[çc][õo]es|ways|tips|things|mistakes|reasons|steps|habits|signs|levels)\b",
         r"^\s*\d{1,2}\s", r"\btop\s*\d+", r"\bos\s+\d+\b"],
        ["{N} erros que {público} comete com {tema} (o #{N} é o pior)", "Os {N} níveis de {tema}: em qual você está?", "{N} coisas sobre {tema} que eu queria saber antes"],
    ),
    Hook(
        "pergunta", "Pergunta / lacuna de curiosidade",
        "Abre uma pergunta que só se fecha assistindo. O cérebro tende a buscar a resposta (efeito Zeigarnik).",
        [r"\?", r"^\s*(por\s?que|porque|como|qual|quais|quanto|ser[áa]|o que|onde|why|how|what|which)\b"],
        ["Por que {grupo} consegue {resultado} e você não?", "O que acontece se você {ação} por {tempo}?", "Quanto custa {coisa} de verdade?"],
    ),
    Hook(
        "segredo", "Segredo / informação oculta",
        "Sugere acesso a algo que poucos sabem. Gera sensação de vantagem e exclusividade.",
        [r"segredo", r"ningu[ée]m\s+(te\s+)?(conta|fala|sabe|mostra)", r"n[ãa]o\s+te\s+(contam|falam|mostram)", r"\bverdade\b",
         r"revelad|revelei|escond", r"em sil[êe]ncio", r"\bsecret", r"nobody", r"\btruth\b", r"hidden"],
        ["O que ninguém te conta sobre {tema}", "O segredo de {referência} para {resultado}", "A verdade sobre {tema} que {grupo} esconde"],
    ),
    Hook(
        "alerta", "Alerta / aversão à perda",
        "Pessoas reagem mais a evitar uma perda do que a obter um ganho. Avisos e erros param a rolagem.",
        [r"\b(pare|parei|nunca|evite|erro|erros|errado|cuidado|perigo|golpe|armadilha|n[ãa]o\s+fa[çc]a|n[ãa]o\s+espere|n[ãa]o\s+compre)\b",
         r"\b(stop|never|don't|mistake|avoid|warning)\b"],
        ["PARE de {ação comum} (faça isso no lugar)", "Nunca {ação} antes de assistir isso", "O erro de {tema} que está te fazendo {perda}"],
    ),
    Hook(
        "experimento", "Experimento / desafio",
        "Mostra um teste real com resultado incerto. O espectador fica para ver o desfecho.",
        [r"\b(testei|tentei|experimentei|experimento|desafio)\b", r"\bpor\s+\d+\s+(dias|semanas|meses)", r"\b\d+\s+dias\b",
         r"\bi\s+tried\b", r"\btested\b", r"challenge", r"for\s+\d+\s+days"],
        ["Testei {método} por {N} dias e isso aconteceu", "Fiz {desafio} por {N} semanas: resultado real"],
    ),
    Hook(
        "transformacao", "Transformação / resultado",
        "Mostra o caminho de um estado A para um estado B. Vende a possibilidade de o espectador chegar lá também.",
        [r"antes e depois", r"\bde\s+[\w$]+.{0,20}\b(a|para|pra)\s+[\w$]+", r"\b(sa[íi] d[aeo]|consegui|ganhei|perdi|mudou minha vida|virei)\b",
         r"\bem\s+\d+\s+(dias|meses|anos)\b", r"\bo que eu fiz\b", r"\bhow i\b", r"before and after"],
        ["Como eu saí de {estado ruim} para {estado bom} em {tempo}", "O que eu fiz em {tempo} para {resultado}"],
    ),
    Hook(
        "tutorial", "Tutorial / como fazer",
        "Promessa clara de utilidade prática. Funciona bem para busca e para salvar o vídeo.",
        [r"\bcomo\s+(fazer|criar|ganhar|montar|come[çc]ar|aprender|ter|ficar|sair|juntar|usar|construir|se)\b", r"passo a passo",
         r"\btutorial\b", r"\bguia\b", r"\baprenda\b", r"how to", r"step by step", r"\bguide\b"],
        ["Como {resultado} mesmo {obstáculo} (passo a passo)", "Guia definitivo de {tema} para iniciantes"],
    ),
    Hook(
        "numero_concreto", "Número concreto / valor",
        "Valores e números específicos dão credibilidade e tornam a promessa tangível.",
        [r"R\$\s?\d", r"US?\$\s?\d", r"\b\d+[.,]?\d*\s?(mil|k|milh[õo]es|reais|d[óo]lares|%)", r"\bsal[áa]rio\b"],
        ["Como {resultado} ganhando R$ {valor}", "{N}% das pessoas não sabem disso sobre {tema}"],
    ),
    Hook(
        "identificacao", "Identificação com o público",
        "Chama um grupo específico ('quem ganha menos de...', 'se você...'). Quem se reconhece sente que o vídeo é para ele.",
        [r"\bpov\b", r"\b(quando|se)\s+voc[êe]\b", r"\bvoc[êe]\s+(que|[ée]|est[áa]|tem|precisa)\b", r"\bquem\s+(ganha|tem|[ée]|nunca|sempre|quer)\b",
         r"\bpara quem\b", r"\bpra quem\b", r"\bif you\b", r"\bwhen you\b"],
        ["Se você {situação do público}, assista isso", "Quem {característica do público} sempre faz essas {N} coisas"],
    ),
    Hook(
        "novidade", "Novidade / urgência",
        "Datas, 'agora' e 'finalmente' sinalizam informação nova e criam medo de ficar para trás.",
        [r"\b(novo|nova|novidade|agora|finalmente|acabou|chegou|urgente|[úu]ltima chance|antes que)\b", r"\b20[2-3]\d\b", r"\b(new|now|finally|breaking)\b"],
        ["{Novidade} chegou: o que muda para {público} em {ano}", "Faça isso antes de {data/evento}"],
    ),
    Hook(
        "comparacao", "Comparação / versus",
        "Coloca duas opções em conflito. O espectador quer saber qual vence e geralmente já tem um lado.",
        [r"\bvs\.?\b", r"\bversus\b", r"\w+\s+x\s+\w+", r"melhor (que|do que)", r"diferen[çc]a entre", r"\bcompar"],
        ["{Opção A} vs {Opção B}: qual vale mais a pena?", "Fiz {A} e {B} ao mesmo tempo: a diferença"],
    ),
    Hook(
        "facilidade", "Facilidade / baixo esforço",
        "Reduz a barreira para agir ('fácil', 'em 5 minutos', 'só 3 ingredientes'). O espectador sente que consegue fazer também.",
        [r"\b(f[áa]cil|f[áa]ceis|r[áa]pido|r[áa]pida|simples|pr[áa]tico|pr[áa]tica|sem esfor[çc]o)\b", r"\b(apenas|s[óo])\s+\d+\b",
         r"\bem\s+\d+\s+(min|minutos|segundos|passos)\b", r"\b(easy|quick|simple)\b"],
        ["{Resultado} em {N} minutos (sem {obstáculo})", "Só {N} {itens} para {resultado}"],
    ),
    Hook(
        "eliminacao", "Benefício por eliminação (sem X / zero X)",
        "Promete o resultado tirando o que o público quer evitar (sem açúcar, sem investir, zero farinha). Resolve a objeção já no título.",
        [r"\bsem\s+(a[çc][úu]car|farinha|gl[úu]ten|leite|lactose|forno|ovo|investir|investimento|dinheiro|aparecer|academia|dieta|gastar|experi[êe]ncia)\b",
         r"\bzero\s+\w+", r"\blow\s?carb\b", r"\bsem\s+\w+\s+e\s+sem\b", r"\bwithout\b", r"\bno\s+\w+\s+needed\b"],
        ["{Resultado} sem {coisa que o público odeia}", "{Tema} zero {restrição}: {benefício}"],
    ),
    Hook(
        "superlativo", "Superlativo / promessa forte",
        "'Melhor', 'perfeito', 'definitivo' elevam a expectativa e passam autoridade.",
        [r"\b(melhor|melhores|maior|[úu]nico|definitivo|perfeit[oa]|incr[íi]vel|irresist[íi]vel|delicios[oa]|mais (r[áa]pido|f[áa]cil|barato|simples))\b",
         r"\b(best|ultimate|only|easiest|fastest|perfect)\b"],
        ["A forma mais simples de {resultado}", "O único {método} que funcionou para mim"],
    ),
    Hook(
        "historia", "História / storytelling",
        "Narrativas em primeira pessoa geram empatia e retenção, porque queremos saber como a história termina.",
        [r"\bhist[óo]ria\b", r"\baconteceu\b", r"\b(eu|minha|meu)\s+\w+", r"\bstory(time)?\b", r"\baos\s+\d+\s+anos\b"],
        ["Aos {idade} anos eu {acontecimento}. Isso mudou tudo", "A história de como {pessoa} {resultado}"],
    ),
    Hook(
        "contrario", "Opinião contrária / quebra de crença",
        "Contradiz o senso comum do nicho. Gera debate nos comentários, e comentário é sinal forte para o algoritmo.",
        [r"\bmito\b", r"\bmentira\b", r"\besque[çc]a\b", r"na verdade", r"n[ãa]o [ée] (sobre|o que)", r"ao contr[áa]rio", r"\bmyth\b", r"\blie\b", r"\bactually\b"],
        ["{Crença comum} é mentira. Aqui está o porquê", "Esqueça {conselho popular}: faça isso"],
    ),
    Hook(
        "choque", "Choque / emoção intensa",
        "Palavras de alta emoção ('absurdo', 'insano') aumentam a taxa de clique e o compartilhamento.",
        [r"\b(chocante|absurdo|insano|inacredit[áa]vel|bizarro|surreal|ningu[ée]m acredita|treta|pol[êe]mic\w*|reagi|reagindo)\b",
         r"\b(shocking|crazy|insane|unbelievable)\b"],
        ["Isso que {pessoa/marca} fez é absurdo", "Ninguém acreditou quando eu {ação}"],
    ),
]

HOOKS_BY_KEY = {h.key: h for h in HOOKS}


def detect_hooks(text: str) -> list[str]:
    return [h.key for h in HOOKS if h.match(text)]


def classify_format(p: Post) -> str:
    if not p.is_video:
        return "Imagem / texto"
    d = p.duration
    if d is None:
        return "Vídeo (duração desconhecida)"
    if d <= 60:
        return "Vertical curto (até 60s)"
    if d <= 180:
        return "Curto (1 a 3 min)"
    if d <= 600:
        return "Médio (3 a 10 min)"
    if d <= 1200:
        return "Longo (10 a 20 min)"
    return "Muito longo (20+ min)"


def title_features(title: str) -> dict:
    words = re.findall(r"\w+", title)
    caps = [w for w in words if len(w) >= 3 and w.isupper() and not w.isdigit()]
    return {
        "length": len(title),
        "caps": len(caps) >= 1,
        "emoji": bool(EMOJI_RE.search(title)),
        "brackets": bool(re.search(r"[\(\[]", title)),
        "separator": bool(re.search(r"\s[—\-|:]\s|:\s", title)),
    }


def title_mold(title: str, niche: str) -> str:
    """Transforma um título real num molde reutilizável."""
    mold = re.sub(r"R\$\s?[\d.,]+\s?(mil|k)?", "R$ {VALOR}", title, flags=re.IGNORECASE)
    mold = re.sub(r"\b\d+[.,]?\d*\b", "{N}", mold)
    for w in sorted(set(content_words(niche)), key=len, reverse=True):
        mold = re.sub(re.escape(w), "{NICHO}", mold, flags=re.IGNORECASE)
    return mold


def _share(group: list[Post], pred) -> float:
    return sum(1 for p in group if pred(p)) / len(group) if group else 0.0


def _example(p: Post) -> dict:
    return {
        "headline": p.headline,
        "url": p.url,
        "platform": p.platform,
        "views": p.views,
        "score": p.metrics.get("score"),
    }


def analyze(posts: list[Post], niche: str) -> dict:
    """Roda métricas, ganchos e formatos e devolve o estudo de padrões."""
    if not posts:
        return {"insights": [], "hooks": [], "formats": [], "summary": {}}

    metrics.compute(posts)
    for p in posts:
        p.hooks = detect_hooks(p.headline)
        p.metrics["opening_hooks"] = detect_hooks(p.opening) if p.opening else []
        p.format = classify_format(p)
        p.metrics["title"] = title_features(p.headline)

    top, rest = metrics.top_share(posts)
    rest = rest or top

    # --- ganchos
    hook_rows = []
    for h in HOOKS:
        has = lambda p, k=h.key: k in p.hooks  # noqa: E731
        st, sr = _share(top, has), _share(rest, has)
        n_all = sum(1 for p in posts if has(p))
        if n_all == 0:
            continue
        matching = sorted((p for p in posts if has(p)), key=lambda p: p.metrics["score"], reverse=True)
        hook_rows.append({
            "key": h.key,
            "label": h.label,
            "why": h.why,
            "count": n_all,
            "share_top": round(st, 3),
            "share_rest": round(sr, 3),
            "lift": round(st / sr, 2) if sr else (None if st == 0 else 99.0),
            "avg_score": round(mean(p.metrics["score"] for p in matching), 1),
            "examples": [_example(p) for p in matching[:3]],
            "templates": h.templates,
        })
    hook_rows.sort(key=lambda r: (r["share_top"] * min(r["lift"] or 0, 5), r["avg_score"]), reverse=True)

    # --- formatos
    fmt_rows = []
    for fmt in sorted({p.format for p in posts}):
        group = [p for p in posts if p.format == fmt]
        fmt_rows.append({
            "format": fmt,
            "count": len(group),
            "share_top": round(_share(top, lambda p, f=fmt: p.format == f), 3),
            "avg_score": round(mean(p.metrics["score"] for p in group), 1),
            "median_views_per_day": median([p.metrics["views_per_day"] for p in group if p.metrics.get("views_per_day")] or [0]),
        })
    fmt_rows.sort(key=lambda r: r["avg_score"], reverse=True)

    # --- títulos
    def feat_share(group, key):
        return round(_share(group, lambda p: p.metrics["title"][key]), 3)

    title_stats = {
        "avg_len_top": round(mean(p.metrics["title"]["length"] for p in top)),
        "avg_len_rest": round(mean(p.metrics["title"]["length"] for p in rest)),
        **{f"{k}_top": feat_share(top, k) for k in ("caps", "emoji", "brackets", "separator")},
        **{f"{k}_rest": feat_share(rest, k) for k in ("caps", "emoji", "brackets", "separator")},
    }

    # --- termos e hashtags dos virais
    niche_words = set(content_words(niche)) | {strip_accents(niche.lower()).replace(" ", "")}
    top_terms = keywords([p.headline for p in top], top=15, exclude=niche_words)
    tag_counter = Counter(t for p in top for t in p.hashtags if strip_accents(t) not in niche_words)
    top_hashtags = tag_counter.most_common(15)

    # --- aberturas faladas (primeiros segundos)
    openings = [
        {"headline": p.headline, "url": p.url, "opening": p.opening, "score": p.metrics["score"],
         "hooks": [HOOKS_BY_KEY[k].label for k in p.metrics["opening_hooks"]]}
        for p in sorted(posts, key=lambda p: p.metrics["score"], reverse=True) if p.opening
    ][:8]
    opening_hook_counts = Counter(k for o in posts for k in o.metrics.get("opening_hooks", []))

    # --- quando publicar
    days = Counter(WEEKDAYS[p.published_at.astimezone(LOCAL_TZ).weekday()] for p in top if p.published_at)
    hours = Counter(
        f"{p.published_at.astimezone(LOCAL_TZ).hour}h"
        for p in top
        if p.published_at and p.metrics.get("has_time", True)
    )

    # só interessa o molde quando ele generaliza algo (número, valor ou termo do nicho)
    molds = [
        {"mold": m, "from": p.headline, "url": p.url}
        for p in top[:10]
        if p.headline and (m := title_mold(p.headline, niche)) != p.headline
    ]

    summary = {
        "total": len(posts),
        "viral_group": len(top),
        "by_platform": dict(Counter(p.platform for p in posts)),
        "median_views_top": median([p.views for p in top if p.views] or [0]),
        "median_views_rest": median([p.views for p in rest if p.views] or [0]),
        "median_outlier_top": median([p.metrics["outlier"] for p in top if p.metrics.get("outlier")] or [0]),
        "median_engagement_top": median([p.metrics["engagement"] for p in top if p.metrics.get("engagement")] or [0]),
    }

    result = {
        "summary": summary,
        "hooks": hook_rows,
        "formats": fmt_rows,
        "titles": title_stats,
        "top_terms": top_terms,
        "top_hashtags": top_hashtags,
        "openings": openings,
        "opening_hooks": [(HOOKS_BY_KEY[k].label, n) for k, n in opening_hook_counts.most_common()],
        "best_days": days.most_common(3),
        "best_hours": hours.most_common(3),
        "molds": molds,
    }
    result["insights"] = build_insights(result)
    return result


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def build_insights(a: dict) -> list[str]:
    """Regras que viram frases do tipo 'siga esse padrão e use esses ganchos'."""
    out: list[str] = []
    strong = [h for h in a["hooks"] if h["share_top"] >= 0.25 and (h["lift"] or 0) >= 1.2][:4]
    for h in strong:
        lift = "presente só nos virais" if h["lift"] == 99.0 else f"{h['lift']:.1f}x mais comum que no restante"
        out.append(f"Use o gancho **{h['label']}**: aparece em {_pct(h['share_top'])} dos virais ({lift}). {h['why']}")
    if not strong and a["hooks"]:
        h = a["hooks"][0]
        out.append(f"O gancho mais frequente entre os virais é **{h['label']}** ({_pct(h['share_top'])}). {h['why']}")

    weak = [h for h in a["hooks"] if h["count"] >= 3 and h["lift"] is not None and h["lift"] < 0.7]
    for h in weak[:2]:
        out.append(f"Evite depender de **{h['label']}**: é comum no nicho, mas aparece menos nos virais ({_pct(h['share_top'])} contra {_pct(h['share_rest'])}).")

    if a["formats"]:
        # evita eleger um formato com 1 ou 2 vídeos só por sorte
        f = next((f for f in a["formats"] if f["count"] >= 3), a["formats"][0])
        out.append(f"Formato com melhor desempenho: **{f['format']}** (score médio {f['avg_score']}, {f['count']} vídeos).")

    t = a["titles"]
    out.append(f"Títulos virais têm em média **{t['avg_len_top']} caracteres** (restante: {t['avg_len_rest']}).")
    if t["caps_top"] - t["caps_rest"] >= 0.15:
        out.append(f"Palavras em CAIXA ALTA aparecem em {_pct(t['caps_top'])} dos títulos virais contra {_pct(t['caps_rest'])} do restante. Destaque 1 ou 2 palavras.")
    if t["brackets_top"] - t["brackets_rest"] >= 0.15:
        out.append(f"Complemento entre parênteses, por exemplo '(não faça isso)', aparece em {_pct(t['brackets_top'])} dos virais.")
    if t["separator_top"] - t["separator_rest"] >= 0.15:
        out.append("Títulos virais usam estrutura em duas partes (promessa + complemento, separados por ':' ou '—').")

    if a["opening_hooks"]:
        labels = ", ".join(f"{l} ({n})" for l, n in a["opening_hooks"][:3])
        out.append(f"Nos primeiros 15 segundos falados, os ganchos mais usados foram: {labels}.")
    if a["top_terms"]:
        out.append("Termos recorrentes nos títulos virais: " + ", ".join(f"'{t}'" for t, _ in a["top_terms"][:8]) + ".")
    if a["best_days"]:
        out.append("Dias de publicação mais comuns entre os virais: " + ", ".join(d for d, _ in a["best_days"]) + ".")
    if a["best_hours"]:
        out.append(f"Horários de publicação mais comuns ({settings.app_timezone}): " + ", ".join(h for h, _ in a["best_hours"]) + ".")
    s = a["summary"]
    if s.get("median_outlier_top"):
        out.append(f"Os virais tiveram mediana de **{s['median_outlier_top']:.1f}x** mais views do que o número de seguidores do autor. Ou seja, o algoritmo levou o vídeo para fora da base.")
    return out
