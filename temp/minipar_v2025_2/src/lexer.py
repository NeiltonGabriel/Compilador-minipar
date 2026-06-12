"""
lexer.py — MiniPar v2025.2
Analisador Léxico estendido com suporte a POO, Paralelismo e Distribuição.
100% backward-compatible com MiniPar v2025.1.
"""

from enum import Enum, auto
from dataclasses import dataclass
from typing import List, Optional, Any


# ─────────────────────────────────────────────────────────────────────────────
# Tipos de Token
# ─────────────────────────────────────────────────────────────────────────────

class TokenType(Enum):
    # ── Literais ──────────────────────────────────────────────────────────────
    NUMBER_LITERAL  = 'NUMBER_LITERAL'
    STRING_LITERAL  = 'STRING_LITERAL'
    TRUE            = 'TRUE'
    FALSE           = 'FALSE'
    NULL_LITERAL    = 'NULL_LITERAL'   # NOVO v2025.2

    # ── Identificador ─────────────────────────────────────────────────────────
    IDENTIFIER      = 'IDENTIFIER'

    # ── Palavras-chave existentes (v2025.1) ───────────────────────────────────
    VAR             = 'VAR'
    FUNC            = 'FUNC'
    IF              = 'IF'
    ELSE            = 'ELSE'
    WHILE           = 'WHILE'
    BREAK           = 'BREAK'
    CONTINUE        = 'CONTINUE'
    RETURN          = 'RETURN'
    PAR             = 'PAR'
    SEQ             = 'SEQ'
    S_CHANNEL       = 'S_CHANNEL'
    C_CHANNEL       = 'C_CHANNEL'

    # ── Tipos existentes ──────────────────────────────────────────────────────
    NUMBER          = 'NUMBER'
    STRING          = 'STRING'
    BOOL            = 'BOOL'
    VOID            = 'VOID'
    LIST            = 'LIST'
    DICT            = 'DICT'
    ANY             = 'ANY'

    # ── Palavras-chave POO (NOVO v2025.2) ─────────────────────────────────────
    CLASS           = 'CLASS'
    NEW             = 'NEW'
    THIS            = 'THIS'
    EXTENDS         = 'EXTENDS'
    SUPER           = 'SUPER'
    OVERRIDE        = 'OVERRIDE'
    ABSTRACT        = 'ABSTRACT'
    STATIC          = 'STATIC'

    # ── Palavras-chave Paralelismo (NOVO v2025.2) ─────────────────────────────
    ASYNC           = 'ASYNC'
    AWAIT           = 'AWAIT'
    SPAWN           = 'SPAWN'
    SYNC_KW         = 'SYNC_KW'

    # ── Palavras-chave Distribuição (NOVO v2025.2) ────────────────────────────
    REMOTE          = 'REMOTE'
    ON              = 'ON'
    NODE            = 'NODE'

    # ── Operadores aritméticos ────────────────────────────────────────────────
    PLUS            = 'PLUS'
    MINUS           = 'MINUS'
    MULTIPLY        = 'MULTIPLY'
    DIVIDE          = 'DIVIDE'
    MODULO          = 'MODULO'

    # ── Operadores de comparação ──────────────────────────────────────────────
    EQ              = 'EQ'
    NEQ             = 'NEQ'
    LT              = 'LT'
    GT              = 'GT'
    LTE             = 'LTE'
    GTE             = 'GTE'

    # ── Operadores lógicos ────────────────────────────────────────────────────
    AND             = 'AND'
    OR              = 'OR'
    NOT             = 'NOT'

    # ── Atribuição e delimitadores ────────────────────────────────────────────
    ASSIGN          = 'ASSIGN'
    LPAREN          = 'LPAREN'
    RPAREN          = 'RPAREN'
    LBRACE          = 'LBRACE'
    RBRACE          = 'RBRACE'
    LBRACKET        = 'LBRACKET'
    RBRACKET        = 'RBRACKET'
    COMMA           = 'COMMA'
    SEMICOLON       = 'SEMICOLON'
    COLON           = 'COLON'
    DOT             = 'DOT'
    ARROW           = 'ARROW'

    # ── Fim de arquivo ────────────────────────────────────────────────────────
    EOF             = 'EOF'


@dataclass
class Token:
    tipo: TokenType
    valor: Any
    linha: int
    coluna: int

    def __repr__(self):
        return f'Token({self.tipo.value}, {self.valor!r}, L{self.linha}:C{self.coluna})'


# ─────────────────────────────────────────────────────────────────────────────
# Lexer
# ─────────────────────────────────────────────────────────────────────────────

