"""
compiler.py — MiniPar v2025.2
Ponto de entrada principal: orquestra todas as fases do compilador.

Uso:
  python compiler.py run    programa.minipar          # executa diretamente
  python compiler.py build  programa.minipar          # gera .c e compila com GCC
  python compiler.py tac    programa.minipar          # imprime TAC
  python compiler.py asm    programa.minipar          # imprime Assembly ARMv7
  python compiler.py check  programa.minipar          # apenas análise semântica
"""

import sys
import os
import argparse
import tempfile

# Adiciona src/ ao path caso seja chamado de fora
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from lexer      import Lexer,             LexerError
from parser     import Parser,            ParseError
from semantic   import SemanticAnalyzer,  SemanticError
from codegen    import CodeGenerator
from c_codegen  import CCodeGenerator
from arm_codegen import ArmCodeGenerator
from backend    import GCCBackend
from runner     import Runner
from symbol_table import SymbolTable


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline central
# ─────────────────────────────────────────────────────────────────────────────

class CompilerPipeline:
    """
    Encapsula todas as fases do compilador.
    Cada fase pode ser executada individualmente ou em cadeia.
    """

    def __init__(self, source: str, filename: str = '<stdin>'):
        self.source   = source
        self.filename = filename
        # Resultados de cada fase (preenchidos sob demanda)
        self.tokens   = None
        self.ast      = None
        self.table    = None
        self.tac      = None

    # ── Fase 1: Análise Léxica ────────────────────────────────────────────────

    def lex(self):
        try:
            lexer = Lexer(self.source)
            self.tokens = lexer.tokenize()
            return self.tokens
        except LexerError as e:
            self._die(f"[Léxico] {e}")

    # ── Fase 2: Análise Sintática ─────────────────────────────────────────────

    def parse(self):
        if self.tokens is None:
            self.lex()
        try:
            parser = Parser(self.tokens)
            self.ast = parser.parse()
            return self.ast
        except ParseError as e:
            self._die(f"[Sintático] {e}")

    # ── Fase 3: Análise Semântica ─────────────────────────────────────────────

    def analyze(self):
        if self.ast is None:
            self.parse()
        analyzer = SemanticAnalyzer()
        ok = analyzer.analyze(self.ast)
        self.table = analyzer.table
        if not ok:
            print(f"\n{'='*60}", file=sys.stderr)
            print(f" Erros semânticos em '{self.filename}':", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)
            for err in analyzer.errors:
                print(f"  {err}", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)
            sys.exit(1)
        return self.table

    # ── Fase 4: Geração de TAC ────────────────────────────────────────────────

    def generate_tac(self):
        if self.table is None:
            self.analyze()
        gen = CodeGenerator(self.table)
        gen.generate(self.ast)
        self.tac = gen.code
        return self.tac

    # ── Fase 5a: Backend C ────────────────────────────────────────────────────

    def generate_c(self) -> str:
        if self.tac is None:
            self.generate_tac()
        cgen = CCodeGenerator(self.table)
        return cgen.generate(self.tac)

    # ── Fase 5b: Backend ARMv7 ────────────────────────────────────────────────

    def generate_arm(self) -> str:
        if self.tac is None:
            self.generate_tac()
        agen = ArmCodeGenerator(self.table)
        return agen.generate(self.tac)

    # ── Execução direta (sem geração de código) ───────────────────────────────

    def run(self):
        if self.table is None:
            self.analyze()
        runner = Runner(symbol_table=self.table)
        runner.run(self.ast)

    # ── Compilar para executável nativo ──────────────────────────────────────

    def build(self, output_path: str, optimization: str = '-O2') -> bool:
        c_code = self.generate_c()
        cgen   = CCodeGenerator(self.table)
        cgen.generate(self.tac)   # re-run para detectar features

        features = set()
        if cgen.uses_parallel:   features.add('parallel')
        if cgen.uses_network:    features.add('distributed')
        if cgen.uses_oop:        features.add('oop')

        # Salvar código C em arquivo temporário
        with tempfile.NamedTemporaryFile(
            suffix='.c', mode='w', delete=False, encoding='utf-8'
        ) as f:
            f.write(c_code)
            c_file = f.name

        try:
            gcc = GCCBackend()
            result = gcc.compile_to_executable(
                c_file, output_path, features, optimization
            )
            if result.success:
                print(f"[build] Executável gerado: {output_path}")
            else:
                print(f"[build] Falha na compilação C:\n{result.stderr}",
                      file=sys.stderr)
            return result.success
        finally:
            os.unlink(c_file)

    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _die(msg: str):
        print(msg, file=sys.stderr)
        sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='minipar',
        description='Compilador MiniPar v2025.2',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
