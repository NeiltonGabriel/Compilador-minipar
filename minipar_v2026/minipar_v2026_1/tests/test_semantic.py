"""
tests/test_semantic.py — Testes do Analisador Semântico MiniPar v2025.2
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from lexer     import Lexer
from parser    import Parser
from semantic  import SemanticAnalyzer


def analyze(src):
    tokens = Lexer(src).tokenize()
    ast    = Parser(tokens).parse()
    sem    = SemanticAnalyzer()
    ok     = sem.analyze(ast)
    return ok, sem.errors, sem.table


def assert_ok(src):
    ok, errs, _ = analyze(src)
    assert ok, f"Erros inesperados: {errs}"

def assert_error(src, fragmento=''):
    ok, errs, _ = analyze(src)
    assert not ok, "Esperava erro semântico, mas passou"
    if fragmento:
        combined = ' '.join(errs)
        assert fragmento.lower() in combined.lower(), \
            f"Fragmento '{fragmento}' não encontrado em erros: {errs}"


# ─── v2025.1: semântica existente ────────────────────────────────────────────

class TestSemanticaV1:
    def test_var_decl_ok(self):
        assert_ok("var number x = 42;")

    def test_var_nao_declarada(self):
        assert_error("func void f() { return y; }", "não declarado")

    def test_var_redeclarada(self):
        assert_error("func void f() { var number x; var number x; }", "já declarada")

    def test_tipo_incompativel_atribuicao(self):
        assert_error("var number x = true;", "incompatível")

    def test_return_tipo_errado(self):
        assert_error(
            "func number f() { return true; }",
            "retorno incompatível"
        )

    def test_return_fora_funcao(self):
        assert_error("return 1;", "fora de função")

    def test_break_fora_loop(self):
        assert_error("func void f() { break; }", "fora de loop")

    def test_continue_fora_loop(self):
        assert_error("func void f() { continue; }", "fora de loop")

    def test_condicao_while_nao_bool(self):
        assert_error(
            "func void f() { while (42) { } }",
            "bool"
        )

    def test_condicao_if_nao_bool(self):
        assert_error(
            "func void f() { if (42) { } }",
            "bool"
        )

    def test_funcao_nao_definida(self):
        assert_error("func void f() { inexistente(); }", "não definida")

    def test_funcao_ok(self):
        assert_ok("""
        func number soma(var number a, var number b) {
            return a + b;
        }
        var number r = soma(1, 2);
        """)

    def test_escopo_correto(self):
        assert_ok("""
        func void f() {
            var number x = 1;
            if (x == 1) {
                var number y = 2;
            }
        }
        """)

    def test_any_compativel_com_tudo(self):
        assert_ok("var any x = 42; var any y = true; var any z = \"texto\";")


# ─── v2025.2: POO ────────────────────────────────────────────────────────────

class TestSemanticaPOO:
    def test_class_simples_ok(self):
        assert_ok("""
        class Ponto {
            var number x;
            var number y;
        }
        var Ponto p = new Ponto();
        """)

    def test_new_classe_inexistente(self):
        assert_error("var Foo f = new Foo();", "não definida")

    def test_member_access_campo_invalido(self):
        assert_error("""
        class A { var number x; }
        var A obj = new A();
        var number v = obj.inexistente;
        """, "não existe")

    def test_method_call_ok(self):
        assert_ok("""
        class Calc {
            func number dobro(var number n) {
                return n * 2;
            }
        }
        var Calc c = new Calc();
        var number r = c.dobro(5);
        """)

    def test_method_inexistente(self):
        assert_error("""
        class A { var number x; }
        var A obj = new A();
        var number r = obj.metodoInexistente();
        """, "não existe")

    def test_heranca_ok(self):
        assert_ok("""
        class Base {
            var number valor;
            func number get() { return valor; }
        }
        class Sub extends Base {
            var string nome;
        }
        var Sub s = new Sub();
        """)

    def test_superclasse_inexistente(self):
        assert_error("""
        class Sub extends BaseInexistente {
            var number x;
        }
        """, "não definida")

    def test_override_sem_metodo_na_super(self):
        assert_error("""
        class Base { var number x; }
        class Sub extends Base {
            override func string descricao() { return "sub"; }
        }
        """, "override")

    def test_override_ok(self):
        assert_ok("""
        class Base {
            func string descricao() { return "base"; }
        }
        class Sub extends Base {
            override func string descricao() { return "sub"; }
        }
        """)

    def test_this_fora_de_classe(self):
        assert_error("func void f() { var any x = this; }", "fora de contexto")

    def test_new_classe_abstrata(self):
        assert_error("""
        abstract class Base { func number f() { return 0; } }
        var Base b = new Base();
        """, "abstrata")

    def test_subtype_polimorfismo(self):
        # Sub é subtipo de Base — atribuição deve ser válida
        assert_ok("""
        class Base { var number x; }
        class Sub extends Base { var number y; }
        var Sub s = new Sub();
        """)


# ─── v2025.2: Paralelismo ────────────────────────────────────────────────────

class TestSemanticaParalelismo:
    def test_par_block_ok(self):
        assert_ok("""
        func void f() { }
        func void g() { }
        par { f(); g(); }
        """)

    def test_spawn_funcao_ok(self):
        assert_ok("""
        func void worker(var number n) { return; }
        var number x = 1;
        spawn worker(x);
        """)

    def test_sync_lock_nao_declarado(self):
        assert_error("""
        func void f() {
            sync(lock_inexistente) { var number x = 1; }
        }
        """, "não declarada")

    def test_sync_ok(self):
        assert_ok("""
        var number meu_lock;
        meu_lock = 0;
        func void f() {
            sync(meu_lock) { meu_lock = meu_lock + 1; }
        }
        """)

    def test_async_func_ok(self):
        assert_ok("""
        async func number calcular(var number n) {
            return n * 2;
        }
        """)


# ─── v2025.2: Distribuição ───────────────────────────────────────────────────

class TestSemanticaDistribuicao:
    def test_node_decl_ok(self):
        assert_ok('node meu_server = "127.0.0.1:7000";')

    def test_remote_func_ok(self):
        assert_ok("""
        remote func number somar(var number a, var number b) {
            return a + b;
        }
        """)

    def test_remote_call_no_invalido(self):
        assert_error("""
        remote func number f(var number x) { return x; }
        func void g() { remote on no_invalido f(1); }
        """, "não declarado")

    def test_remote_call_func_nao_remote(self):
        assert_error("""
        node server = "127.0.0.1:7000";
        func number f(var number x) { return x; }
        func void g() { remote on server f(1); }
        """, "não é marcada como 'remote'")

    def test_remote_func_tipo_nao_serializavel(self):
        assert_error("""
        class MinhaClasse { var number x; }
        remote func MinhaClasse criarObjeto() {
            return new MinhaClasse();
        }
        """, "serializável")

    def test_remote_call_completo_ok(self):
        assert_ok("""
        node servidor = "127.0.0.1:9000";
        remote func number dobrar(var number n) { return n * 2; }
        func void main() {
            remote on servidor dobrar(5);
        }
        """)
