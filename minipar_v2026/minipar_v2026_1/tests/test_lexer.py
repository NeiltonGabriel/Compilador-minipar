"""
tests/test_lexer.py — Testes do Lexer MiniPar v2025.2
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from lexer import Lexer, LexerError, TokenType


def tokenize(src):
    return Lexer(src).tokenize()

def tipos(src):
    return [t.tipo for t in tokenize(src) if t.tipo != TokenType.EOF]

def valores(src):
    return [t.valor for t in tokenize(src) if t.tipo != TokenType.EOF]


# ─── v2025.1: tokens existentes ───────────────────────────────────────────────

class TestTokensV1:
    def test_numero_inteiro(self):
        ts = tokenize("42")
        assert ts[0].tipo  == TokenType.NUMBER_LITERAL
        assert ts[0].valor == 42.0

    def test_numero_decimal(self):
        assert tokenize("3.14")[0].valor == pytest.approx(3.14)

    def test_string(self):
        ts = tokenize('"hello world"')
        assert ts[0].tipo  == TokenType.STRING_LITERAL
        assert ts[0].valor == "hello world"

    def test_string_escape(self):
        ts = tokenize('"linha1\\nlinha2"')
        assert '\n' in ts[0].valor

    def test_keywords_v1(self):
        src = "var func if else while break continue return par seq true false"
        typs = tipos(src)
        assert TokenType.VAR      in typs
        assert TokenType.FUNC     in typs
        assert TokenType.IF       in typs
        assert TokenType.ELSE     in typs
        assert TokenType.WHILE    in typs
        assert TokenType.BREAK    in typs
        assert TokenType.CONTINUE in typs
        assert TokenType.RETURN   in typs
        assert TokenType.PAR      in typs
        assert TokenType.SEQ      in typs
        assert TokenType.TRUE     in typs
        assert TokenType.FALSE    in typs

    def test_tipos_v1(self):
        src = "number string bool void list dict any"
        typs = tipos(src)
        assert TokenType.NUMBER in typs
        assert TokenType.STRING in typs
        assert TokenType.BOOL   in typs
        assert TokenType.VOID   in typs
        assert TokenType.LIST   in typs
        assert TokenType.DICT   in typs
        assert TokenType.ANY    in typs

    def test_operadores(self):
        src = "+ - * / % == != < > <= >= && || !"
        typs = tipos(src)
        assert TokenType.PLUS     in typs
        assert TokenType.MINUS    in typs
        assert TokenType.MULTIPLY in typs
        assert TokenType.DIVIDE   in typs
        assert TokenType.MODULO   in typs
        assert TokenType.EQ       in typs
        assert TokenType.NEQ      in typs
        assert TokenType.LT       in typs
        assert TokenType.GT       in typs
        assert TokenType.LTE      in typs
        assert TokenType.GTE      in typs
        assert TokenType.AND      in typs
        assert TokenType.OR       in typs
        assert TokenType.NOT      in typs

    def test_delimitadores(self):
        src = "( ) { } [ ] , ; : . ->"
        typs = tipos(src)
        assert TokenType.LPAREN    in typs
        assert TokenType.RPAREN    in typs
        assert TokenType.LBRACE    in typs
        assert TokenType.RBRACE    in typs
        assert TokenType.LBRACKET  in typs
        assert TokenType.RBRACKET  in typs
        assert TokenType.COMMA     in typs
        assert TokenType.SEMICOLON in typs
        assert TokenType.COLON     in typs
        assert TokenType.DOT       in typs
        assert TokenType.ARROW     in typs

    def test_identificador(self):
        ts = tokenize("minha_var_123")
        assert ts[0].tipo  == TokenType.IDENTIFIER
        assert ts[0].valor == "minha_var_123"

    def test_comentario_linha(self):
        src = "42 // isto é um comentário\n99"
        vals = [t.valor for t in tokenize(src) if t.tipo == TokenType.NUMBER_LITERAL]
        assert vals == [42.0, 99.0]

    def test_comentario_bloco(self):
        src = "1 /* bloco\nmultilinhas */ 2"
        vals = [t.valor for t in tokenize(src) if t.tipo == TokenType.NUMBER_LITERAL]
        assert vals == [1.0, 2.0]

    def test_comentario_hash(self):
        src = "# comentário Python\n42"
        vals = [t.valor for t in tokenize(src) if t.tipo == TokenType.NUMBER_LITERAL]
        assert vals == [42.0]

    def test_posicao_linha_coluna(self):
        src = "var\n  x"
        ts = [t for t in tokenize(src) if t.tipo != TokenType.EOF]
        assert ts[0].linha == 1
        assert ts[1].linha == 2

    def test_erro_caractere_invalido(self):
        with pytest.raises(LexerError):
            tokenize("@invalido")


# ─── v2025.2: novos tokens ────────────────────────────────────────────────────

class TestTokensV2:
    def test_keywords_poo(self):
        src = "class new this extends super null override abstract static"
        typs = tipos(src)
        assert TokenType.CLASS       in typs
        assert TokenType.NEW         in typs
        assert TokenType.THIS        in typs
        assert TokenType.EXTENDS     in typs
        assert TokenType.SUPER       in typs
        assert TokenType.NULL_LITERAL in typs
        assert TokenType.OVERRIDE    in typs
        assert TokenType.ABSTRACT    in typs
        assert TokenType.STATIC      in typs

    def test_keywords_paralelo(self):
        src = "async await spawn sync"
        typs = tipos(src)
        assert TokenType.ASYNC   in typs
        assert TokenType.AWAIT   in typs
        assert TokenType.SPAWN   in typs
        assert TokenType.SYNC_KW in typs

    def test_keywords_distribuido(self):
        src = "remote on node"
        typs = tipos(src)
        assert TokenType.REMOTE in typs
        assert TokenType.ON     in typs
        assert TokenType.NODE   in typs

    def test_dot_separado(self):
        src = "obj.campo"
        typs = tipos(src)
        assert TokenType.DOT in typs

    def test_backward_compat(self):
        # programa v2025.1 puro deve tokenizar sem erros
        src = """
        var number x = 10;
        func number soma(var number a, var number b) {
            return a + b;
        }
        while (x > 0) {
            x = x - 1;
        }
        """
        ts = tokenize(src)
        assert ts[-1].tipo == TokenType.EOF
