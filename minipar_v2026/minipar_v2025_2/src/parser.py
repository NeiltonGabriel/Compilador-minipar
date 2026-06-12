"""
parser.py — MiniPar v2025.2
Parser descendente recursivo LL(1) estendido.
100% backward-compatible: todos os programas v2025.1 continuam parseando.
"""

from lexer import TokenType, Token
from ast_nodes import *
from typing import List, Optional


class ParseError(Exception):
    pass


class Parser:

    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers de navegação
    # ─────────────────────────────────────────────────────────────────────────

    def atual(self) -> Token:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return self.tokens[-1]  # EOF

    def peek(self, offset: int = 1) -> Token:
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return self.tokens[-1]

    def avancar(self) -> Token:
        tok = self.atual()
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return tok

    def consumir(self, tipo: TokenType, mensagem: str) -> Token:
        if self.atual().tipo != tipo:
            raise ParseError(
                f"{mensagem} — encontrado '{self.atual().valor}' "
                f"(tipo {self.atual().tipo.value}) na linha {self.atual().linha}"
            )
        return self.avancar()

    def casa(self, *tipos: TokenType) -> bool:
        return self.atual().tipo in tipos

    # ─────────────────────────────────────────────────────────────────────────
    # Ponto de entrada
    # ─────────────────────────────────────────────────────────────────────────

    def parse(self) -> Program:
        decls = []
        while not self.casa(TokenType.EOF):
            decls.append(self.declaracao())
        return Program(declarations=decls)

    # ─────────────────────────────────────────────────────────────────────────
    # Declarações de alto nível
    # ─────────────────────────────────────────────────────────────────────────

    def declaracao(self):
        # ── NOVO v2025.2 ──────────────────────────────────────────────────────
        if self.casa(TokenType.ABSTRACT):
            return self.decl_classe(abstrata=True)
        if self.casa(TokenType.CLASS):
            return self.decl_classe()
        if self.casa(TokenType.NODE):
            return self.decl_node()
        if self.casa(TokenType.REMOTE) and self.peek(1).tipo == TokenType.FUNC:
            return self.decl_funcao_remota()
        if self.casa(TokenType.ASYNC) and self.peek(1).tipo == TokenType.FUNC:
            return self.decl_funcao_async()

        # ── Existentes v2025.1 (inalterados) ──────────────────────────────────
        if self.casa(TokenType.FUNC):
            return self.decl_funcao()
        if self.casa(TokenType.VAR):
            return self.decl_variavel()
        if self.casa(TokenType.S_CHANNEL, TokenType.C_CHANNEL):
            return self.decl_canal()
        return self.comando()

    # ─────────────────────────────────────────────────────────────────────────
    # NOVO: Declaração de Classe
    # ─────────────────────────────────────────────────────────────────────────

    def decl_classe(self, abstrata: bool = False) -> ClassDecl:
        linha = self.atual().linha
        if abstrata:
            self.consumir(TokenType.ABSTRACT, "Esperado 'abstract'")
        self.consumir(TokenType.CLASS, "Esperado 'class'")
        nome = self.consumir(TokenType.IDENTIFIER, "Esperado nome da classe").valor

        superclasse = None
        if self.casa(TokenType.EXTENDS):
            self.avancar()
            superclasse = self.consumir(
                TokenType.IDENTIFIER, "Esperado nome da superclasse após 'extends'"
            ).valor

        self.consumir(TokenType.LBRACE, "Esperado '{' no corpo da classe")

        campos: List[FieldDecl] = []
        metodos: List[MethodDecl] = []
        construtor: Optional[ConstructorDecl] = None

        while not self.casa(TokenType.RBRACE, TokenType.EOF):
            is_static = False
            is_override = False

            if self.casa(TokenType.STATIC):
                is_static = True
                self.avancar()
            if self.casa(TokenType.OVERRIDE):
                is_override = True
                self.avancar()

            if self.casa(TokenType.VAR):
                # Campo de instância ou estático
                v = self.decl_variavel()
                campos.append(FieldDecl(
                    nome=v.nome, tipo=v.tipo,
                    inicializador=v.inicializador,
                    is_static=is_static,
                    linha=v.linha, coluna=v.coluna
                ))

            elif self.casa(TokenType.FUNC):
                self.avancar()
                linha_m = self.atual().linha

                # Verificar se próximo token é o nome da classe → construtor
                if self.atual().valor == nome and self.atual().tipo == TokenType.IDENTIFIER:
                    # Construtor
                    self.avancar()  # consome nome
                    self.consumir(TokenType.LPAREN, "Esperado '(' no construtor")
                    params = self._parse_parametros()
                    self.consumir(TokenType.RPAREN, "Esperado ')' no construtor")
                    corpo = self.bloco()
                    construtor = ConstructorDecl(nome, params, corpo, linha_m)
                else:
                    # Método normal: primeiro vem o tipo de retorno
                    tipo_ret = self.tipo_especificador()
                    method_nome = self.consumir(
                        TokenType.IDENTIFIER, "Esperado nome do método"
                    ).valor
                    self.consumir(TokenType.LPAREN, "Esperado '('")
                    params = self._parse_parametros()
                    self.consumir(TokenType.RPAREN, "Esperado ')'")
                    corpo = self.bloco()
                    metodos.append(MethodDecl(
                        nome=method_nome, tipo_retorno=tipo_ret,
                        parametros=params, corpo=corpo,
                        is_static=is_static, is_override=is_override,
                        linha=linha_m
                    ))
            else:
                raise ParseError(
                    f"Membro de classe inválido '{self.atual().valor}' "
                    f"na linha {self.atual().linha}"
                )

        self.consumir(TokenType.RBRACE, "Esperado '}' para fechar classe")
        return ClassDecl(nome, superclasse, campos, metodos, construtor,
                         is_abstract=abstrata, linha=linha)

    # ─────────────────────────────────────────────────────────────────────────
    # NOVO: Declarações de distribuição e async
    # ─────────────────────────────────────────────────────────────────────────

    def decl_node(self) -> NodeDecl:
        linha = self.atual().linha
        self.consumir(TokenType.NODE, "Esperado 'node'")
        nome = self.consumir(TokenType.IDENTIFIER, "Esperado nome do nó").valor
        self.consumir(TokenType.ASSIGN, "Esperado '='")
        endereco = self.consumir(
            TokenType.STRING_LITERAL, "Esperado string com 'ip:porta'"
        ).valor
        self.consumir(TokenType.SEMICOLON, "Esperado ';'")
        return NodeDecl(nome=nome, endereco=endereco, linha=linha)

    def decl_funcao_remota(self) -> RemoteFuncDecl:
        linha = self.atual().linha
        self.consumir(TokenType.REMOTE, "Esperado 'remote'")
        func = self.decl_funcao()
        return RemoteFuncDecl(func_decl=func, linha=linha)

    def decl_funcao_async(self) -> AsyncFuncDecl:
        linha = self.atual().linha
        self.consumir(TokenType.ASYNC, "Esperado 'async'")
        self.consumir(TokenType.FUNC, "Esperado 'func'")
        tipo_ret = self.tipo_especificador()
        nome = self.consumir(TokenType.IDENTIFIER, "Esperado nome da função").valor
        self.consumir(TokenType.LPAREN, "Esperado '('")
        params = self._parse_parametros()
        self.consumir(TokenType.RPAREN, "Esperado ')'")
        corpo = self.bloco()
        return AsyncFuncDecl(nome=nome, tipo_retorno=tipo_ret,
                             parametros=params, corpo=corpo, linha=linha)

    # ─────────────────────────────────────────────────────────────────────────
    # Declarações existentes v2025.1 (inalteradas)
    # ─────────────────────────────────────────────────────────────────────────

    def decl_funcao(self) -> FuncDecl:
        linha = self.atual().linha
        self.consumir(TokenType.FUNC, "Esperado 'func'")
        tipo_ret = self.tipo_especificador()
        nome = self.consumir(TokenType.IDENTIFIER, "Esperado nome da função").valor
        self.consumir(TokenType.LPAREN, "Esperado '('")
        params = self._parse_parametros()
        self.consumir(TokenType.RPAREN, "Esperado ')'")
        corpo = self.bloco()
        return FuncDecl(tipo_retorno=tipo_ret, nome=nome,
                        parametros=params, corpo=corpo, linha=linha)

    def decl_variavel(self) -> VarDecl:
        linha = self.atual().linha
        coluna = self.atual().coluna
        self.consumir(TokenType.VAR, "Esperado 'var'")
        tipo = self.tipo_especificador()
        nome = self.consumir(TokenType.IDENTIFIER, "Esperado nome da variável").valor
        inicializador = None
        if self.casa(TokenType.ASSIGN):
            self.avancar()
            inicializador = self.expressao()
        self.consumir(TokenType.SEMICOLON, "Esperado ';'")
        return VarDecl(nome=nome, tipo=tipo, inicializador=inicializador,
                       linha=linha, coluna=coluna)

    def decl_canal(self) -> ChannelDecl:
        linha = self.atual().linha
        tipo_canal = self.avancar().valor
        nome = self.consumir(TokenType.IDENTIFIER, "Esperado nome do canal").valor
        self.consumir(TokenType.LPAREN, "Esperado '('")
        args = self._parse_argumentos()
        self.consumir(TokenType.RPAREN, "Esperado ')'")
        self.consumir(TokenType.SEMICOLON, "Esperado ';'")
        return ChannelDecl(tipo_canal=tipo_canal, nome=nome,
                           argumentos=args, linha=linha)

    # ─────────────────────────────────────────────────────────────────────────
    # Comandos (statements)
    # ─────────────────────────────────────────────────────────────────────────

    def comando(self):
        linha = self.atual().linha

        # ── NOVO v2025.2 ──────────────────────────────────────────────────────
        if self.casa(TokenType.SPAWN):
            return self._parse_spawn()
        if self.casa(TokenType.SYNC_KW):
            return self._parse_sync()
        if self.casa(TokenType.REMOTE):
            return self._parse_remote_stmt()

        # ── Existentes v2025.1 (inalterados) ──────────────────────────────────
        if self.casa(TokenType.IF):
            return self._parse_if()
        if self.casa(TokenType.WHILE):
            return self._parse_while()
        if self.casa(TokenType.PAR):
            return self._parse_par()
        if self.casa(TokenType.SEQ):
            return self._parse_seq()
        if self.casa(TokenType.RETURN):
            return self._parse_return()
        if self.casa(TokenType.BREAK):
            self.avancar()
            self.consumir(TokenType.SEMICOLON, "Esperado ';'")
            return BreakStmt(linha=linha)
        if self.casa(TokenType.CONTINUE):
            self.avancar()
            self.consumir(TokenType.SEMICOLON, "Esperado ';'")
            return ContinueStmt(linha=linha)
        if self.casa(TokenType.VAR):
            return self.decl_variavel()
        if self.casa(TokenType.LBRACE):
            return self.bloco()

        # Expressão ou atribuição
        return self._parse_expr_or_assign()

    # ─────────────────────────────────────────────────────────────────────────
    # NOVO: Spawn, Sync, Remote statements
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_spawn(self):
        linha = self.atual().linha
        self.consumir(TokenType.SPAWN, "Esperado 'spawn'")

        # spawn on <alvo> { ... } — spawn distribuído
        if self.casa(TokenType.ON):
            self.avancar()
            alvo = self.expressao()
            corpo = self.bloco()
            return RemoteSpawnStmt(alvo=alvo, corpo=corpo, linha=linha)

        # spawn { bloco anônimo } — thread local anônima
        if self.casa(TokenType.LBRACE):
            corpo = self.bloco()
            return SpawnStmt(call_expr=None, body=corpo, linha=linha)

        # spawn funcao(args); — thread de função
        call = self.expressao()
        self.consumir(TokenType.SEMICOLON, "Esperado ';' após spawn")
        return SpawnStmt(call_expr=call, body=None, linha=linha)

    def _parse_sync(self) -> SyncStmt:
        linha = self.atual().linha
        self.consumir(TokenType.SYNC_KW, "Esperado 'sync'")
        self.consumir(TokenType.LPAREN, "Esperado '('")
        lock = self.consumir(
            TokenType.IDENTIFIER, "Esperado nome do mutex"
        ).valor
        self.consumir(TokenType.RPAREN, "Esperado ')'")
        corpo = self.bloco()
        return SyncStmt(lock_var=lock, corpo=corpo, linha=linha)

    def _parse_remote_stmt(self):
        linha = self.atual().linha
        self.consumir(TokenType.REMOTE, "Esperado 'remote'")
        self.consumir(TokenType.ON, "Esperado 'on' após 'remote'")
        alvo = self.expressao()
        call = self.expressao()
        self.consumir(TokenType.SEMICOLON, "Esperado ';'")
        return RemoteCallStmt(alvo=alvo, function_call=call,
                              result_var=None, linha=linha)

    # ─────────────────────────────────────────────────────────────────────────
    # Comandos existentes v2025.1 (inalterados)
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_if(self) -> IfStmt:
        linha = self.atual().linha
        self.consumir(TokenType.IF, "Esperado 'if'")
        self.consumir(TokenType.LPAREN, "Esperado '('")
        cond = self.expressao()
        self.consumir(TokenType.RPAREN, "Esperado ')'")
        corpo_se = self.bloco()
        corpo_senao = None
        if self.casa(TokenType.ELSE):
            self.avancar()
            corpo_senao = self.bloco()
        return IfStmt(condicao=cond, corpo_se=corpo_se,
                      corpo_senao=corpo_senao, linha=linha)

    def _parse_while(self) -> WhileStmt:
        linha = self.atual().linha
        self.consumir(TokenType.WHILE, "Esperado 'while'")
        self.consumir(TokenType.LPAREN, "Esperado '('")
        cond = self.expressao()
        self.consumir(TokenType.RPAREN, "Esperado ')'")
        corpo = self.bloco()
        return WhileStmt(condicao=cond, corpo=corpo, linha=linha)

    def _parse_par(self) -> ParBlock:
        linha = self.atual().linha
        self.consumir(TokenType.PAR, "Esperado 'par'")
        bloco = self.bloco()
        return ParBlock(statements=bloco.statements, linha=linha)

    def _parse_seq(self) -> SeqBlock:
        linha = self.atual().linha
        self.consumir(TokenType.SEQ, "Esperado 'seq'")
        bloco = self.bloco()
        return SeqBlock(statements=bloco.statements, linha=linha)

    def _parse_return(self) -> ReturnStmt:
        linha = self.atual().linha
        self.consumir(TokenType.RETURN, "Esperado 'return'")
        expr = None
        if not self.casa(TokenType.SEMICOLON):
            expr = self.expressao()
        self.consumir(TokenType.SEMICOLON, "Esperado ';'")
        return ReturnStmt(expressao=expr, linha=linha)

    def _parse_expr_or_assign(self):
        linha = self.atual().linha
        expr = self.expressao()

        if self.casa(TokenType.ASSIGN):
            # Atribuição: alvo = expressao
            self.avancar()
            valor = self.expressao()
            self.consumir(TokenType.SEMICOLON, "Esperado ';'")
            if isinstance(expr, Identifier):
                return AssignStmt(alvo=expr.nome, expressao=valor, linha=linha)
            # Atribuição a membro: obj.campo = valor
            if isinstance(expr, MemberAccessExpr):
                return AssignStmt(alvo=expr, expressao=valor, linha=linha)
            raise ParseError(f"Alvo de atribuição inválido na linha {linha}")

        self.consumir(TokenType.SEMICOLON, "Esperado ';'")
        return ExprStmt(expr=expr, linha=linha)

    def bloco(self) -> Block:
        linha = self.atual().linha
        self.consumir(TokenType.LBRACE, "Esperado '{'")
        stmts = []
        while not self.casa(TokenType.RBRACE, TokenType.EOF):
            stmts.append(self.comando())
        self.consumir(TokenType.RBRACE, "Esperado '}'")
        return Block(statements=stmts, linha=linha)

    # ─────────────────────────────────────────────────────────────────────────
    # Expressões (com precedência)
    # ─────────────────────────────────────────────────────────────────────────

    def expressao(self):
        return self._expr_or()

    def _expr_or(self):
        esq = self._expr_and()
        while self.casa(TokenType.OR):
            op = self.avancar().valor
            dir_ = self._expr_and()
            esq = BinaryExpr(esq, op, dir_)
        return esq

    def _expr_and(self):
        esq = self._expr_comp()
        while self.casa(TokenType.AND):
            op = self.avancar().valor
            dir_ = self._expr_comp()
            esq = BinaryExpr(esq, op, dir_)
        return esq

    def _expr_comp(self):
        esq = self._expr_add()
        while self.casa(TokenType.EQ, TokenType.NEQ,
                         TokenType.LT, TokenType.GT,
                         TokenType.LTE, TokenType.GTE):
            op = self.avancar().valor
            dir_ = self._expr_add()
            esq = BinaryExpr(esq, op, dir_)
        return esq

    def _expr_add(self):
        esq = self._expr_mul()
        while self.casa(TokenType.PLUS, TokenType.MINUS):
            op = self.avancar().valor
            dir_ = self._expr_mul()
            esq = BinaryExpr(esq, op, dir_)
        return esq

    def _expr_mul(self):
        esq = self._unary()
        while self.casa(TokenType.MULTIPLY, TokenType.DIVIDE, TokenType.MODULO):
            op = self.avancar().valor
            dir_ = self._unary()
            esq = BinaryExpr(esq, op, dir_)
        return esq

    def _unary(self):
        if self.casa(TokenType.NOT, TokenType.MINUS):
            op = self.avancar().valor
            return UnaryExpr(op, self._unary())
        return self._postfix()

    def _postfix(self):
        """
        Lida com:
        - expr[idx]      — acesso a array (existente)
        - expr.campo     — acesso a campo (NOVO)
        - expr.metodo()  — chamada de método (NOVO)
        """
        expr = self._primary()
        while True:
            if self.casa(TokenType.LBRACKET):
                self.avancar()
                idx = self.expressao()
                self.consumir(TokenType.RBRACKET, "Esperado ']'")
                expr = IndexAccess(obj=expr, indice=idx,
                                   linha=self.atual().linha)

            elif self.casa(TokenType.DOT):
                linha = self.atual().linha
                self.avancar()
                membro = self.consumir(
                    TokenType.IDENTIFIER, "Esperado nome do membro após '.'"
                ).valor
                if self.casa(TokenType.LPAREN):
                    # Chamada de método
                    self.avancar()
                    args = self._parse_argumentos()
                    self.consumir(TokenType.RPAREN, "Esperado ')'")
                    expr = MethodCallExpr(obj=expr, metodo=membro,
                                         argumentos=args, linha=linha)
                else:
                    # Acesso a atributo
                    expr = MemberAccessExpr(obj=expr, membro=membro, linha=linha)
            else:
                break
        return expr

    def _primary(self):
        tok = self.atual()

        # ── NOVO v2025.2 ──────────────────────────────────────────────────────
        if self.casa(TokenType.NEW):
            return self._parse_new_expr()

        if self.casa(TokenType.THIS):
            self.avancar()
            return ThisExpr(linha=tok.linha, coluna=tok.coluna)

        if self.casa(TokenType.NULL_LITERAL):
            self.avancar()
            return NullLiteral(linha=tok.linha, coluna=tok.coluna)

        if self.casa(TokenType.SUPER):
            return self._parse_super_call()

        if self.casa(TokenType.AWAIT):
            self.avancar()
            expr = self.expressao()
            return AwaitExpr(future_expr=expr, linha=tok.linha, coluna=tok.coluna)

        # ── Existentes v2025.1 (inalterados) ──────────────────────────────────
        if self.casa(TokenType.NUMBER_LITERAL):
            self.avancar()
            return NumberLiteral(valor=tok.valor, linha=tok.linha)

        if self.casa(TokenType.STRING_LITERAL):
            self.avancar()
            return StringLiteral(valor=tok.valor, linha=tok.linha)

        if self.casa(TokenType.TRUE):
            self.avancar()
            return BoolLiteral(valor=True, linha=tok.linha)

        if self.casa(TokenType.FALSE):
            self.avancar()
            return BoolLiteral(valor=False, linha=tok.linha)

        if self.casa(TokenType.LPAREN):
            self.avancar()
            expr = self.expressao()
            self.consumir(TokenType.RPAREN, "Esperado ')'")
            return expr

        if self.casa(TokenType.LBRACKET):
            return self._parse_list_literal()

        if self.casa(TokenType.LBRACE):
            return self._parse_dict_literal()

        if self.casa(TokenType.IDENTIFIER):
            nome = self.avancar().valor
            if self.casa(TokenType.LPAREN):
                # Chamada de função
                self.avancar()
                args = self._parse_argumentos()
                self.consumir(TokenType.RPAREN, "Esperado ')'")
                return FunctionCall(nome=nome, argumentos=args, linha=tok.linha)
            return Identifier(nome=nome, linha=tok.linha)

        raise ParseError(
            f"Expressão esperada, encontrado '{tok.valor}' na linha {tok.linha}"
        )

    def _parse_new_expr(self) -> NewExpr:
        linha = self.atual().linha
        self.consumir(TokenType.NEW, "Esperado 'new'")
        nome_classe = self.consumir(
            TokenType.IDENTIFIER, "Esperado nome da classe após 'new'"
        ).valor
        self.consumir(TokenType.LPAREN, "Esperado '('")
        args = self._parse_argumentos()
        self.consumir(TokenType.RPAREN, "Esperado ')'")
        return NewExpr(class_name=nome_classe, argumentos=args, linha=linha)

    def _parse_super_call(self) -> SuperCallExpr:
        linha = self.atual().linha
        self.consumir(TokenType.SUPER, "Esperado 'super'")
        self.consumir(TokenType.DOT, "Esperado '.' após 'super'")
        metodo = self.consumir(
            TokenType.IDENTIFIER, "Esperado nome do método"
        ).valor
        self.consumir(TokenType.LPAREN, "Esperado '('")
        args = self._parse_argumentos()
        self.consumir(TokenType.RPAREN, "Esperado ')'")
        return SuperCallExpr(metodo=metodo, argumentos=args, linha=linha)

    def _parse_list_literal(self) -> ListLiteral:
        linha = self.atual().linha
        self.consumir(TokenType.LBRACKET, "Esperado '['")
        elementos = []
        if not self.casa(TokenType.RBRACKET):
            elementos.append(self.expressao())
            while self.casa(TokenType.COMMA):
                self.avancar()
                elementos.append(self.expressao())
        self.consumir(TokenType.RBRACKET, "Esperado ']'")
        return ListLiteral(elementos=elementos, linha=linha)

    def _parse_dict_literal(self) -> DictLiteral:
        linha = self.atual().linha
        self.consumir(TokenType.LBRACE, "Esperado '{'")
        pares = []
        if not self.casa(TokenType.RBRACE):
            k = self.expressao()
            self.consumir(TokenType.COLON, "Esperado ':'")
            v = self.expressao()
            pares.append((k, v))
            while self.casa(TokenType.COMMA):
                self.avancar()
                k = self.expressao()
                self.consumir(TokenType.COLON, "Esperado ':'")
                v = self.expressao()
                pares.append((k, v))
        self.consumir(TokenType.RBRACE, "Esperado '}'")
        return DictLiteral(pares=pares, linha=linha)

    # ─────────────────────────────────────────────────────────────────────────
    # Utilitários de parsing
    # ─────────────────────────────────────────────────────────────────────────

    def tipo_especificador(self) -> str:
        tipos_validos = {
            TokenType.NUMBER, TokenType.STRING, TokenType.BOOL,
            TokenType.VOID, TokenType.LIST, TokenType.DICT, TokenType.ANY,
            TokenType.IDENTIFIER,   # tipos de classe
        }
        if self.atual().tipo not in tipos_validos:
            raise ParseError(
                f"Tipo esperado, encontrado '{self.atual().valor}' "
                f"na linha {self.atual().linha}"
            )
        return self.avancar().valor

    def _parse_parametros(self) -> List[VarDecl]:
        params = []
        if self.casa(TokenType.VAR):
            params.append(self.decl_variavel_parametro())
            while self.casa(TokenType.COMMA):
                self.avancar()
                params.append(self.decl_variavel_parametro())
        return params

    def decl_variavel_parametro(self) -> VarDecl:
        """Parâmetro: var tipo nome  (sem inicializador, sem ;)"""
        linha = self.atual().linha
        self.consumir(TokenType.VAR, "Esperado 'var'")
        tipo = self.tipo_especificador()
        nome = self.consumir(
            TokenType.IDENTIFIER, "Esperado nome do parâmetro"
        ).valor
        return VarDecl(nome=nome, tipo=tipo, inicializador=None, linha=linha)

    def _parse_argumentos(self) -> List:
        args = []
        if not self.casa(TokenType.RPAREN, TokenType.EOF):
            args.append(self.expressao())
            while self.casa(TokenType.COMMA):
                self.avancar()
                args.append(self.expressao())
        return args
