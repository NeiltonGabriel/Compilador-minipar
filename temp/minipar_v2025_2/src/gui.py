"""
gui.py — MiniPar v2025.2
Interface gráfica Web baseada em Gradio.

Uso:
    python src/gui.py              # abre no navegador padrão (porta 7860)
    python src/gui.py --port 8080  # porta customizada
    python src/gui.py --share      # gera link público temporário (ngrok)
"""

import sys
import os
import io
import argparse
import traceback
import tempfile

# Garante que src/ esteja no path quando executado de qualquer diretório
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gradio as gr

from lexer        import Lexer,            LexerError
from parser       import Parser,           ParseError
from semantic     import SemanticAnalyzer
from codegen      import CodeGenerator
from c_codegen    import CCodeGenerator
from arm_codegen  import ArmCodeGenerator
from backend      import GCCBackend
from runner       import Runner
from symbol_table import SymbolTable


# ─────────────────────────────────────────────────────────────────────────────
# Exemplos embutidos
# ─────────────────────────────────────────────────────────────────────────────

_EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), '..', 'examples')

def _load_example(filename: str) -> str:
    path = os.path.join(_EXAMPLES_DIR, filename)
    if os.path.isfile(path):
        return open(path, encoding='utf-8').read()
    return ''

EXAMPLES = {
    "── Selecione um exemplo ──": "",
    "▶ Olá Mundo": """\
// Olá Mundo em MiniPar
var string nome = "MiniPar";
print("Olá,", nome);
print("Versão: 2025.2");
""",
    "▶ Fibonacci Recursivo": """\
func number fib(var number n) {
    if (n == 0) { return 0; }
    if (n == 1) { return 1; }
    return fib(n - 1) + fib(n - 2);
}
print("fib(0)  =", fib(0));
print("fib(5)  =", fib(5));
print("fib(10) =", fib(10));
""",
    "▶ Fatorial Iterativo": """\
func number fat(var number n) {
    var number resultado = 1;
    var number i = 2;
    while (i <= n) {
        resultado = resultado * i;
        i = i + 1;
    }
    return resultado;
}
print("5!  =", fat(5));
print("10! =", fat(10));
""",
    "▶ POO — Classe Ponto": _load_example('ponto.minipar'),
    "▶ POO — Fila": _load_example('fila.minipar'),
    "▶ Paralelismo — par/spawn/sync": _load_example('paralelo.minipar'),
    "▶ Distribuído — remote/node": _load_example('distribuido.minipar'),
    "▶ Híbrido — POO + Par + Dist.": _load_example('hibrido.minipar'),
    "▶ Quicksort": """\
func list quicksort(var list arr) {
    if (len(arr) <= 1) { return arr; }
    var any pivot = arr[0];
    var list menores = [];
    var list maiores = [];
    var number i = 1;
    while (i < len(arr)) {
        if (arr[i] <= pivot) {
            append(menores, arr[i]);
        } else {
            append(maiores, arr[i]);
        }
        i = i + 1;
    }
    return quicksort(menores);
}
var list dados = [5, 3, 8, 1, 9, 2, 7, 4, 6];
print("Original:", dados);
var list ord = quicksort(dados);
print("Ordenado:", ord);
""",
}


# ─────────────────────────────────────────────────────────────────────────────
# Funções de backend chamadas pelo Gradio
# ─────────────────────────────────────────────────────────────────────────────

def _pipeline(source: str):
    """Roda Lexer → Parser → Semântico → TAC e retorna (ast, table, tac) ou lança."""
    tokens = Lexer(source).tokenize()
    ast    = Parser(tokens).parse()
    sem    = SemanticAnalyzer()
    ok     = sem.analyze(ast)
    if not ok:
        raise ValueError("ERROS SEMÂNTICOS:\n" + "\n".join(sem.errors))
    cg = CodeGenerator(sem.table)
    cg.generate(ast)
    return ast, sem.table, cg.code


