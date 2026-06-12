"""
backend.py — MiniPar v2025.2
Invocador do GCC: compila o arquivo .c gerado pelo CCodeGenerator
para um executável nativo, com as flags corretas para cada feature.
"""

import subprocess
import shutil
import os
import sys
from dataclasses import dataclass, field
from typing import Optional, Set


@dataclass
class BackendInfo:
    gcc_path:    str
    version:     str
    has_pthread: bool
    has_cjson:   bool
    has_math:    bool


@dataclass
class CompileResult:
    success:     bool
    output_path: str = ''
    stdout:      str = ''
    stderr:      str = ''
    returncode:  int = 0


class GCCBackend:
    """
    Invoca o GCC para compilar código C gerado pelo CCodeGenerator.
    Detecta automaticamente quais bibliotecas são necessárias.
    """

    def __init__(self, gcc_path: str = 'gcc'):
        self.gcc_path = gcc_path
        self._info: Optional[BackendInfo] = None

    # ─────────────────────────────────────────────────────────────────────────
    # API pública
    # ─────────────────────────────────────────────────────────────────────────

    def compile_to_executable(
        self,
        c_file:       str,
        output_exe:   str,
        features:     Optional[Set[str]] = None,
        optimization: str = '-O2',
        extra_flags:  Optional[list] = None,
    ) -> CompileResult:
        """
        Compila c_file para output_exe.

        features: conjunto de strings indicando quais bibliotecas usar:
            'parallel'    → -lpthread
            'distributed' → -lcjson
            'math'        → -lm

        Retorna CompileResult com sucesso/falha e saídas do compilador.
        """
        if not os.path.isfile(c_file):
            return CompileResult(
                success=False,
                stderr=f'Arquivo C não encontrado: {c_file}'
            )

        features = features or set()
        cmd = self._build_command(c_file, output_exe, features,
                                  optimization, extra_flags or [])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            success = result.returncode == 0
            return CompileResult(
                success=success,
                output_path=output_exe if success else '',
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode
            )
        except FileNotFoundError:
            return CompileResult(
                success=False,
                stderr=f'GCC não encontrado: {self.gcc_path}\n'
                       f'Instale com: sudo apt install gcc'
            )
        except subprocess.TimeoutExpired:
            return CompileResult(
                success=False,
                stderr='Timeout: compilação demorou mais de 60 segundos.'
            )

    def compile_and_run(
        self,
        c_file:     str,
        features:   Optional[Set[str]] = None,
        stdin_data: str = '',
    ) -> CompileResult:
        """
        Compila e executa imediatamente, retornando a saída do programa.
        Útil para testes rápidos.
        """
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='', delete=False) as tmp:
            exe_path = tmp.name
        try:
            compile_result = self.compile_to_executable(c_file, exe_path, features)
            if not compile_result.success:
                return compile_result
            run_result = subprocess.run(
                [exe_path],
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=30
            )
            return CompileResult(
                success=True,
                output_path=exe_path,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
                returncode=run_result.returncode
            )
        finally:
            if os.path.exists(exe_path):
                os.unlink(exe_path)

    def get_info(self) -> BackendInfo:
        """Coleta informações sobre o GCC e bibliotecas disponíveis."""
        if self._info:
            return self._info

        # Versão do GCC
        try:
            result = subprocess.run(
                [self.gcc_path, '--version'],
                capture_output=True, text=True
            )
            version = result.stdout.split('\n')[0] if result.returncode == 0 else 'desconhecido'
        except FileNotFoundError:
            version = 'GCC não encontrado'

        # Verificar pthread
        has_pthread = self._check_lib('pthread')

        # Verificar cJSON
        has_cjson = self._check_lib('cjson') or self._check_header('cjson/cJSON.h')

        # math.h está sempre disponível
        has_math = True

        self._info = BackendInfo(
            gcc_path=self.gcc_path,
            version=version,
            has_pthread=has_pthread,
            has_cjson=has_cjson,
            has_math=has_math
        )
        return self._info

    def print_info(self):
        info = self.get_info()
        print(f'GCC: {info.gcc_path}')
        print(f'Versão: {info.version}')
        print(f'pthreads: {"✓" if info.has_pthread else "✗  (sudo apt install libpthread-stubs0-dev)"}')
        print(f'cJSON:    {"✓" if info.has_cjson else "✗  (sudo apt install libcjson-dev)"}')

    # ─────────────────────────────────────────────────────────────────────────
    # Construção do comando GCC
    # ─────────────────────────────────────────────────────────────────────────

    def _build_command(
        self,
        c_file:       str,
        output_exe:   str,
        features:     Set[str],
        optimization: str,
        extra_flags:  list,
    ) -> list:
        cmd = [
            self.gcc_path,
            c_file,
            '-o', output_exe,
            '-std=c11',
            optimization,
            '-Wall',
            '-Wno-unused-variable',
            '-Wno-unused-function',
        ]

        # Bibliotecas condicionais por feature
        if 'parallel' in features or 'oop' in features:
            cmd.append('-lpthread')

        if 'distributed' in features or 'network' in features:
            cmd.append('-lcjson')

        # math.h sempre necessário (usamos funções matemáticas no runtime)
        cmd.append('-lm')

        # Flags extras do usuário
        cmd.extend(extra_flags)

        return cmd

    # ─────────────────────────────────────────────────────────────────────────
    # Verificação de bibliotecas
    # ─────────────────────────────────────────────────────────────────────────

    def _check_lib(self, lib: str) -> bool:
        """Verifica se uma biblioteca está disponível para linkagem."""
        import tempfile, os
        test_src = f'int main(void) {{ return 0; }}'
        with tempfile.NamedTemporaryFile(suffix='.c', mode='w', delete=False) as f:
            f.write(test_src)
            src = f.name
        with tempfile.NamedTemporaryFile(suffix='', delete=False) as f:
            out = f.name
        try:
            r = subprocess.run(
                [self.gcc_path, src, '-o', out, f'-l{lib}'],
                capture_output=True
            )
            return r.returncode == 0
        except FileNotFoundError:
            return False
        finally:
            for p in (src, out):
                if os.path.exists(p):
                    os.unlink(p)

    def _check_header(self, header: str) -> bool:
        """Verifica se um header existe no sistema."""
        import tempfile, os
        test_src = f'#include <{header}>\nint main(void) {{ return 0; }}'
        with tempfile.NamedTemporaryFile(suffix='.c', mode='w', delete=False) as f:
            f.write(test_src)
            src = f.name
        with tempfile.NamedTemporaryFile(suffix='', delete=False) as f:
            out = f.name
        try:
            r = subprocess.run(
                [self.gcc_path, src, '-o', out],
                capture_output=True
            )
            return r.returncode == 0
        except FileNotFoundError:
            return False
        finally:
            for p in (src, out):
                if os.path.exists(p):
                    os.unlink(p)
