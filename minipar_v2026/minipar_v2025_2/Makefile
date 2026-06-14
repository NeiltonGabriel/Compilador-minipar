# Makefile — MiniPar v2025.2
# Uso: make <alvo>

PYTHON   := python3
SRC      := src
TESTS    := tests
EXAMPLES := examples

.PHONY: help test test-lexer test-parser test-semantic test-codegen \
        run-ponto run-fila run-paralelo run-distribuido run-hibrido \
        tac-ponto asm-ponto build-ponto lint clean

# ─── Ajuda ───────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "  MiniPar v2025.2 — Comandos disponíveis"
	@echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "  Testes:"
	@echo "    make test           → executa toda a suite de testes"
	@echo "    make test-lexer     → testa apenas o Lexer"
	@echo "    make test-parser    → testa apenas o Parser"
	@echo "    make test-semantic  → testa apenas o Semântico"
	@echo "    make test-codegen   → testa TAC, backends e runner"
	@echo ""
	@echo "  Execução de exemplos:"
	@echo "    make run-ponto      → executa examples/ponto.minipar"
	@echo "    make run-fila       → executa examples/fila.minipar"
	@echo "    make run-paralelo   → executa examples/paralelo.minipar"
	@echo "    make run-distribuido→ executa examples/distribuido.minipar"
	@echo "    make run-hibrido    → executa examples/hibrido.minipar"
	@echo ""
	@echo "  Inspeção de código gerado:"
	@echo "    make tac-ponto      → imprime TAC de ponto.minipar"
	@echo "    make asm-ponto      → imprime Assembly ARMv7 de ponto.minipar"
	@echo "    make build-ponto    → compila ponto.minipar para executável"
	@echo ""
	@echo "  Utilitários:"
	@echo "    make lint           → verifica sintaxe dos arquivos Python"
	@echo "    make clean          → remove artefatos gerados"
	@echo ""

# ─── Testes ──────────────────────────────────────────────────────────────────

test:
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "  Executando suite completa de testes"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	$(PYTHON) -m pytest $(TESTS)/ -v --tb=short

test-lexer:
	$(PYTHON) -m pytest $(TESTS)/test_lexer.py -v --tb=short

test-parser:
	$(PYTHON) -m pytest $(TESTS)/test_parser.py -v --tb=short

test-semantic:
	$(PYTHON) -m pytest $(TESTS)/test_semantic.py -v --tb=short

test-codegen:
	$(PYTHON) -m pytest $(TESTS)/test_codegen_and_runner.py -v --tb=short

test-cov:
	$(PYTHON) -m pytest $(TESTS)/ -v --tb=short \
		--cov=$(SRC) --cov-report=term-missing

# ─── Execução de exemplos ────────────────────────────────────────────────────

run-ponto:
	$(PYTHON) $(SRC)/compiler.py run $(EXAMPLES)/ponto.minipar

run-fila:
	$(PYTHON) $(SRC)/compiler.py run $(EXAMPLES)/fila.minipar

run-paralelo:
	$(PYTHON) $(SRC)/compiler.py run $(EXAMPLES)/paralelo.minipar

run-distribuido:
	$(PYTHON) $(SRC)/compiler.py run $(EXAMPLES)/distribuido.minipar

run-hibrido:
	$(PYTHON) $(SRC)/compiler.py run $(EXAMPLES)/hibrido.minipar

# ─── TAC ─────────────────────────────────────────────────────────────────────

tac-ponto:
	$(PYTHON) $(SRC)/compiler.py tac $(EXAMPLES)/ponto.minipar

tac-fila:
	$(PYTHON) $(SRC)/compiler.py tac $(EXAMPLES)/fila.minipar

tac-paralelo:
	$(PYTHON) $(SRC)/compiler.py tac $(EXAMPLES)/paralelo.minipar

tac-distribuido:
	$(PYTHON) $(SRC)/compiler.py tac $(EXAMPLES)/distribuido.minipar

