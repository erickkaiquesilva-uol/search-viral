# Memória do projeto: decisões e porquês

Registro das decisões técnicas do Search Viral, para quem continuar o projeto (pessoas ou agentes de IA).
**Regra:** toda decisão nova ou mudança de rumo entra aqui, com data, motivo e alternativas descartadas. Não apague decisões antigas. Marque como `SUBSTITUÍDA por #N`.

Formato de cada entrada: **Contexto** (o problema), **Decisão** (o que foi feito), **Porquê**, **Alternativas descartadas** e **Consequências**.

---

## Objetivo do produto (2026-11-05)

- Buscar vídeos que estão viralizando **por nicho** escolhido pelo usuário, no YouTube, Instagram e X/Twitter, sempre com o **link de onde cada referência veio**.
- Explicar **por que** o formato viraliza ("siga esse padrão, use esses ganchos").
- Gerar **relatório sobre um tema** qualquer, a partir de fontes da web.
- Ser **web**, rodar **Python**, e usar **IA só nas partes muito relevantes**, para economizar tokens.

---

## 1. Stack: FastAPI + HTML/JS puro, sem etapa de build (2026-11-05)

- **Decisão:** backend em FastAPI, página única com Jinja2, JavaScript e CSS puros em `app/static/`.
- **Porquê:** o projeto é Python-first, com uma pessoa usando. Assim não há Node, bundler ou segundo deploy, e tudo sobe com um comando.
- **Alternativas descartadas:** React/Next (exige build e dois processos); Streamlit (rápido, mas limita o layout e o histórico com links).
- **Consequências:** se a interface crescer muito (login, vários usuários), vale migrar o front. A API REST em `/api/*` já está separada para isso.

## 2. Python 3.10 ou mais novo (2026-11-05)

- **Contexto:** a máquina original só tinha o Python 3.9 do macOS, e o `yt-dlp` atual não suporta mais o 3.9.
- **Decisão:** exigir 3.10+. O `Makefile` procura o Python mais novo disponível (3.13, 3.12, 3.11, 3.10). A variável `PYTHON` no `.env` força um Python específico.
- **Consequências:** em máquina nova, rode `make setup`. Se faltar Python, a mensagem de erro explica como instalar.

## 3. Nenhum caminho fixo de máquina no código (2026-11-05)

- **Decisão:** todo caminho é relativo à raiz do projeto, calculada em `app/config.py` (`ROOT`) e no `Makefile` (`ROOT`). O que varia por máquina fica no `.env`: `DB_PATH`, `HOST`, `PORT`, `APP_TIMEZONE`, `PYTHON`, `VENV` e as chaves.
- **Porquê:** o repositório não pode carregar `/Users/<pessoa>/...` nem segredos.
- **Regras:**
  - `.env`, `.venv/`, `data/` e `__pycache__/` estão no `.gitignore`. Só o `.env.example` sobe.
  - O `.venv/pyvenv.cfg` contém caminhos absolutos da máquina. Isso é normal do Python, e o arquivo não sobe. Ao trocar de máquina, rode `make reset-venv`.
  - Nunca escreva `/Users/...`, `/home/...` ou `/opt/homebrew/...` em arquivos versionados. Para conferir: `git grep -nE "/Users/|/home/|homebrew"`.
- **Atenção:** o `Makefile` faz `include .env`. Valores com `$` precisam ser escritos como `$$` no `.env`.

## 4. YouTube via yt-dlp, sem API key (2026-11-05)

- **Decisão:** usar o `yt-dlp` para ler a página de resultados de busca e os detalhes de cada vídeo (`app/collectors/youtube.py`).
- **Porquê:** dispensa chave e cota (a YouTube Data API tem cota diária baixa) e traz views, likes, comentários, inscritos, data, tags e legendas.
- **Alternativas descartadas:** YouTube Data API (cota e cadastro). Ela pode virar um coletor opcional no futuro.
- **Detalhes importantes:**
  - **Filtros de busca:** o parâmetro `sp` é um protobuf em base64, montado em `_sp()` com data de envio, tipo vídeo e duração curta. Testes: `sp` para "Shorts" (tipo 9) não retorna nada. Por isso os curtos são buscados com o filtro de duração "menos de 4 min".
  - **Duas buscas em paralelo:** uma geral e outra só de curtos. Cada uma fica com metade das vagas, porque os curtos têm menos views absolutas e sumiriam se tudo fosse ordenado junto.
  - **Coleta em duas fases:** uma busca rasa e rápida, depois o detalhamento (`YT_ENRICH_TOP`, padrão 25) intercalando longos e curtos. Os vídeos sem detalhe ficam sem data e engajamento e recebem score baixo.
  - **Abertura falada:** a legenda em `json3` dos primeiros 15s (`YT_TRANSCRIPT_TOP`, padrão 8). Prioridade da legenda: manual no idioma, depois automática original no idioma, automática traduzida, e por fim qualquer original. Muitos canais "farm" são dublados por IA, e o áudio original deles está em outra língua.
  - **Idioma:** sem `lang`, o YouTube devolvia títulos traduzidos para o inglês. Por isso enviamos `extractor_args youtube lang=YT_LANG` e `Accept-Language`.
  - **Tempo medido:** cerca de 12s para 30 vídeos.
  - **Fragilidade:** o YouTube muda o site com frequência. Primeiro passo quando quebrar: `make update-ytdlp`.

