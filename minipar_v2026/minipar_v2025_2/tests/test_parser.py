"""
tests/test_parser.py — Testes do Parser MiniPar v2025.2
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from lexer   import Lexer
from parser  import Parser, ParseError
from ast_nodes import *


def parse(src):
    tokens = Lexer(src).tokenize()
    return Parser(tokens).parse()

def first_decl(src):
    return parse(src).declarations[0]


# ─── v2025.1: construtos existentes ──────────────────────────────────────────

class TestParserV1:
    def test_var_decl_simples(self):
        node = first_decl("var number x;")
        assert isinstance(node, VarDecl)
        assert node.nome == 'x'
        assert node.tipo == 'number'
        assert node.inicializador is None

    def test_var_decl_com_inicializador(self):
        node = first_decl("var number x = 42;")
        assert isinstance(node, VarDecl)
        assert isinstance(node.inicializador, NumberLiteral)
        assert node.inicializador.valor == 42.0

    def test_func_decl(self):
        src = "func number soma(var number a, var number b) { return a + b; }"
        node = first_decl(src)
        assert isinstance(node, FuncDecl)
        assert node.nome == 'soma'
        assert node.tipo_retorno == 'number'
        assert len(node.parametros) == 2

    def test_if_stmt(self):
        src = "func void f() { if (x > 0) { return; } }"
        corpo = first_decl(src).corpo
        assert isinstance(corpo.statements[0], IfStmt)

    def test_if_else_stmt(self):
        src = "func void f() { if (x) { return; } else { return; } }"
        node = first_decl(src).corpo.statements[0]
        assert isinstance(node, IfStmt)
        assert node.corpo_senao is not None

    def test_while_stmt(self):
        src = "func void f() { while (x > 0) { x = x - 1; } }"
        node = first_decl(src).corpo.statements[0]
        assert isinstance(node, WhileStmt)

    def test_return_com_valor(self):
        src = "func number f() { return 42; }"
        node = first_decl(src).corpo.statements[0]
        assert isinstance(node, ReturnStmt)
        assert isinstance(node.expressao, NumberLiteral)

    def test_return_sem_valor(self):
        src = "func void f() { return; }"
        node = first_decl(src).corpo.statements[0]
        assert isinstance(node, ReturnStmt)
        assert node.expressao is None

    def test_break_continue(self):
        src = "func void f() { while(true) { break; continue; } }"
        stmts = first_decl(src).corpo.statements[0].corpo.statements
        assert isinstance(stmts[0], BreakStmt)
        assert isinstance(stmts[1], ContinueStmt)

    def test_par_block(self):
        src = "func void f() { par { print(1); print(2); } }"
        node = first_decl(src).corpo.statements[0]
        assert isinstance(node, ParBlock)
        assert len(node.statements) == 2

    def test_expressao_binaria_precedencia(self):
        src = "var number x = 2 + 3 * 4;"
        expr = first_decl(src).inicializador
        # + deve ser raiz; * deve ser filho direito
        assert isinstance(expr, BinaryExpr)
        assert expr.operador == '+'
        assert isinstance(expr.direita, BinaryExpr)
        assert expr.direita.operador == '*'

    def test_chamada_funcao(self):
        src = "func void f() { print(1, 2, 3); }"
        node = first_decl(src).corpo.statements[0]
        assert isinstance(node, ExprStmt)
        assert isinstance(node.expr, FunctionCall)
        assert len(node.expr.argumentos) == 3

    def test_atribuicao(self):
        src = "func void f() { x = 42; }"
        node = first_decl(src).corpo.statements[0]
        assert isinstance(node, AssignStmt)
        assert node.alvo == 'x'

    def test_lista_literal(self):
        src = "var list l = [1, 2, 3];"
        node = first_decl(src)
        assert isinstance(node.inicializador, ListLiteral)
        assert len(node.inicializador.elementos) == 3

    def test_dict_literal(self):
        src = 'var dict d = {"a": 1, "b": 2};'
        node = first_decl(src)
        assert isinstance(node.inicializador, DictLiteral)
        assert len(node.inicializador.pares) == 2

    def test_parse_error(self):
        with pytest.raises(ParseError):
            parse("func { }")   # falta tipo e nome


# ─── v2025.2: POO ────────────────────────────────────────────────────────────

class TestParserPOO:
    def test_class_simples(self):
        src = """
        class Ponto {
            var number x;
            var number y;
        }
        """
        node = first_decl(src)
        assert isinstance(node, ClassDecl)
        assert node.nome == 'Ponto'
        assert node.superclasse is None
        assert len(node.campos) == 2

    def test_class_com_heranca(self):
        src = """
        class Circulo extends Forma {
            var number raio;
        }
        """
        node = first_decl(src)
        assert isinstance(node, ClassDecl)
        assert node.superclasse == 'Forma'

    def test_class_com_construtor(self):
        src = """
        class Ponto {
            var number x;
            func Ponto(var number px) {
                x = px;
            }
        }
        """
        node = first_decl(src)
        assert node.construtor is not None
        assert isinstance(node.construtor, ConstructorDecl)
        assert node.construtor.class_name == 'Ponto'

    def test_class_com_metodo(self):
        src = """
        class Ponto {
            var number x;
            func number getX() {
                return x;
            }
        }
        """
        node = first_decl(src)
        assert len(node.metodos) == 1
        assert node.metodos[0].nome == 'getX'
        assert node.metodos[0].tipo_retorno == 'number'

    def test_class_metodo_override(self):
        src = """
        class Sub extends Base {
            override func string descricao() {
                return "sub";
            }
        }
        """
        node = first_decl(src)
        assert node.metodos[0].is_override is True

    def test_new_expr(self):
        src = "var Ponto p = new Ponto(1, 2);"
        node = first_decl(src)
        assert isinstance(node.inicializador, NewExpr)
        assert node.inicializador.class_name == 'Ponto'
        assert len(node.inicializador.argumentos) == 2

    def test_member_access(self):
        src = "func void f() { var number v = obj.campo; }"
        decl = first_decl(src).corpo.statements[0]
        assert isinstance(decl.inicializador, MemberAccessExpr)
        assert decl.inicializador.membro == 'campo'

    def test_method_call(self):
        src = "func void f() { var number r = obj.metodo(1, 2); }"
        decl = first_decl(src).corpo.statements[0]
        assert isinstance(decl.inicializador, MethodCallExpr)
        assert decl.inicializador.metodo == 'metodo'
        assert len(decl.inicializador.argumentos) == 2

    def test_this_expr(self):
        src = """
        class A {
            var number x;
            func number getX() { return this.x; }
        }
        """
        node = first_decl(src)
        ret = node.metodos[0].corpo.statements[0]
        assert isinstance(ret, ReturnStmt)
        assert isinstance(ret.expressao, MemberAccessExpr)
        assert isinstance(ret.expressao.obj, ThisExpr)

    def test_null_literal(self):
        src = "var any x = null;"
        node = first_decl(src)
        assert isinstance(node.inicializador, NullLiteral)

    def test_abstract_class(self):
        src = "abstract class Base { func number f() { return 0; } }"
        node = first_decl(src)
        assert node.is_abstract is True


# ─── v2025.2: Paralelismo ────────────────────────────────────────────────────

class TestParserParalelismo:
    def test_spawn_funcao(self):
        src = "func void f() { spawn calcular(x); }"
        node = first_decl(src).corpo.statements[0]
        assert isinstance(node, SpawnStmt)
        assert node.call_expr is not None
        assert node.body is None

    def test_spawn_bloco(self):
        src = "func void f() { spawn { print(1); } }"
        node = first_decl(src).corpo.statements[0]
        assert isinstance(node, SpawnStmt)
        assert node.call_expr is None
        assert node.body is not None

    def test_sync_stmt(self):
        src = "func void f() { sync(meu_lock) { x = x + 1; } }"
        node = first_decl(src).corpo.statements[0]
        assert isinstance(node, SyncStmt)
        assert node.lock_var == 'meu_lock'

    def test_async_func(self):
        src = "async func number calcular(var number n) { return n; }"
        node = first_decl(src)
        assert isinstance(node, AsyncFuncDecl)
        assert node.nome == 'calcular'
        assert node.tipo_retorno == 'number'

    def test_await_expr(self):
        src = "func void f() { var number r = await calcular(5); }"
        decl = first_decl(src).corpo.statements[0]
        assert isinstance(decl.inicializador, AwaitExpr)


# ─── v2025.2: Distribuição ───────────────────────────────────────────────────

class TestParserDistribuicao:
    def test_node_decl(self):
        src = 'node meu_server = "192.168.1.1:8080";'
        node = first_decl(src)
        assert isinstance(node, NodeDecl)
        assert node.nome == 'meu_server'
        assert node.endereco == '192.168.1.1:8080'

    def test_remote_func(self):
        src = "remote func number somar(var number a, var number b) { return a + b; }"
        node = first_decl(src)
        assert isinstance(node, RemoteFuncDecl)
        assert node.func_decl.nome == 'somar'

    def test_remote_call_stmt(self):
        src = "func void f() { remote on servidor somar(1, 2); }"
        node = first_decl(src).corpo.statements[0]
        assert isinstance(node, RemoteCallStmt)

    def test_spawn_on(self):
        src = "func void f() { spawn on worker { print(1); } }"
        node = first_decl(src).corpo.statements[0]
        assert isinstance(node, RemoteSpawnStmt)
        assert node.corpo is not None