class Lexer:
    """
    Analisador léxico MiniPar v2025.2.
    Extensão aditiva: todas as palavras-chave v2025.1 são preservadas.
    """

    # ── Palavras-chave v2025.1 (inalteradas) ──────────────────────────────────
    KEYWORDS_V1 = {
        'var':       TokenType.VAR,
        'func':      TokenType.FUNC,
        'if':        TokenType.IF,
        'else':      TokenType.ELSE,
        'while':     TokenType.WHILE,
        'break':     TokenType.BREAK,
        'continue':  TokenType.CONTINUE,
        'return':    TokenType.RETURN,
        'true':      TokenType.TRUE,
        'false':     TokenType.FALSE,
        'par':       TokenType.PAR,
        'seq':       TokenType.SEQ,
        's_channel': TokenType.S_CHANNEL,
        'c_channel': TokenType.C_CHANNEL,
        'number':    TokenType.NUMBER,
        'string':    TokenType.STRING,
        'bool':      TokenType.BOOL,
        'void':      TokenType.VOID,
        'list':      TokenType.LIST,
        'dict':      TokenType.DICT,
        'any':       TokenType.ANY,
    }

    # ── Novas palavras-chave v2025.2 ──────────────────────────────────────────
    KEYWORDS_V2 = {
        # POO
        'class':    TokenType.CLASS,
        'new':      TokenType.NEW,
        'this':     TokenType.THIS,
        'extends':  TokenType.EXTENDS,
        'super':    TokenType.SUPER,
        'null':     TokenType.NULL_LITERAL,
        'override': TokenType.OVERRIDE,
        'abstract': TokenType.ABSTRACT,
        'static':   TokenType.STATIC,
        # Paralelismo
        'async':    TokenType.ASYNC,
        'await':    TokenType.AWAIT,
        'spawn':    TokenType.SPAWN,
        'sync':     TokenType.SYNC_KW,
        # Distribuição
        'remote':   TokenType.REMOTE,
        'on':       TokenType.ON,
        'node':     TokenType.NODE,
    }

    def __init__(self, texto: str):
        self.source = texto
        self.pos = 0
        self.linha = 1
        self.coluna = 1
        self.tokens: List[Token] = []
        # Mescla de todas as palavras-chave (v2 sobrescreve se houver conflito)
        self.KEYWORDS = {**self.KEYWORDS_V1, **self.KEYWORDS_V2}

    # ── API pública ───────────────────────────────────────────────────────────

    def tokenize(self) -> List[Token]:
        while self.pos < len(self.source):
            self._skip_whitespace_and_comments()
            if self.pos >= len(self.source):
                break

            c = self.source[self.pos]

            if c.isdigit():
                self._read_number()
            elif c == '"':
                self._read_string()
            elif c.isalpha() or c == '_':
                self._read_identifier_or_keyword()
            elif c == '+':
                self._add(TokenType.PLUS, '+'); self.pos += 1
            elif c == '-':
                if self._peek(1) == '>':
                    self._add(TokenType.ARROW, '->'); self.pos += 2
                else:
                    self._add(TokenType.MINUS, '-'); self.pos += 1
            elif c == '*':
                self._add(TokenType.MULTIPLY, '*'); self.pos += 1
            elif c == '/':
                self._add(TokenType.DIVIDE, '/'); self.pos += 1
            elif c == '%':
                self._add(TokenType.MODULO, '%'); self.pos += 1
            elif c == '=':
                if self._peek(1) == '=':
                    self._add(TokenType.EQ, '=='); self.pos += 2
                else:
                    self._add(TokenType.ASSIGN, '='); self.pos += 1
            elif c == '!':
                if self._peek(1) == '=':
                    self._add(TokenType.NEQ, '!='); self.pos += 2
                else:
                    self._add(TokenType.NOT, '!'); self.pos += 1
            elif c == '<':
                if self._peek(1) == '=':
                    self._add(TokenType.LTE, '<='); self.pos += 2
                else:
                    self._add(TokenType.LT, '<'); self.pos += 1
            elif c == '>':
                if self._peek(1) == '=':
                    self._add(TokenType.GTE, '>='); self.pos += 2
                else:
                    self._add(TokenType.GT, '>'); self.pos += 1
            elif c == '&' and self._peek(1) == '&':
                self._add(TokenType.AND, '&&'); self.pos += 2
            elif c == '|' and self._peek(1) == '|':
                self._add(TokenType.OR, '||'); self.pos += 2
            elif c == '(':
                self._add(TokenType.LPAREN, '('); self.pos += 1
            elif c == ')':
                self._add(TokenType.RPAREN, ')'); self.pos += 1
            elif c == '{':
                self._add(TokenType.LBRACE, '{'); self.pos += 1
            elif c == '}':
                self._add(TokenType.RBRACE, '}'); self.pos += 1
            elif c == '[':
                self._add(TokenType.LBRACKET, '['); self.pos += 1
            elif c == ']':
                self._add(TokenType.RBRACKET, ']'); self.pos += 1
            elif c == ',':
                self._add(TokenType.COMMA, ','); self.pos += 1
            elif c == ';':
                self._add(TokenType.SEMICOLON, ';'); self.pos += 1
            elif c == ':':
                self._add(TokenType.COLON, ':'); self.pos += 1
            elif c == '.':
                self._add(TokenType.DOT, '.'); self.pos += 1
            else:
                raise LexerError(
                    f"Caractere inesperado '{c}' na linha {self.linha}, coluna {self.coluna}"
                )

        self._add(TokenType.EOF, None)
        return self.tokens

    # ── Helpers internos ──────────────────────────────────────────────────────

    def _add(self, tipo: TokenType, valor: Any):
        self.tokens.append(Token(tipo, valor, self.linha, self.coluna))

    def _peek(self, offset: int = 1) -> Optional[str]:
        idx = self.pos + offset
        return self.source[idx] if idx < len(self.source) else None

    def _skip_whitespace_and_comments(self):
        while self.pos < len(self.source):
            c = self.source[self.pos]
            if c == '\n':
                self.linha += 1
                self.coluna = 1
                self.pos += 1
            elif c in (' ', '\t', '\r'):
                self.coluna += 1
                self.pos += 1
            elif c == '/' and self._peek(1) == '/':
                # Comentário de linha
                while self.pos < len(self.source) and self.source[self.pos] != '\n':
                    self.pos += 1
            elif c == '/' and self._peek(1) == '*':
                # Comentário de bloco
                self.pos += 2
                while self.pos < len(self.source):
                    if self.source[self.pos] == '*' and self._peek(1) == '/':
                        self.pos += 2
                        break
                    if self.source[self.pos] == '\n':
                        self.linha += 1
                        self.coluna = 1
                    self.pos += 1
            elif c == '#':
                # Comentário estilo Python (aceito por compatibilidade)
                while self.pos < len(self.source) and self.source[self.pos] != '\n':
                    self.pos += 1
            else:
                break

    def _read_number(self):
        start_col = self.coluna
        num_str = ''
        while self.pos < len(self.source) and self.source[self.pos].isdigit():
            num_str += self.source[self.pos]
            self.pos += 1
            self.coluna += 1
        if self.pos < len(self.source) and self.source[self.pos] == '.':
            next_c = self._peek(1)
            if next_c and next_c.isdigit():
                num_str += '.'
                self.pos += 1
                self.coluna += 1
                while self.pos < len(self.source) and self.source[self.pos].isdigit():
                    num_str += self.source[self.pos]
                    self.pos += 1
                    self.coluna += 1
        self.tokens.append(Token(TokenType.NUMBER_LITERAL, float(num_str), self.linha, start_col))

    def _read_string(self):
        start_col = self.coluna
        self.pos += 1  # pular "
        self.coluna += 1
        value = ''
        while self.pos < len(self.source) and self.source[self.pos] != '"':
            c = self.source[self.pos]
            if c == '\\':
                self.pos += 1
                esc = self.source[self.pos] if self.pos < len(self.source) else ''
                value += {'n': '\n', 't': '\t', '"': '"', '\\': '\\'}.get(esc, esc)
            else:
                if c == '\n':
                    self.linha += 1
                    self.coluna = 0
                value += c
            self.pos += 1
            self.coluna += 1
        if self.pos >= len(self.source):
            raise LexerError(f"String não fechada na linha {self.linha}")
        self.pos += 1   # pular "
        self.coluna += 1
        self.tokens.append(Token(TokenType.STRING_LITERAL, value, self.linha, start_col))

    def _read_identifier_or_keyword(self):
        start_col = self.coluna
        word = ''
        while self.pos < len(self.source) and (self.source[self.pos].isalnum() or self.source[self.pos] == '_'):
            word += self.source[self.pos]
            self.pos += 1
            self.coluna += 1
        tipo = self.KEYWORDS.get(word, TokenType.IDENTIFIER)
        self.tokens.append(Token(tipo, word, self.linha, start_col))


class LexerError(Exception):
    pass
