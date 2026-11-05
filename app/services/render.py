"""Markdown -> HTML seguro.

Os relatórios contêm textos de terceiros (títulos, legendas, páginas web) e da IA,
então todo HTML bruto é escapado e só links http(s) são mantidos.
"""

import re

import markdown as md

_HREF_RE = re.compile(r'href="(?!https?://)[^"]*"', re.IGNORECASE)


def to_html(text: str) -> str:
    safe = (text or "").replace("&", "&amp;").replace("<", "&lt;")
    html = md.markdown(safe, extensions=["tables", "sane_lists"])
    html = _HREF_RE.sub('href="#"', html)
    return html.replace("<a href=", '<a target="_blank" rel="noopener noreferrer" href=')


def cell(text) -> str:
    """Valor seguro para uma célula de tabela Markdown."""
    return re.sub(r"\s+", " ", str(text if text is not None else "")).replace("|", "/").strip()


def link(label: str, url: str) -> str:
    label = cell(label).replace("[", "(").replace("]", ")") or url
    url = (url or "").replace(" ", "%20").replace(")", "%29")
    return f"[{label}]({url})" if url.startswith(("http://", "https://")) else label


def num(n) -> str:
    if n is None:
        return "-"
    n = float(n)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "mil")):
        if abs(n) >= div:
            return f"{n / div:.1f}{suffix}".replace(".0", "")
    return f"{n:.0f}" if n == int(n) or abs(n) >= 10 else f"{n:.1f}"