tac-hibrido:
	$(PYTHON) $(SRC)/compiler.py tac $(EXAMPLES)/hibrido.minipar

# ─── Assembly ARMv7 ──────────────────────────────────────────────────────────

asm-ponto:
	$(PYTHON) $(SRC)/compiler.py asm $(EXAMPLES)/ponto.minipar

asm-fila:
	$(PYTHON) $(SRC)/compiler.py asm $(EXAMPLES)/fila.minipar

asm-paralelo:
	$(PYTHON) $(SRC)/compiler.py asm $(EXAMPLES)/paralelo.minipar

asm-hibrido:
	$(PYTHON) $(SRC)/compiler.py asm $(EXAMPLES)/hibrido.minipar

# ─── Build (GCC) ─────────────────────────────────────────────────────────────

build-ponto:
	$(PYTHON) $(SRC)/compiler.py build $(EXAMPLES)/ponto.minipar \
		-o /tmp/ponto_exe --show-c

build-paralelo:
	$(PYTHON) $(SRC)/compiler.py build $(EXAMPLES)/paralelo.minipar \
		-o /tmp/paralelo_exe

build-hibrido:
	$(PYTHON) $(SRC)/compiler.py build $(EXAMPLES)/hibrido.minipar \
		-o /tmp/hibrido_exe

# ─── Verificação semântica ────────────────────────────────────────────────────

check-ponto:
	$(PYTHON) $(SRC)/compiler.py check $(EXAMPLES)/ponto.minipar

check-hibrido:
	$(PYTHON) $(SRC)/compiler.py check $(EXAMPLES)/hibrido.minipar

# ─── Tokens (debug) ──────────────────────────────────────────────────────────

tokens-ponto:
	$(PYTHON) $(SRC)/compiler.py tokens $(EXAMPLES)/ponto.minipar

# ─── Utilitários ─────────────────────────────────────────────────────────────

lint:
	@echo "Verificando sintaxe Python..."
	$(PYTHON) -m py_compile $(SRC)/lexer.py       && echo "  ✓ lexer.py"
	$(PYTHON) -m py_compile $(SRC)/ast_nodes.py   && echo "  ✓ ast_nodes.py"
	$(PYTHON) -m py_compile $(SRC)/parser.py      && echo "  ✓ parser.py"
	$(PYTHON) -m py_compile $(SRC)/symbol_table.py && echo "  ✓ symbol_table.py"
	$(PYTHON) -m py_compile $(SRC)/semantic.py    && echo "  ✓ semantic.py"
	$(PYTHON) -m py_compile $(SRC)/codegen.py     && echo "  ✓ codegen.py"
	$(PYTHON) -m py_compile $(SRC)/c_codegen.py   && echo "  ✓ c_codegen.py"
	$(PYTHON) -m py_compile $(SRC)/arm_codegen.py && echo "  ✓ arm_codegen.py"
	$(PYTHON) -m py_compile $(SRC)/backend.py     && echo "  ✓ backend.py"
	$(PYTHON) -m py_compile $(SRC)/runner.py      && echo "  ✓ runner.py"
	$(PYTHON) -m py_compile $(SRC)/compiler.py    && echo "  ✓ compiler.py"
	@echo "Todos os arquivos OK."

clean:
	@echo "Limpando artefatos..."
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc"       -delete 2>/dev/null || true
	find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.c.tmp"     -delete 2>/dev/null || true
	rm -f /tmp/ponto_exe /tmp/paralelo_exe /tmp/hibrido_exe
	@echo "Pronto."

# ─── Interface Gráfica ───────────────────────────────────────────────────────

gui:
	$(PYTHON) $(SRC)/gui.py

gui-share:
	$(PYTHON) $(SRC)/gui.py --share

gui-port:
	$(PYTHON) $(SRC)/gui.py --port $(PORT)

install-gradio:
	pip install gradio --break-system-packages
