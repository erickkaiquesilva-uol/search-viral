# Search Viral: comandos do projeto.
# Nada aqui depende da máquina de quem escreveu: o Python, o SO e a pasta do
# projeto são detectados na hora, e o resto vem do .env (veja .env.example).

SHELL := /bin/bash
.DEFAULT_GOAL := help

# Pasta do projeto = onde este Makefile está (funciona em qualquer máquina)
ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

# Carrega o .env, se existir, e exporta as variáveis para os comandos
-include $(ROOT)/.env
export

VENV ?= .venv
ifeq ($(strip $(VENV)),)
VENV := .venv
endif
HOST ?= 127.0.0.1
PORT ?= 8000

# Python: usa o PYTHON do .env; se vazio, procura o mais novo >= 3.10 no PATH
ifeq ($(strip $(PYTHON)),)
PYTHON := $(shell for p in python3.13 python3.12 python3.11 python3.10 python3; do \
	if command -v $$p >/dev/null 2>&1 && $$p -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then \
	command -v $$p; break; fi; done)
endif

OS := $(shell uname -s 2>/dev/null || echo Windows)
VENV_DIR := $(ROOT)/$(VENV)
ifeq ($(OS),Windows)
VENV_BIN := $(VENV_DIR)/Scripts
else
VENV_BIN := $(VENV_DIR)/bin
endif
PY := $(VENV_BIN)/python

.PHONY: help info setup env venv install run dev check update-ytdlp clean reset-venv

help: ## Lista os comandos
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

info: ## Mostra o que foi detectado nesta máquina
	@echo "Projeto:   $(ROOT)"
	@echo "Sistema:   $(OS)"
	@echo "Python:    $(if $(PYTHON),$(PYTHON) ($$($(PYTHON) --version 2>&1)),NÃO ENCONTRADO (precisa de 3.10+))"
	@echo "Venv:      $(VENV_DIR) $$( [ -x "$(PY)" ] && echo '(ok)' || echo '(não criado: rode make setup)')"
	@echo ".env:      $$( [ -f "$(ROOT)/.env" ] && echo 'ok' || echo 'ausente (make setup cria a partir do .env.example)')"
	@echo "Servidor:  http://$(HOST):$(PORT)"
	@echo "IA:        $$( [ -n "$(AI_API_KEY)" ] && echo "ligada ($(AI_MODEL))" || echo 'desligada (AI_API_KEY vazio)')"
	@echo "Apify:     $$( [ -n "$(APIFY_TOKEN)" ] && echo 'ok' || echo 'sem token (Instagram/X desligados)')"

setup: env venv install ## Primeira instalação: cria .env, venv e instala dependências
	@echo "Pronto. Rode: make run"

env: ## Cria o .env a partir do .env.example (não sobrescreve)
	@[ -f "$(ROOT)/.env" ] && echo ".env já existe, mantido." || (cp "$(ROOT)/.env.example" "$(ROOT)/.env" && echo ".env criado. Edite as chaves se quiser IA/Apify.")

venv:
	@if [ -z "$(PYTHON)" ]; then \
		echo "Python 3.10+ não encontrado. Instale (ex.: 'brew install python@3.12' no macOS, 'apt install python3.12-venv' no Ubuntu) ou defina PYTHON no .env."; exit 1; fi
	@[ -x "$(PY)" ] || (echo "Criando venv com $(PYTHON)" && "$(PYTHON)" -m venv "$(VENV_DIR)")

install: venv ## Instala/atualiza as dependências
	@"$(PY)" -m pip install -q --upgrade pip
	@"$(PY)" -m pip install -q -r "$(ROOT)/requirements.txt"
	@echo "Dependências instaladas."

# "exec" faz o uvicorn substituir o shell: Ctrl+C/kill chegam direto nele e não sobra processo órfão na porta
run: venv ## Sobe o servidor
	@cd "$(ROOT)" && exec "$(PY)" -m uvicorn app.main:app --host "$(HOST)" --port "$(PORT)"

dev: venv ## Sobe o servidor recarregando ao salvar
	@cd "$(ROOT)" && exec "$(PY)" -m uvicorn app.main:app --host "$(HOST)" --port "$(PORT)" --reload

check: venv ## Verifica se o código compila e importa
	@cd "$(ROOT)" && "$(PY)" -m compileall -q app && "$(PY)" -c "import app.main; print('ok')"

update-ytdlp: venv ## Atualiza o yt-dlp (use quando o YouTube parar de responder)
	@"$(PY)" -m pip install -q -U yt-dlp && "$(PY)" -m yt_dlp --version

clean: ## Remove caches do Python (mantém venv e banco)
	@find "$(ROOT)/app" -name __pycache__ -type d -prune -exec rm -rf {} +
	@echo "Caches removidos."

reset-venv: ## Apaga e recria o venv (útil ao trocar de máquina/Python)
	@rm -rf "$(VENV_DIR)"
	@$(MAKE) --no-print-directory install