def action_executar(source: str) -> str:
    """Executa o programa diretamente via runner e devolve a saída."""
    if not source.strip():
        return "⚠️  Nenhum código para executar."
    captured = io.StringIO()
    import builtins
    orig_print = builtins.print
    def patched_print(*args, **kwargs):
        end = kwargs.get('end', '\n')
        sep = kwargs.get('sep', ' ')
        captured.write(sep.join(str(a) for a in args) + end)
    builtins.print = patched_print
    try:
        tokens = Lexer(source).tokenize()
        ast    = Parser(tokens).parse()
        sem    = SemanticAnalyzer()
        ok     = sem.analyze(ast)
        if not ok:
            return "❌  Erros semânticos:\n" + "\n".join(sem.errors)
        runner = Runner(symbol_table=sem.table)
        runner.run(ast)
        out = captured.getvalue()
        return out if out.strip() else "(programa executado sem saída)"
    except (LexerError, ParseError) as e:
        return f"❌  Erro de sintaxe:\n{e}"
    except Exception as e:
        return f"❌  Erro em tempo de execução:\n{traceback.format_exc()}"
    finally:
        builtins.print = orig_print


def action_tokens(source: str) -> str:
    if not source.strip():
        return "⚠️  Nenhum código."
    try:
        tokens = Lexer(source).tokenize()
        linhas = []
        for tok in tokens:
            linhas.append(
                f"L{tok.linha:3d}:C{tok.coluna:<3d}  "
                f"{tok.tipo.value:<20}  {repr(tok.valor)}"
            )
        return "\n".join(linhas)
    except LexerError as e:
        return f"❌  Erro léxico:\n{e}"


def action_ast(source: str) -> str:
    if not source.strip():
        return "⚠️  Nenhum código."
    try:
        tokens = Lexer(source).tokenize()
        ast    = Parser(tokens).parse()
        return _format_ast(ast)
    except (LexerError, ParseError) as e:
        return f"❌  {e}"


def action_semantico(source: str) -> str:
    if not source.strip():
        return "⚠️  Nenhum código."
    try:
        tokens = Lexer(source).tokenize()
        ast    = Parser(tokens).parse()
        sem    = SemanticAnalyzer()
        ok     = sem.analyze(ast)
        if ok:
            out = ["✅  Análise semântica concluída sem erros.\n"]
            out.append("── Tabela de Símbolos ──")
            for scope in sem.table.pilha_escopos:
                out.append(f"\nEscopo '{scope.nome}' (nível {scope.nivel}):")
                for nome, sym in scope.simbolos.items():
                    out.append(
                        f"  {nome:<20} {sym.tipo_dados:<12} "
                        f"[{sym.tipo_simbolo.value}]"
                        + (" remote" if sym.is_remote else "")
                        + (" async"  if sym.is_async  else "")
                    )
            if sem.table.class_registry:
                out.append("\n── Classes ──")
                for nome, desc in sem.table.class_registry.items():
                    out.append(f"\n  {nome}  ({desc.size_bytes} bytes)")
                    if desc.superclasse:
                        out.append(f"    extends {desc.superclasse.nome}")
                    for f in desc._fields:
                        out.append(f"    campo  {f.nome:<16} {f.tipo_dados}  @offset {f.offset}")
                    for e in desc._vtable:
                        idx = desc.get_vtable_index(e.method_name)
                        out.append(f"    método [{idx}] {e.method_name}() → {e.return_type}")
            if sem.table.node_registry:
                out.append("\n── Nós Remotos ──")
                for nome, addr in sem.table.node_registry.items():
                    out.append(f"  {nome} → {addr}")
            return "\n".join(out)
        else:
            return "❌  Erros semânticos:\n" + "\n".join(sem.errors)
    except (LexerError, ParseError) as e:
        return f"❌  {e}"
    except Exception as e:
        return f"❌  {traceback.format_exc()}"


def action_tac(source: str) -> str:
    if not source.strip():
        return "⚠️  Nenhum código."
    try:
        ast, table, tac = _pipeline(source)
        linhas = []
        for i, instr in enumerate(tac):
            linhas.append(f"  {i:4d}:  {instr}")
        return "\n".join(linhas)
    except ValueError as e:
        return f"❌  {e}"
    except (LexerError, ParseError) as e:
        return f"❌  {e}"
    except Exception as e:
        return f"❌  {traceback.format_exc()}"


def action_codigo_c(source: str) -> str:
    if not source.strip():
        return "⚠️  Nenhum código."
    try:
        ast, table, tac = _pipeline(source)
        cgen = CCodeGenerator(table)
        return cgen.generate(tac)
    except ValueError as e:
        return f"❌  {e}"
    except (LexerError, ParseError) as e:
        return f"❌  {e}"
    except Exception as e:
        return f"❌  {traceback.format_exc()}"


def action_assembly(source: str) -> str:
    if not source.strip():
        return "⚠️  Nenhum código."
    try:
        ast, table, tac = _pipeline(source)
        agen = ArmCodeGenerator(table)
        return agen.generate(tac)
    except ValueError as e:
        return f"❌  {e}"
    except (LexerError, ParseError) as e:
        return f"❌  {e}"
    except Exception as e:
        return f"❌  {traceback.format_exc()}"


