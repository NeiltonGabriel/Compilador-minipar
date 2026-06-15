"""
tests/test_codegen_and_runner.py
Testes do gerador de TAC, backends C/ARM e executor (runner).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from lexer       import Lexer
from parser      import Parser
from semantic    import SemanticAnalyzer
from codegen     import CodeGenerator, TACOp
from c_codegen   import CCodeGenerator
from arm_codegen import ArmCodeGenerator
from runner      import Runner


def full_pipeline(src):
    tokens  = Lexer(src).tokenize()
    ast     = Parser(tokens).parse()
    sem     = SemanticAnalyzer()
    sem.analyze(ast)
    table   = sem.table
    cg      = CodeGenerator(table)
    cg.generate(ast)
    return ast, table, cg.code


def run_program(src):
    """Executa o programa via runner e retorna a lista de prints capturados."""
    tokens = Lexer(src).tokenize()
    ast    = Parser(tokens).parse()
    sem    = SemanticAnalyzer()
    sem.analyze(ast)
    runner = Runner(symbol_table=sem.table)
    runner.run(ast)


def tac_ops(src):
    _, _, code = full_pipeline(src)
    return [i.op for i in code]


# ─── TAC: instruções existentes v2025.1 ──────────────────────────────────────

class TestTACV1:
    def test_assign_gera_tac(self):
        ops = tac_ops("var number x = 42;")
        assert TACOp.ASSIGN in ops

    def test_binaria_gera_tac(self):
        ops = tac_ops("var number x = 2 + 3;")
        assert TACOp.ADD in ops

    def test_if_gera_if_false_e_labels(self):
        src = "func void f() { if (x > 0) { var number y = 1; } }"
        ops = tac_ops(src)
        assert TACOp.IF_FALSE in ops
        assert TACOp.LABEL    in ops
        assert TACOp.GOTO     in ops

    def test_while_gera_loop(self):
        src = "func void f() { while (x > 0) { x = x - 1; } }"
        ops = tac_ops(src)
        assert TACOp.IF_FALSE in ops
        assert TACOp.GOTO     in ops

    def test_func_begin_end(self):
        src = "func number f() { return 1; }"
        ops = tac_ops(src)
        assert TACOp.FUNC_BEGIN in ops
        assert TACOp.FUNC_END   in ops
        assert TACOp.RETURN     in ops

    def test_call_gera_params(self):
        src = "func void f() { print(1, 2); }"
        ops = tac_ops(src)
        assert TACOp.PARAM in ops
        assert TACOp.CALL  in ops


# ─── TAC: POO ────────────────────────────────────────────────────────────────

class TestTACPOO:
    def test_alloc_obj(self):
        src = """
        class Ponto { var number x; }
        var Ponto p = new Ponto();
        """
        ops = tac_ops(src)
        assert TACOp.ALLOC_OBJ in ops

    def test_set_vtable(self):
        src = """
        class A { var number x; }
        var A a = new A();
        """
        ops = tac_ops(src)
        assert TACOp.SET_VTABLE in ops

    def test_store_load_field(self):
        src = """
        class Ponto { var number x; var number y; }
        var Ponto p = new Ponto();
        p.x = 10;
        var number v = p.x;
        """
        ops = tac_ops(src)
        assert TACOp.STORE_FIELD in ops
        assert TACOp.LOAD_FIELD  in ops

    def test_vcall_com_heranca(self):
        src = """
        class Base { func string nome() { return "base"; } }
        class Sub extends Base {
            override func string nome() { return "sub"; }
        }
        var Sub s = new Sub();
        var string n = s.nome();
        """
        ops = tac_ops(src)
        assert TACOp.LOAD_VTABLE in ops
        assert TACOp.VCALL       in ops

    def test_static_call_sem_heranca(self):
        src = """
        class Calc { func number dobro(var number n) { return n * 2; } }
        var Calc c = new Calc();
        var number r = c.dobro(5);
        """
        ops = tac_ops(src)
        assert TACOp.STATIC_CALL in ops

    def test_construtor_sintetico(self):
        src = """
        class A { var number x; var number y; }
        var A a = new A();
        """
        _, _, code = full_pipeline(src)
        # FUNC_BEGIN deve conter label do construtor
        begins = [i.arg1 for i in code if i.op == TACOp.FUNC_BEGIN]
        assert any('___ctor' in (b or '') for b in begins)


# ─── TAC: Paralelismo ────────────────────────────────────────────────────────

class TestTACParalelismo:
    def test_par_block_gera_spawn_join(self):
        src = """
        func void f() { var number x = 1; }
        func void g() { var number x = 2; }
        par { f(); g(); }
        """
        ops = tac_ops(src)
        assert TACOp.PAR_BEGIN    in ops
        assert TACOp.PAR_END      in ops
        assert TACOp.SPAWN_THREAD in ops
        assert TACOp.THREAD_JOIN  in ops

    def test_spawn_stmt(self):
        src = """
        func void worker(var number n) { return; }
        spawn worker(1);
        """
        ops = tac_ops(src)
        assert TACOp.SPAWN_THREAD in ops

    def test_sync_stmt_gera_mutex(self):
        src = """
        var number lock;
        lock = 0;
        func void f() {
            sync(lock) { lock = lock + 1; }
        }
        """
        ops = tac_ops(src)
        assert TACOp.MUTEX_INIT   in ops
        assert TACOp.MUTEX_LOCK   in ops
        assert TACOp.MUTEX_UNLOCK in ops


# ─── TAC: Distribuição ───────────────────────────────────────────────────────

class TestTACDistribuicao:
    def test_node_decl_gera_connect(self):
        src = 'node server = "127.0.0.1:7000";'
        ops = tac_ops(src)
        assert TACOp.CONNECT_NODE in ops

    def test_remote_func_gera_dois_blocos(self):
        src = """
        remote func number f(var number x) { return x; }
        """
        _, _, code = full_pipeline(src)
        begins = [i.arg1 for i in code if i.op == TACOp.FUNC_BEGIN]
        assert any(b and b.startswith('__impl_') for b in begins)
        assert any(b == 'f' for b in begins)

    def test_remote_func_gera_serialize_rpc(self):
        src = """
        remote func number somar(var number a, var number b) {
            return a + b;
        }
        """
        ops = tac_ops(src)
        assert TACOp.SERIALIZE    in ops
        assert TACOp.PARAM_REMOTE in ops
        assert TACOp.RPC_CALL     in ops
        assert TACOp.DESERIALIZE  in ops


# ─── Backend C ───────────────────────────────────────────────────────────────

class TestBackendC:
    def _gen_c(self, src):
        ast, table, tac = full_pipeline(src)
        cgen = CCodeGenerator(table)
        return cgen.generate(tac)

    def test_gera_includes_base(self):
        c = self._gen_c("var number x = 1;")
        assert '#include <stdio.h>' in c
        assert '#include <stdlib.h>' in c

    def test_gera_pthread_quando_par(self):
        src = """
        func void f() { var number x = 1; }
        par { f(); f(); }
        """
        c = self._gen_c(src)
        assert 'pthread.h' in c

    def test_gera_struct_para_classe(self):
        src = """
        class Ponto { var number x; var number y; }
        var Ponto p = new Ponto();
        """
        c = self._gen_c(src)
        assert 'typedef struct Ponto' in c
        assert 'void** __vtable' in c

    def test_gera_vtable_global(self):
        src = """
        class A {
            func number f() { return 1; }
        }
        var A a = new A();
        """
        c = self._gen_c(src)
        assert '__vtable_A' in c

    def test_gera_malloc_para_alloc_obj(self):
        src = """
        class A { var number x; }
        var A a = new A();
        """
        c = self._gen_c(src)
        assert 'malloc' in c

    def test_gera_cjson_quando_remote(self):
        src = """
        remote func number f(var number x) { return x; }
        """
        c = self._gen_c(src)
        assert 'cjson' in c.lower()

    def test_gera_mutex_global(self):
        src = """
        var number lock; lock = 0;
        func void f() { sync(lock) { lock = lock + 1; } }
        """
        c = self._gen_c(src)
        assert 'pthread_mutex_t' in c
        assert 'PTHREAD_MUTEX_INITIALIZER' in c


# ─── Backend ARMv7 ───────────────────────────────────────────────────────────

class TestBackendARM:
    def _gen_arm(self, src):
        ast, table, tac = full_pipeline(src)
        agen = ArmCodeGenerator(table)
        return agen.generate(tac)

    def test_gera_secao_text(self):
        asm = self._gen_arm("var number x = 1;")
        assert '.text' in asm

    def test_gera_global_main(self):
        asm = self._gen_arm("var number x = 1;")
        assert '.global main' in asm

    def test_gera_heap_simulado(self):
        src = """
        class A { var number x; }
        var A a = new A();
        """
        asm = self._gen_arm(src)
        assert '__heap_ptr' in asm
        assert '__heap_base' in asm

    def test_gera_vtable_em_rodata(self):
        src = """
        class A { func number f() { return 1; } }
        var A a = new A();
        """
        asm = self._gen_arm(src)
        assert '__vtable_A' in asm
        assert '.rodata' in asm

    def test_gera_bl_para_call(self):
        src = "func void f() { } f();"
        asm = self._gen_arm(src)
        assert 'bl f' in asm or 'bl  f' in asm

    def test_par_gera_comentario_cpulator(self):
        src = """
        func void f() { var number x = 1; }
        par { f(); f(); }
        """
        asm = self._gen_arm(src)
        assert 'CPUlator' in asm or 'simulad' in asm

    def test_remote_gera_stub(self):
        src = 'node s = "127.0.0.1:7000";'
        asm = self._gen_arm(src)
        assert 'stub' in asm.lower() or 'CONNECT_NODE' in asm


# ─── Runner ──────────────────────────────────────────────────────────────────

class TestRunner:
    def _run_capture(self, src):
        """Executa e captura print via monkeypatch."""
        outputs = []
        import builtins
        orig = builtins.print
        builtins.print = lambda *a, **kw: outputs.append(' '.join(str(x) for x in a))
        try:
            run_program(src)
        finally:
            builtins.print = orig
        return outputs

    def test_aritmetica_basica(self):
        out = self._run_capture("print(2 + 3 * 4);")
        assert '14.0' in out[0]

    def test_if_else(self):
        out = self._run_capture("""
        var number x = 10;
        if (x > 5) { print("maior"); } else { print("menor"); }
        """)
        assert 'maior' in out[0]

    def test_while(self):
        out = self._run_capture("""
        var number soma = 0;
        var number i = 1;
        while (i <= 5) {
            soma = soma + i;
            i = i + 1;
        }
        print(soma);
        """)
        assert '15.0' in out[0]

    def test_funcao_recursiva(self):
        out = self._run_capture("""
        func number fat(var number n) {
            if (n == 0) { return 1; }
            return n * fat(n - 1);
        }
        print(fat(5));
        """)
        assert '120.0' in out[0]

    def test_new_e_campo(self):
        out = self._run_capture("""
        class Ponto {
            var number x;
            var number y;
            func Ponto(var number px, var number py) {
                x = px;
                y = py;
            }
        }
        var Ponto p = new Ponto(3, 4);
        print(p.x);
        print(p.y);
        """)
        assert '3.0' in out[0]
        assert '4.0' in out[1]

    def test_metodo_de_classe(self):
        out = self._run_capture("""
        class Calc {
            func number dobro(var number n) {
                return n * 2;
            }
        }
        var Calc c = new Calc();
        print(c.dobro(7));
        """)
        assert '14.0' in out[0]

    def test_heranca_e_override(self):
        out = self._run_capture("""
        class Animal {
            func string falar() { return "..."; }
        }
        class Cachorro extends Animal {
            override func string falar() { return "Au!"; }
        }
        var Cachorro d = new Cachorro();
        print(d.falar());
        """)
        assert 'Au!' in out[0]

    def test_par_block_executa(self):
        # Par deve executar (em qualquer ordem) e não travar
        out = self._run_capture("""
        var number x = 0;
        func void inc() { x = x + 1; }
        par { inc(); inc(); inc(); }
        print(x);
        """)
        # Resultado deve ser 3 (com ou sem threads)
        assert '3' in out[0]

    def test_sync_protege_contador(self):
        out = self._run_capture("""
        var number contador = 0;
        var number lock = 0;
        func void inc() {
            sync(lock) { contador = contador + 1; }
        }
        par { inc(); inc(); inc(); inc(); inc(); }
        print(contador);
        """)
        assert '5' in out[0]

    def test_listas(self):
        out = self._run_capture("""
        var list l = [10, 20, 30];
        print(len(l));
        print(l[1]);
        """)
        assert '3' in out[0]
        assert '20' in out[1]

    def test_backward_compat_fibonacci(self):
        out = self._run_capture("""
        func number fib(var number n) {
            if (n == 0) { return 0; }
            if (n == 1) { return 1; }
            return fib(n - 1) + fib(n - 2);
        }
        print(fib(10));
        """)
        assert '55' in out[0]