comandos:
  run    <arquivo>   Executa diretamente (interpreta a AST)
  build  <arquivo>   Compila para executável nativo via GCC
  tac    <arquivo>   Imprime o código intermediário (TAC)
  asm    <arquivo>   Imprime o Assembly ARMv7 gerado
  check  <arquivo>   Apenas análise léxica, sintática e semântica
  tokens <arquivo>   Imprime os tokens (fase léxica)

exemplos:
  python compiler.py run   examples/ponto.minipar
  python compiler.py build examples/fibonacci.minipar -o fibonacci
  python compiler.py tac   examples/quicksort.minipar
  python compiler.py asm   examples/fatorial.minipar
        """
    )
    p.add_argument('command', choices=['run','build','tac','asm','check','tokens'])
    p.add_argument('file',    help='Arquivo .minipar de entrada')
    p.add_argument('-o', '--output', default='',
                   help='Caminho do executável de saída (para build)')
    p.add_argument('-O', '--opt', default='-O2',
                   choices=['-O0','-O1','-O2','-O3','-Os'],
                   help='Nível de otimização do GCC (padrão: -O2)')
    p.add_argument('--show-c',   action='store_true',
                   help='(build) também imprime o código C gerado')
    p.add_argument('--show-tac', action='store_true',
                   help='(build/asm) também imprime o TAC')
    return p


def main():
    parser = build_arg_parser()
    args   = parser.parse_args()

    # Ler arquivo fonte
    if not os.path.isfile(args.file):
        print(f"Arquivo não encontrado: {args.file}", file=sys.stderr)
        sys.exit(1)

    with open(args.file, encoding='utf-8') as f:
        source = f.read()

    pipeline = CompilerPipeline(source, filename=args.file)

    # ── tokens ────────────────────────────────────────────────────────────────
    if args.command == 'tokens':
        tokens = pipeline.lex()
        print(f"\n{'='*60}")
        print(f"  TOKENS — {args.file}")
        print(f"{'='*60}")
        for tok in tokens:
            print(f"  {tok}")
        print()

    # ── check ─────────────────────────────────────────────────────────────────
    elif args.command == 'check':
        pipeline.analyze()
        print(f"[check] OK — sem erros em '{args.file}'")
        pipeline.table.print_table()

    # ── tac ───────────────────────────────────────────────────────────────────
    elif args.command == 'tac':
        tac = pipeline.generate_tac()
        print(f"\n{'='*60}")
        print(f"  THREE-ADDRESS CODE — {args.file}")
        print(f"{'='*60}")
        for i, instr in enumerate(tac):
            print(f"  {i:4d}: {instr}")
        print()

    # ── asm ───────────────────────────────────────────────────────────────────
    elif args.command == 'asm':
        if args.show_tac:
            tac = pipeline.generate_tac()
            print(f"\n{'='*60}")
            print(f"  TAC — {args.file}")
            print(f"{'='*60}")
            for i, instr in enumerate(tac):
                print(f"  {i:4d}: {instr}")
        asm = pipeline.generate_arm()
        print(f"\n{'='*60}")
        print(f"  ARM ASSEMBLY v7 — {args.file}")
        print(f"{'='*60}")
        print(asm)

    # ── run ───────────────────────────────────────────────────────────────────
    elif args.command == 'run':
        pipeline.run()

    # ── build ─────────────────────────────────────────────────────────────────
    elif args.command == 'build':
        if args.show_tac:
            tac = pipeline.generate_tac()
            print(f"\n{'='*60}\n  TAC\n{'='*60}")
            for i, instr in enumerate(tac):
                print(f"  {i:4d}: {instr}")

        c_code = pipeline.generate_c()

        if args.show_c:
            print(f"\n{'='*60}\n  CÓDIGO C GERADO\n{'='*60}")
            for i, line in enumerate(c_code.splitlines(), 1):
                print(f"  {i:4d}  {line}")

        # Determinar caminho de saída
        output = args.output or os.path.splitext(args.file)[0]
        pipeline.build(output, optimization=args.opt)


if __name__ == '__main__':
    main()