def action_compilar_gcc(source: str) -> str:
    if not source.strip():
        return "⚠️  Nenhum código."
    try:
        ast, table, tac = _pipeline(source)
        cgen  = CCodeGenerator(table)
        c_src = cgen.generate(tac)

        features = set()
        if cgen.uses_parallel:   features.add('parallel')
        if cgen.uses_network:    features.add('distributed')
        if cgen.uses_oop:        features.add('oop')

        with tempfile.NamedTemporaryFile(suffix='.c', mode='w',
                                         delete=False, encoding='utf-8') as f:
            f.write(c_src)
            c_file = f.name

        with tempfile.NamedTemporaryFile(suffix='', delete=False) as f:
            exe_path = f.name

        try:
            gcc = GCCBackend()
            result = gcc.compile_to_executable(c_file, exe_path, features)
            if result.success:
                return (
                    f"✅  Compilação bem-sucedida!\n"
                    f"    Executável: {exe_path}\n"
                    f"    Features:   {features or {'básico'}}\n\n"
                    f"── Código C gerado ({len(c_src.splitlines())} linhas) ──\n\n"
                    + c_src
                )
            else:
                return (
                    f"❌  Erro no GCC:\n{result.stderr}\n\n"
                    f"── Código C que falhou ──\n\n{c_src}"
                )
        finally:
            for p in (c_file, exe_path):
                if os.path.exists(p):
                    try: os.unlink(p)
                    except: pass

    except ValueError as e:
        return f"❌  {e}"
    except (LexerError, ParseError) as e:
        return f"❌  {e}"
    except Exception as e:
        return f"❌  {traceback.format_exc()}"


def action_carregar_exemplo(nome: str) -> str:
    return EXAMPLES.get(nome, "")


# ─────────────────────────────────────────────────────────────────────────────
# Formatação da AST
# ─────────────────────────────────────────────────────────────────────────────

def _format_ast(node, indent: int = 0) -> str:
    prefix = "  " * indent
    name   = type(node).__name__

    # Nós com filhos relevantes
    if hasattr(node, '__dataclass_fields__'):
        lines = [f"{prefix}{name}"]
        for field_name, _ in node.__dataclass_fields__.items():
            val = getattr(node, field_name)
            if field_name in ('linha', 'coluna'):
                continue
            if val is None:
                continue
            if isinstance(val, list):
                if val:
                    lines.append(f"{prefix}  {field_name}:")
                    for item in val:
                        if hasattr(item, '__dataclass_fields__'):
                            lines.append(_format_ast(item, indent + 2))
                        elif isinstance(item, tuple):
                            lines.append(f"{prefix}    {item}")
                        else:
                            lines.append(f"{prefix}    {repr(item)}")
            elif hasattr(val, '__dataclass_fields__'):
                lines.append(f"{prefix}  {field_name}:")
                lines.append(_format_ast(val, indent + 2))
            else:
                lines.append(f"{prefix}  {field_name}: {repr(val)}")
        return "\n".join(lines)
    return f"{prefix}{repr(node)}"


# ─────────────────────────────────────────────────────────────────────────────
# Construção da interface Gradio
# ─────────────────────────────────────────────────────────────────────────────

