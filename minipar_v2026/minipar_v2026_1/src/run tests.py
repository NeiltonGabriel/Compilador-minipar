#!/usr/bin/env python3
"""
run_tests.py — suíte de regressão (esqueleto / ponto de partida).
Para cada examples/*.minipar: roda o interpretador (referência), o executável C
e o armsim (ARM), e compara as saídas.

Limitação conhecida: a impressão de double no ARM pode divergir no último dígito
de dízimas; este script reporta como 'DIGITO' (diferença só no fim de número).

Uso: python3 tests/run_tests.py
"""
import os, sys, glob, subprocess, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC  = os.path.join(ROOT, 'src')
sys.path.insert(0, SRC)
from compiler import CompilerPipeline   # noqa: E402


def interp(path):
    out = subprocess.run([sys.executable, os.path.join(ROOT, 'compiler.py'),
                          'run', path], capture_output=True, text=True)
    return out.stdout


def gen_c_exe(src, name):
    p = CompilerPipeline(src, name)
    c = p.generate_c()
    cf = tempfile.NamedTemporaryFile(suffix='.c', delete=False, mode='w')
    cf.write(c); cf.close()
    exe = cf.name[:-2]
    r = subprocess.run(['gcc', cf.name, '-o', exe, '-lm',
                        '-Wno-unused-function'], capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    return out.stdout, ''


def gen_arm(src, name):
    p = CompilerPipeline(src, name)
    s = p.generate_arm()
    sf = tempfile.NamedTemporaryFile(suffix='.s', delete=False, mode='w')
    sf.write(s); sf.close()
    out = subprocess.run([sys.executable, os.path.join(ROOT, 'armsim.py'),
                          sf.name], capture_output=True, text=True)
    return out.stdout


def main():
    examples = sorted(glob.glob(os.path.join(ROOT, 'examples', '*.minipar')))
    for path in examples:
        name = os.path.splitext(os.path.basename(path))[0]
        src = open(path).read()
        ref = interp(path)
        c_out, err = gen_c_exe(src, name)
        a_out = gen_arm(src, name)
        c_ok = (c_out == ref)
        a_ok = (a_out == c_out)
        print(f'{name:32s}  C={"OK" if c_ok else "DIFERE"}'
              f'  ARM={"OK" if a_ok else "DIFERE"}')


if __name__ == '__main__':
    main()