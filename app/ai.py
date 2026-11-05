"""Cliente de IA mínimo (API compatível com OpenAI) com cache.

Regras para economizar tokens:
- a IA só recebe dados já resumidos pelo Python (estatísticas + poucos exemplos);
- limite de tokens de resposta configurável (AI_MAX_TOKENS);
- respostas ficam em cache no SQLite: a mesma entrada nunca é paga duas vezes.
"""

from __future__ import annotations

import hashlib
import logging

import httpx

from . import db
from .config import settings

log = logging.getLogger(__name__)


class AIError(RuntimeError):
    pass


def complete(system: str, user: str, max_tokens: int | None = None, temperature: float = 0.4,
             cache_key: str | None = None) -> dict:
    """Retorna {"text": str, "cached": bool, "usage": dict | None}.

    cache_key: chave "semântica" opcional (ex.: nicho + filtros + dia). Sem ela, o cache
    usa o prompt inteiro, que muda sempre que um número de views muda.
    """
    if not settings.ai_enabled:
        raise AIError("IA desativada: defina AI_API_KEY no .env")

    max_tokens = min(max_tokens or settings.ai_max_tokens, settings.ai_max_tokens)
    basis = cache_key or f"{system}|{user}"
    key = hashlib.sha256(f"{settings.ai_model}|{max_tokens}|{basis}".encode()).hexdigest()
    cached = db.cache_get(key)
    if cached is not None:
        return {"text": cached, "cached": True, "usage": None}

    try:
        resp = httpx.post(
            f"{settings.ai_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.ai_api_key}"},
            json={
                "model": settings.ai_model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=120,
        )
    except httpx.HTTPError as exc:
        raise AIError(f"Falha ao chamar a IA: {exc}") from exc
    if resp.status_code >= 400:
        raise AIError(f"IA respondeu {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    if text:
        db.cache_set(key, text)
    return {"text": text, "cached": False, "usage": data.get("usage")}
