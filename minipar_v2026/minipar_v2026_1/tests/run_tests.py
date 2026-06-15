#!/usr/bin/env python3
"""
run_tests.py — suite de regressao de paridade do compilador MiniPar.

Para cada exemplo da SUITE, executa:
  - o interpretador (compiler.py run)  -> REFERENCIA de correcao
  - o executavel C (gerado + GCC)
  - o backend ARM (via armsim.py)
e compara as saidas.

Status por backend:
  OK     -> saida identica a referencia
  DIGITO -> difere apenas no ultimo digito de um double (mesmo valor numerico);
            limitacao conhecida da impressao de double no ARM (ver MANUAL,
            secao 5). Conta como aprovado.
  DIFERE -> saida textual diferente -> FALHA
  ERRO   -> o backend nao compilou/executou -> FALHA

Os exemplos de paralelismo/distribuicao (Passo 3) ainda nao tem backend C/ARM;
sao executados apenas no interpretador como smoke test.

Uso: python3 tests/run_tests.py
"""
import os, sys, glob, subprocess, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC  = os.path.join(ROOT, 'src')
EXAMPLES = os.path.join(ROOT, 'examples')
sys.path.insert(0, SRC)
from compiler import CompilerPipeline  # noqa: E402

# Exemplos com paridade total esperada nos tres alvos.
SUITE = [
    '01_aritmetica', '02_controle_fluxo', '03_recursao', '04_funcoes',
    '05_poo_campos', '06_poo_polimorfismo', '07_dizima', '08_builtins',
    'heranca_super', 'ponto',
    # Passo 3 (determinísticos: saída independe da ordem das threads):
    '09_par_contador', 'distribuido', 'hibrido',
]
# Exemplos só validados no interpretador:
#  - paralelo: usa prints dentro do par{}; a ORDEM no C (pthreads reais) é
#    não-determinística (no ARM roda sequencial e bate). Demo, não regressão.
#  - fila: depende de list/dict, sem runtime nos backends (Passo 3 futuro).
INTERP_ONLY = ['paralelo', 'fila']


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def interp(path):
    r = _run([sys.executable, os.path.join(SRC, 'compiler.py'), 'run', path])
    return r.stdout, r.returncode, r.stderr


def c_output(src, name):
    try:
        c = CompilerPipeline(src, name).generate_c()
    except Exception as e:
        return None, f'gen_c: {e}'
    with tempfile.NamedTemporaryFile(suffix='.c', delete=False, mode='w') as f:
        f.write(c); cf = f.name
    exe = cf[:-2]
    r = _run(['gcc', cf, '-o', exe, '-lm', '-lpthread', '-Wno-unused-function',
              '-Wno-incompatible-pointer-types'])
    if r.returncode != 0:
        return None, r.stderr.strip().splitlines()[-1] if r.stderr else 'gcc falhou'
    out = _run([exe])
    return out.stdout, ''


def arm_output(src, name):
    try:
        s = CompilerPipeline(src, name).generate_arm()
    except Exception as e:
        return None, f'gen_arm: {e}'
    with tempfile.NamedTemporaryFile(suffix='.s', delete=False, mode='w') as f:
        f.write(s); sf = f.name
    r = _run([sys.executable, os.path.join(SRC, 'armsim.py'), sf])
    if r.returncode != 0:
        return None, (r.stderr.strip().splitlines()[-1] if r.stderr else 'armsim falhou')
    return r.stdout, ''


def compare(ref, got):
    """OK / DIGITO / DIFERE, tolerando diferenca so de formatacao de double."""
    if got is None:
        return 'ERRO'
    if ref == got:
        return 'OK'
    lr, lg = ref.splitlines(), got.splitlines()
    if len(lr) != len(lg):
        return 'DIFERE'
    digit = False
    for a, b in zip(lr, lg):
        if a == b:
            continue
        ta, tb = a.split(), b.split()
        if len(ta) != len(tb):
            return 'DIFERE'
        for u, v in zip(ta, tb):
            if u == v:
                continue
            try:
                if float(u) == float(v):
                    digit = True
                    continue
            except ValueError:
                return 'DIFERE'
            return 'DIFERE'
    return 'DIGITO' if digit else 'OK'


def main():
    print('=' * 64)
    print('  SUITE DE PARIDADE — MiniPar (interpretador = referencia)')
    print('=' * 64)
    print(f'  {"exemplo":24s} {"C":8s} {"ARM":8s}')
    print('-' * 64)

    failures = 0
    PASS = {'OK', 'DIGITO'}
    for name in SUITE:
        path = os.path.join(EXAMPLES, name + '.minipar')
        if not os.path.isfile(path):
            print(f'  {name:24s} (ausente)')
            failures += 1
            continue
        src = open(path).read()
        ref, rc, err = interp(path)
        if rc != 0:
            print(f'  {name:24s} INTERP ERRO: {err.strip()[:40]}')
            failures += 1
            continue
        c_out, c_err = c_output(src, name)
        a_out, a_err = arm_output(src, name)
        sc = compare(ref, c_out)
        sa = compare(ref, a_out)
        extra = ''
        if sc == 'ERRO':
            extra += f'  C:{c_err[:40]}'
        if sa == 'ERRO':
            extra += f'  ARM:{a_err[:40]}'
        print(f'  {name:24s} {sc:8s} {sa:8s}{extra}')
        if sc not in PASS or sa not in PASS:
            failures += 1

    print('-' * 64)
    print('  Smoke test (apenas interpretador — Passo 3):')
    for name in INTERP_ONLY:
        path = os.path.join(EXAMPLES, name + '.minipar')
        if not os.path.isfile(path):
            continue
        _, rc, err = interp(path)
        status = 'OK' if rc == 0 else f'ERRO: {err.strip()[:40]}'
        print(f'  {name:24s} {status}')

    print('=' * 64)
    if failures:
        print(f'  RESULTADO: {failures} falha(s) na suite.')
        sys.exit(1)
    print('  RESULTADO: suite verde (OK/DIGITO em todos os alvos).')


if __name__ == '__main__':
    main()