CSS = """
/* ── Paleta MiniPar ── */
:root {
    --mp-bg:       #0f1117;
    --mp-surface:  #1a1d27;
    --mp-border:   #2a2d3e;
    --mp-accent:   #7c6af7;
    --mp-accent2:  #4fc3f7;
    --mp-green:    #4caf82;
    --mp-red:      #f44336;
    --mp-text:     #e0e0f0;
    --mp-subtext:  #8b8fad;
}

body, .gradio-container {
    background: var(--mp-bg) !important;
    color: var(--mp-text) !important;
    font-family: 'Inter', 'Segoe UI', sans-serif;
}

/* Editor de código */
textarea, .code-editor textarea {
    background:    #0d1117 !important;
    color:         #cdd6f4 !important;
    font-family:   'JetBrains Mono', 'Fira Code', 'Courier New', monospace !important;
    font-size:     13px !important;
    border:        1px solid var(--mp-border) !important;
    border-radius: 8px !important;
    padding:       12px !important;
}

/* Output */
.output-text textarea {
    background: #0d1117 !important;
    color: #a6e3a1 !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 12px !important;
}

/* Botões */
button.primary {
    background: linear-gradient(135deg, var(--mp-accent), #5b4dd4) !important;
    border: none !important;
    border-radius: 8px !important;
    color: white !important;
    font-weight: 600 !important;
    transition: opacity 0.2s !important;
}
button.primary:hover { opacity: 0.85 !important; }

button.secondary {
    background: var(--mp-surface) !important;
    border: 1px solid var(--mp-border) !important;
    border-radius: 6px !important;
    color: var(--mp-text) !important;
}

/* Tabs */
.tab-nav button {
    background: transparent !important;
    border-bottom: 2px solid transparent !important;
    color: var(--mp-subtext) !important;
    font-weight: 500 !important;
}
.tab-nav button.selected {
    border-bottom-color: var(--mp-accent) !important;
    color: var(--mp-text) !important;
}

/* Títulos */
h1, h2, h3 { color: var(--mp-text) !important; }

/* Dropdown */
select, .gr-dropdown {
    background: var(--mp-surface) !important;
    border: 1px solid var(--mp-border) !important;
    color: var(--mp-text) !important;
    border-radius: 6px !important;
}

/* Label */
label span { color: var(--mp-subtext) !important; }
"""

