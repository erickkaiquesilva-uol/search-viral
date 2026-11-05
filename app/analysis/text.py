"""Processamento de texto local (sem IA): palavras-chave e resumo extrativo."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

STOPWORDS = set(
    """
a à às ao aos as o os um uma uns umas de do da dos das no na nos nas em por para pra pro com sem sob sobre
entre até após e ou mas que se como quando onde porque por que qual quais quem cujo é são foi era ser estar
está estão tem têm ter há seu sua seus suas meu minha meus minhas teu tua nosso nossa eles elas ele ela eu
tu você vocês nós isso isto aquilo esse essa esses essas este esta estes estas aquele aquela mais menos muito
muita muitos muitas já não sim também só ainda todo toda todos todas outro outra outros outras mesmo mesma
pelo pela pelos pelas num numa lhe lhes me te nos vos foi ser sido sendo faz fazer vai vão vou pode podem
cada qualquer tudo nada algo alguém ninguém então assim aqui ali lá agora hoje vez vezes ano anos dia dias
the a an of to in on for with and or but is are was were be been it this that these those you your we our
they their he she his her i my me at by from as not no yes do does did have has had will would can could
how what why who when where which so if than then there here just about into out up down more most all any
video vídeo videos vídeos shorts short canal inscreva like link bio www http https com br
além novo nova novos novas maior menor uso forma formas sobre através partir exemplo sempre bem deve devem
precisa precisam sendo segundo outro grande grandes melhor pior parte partes tipo tipos caso casos ponto
pontos acordo então porém contudo entanto enquanto apenas tanto quanto próprio própria possível diversos
diversas vários várias principais principal importante importantes base meio fato modo maneira
""".split()
)

WORD_RE = re.compile(r"[a-zA-ZÀ-ÿ0-9][a-zA-ZÀ-ÿ0-9\-']+", re.UNICODE)
SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý0-9\"“])")


def strip_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def tokenize(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text or "")]


def content_words(text: str) -> list[str]:
    return [w for w in tokenize(text) if w not in STOPWORDS and len(w) > 2 and not w.isdigit()]


def keywords(texts: list[str], top: int = 20, exclude: set[str] | None = None) -> list[tuple[str, int]]:
    """Termos (1 e 2 palavras) mais frequentes, contados uma vez por documento."""
    exclude = {strip_accents(e.lower()) for e in (exclude or set())}
    df: Counter[str] = Counter()
    for t in texts:
        words = content_words(t)
        grams = set(words) | {f"{a} {b}" for a, b in zip(words, words[1:])}
        # descarta termos formados só por palavras da consulta/nicho
        df.update(g for g in grams if not all(strip_accents(w) in exclude for w in g.split()))
    # bigramas que aparecem bastante "absorvem" os unigramas que os compõem
    result: list[tuple[str, int]] = []
    for term, n in df.most_common(top * 3):
        if n < 2:
            break
        if " " not in term and any(term in r.split() and c >= n * 0.8 for r, c in result if " " in r):
            continue
        result.append((term, n))
        if len(result) >= top:
            break
    return result


def split_sentences(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n+", text or ""):
        for s in SENT_RE.split(para.strip()):
            s = s.strip()
            # ignora títulos/menus (sem pontuação final) e frases curtas ou longas demais
            if 50 <= len(s) <= 400 and s[-1] in ".!?;”\")":
                out.append(s)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def extractive_summary(docs: list[dict], query: str, max_sentences: int = 15, per_doc: int = 3) -> list[dict]:
    """Escolhe as frases mais informativas dos documentos.

    docs: [{"id": int, "text": str}]
    Pontuação = soma dos pesos (idf * frequência global) das palavras da frase,
    com bônus para termos da consulta e números (fatos/dados), normalizada pelo tamanho.
    """
    sentences: list[tuple[int, int, str]] = []  # (doc_id, posição, frase)
    for d in docs:
        for i, s in enumerate(split_sentences(d["text"])):
            sentences.append((d["id"], i, s))
    if not sentences:
        return []

    n_docs = max(1, len(docs))
    doc_freq: Counter[str] = Counter()
    term_freq: Counter[str] = Counter()
    for d in docs:
        words = content_words(d["text"])
        term_freq.update(words)
        doc_freq.update(set(words))

    query_terms = {strip_accents(w) for w in content_words(query)}

    def weight(w: str) -> float:
        # Termos que aparecem em vários documentos formam o "consenso" do tema.
        # As palavras da própria consulta ficam de fora: estão em tudo e não informam nada.
        if strip_accents(w) in query_terms:
            return 0.0
        return math.log1p(term_freq[w]) * (doc_freq[w] / n_docs)

    scored = []
    for doc_id, pos, s in sentences:
        words = content_words(s)
        if len(words) < 6:
            continue
        score = sum(weight(w) for w in set(words)) / math.sqrt(len(words))
        if query_terms & {strip_accents(w) for w in words}:
            score *= 1.1
        if re.search(r"\d", s):
            score *= 1.2  # números costumam indicar fatos/dados
        score *= 1 + 0.2 / (1 + pos)
        scored.append((score, doc_id, pos, s, set(words)))

    scored.sort(key=lambda x: x[0], reverse=True)
    chosen, chosen_words, per_doc_count = [], [], Counter()
    for score, doc_id, pos, s, words in scored:
        if per_doc_count[doc_id] >= per_doc:
            continue
        if any(_jaccard(words, cw) > 0.45 for cw in chosen_words):
            continue  # quase duplicada de uma frase já escolhida
        chosen.append({"doc_id": doc_id, "pos": pos, "text": s, "score": round(score, 3)})
        chosen_words.append(words)
        per_doc_count[doc_id] += 1
        if len(chosen) >= max_sentences:
            break
    return chosen