## 5. Instagram e X via Apify (2026-11-05)

- **Contexto:** as duas redes exigem login e bloqueiam scraping direto. O `snscrape` (X) morreu, e o `instaloader` exige sessão e leva bloqueios.
- **Decisão:** usar atores do Apify pela API `run-sync-get-dataset-items` (`app/collectors/apify.py`). Os atores são configuráveis no `.env` (`APIFY_INSTAGRAM_ACTOR`, `APIFY_TWITTER_ACTOR`).
- **Porquê:** é o caminho mais estável sem manter contas falsas.
- **Consequências:**
  - É **pago** (tem plano gratuito limitado). O ator de tweets exige no mínimo 50 itens por consulta e não roda via API no plano grátis.
  - **Ainda não foi testado com token real.** O mapeamento de campos foi escrito com base na documentação e tenta vários nomes de campo. Na primeira execução real, confira se views, likes e data vêm preenchidos.
  - No Instagram, o nicho vira hashtag sem acento e sem espaço (`niche_to_hashtag`).
  - Sem token, a interface desabilita essas redes e o relatório avisa.

## 6. Score de viralização (2026-11-05)

- **Decisão** (`app/analysis/metrics.py`): o score vai de 0 a 100 e é a média ponderada de **percentis dentro da mesma rede**:
  - 45% views por dia (velocidade);
  - 30% views/seguidores do autor (indica que o algoritmo levou o vídeo para fora da base);
  - 25% engajamento, calculado como (likes + comentários + compartilhamentos) / views.
- **Porquê:** views absolutas favorecem canais grandes. O percentil por rede permite comparar YouTube com Instagram sem misturar escalas.
- **Grupo viral:** é o melhor quartil (25%) por score. O "lift" de um padrão é a % no grupo viral dividida pela % no restante.
- **Limite conhecido:** com poucos vídeos (menos de 15), os percentis ficam instáveis.

## 7. Ganchos e padrões por regras, sem IA (2026-11-05)

- **Decisão:** os ganchos são detectados por expressões regulares em português e inglês (`HOOKS` em `app/analysis/hooks.py`). Cada gancho tem nome, "por que funciona" e moldes de título.
- **Ganchos atuais:** lista, pergunta, segredo, alerta, experimento, transformação, tutorial, número concreto, identificação, novidade, comparação, facilidade, eliminação ("sem X / zero X"), superlativo, história, opinião contrária e choque.
- **Histórico:** "facilidade" e "eliminação" entraram depois do teste com "receitas fit", em que os títulos virais eram do tipo "SEM AÇÚCAR, Fácil e Rápido" e nenhum gancho existente os pegava.
- **Também por regras:** formato por duração, características do título (tamanho, CAIXA ALTA, parênteses, separador), termos e hashtags, dia e horário (no fuso `APP_TIMEZONE`) e moldes de título (números viram `{N}`, valores viram `R$ {VALOR}`, termos do nicho viram `{NICHO}`). Um molde só aparece se for diferente do título original.
- **Insights:** frases geradas por regra em `build_insights`. Um gancho entra como "use" se aparece em 25% ou mais dos virais com lift de 1.2 ou mais, e como "evite" se tem 3 ou mais ocorrências com lift abaixo de 0.7. O formato vencedor exige pelo menos 3 vídeos.
- **Porquê:** é grátis, rápido, explicável e funciona sem IA. A IA só complementa.

## 8. IA opcional, mínima e com cache (2026-11-05)

- **Decisão** (`app/ai.py`): um cliente HTTP único para qualquer API **compatível com OpenAI** (OpenAI, OpenRouter, Groq, Gemini e Ollama local). A IA fica desligada se `AI_API_KEY` estiver vazio, e a interface mostra o checkbox desabilitado.
- **Economia de tokens:**
  - A IA nunca recebe HTML nem páginas inteiras, só **estatísticas e no máximo 10 exemplos** (no estudo de virais) ou **15 trechos de até 320 caracteres** (no relatório). Isso dá de 800 a 900 tokens de entrada, medidos.
  - `AI_MAX_TOKENS` limita a resposta.
  - O cache fica no SQLite (`ai_cache`), com chave "semântica": tipo + consulta + filtros + **dia**. A mesma busca no mesmo dia custa 0 tokens. Sem essa chave, qualquer view nova mudaria o prompt e invalidaria o cache.
- **Saídas pedidas:** no estudo de virais, por que viraliza, padrão para copiar, 10 ganchos prontos e 2 roteiros. No relatório de tema, resumo, pontos com citações [n], dados, divergências e 5 ideias de vídeo.
- **Ainda não testado com chave real.** O caminho foi validado com um servidor falso compatível com OpenAI (prompt, uso de tokens, cache e limpeza do HTML).

