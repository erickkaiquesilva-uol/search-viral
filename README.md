# Search Viral

Aplicação web em Python que busca vídeos virais por nicho (YouTube, Instagram e X), explica **por que** eles viralizam (ganchos, formatos, títulos, aberturas) e gera relatórios sobre qualquer tema com fontes linkadas.

A IA é opcional e entra só no final: o Python coleta, mede e resume, e a IA recebe apenas o resumo (~800 a 1.000 tokens de entrada por estudo). Respostas ficam em cache, então repetir a mesma busca no mesmo dia custa 0 tokens.

## Como rodar

Requisitos: Python 3.10+ e `make`.

```bash
make setup     # cria .env (a partir do .env.example), venv e instala dependências
make run       # sobe em http://127.0.0.1:8000 (HOST/PORT vêm do .env)
```

Outros comandos: `make info` (mostra Python, venv e chaves detectados nesta máquina), `make dev` (recarrega ao salvar), `make check`, `make update-ytdlp`, `make reset-venv` (ao trocar de máquina ou de Python). Use `make help` para ver todos.

O `Makefile` não tem caminho fixo: ele descobre a pasta do projeto e o Python mais novo disponível. Para forçar um Python, defina `PYTHON=` no `.env`.

As decisões técnicas e os porquês estão em [docs/MEMORIA.md](docs/MEMORIA.md). Leia antes de mudar o projeto e acrescente as decisões novas.

## O que cada tela faz

**Virais por nicho**: informe o nicho, as redes, o período e a quantidade.
1. Coleta: no YouTube via `yt-dlp`, sem API key, numa busca geral e outra só de vídeos curtos. Instagram e X usam o Apify.
2. Métricas por vídeo: views/dia, views/seguidores (vídeo que foi além da própria base) e engajamento. Com elas, calcula um **score de 0 a 100** em percentil dentro de cada rede.
3. Estudo por regras: compara o quartil viral com o restante para cada gancho (lista, pergunta, segredo, alerta, "sem X", facilidade, identificação etc.), formato/duração, padrão de título, termos, hashtags, dia e horário.
4. Abertura falada: lê a legenda dos primeiros 15s dos melhores vídeos, que é o gancho falado.
5. Com a opção "Gerar estudo com IA": devolve por que viraliza, o padrão para copiar, 10 ganchos prontos e 2 roteiros.

**Relatório de tema**: busca na web e em notícias (DuckDuckGo), lê as páginas (`trafilatura`), escolhe as frases mais informativas com um resumo extrativo local, levanta termos-chave, vídeos populares e ideias de vídeo. Com a IA ligada, ela redige o relatório citando as fontes `[n]`.

Todo resultado vai para o **Histórico** (SQLite em `data/app.db`) e pode ser baixado em `.md`.

## Configuração (.env)

| Variável | Para quê |
|---|---|
| `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL` | Qualquer API compatível com OpenAI (OpenAI, OpenRouter, Groq, Gemini, Ollama local) |
| `AI_MAX_TOKENS` | Teto de tokens da resposta |
| `APIFY_TOKEN` | Libera Instagram e X ([apify.com](https://apify.com)) |
| `YT_LANG`, `YT_REGION` | Idioma/região dos resultados (padrão `pt`/`BR`) |
| `YT_ENRICH_TOP`, `YT_TRANSCRIPT_TOP` | Quantos vídeos recebem coleta detalhada e leitura da abertura |
| `DB_PATH` | Banco SQLite (relativo à raiz do projeto, padrão `data/app.db`) |
| `HOST`, `PORT` | Endereço do servidor |
| `APP_TIMEZONE` | Fuso para "melhor dia/horário" (padrão `America/Sao_Paulo`) |
| `PYTHON`, `VENV` | Python e pasta do venv usados pelo `Makefile` (vazio = detecta) |

## Limitações

- **Instagram e X** bloqueiam scraping sem login, por isso usam atores pagos do Apify (`apify/instagram-hashtag-scraper` e `apidojo/tweet-scraper`). O plano gratuito do ator de tweets é limitado a poucos itens e não roda via API.
- O YouTube muda o site com frequência. Se a busca parar de funcionar, atualize: `.venv/bin/pip install -U yt-dlp`.
- O estudo usa metadados (título, duração, métricas, legenda). Ele não analisa imagem nem edição do vídeo.
- Os ganchos são detectados por expressões regulares em `app/analysis/hooks.py`. Para criar ganchos do seu nicho, adicione itens à lista `HOOKS`.

## Estrutura

```
app/
  main.py              rotas FastAPI e jobs em segundo plano
  collectors/          youtube.py (yt-dlp), apify.py (Instagram e X)
  analysis/            metrics.py (score), hooks.py (ganchos e padrões), text.py (palavras-chave e resumo)
  services/            viral.py, research.py, render.py (Markdown seguro)
  ai.py                cliente de IA com cache
  templates/, static/  interface web
```