def build_interface() -> gr.Blocks:
    with gr.Blocks(
        title="MiniPar Compiler v2025.2",
        theme=gr.themes.Base(
            primary_hue="violet",
            neutral_hue="slate",
        ),
        css=CSS,
    ) as app:

        # ── Cabeçalho ──────────────────────────────────────────────────────
        gr.HTML("""
        <div style="
            padding: 24px 0 8px;
            text-align: center;
        ">
            <h1 style="
                font-size: 2rem;
                font-weight: 700;
                margin: 0;
                background: linear-gradient(135deg, #7c6af7, #4fc3f7);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            ">
                ⚡ MiniPar Compiler
            </h1>
            <p style="
                color: #8b8fad;
                margin: 6px 0 0;
                font-size: 0.95rem;
            ">
                v2025.2 &nbsp;·&nbsp;
                Orientação a Objetos &nbsp;·&nbsp;
                Paralelismo Avançado &nbsp;·&nbsp;
                Execução Distribuída
            </p>
        </div>
        """)

        # ── Layout principal: código à esquerda, saída à direita ───────────
        with gr.Row(equal_height=False):

            # ── Coluna esquerda: editor ─────────────────────────────────────
            with gr.Column(scale=5):

                with gr.Row():
                    exemplo_dd = gr.Dropdown(
                        choices=list(EXAMPLES.keys()),
                        value="── Selecione um exemplo ──",
                        label="Exemplos",
                        interactive=True,
                        scale=4,
                    )
                    btn_carregar = gr.Button(
                        "Carregar", variant="secondary", scale=1, min_width=80
                    )

                editor = gr.Code(
                    label="Código-Fonte MiniPar",
                    language="javascript",   # Gradio não tem MiniPar; js dá highlight similar
                    lines=30,
                    value="""\
// Bem-vindo ao MiniPar v2025.2!
// Selecione um exemplo acima ou escreva seu código aqui.

func number fatorial(var number n) {
    if (n == 0) { return 1; }
    return n * fatorial(n - 1);
}

print("5! =", fatorial(5));
print("Olá, MiniPar!");
""",
                    interactive=True,
                )

                # Linha de botões de ação
                with gr.Row():
                    btn_run   = gr.Button("▶  Executar",   variant="primary",    scale=3)
                    btn_check = gr.Button("✔  Verificar",  variant="secondary",  scale=2)
                    btn_gcc   = gr.Button("⚙  Compilar C", variant="secondary",  scale=2)
                    btn_clear = gr.Button("✕  Limpar",     variant="secondary",  scale=1)

            # ── Coluna direita: saídas em abas ──────────────────────────────
            with gr.Column(scale=5):

                with gr.Tabs() as tabs_out:

                    with gr.Tab("🖥  Saída"):
                        out_run = gr.Code(
                            label="Resultado da Execução",
                            language=None,
                            lines=30,
                            interactive=False,
                        )

                    with gr.Tab("🔍 Tokens"):
                        btn_tokens = gr.Button("Gerar Tokens", variant="secondary")
                        out_tokens = gr.Code(
                            label="Tokens (Análise Léxica)",
                            language=None,
                            lines=28,
                            interactive=False,
                        )

                    with gr.Tab("🌲 AST"):
                        btn_ast = gr.Button("Gerar AST", variant="secondary")
                        out_ast = gr.Code(
                            label="Árvore Sintática Abstrata",
                            language=None,
                            lines=28,
                            interactive=False,
                        )

                    with gr.Tab("✔ Semântica"):
                        out_sem = gr.Code(
                            label="Análise Semântica + Tabela de Símbolos",
                            language=None,
                            lines=28,
                            interactive=False,
                        )

                    with gr.Tab("📋 TAC"):
                        btn_tac = gr.Button("Gerar TAC", variant="secondary")
                        out_tac = gr.Code(
                            label="Código Intermediário (Three-Address Code)",
                            language=None,
                            lines=28,
                            interactive=False,
                        )

                    with gr.Tab("🇨  Código C"):
                        btn_c = gr.Button("Gerar Código C", variant="secondary")
                        out_c = gr.Code(
                            label="Código C Gerado",
                            language="c",
                            lines=28,
                            interactive=False,
                        )

                    with gr.Tab("⚙  Assembly ARMv7"):
                        btn_arm = gr.Button("Gerar Assembly ARMv7", variant="secondary")
                        out_arm = gr.Code(
                            label="Assembly ARMv7 (CPUlator)",
                            language=None,
                            lines=28,
                            interactive=False,
                        )

                    with gr.Tab("🔨 GCC Build"):
                        out_gcc = gr.Code(
                            label="Resultado da Compilação GCC",
                            language="c",
                            lines=28,
                            interactive=False,
                        )

        # ── Rodapé ─────────────────────────────────────────────────────────
        gr.HTML("""
        <div style="
            text-align: center;
            padding: 16px 0 4px;
            color: #8b8fad;
            font-size: 0.82rem;
            border-top: 1px solid #2a2d3e;
            margin-top: 12px;
        ">
            MiniPar v2025.2 &nbsp;·&nbsp;
            UFAL — Ciência da Computação &nbsp;·&nbsp;
            Pipeline: Lexer → Parser → Semântico → TAC → C/ARMv7
        </div>
        """)

        # ── Ligações de eventos ─────────────────────────────────────────────

        # Carregar exemplo
        btn_carregar.click(
            fn=action_carregar_exemplo,
            inputs=[exemplo_dd],
            outputs=[editor],
        )
        # Carregar também ao mudar o dropdown
        exemplo_dd.change(
            fn=action_carregar_exemplo,
            inputs=[exemplo_dd],
            outputs=[editor],
        )

        # Executar
        btn_run.click(
            fn=action_executar,
            inputs=[editor],
            outputs=[out_run],
        )

        # Verificar (semântica)
        btn_check.click(
            fn=action_semantico,
            inputs=[editor],
            outputs=[out_sem],
        )

        # Compilar com GCC
        btn_gcc.click(
            fn=action_compilar_gcc,
            inputs=[editor],
            outputs=[out_gcc],
        )

        # Limpar editor
        btn_clear.click(
            fn=lambda: "",
            outputs=[editor],
        )

        # Botões dentro das abas
        btn_tokens.click(fn=action_tokens,    inputs=[editor], outputs=[out_tokens])
        btn_ast.click(   fn=action_ast,       inputs=[editor], outputs=[out_ast])
        btn_tac.click(   fn=action_tac,       inputs=[editor], outputs=[out_tac])
        btn_c.click(     fn=action_codigo_c,  inputs=[editor], outputs=[out_c])
        btn_arm.click(   fn=action_assembly,  inputs=[editor], outputs=[out_arm])

    return app


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Interface gráfica do compilador MiniPar v2025.2"
    )
    p.add_argument('--port',   type=int,  default=7860,  help="Porta HTTP (padrão 7860)")
    p.add_argument('--host',   type=str,  default='0.0.0.0', help="Host (padrão 0.0.0.0)")
    p.add_argument('--share',  action='store_true',  help="Gerar link público ngrok")
    p.add_argument('--no-browser', action='store_true', help="Não abrir o navegador")
    args = p.parse_args()

    app = build_interface()
    print(f"\n{'='*55}")
    print(f"  MiniPar Compiler v2025.2 — Interface Web")
    print(f"{'='*55}")
    print(f"  Acesse: http://localhost:{args.port}")
    if args.share:
        print(f"  Gerando link público (ngrok)...")
    print(f"{'='*55}\n")

    app.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        inbrowser=not args.no_browser,
        quiet=False,
    )


if __name__ == '__main__':
    main()