## 9. Relatório de tema (2026-11-05)

- **Decisão** (`app/services/research.py`):
  - A busca usa `ddgs` (DuckDuckGo), com resultados da web e notícias, na região `YT_REGION-YT_LANG`.
  - O texto das páginas sai do `trafilatura`.
  - O resumo é **extrativo e local** (`app/analysis/text.py`). O peso de cada palavra vem da frequência combinada com a presença em vários documentos (o "consenso" do tema). As palavras da própria consulta têm peso 0, porque aparecem em tudo. Frases com número ganham bônus. Ficam de fora títulos e menus (sem pontuação final) e frases quase duplicadas (Jaccard acima de 0.45). O limite é de 3 frases por fonte.
- **Filtros:**
  - Domínios de redes sociais são ignorados como fonte de texto.
  - Com `YT_LANG=pt`, páginas em espanhol são descartadas por heurística de palavras. Isso foi necessário porque "marketing de afiliados" trazia muitos resultados em espanhol.
- **Sem IA:** o relatório traz trechos com [n], termos-chave, ideias de vídeo montadas com moldes de ganchos, vídeos populares do YouTube e a lista de fontes.

## 10. Jobs em segundo plano e histórico (2026-11-05)

- **Decisão:** as buscas rodam em threads (`app/jobs.py`, 2 em paralelo), com progresso consultado pelo front a cada 1,5s. O resultado completo é salvo em JSON no SQLite (`searches`).
- **Porquê:** as buscas levam de 10s a alguns minutos (o Apify é lento), e isso estoura o tempo limite de uma requisição comum. Fila externa (Celery/Redis) seria exagero para uso pessoal.
- **Consequências:** jobs em andamento se perdem se o servidor reiniciar, mas os já concluídos ficam no banco. Para vários usuários ou deploy, troque por uma fila de verdade.
- **Links diretos:** `/#busca-<id>` abre uma busca salva.

## 11. Segurança (2026-11-05)

- Títulos, legendas, páginas e respostas da IA são **dados de terceiros**:
  - o Markdown é convertido com o HTML bruto escapado, e só links `http(s)` sobrevivem (`app/services/render.py`);
  - no JavaScript, tudo passa por `esc()` e `safeUrl()`.
- Os segredos ficam só no `.env`. O token do Apify vai no header `Authorization`, nunca na URL (para não aparecer em logs).
- O servidor escuta em `127.0.0.1` por padrão, e não existe login. **Não exponha na internet sem autenticação.**

## 12. Rede corporativa / proxy com certificado próprio (2026-11-05)

- **Contexto:** na máquina original, o HTTPS falhava com `CERTIFICATE_VERIFY_FAILED`, porque o proxy usa uma CA corporativa que só existe no repositório de certificados do sistema.
- **Decisão:** `truststore.inject_into_ssl()` em `app/__init__.py` (faz o Python usar os certificados do SO), mais `compat_opts: ["no-certifi"]` no yt-dlp.
- **Consequências:** é inofensivo em redes normais. Se o `truststore` não estiver instalado, o app segue com o padrão.

## 13. Makefile e memória do projeto (2026-11-05)

- **Decisão:** o `Makefile` é a porta de entrada (`make setup`, `make run`, `make info`). Ele detecta a pasta do projeto, o SO e o Python, e lê o `.env`. Este arquivo (`docs/MEMORIA.md`) guarda as decisões, e `.kiro/steering/memoria.md` faz o Kiro carregá-lo em toda conversa.
- **Porquê:** quem clonar o projeto em outra máquina não precisa saber caminhos nem versões, e quem continuar o trabalho (pessoa ou agente) entende o que já foi decidido antes de mudar.
- **Validação:** uma cópia limpa do repositório numa pasta temporária rodou `make setup` e `make check` sem ajustes.

---

## Como estender

- **Novo gancho:** adicione um `Hook(...)` em `HOOKS` (`app/analysis/hooks.py`) com nome, porquê, regex e moldes. O resto (tabela, insights, relatório e badges) é automático.
- **Nova rede** (ex.: TikTok): crie o coletor em `app/collectors/` devolvendo `Post`, registre-o em `collectors` (`app/services/viral.py`), adicione o checkbox em `index.html` e o rótulo em `PLATFORM_LABELS` e `PLATFORM` (JS).
- **Novo campo de configuração:** declare em `Settings` (`app/config.py`) e documente no `.env.example`.

## Pendências e ideias

- [ ] Validar Instagram e X com um `APIFY_TOKEN` real e ajustar o mapeamento de campos.
- [ ] Validar a IA com uma chave real e ajustar o prompt pela qualidade da resposta.
- [ ] TikTok (via Apify `clockworks/tiktok-scraper`), que é a fonte mais óbvia de virais curtos.
- [ ] YouTube Data API como coletor opcional, mais estável que o scraping.
- [ ] Testes automatizados para `hooks.py`, `metrics.py` e `text.py`, que são lógica pura e fácil de testar.
