"""
semantic.py — MiniPar v2026.1
Analisador semântico estendido com suporte a POO, paralelismo e distribuição.
"""

from ast_nodes import *
from symbol_table import SymbolTable, SymbolType, ClassDescriptor
from typing import List, Optional


class SemanticError(Exception):
    pass


# Tipos primitivos que podem ser serializados via rede (JSON)
SERIALIZABLE_TYPES = frozenset({
    'number', 'string', 'bool', 'list', 'dict', 'null', 'any', 'void'
})


class SemanticAnalyzer:

    def __init__(self):
        self.table = SymbolTable()
        self.errors: List[str] = []
        self._em_funcao = False
        self._em_loop = False
        self._tipo_retorno_funcao: Optional[str] = None
        self._classe_atual: Optional[str] = None  # NOVO: contexto de classe
        self._inicializar_builtins()

    # ─────────────────────────────────────────────────────────────────────────
    # API pública
    # ─────────────────────────────────────────────────────────────────────────

    def analyze(self, ast) -> bool:
        """Retorna True se sem erros semânticos."""
        self.errors = []
        try:
            self.visit(ast)
        except SemanticError as e:
            self.errors.append(str(e))
        return len(self.errors) == 0

    def visit(self, node):
        method = f'visit_{type(node).__name__}'
        visitor = getattr(self, method, self._visit_default)
        return visitor(node)

    def _visit_default(self, node):
        # Visita genérica: percorre filhos sem análise específica
        for attr in vars(node).values():
            if isinstance(attr, list):
                for item in attr:
                    if hasattr(item, '__dataclass_fields__'):
                        self.visit(item)
            elif hasattr(attr, '__dataclass_fields__'):
                self.visit(attr)

    # ─────────────────────────────────────────────────────────────────────────
    # Programa — duas passagens
    # ─────────────────────────────────────────────────────────────────────────

    def visit_Program(self, node: Program):
        # Passagem 1: registrar nomes de classes e nós (forward declarations)
        for decl in node.declarations:
            if isinstance(decl, ClassDecl):
                if decl.nome not in self.table.class_registry:
                    placeholder = ClassDescriptor(decl.nome)
                    placeholder.is_abstract = decl.is_abstract
                    self.table.class_registry[decl.nome] = placeholder
            elif isinstance(decl, NodeDecl):
                self.table.register_node(decl.nome, decl.endereco)

        # Passagem 2: análise completa
        for decl in node.declarations:
            self.visit(decl)

    # ─────────────────────────────────────────────────────────────────────────
    # Declarações existentes v2026.1 (inalteradas)
    # ─────────────────────────────────────────────────────────────────────────

    def visit_VarDecl(self, node: VarDecl):
        if self.table.lookup_local(node.nome):
            self._error(f"Variável '{node.nome}' já declarada neste escopo", node.linha)
            return
        tipo_init = None
        if node.inicializador:
            tipo_init = self.visit(node.inicializador)
            if tipo_init and tipo_init != node.tipo:
                if not self.table.is_subtype_of(tipo_init, node.tipo):
                    self._error(
                        f"Tipo incompatível em '{node.nome}': "
                        f"esperado '{node.tipo}', obtido '{tipo_init}'", node.linha
                    )
        self.table.add_symbol(node.nome, SymbolType.VARIABLE, node.tipo,
                              linha=node.linha,
                              inicializado=node.inicializador is not None)

    def visit_FuncDecl(self, node: FuncDecl):
        if self.table.lookup_local(node.nome):
            self._error(f"Função '{node.nome}' já declarada", node.linha)
            return
        param_types = [p.tipo for p in node.parametros]
        self.table.add_symbol(
            node.nome, SymbolType.FUNCTION, node.tipo_retorno,
            linha=node.linha,
            tipos_parametros=param_types,
            tipo_retorno=node.tipo_retorno
        )
        self.table.enter_scope(f'func_{node.nome}')
        for param in node.parametros:
            self.table.add_symbol(param.nome, SymbolType.PARAMETER, param.tipo,
                                  linha=param.linha)
        prev_func, prev_ret = self._em_funcao, self._tipo_retorno_funcao
        self._em_funcao = True
        self._tipo_retorno_funcao = node.tipo_retorno
        self.visit(node.corpo)
        self._em_funcao, self._tipo_retorno_funcao = prev_func, prev_ret
        self.table.exit_scope()

    def visit_Block(self, node: Block):
        for stmt in node.statements:
            self.visit(stmt)

    def visit_IfStmt(self, node: IfStmt):
        tipo_cond = self.visit(node.condicao)
        if tipo_cond and tipo_cond != 'bool':
            self._error(f"Condição do 'if' deve ser bool, obtido '{tipo_cond}'", node.linha)
        self.table.enter_scope('if_then')
        self.visit(node.corpo_se)
        self.table.exit_scope()
        if node.corpo_senao:
            self.table.enter_scope('if_else')
            self.visit(node.corpo_senao)
            self.table.exit_scope()

    def visit_WhileStmt(self, node: WhileStmt):
        tipo_cond = self.visit(node.condicao)
        if tipo_cond and tipo_cond != 'bool':
            self._error(f"Condição do 'while' deve ser bool, obtido '{tipo_cond}'", node.linha)
        prev = self._em_loop
        self._em_loop = True
        self.table.enter_scope('while')
        self.visit(node.corpo)
        self.table.exit_scope()
        self._em_loop = prev

    def visit_ReturnStmt(self, node: ReturnStmt):
        if not self._em_funcao:
            self._error("'return' fora de função", node.linha)
            return
        if node.expressao is None:
            if self._tipo_retorno_funcao != 'void':
                self._error(f"Função espera retorno '{self._tipo_retorno_funcao}', "
                            f"mas 'return' sem valor", node.linha)
        else:
            tipo = self.visit(node.expressao)
            if tipo and not self.table.is_subtype_of(tipo, self._tipo_retorno_funcao):
                self._error(
                    f"Tipo de retorno incompatível: "
                    f"esperado '{self._tipo_retorno_funcao}', obtido '{tipo}'", node.linha
                )

    def visit_BreakStmt(self, node: BreakStmt):
        if not self._em_loop:
            self._error("'break' fora de loop", node.linha)

    def visit_ContinueStmt(self, node: ContinueStmt):
        if not self._em_loop:
            self._error("'continue' fora de loop", node.linha)

    def visit_AssignStmt(self, node: AssignStmt):
        if isinstance(node.alvo, str):
            # Atribuição simples: x = expr
            sym = self.table.lookup(node.alvo)
            if sym is None:
                self._error(f"Variável '{node.alvo}' não declarada", node.linha)
                return
            tipo_val = self.visit(node.expressao)
            if tipo_val and not self.table.is_subtype_of(tipo_val, sym.tipo_dados):
                self._error(
                    f"Tipo incompatível em atribuição a '{node.alvo}': "
                    f"esperado '{sym.tipo_dados}', obtido '{tipo_val}'", node.linha
                )
        elif isinstance(node.alvo, MemberAccessExpr):
            # Atribuição a campo: obj.campo = expr
            self.visit(node.alvo)
            self.visit(node.expressao)

    def visit_ExprStmt(self, node: ExprStmt):
        self.visit(node.expr)

    def visit_FunctionCall(self, node: FunctionCall):
        sym = self.table.lookup(node.nome)
        if sym is None:
            self._error(f"Função '{node.nome}' não definida", node.linha)
            return 'unknown'
        if sym.tipo_simbolo not in (SymbolType.FUNCTION, SymbolType.METHOD):
            # Pode ser builtin — aceitar
            pass
        # Verificar arity (se temos tipos de parâmetros registrados)
        if sym.tipos_parametros:
            if len(node.argumentos) != len(sym.tipos_parametros):
                self._error(
                    f"Função '{node.nome}' espera {len(sym.tipos_parametros)} "
                    f"argumentos, recebeu {len(node.argumentos)}", node.linha
                )
        for arg in node.argumentos:
            self.visit(arg)
        return sym.tipo_retorno or sym.tipo_dados

    def visit_BinaryExpr(self, node: BinaryExpr):
        t_esq = self.visit(node.esquerda)
        t_dir = self.visit(node.direita)
        op = node.operador
        if op in ('+', '-', '*', '/', '%'):
            if op == '+' and 'string' in (t_esq, t_dir):
                return 'string'
            if t_esq == 'number' and t_dir == 'number':
                return 'number'
            if t_esq != 'any' and t_dir != 'any':
                self._error(
                    f"Operador '{op}' requer 'number', obtido '{t_esq}' e '{t_dir}'",
                    node.linha
                )
            return 'number'
        if op in ('==', '!=', '<', '>', '<=', '>='):
            return 'bool'
        if op in ('&&', '||'):
            if t_esq == 'bool' and t_dir == 'bool':
                return 'bool'
            self._error(f"Operador '{op}' requer 'bool'", node.linha)
            return 'bool'
        return 'unknown'

    def visit_UnaryExpr(self, node: UnaryExpr):
        tipo = self.visit(node.operando)
        if node.operador == '!' and tipo != 'bool':
            self._error(f"Operador '!' requer 'bool', obtido '{tipo}'", node.linha)
        return tipo

    def visit_Identifier(self, node: Identifier):
        sym = self.table.lookup(node.nome)
        if sym is None:
            self._error(f"Identificador '{node.nome}' não declarado", node.linha)
            return 'unknown'
        return sym.tipo_dados

    def visit_NumberLiteral(self, node): return 'number'
    def visit_StringLiteral(self, node): return 'string'
    def visit_BoolLiteral(self, node):   return 'bool'
    def visit_NullLiteral(self, node):   return 'null'
    def visit_ListLiteral(self, node):   return 'list'
    def visit_DictLiteral(self, node):   return 'dict'

    def visit_IndexAccess(self, node: IndexAccess):
        self.visit(node.obj)
        self.visit(node.indice)
        return 'any'

    def visit_ParBlock(self, node: ParBlock):
        self.table.enter_scope('par')
        for stmt in node.statements:
            self.visit(stmt)
        self.table.exit_scope()

    def visit_SeqBlock(self, node: SeqBlock):
        self.table.enter_scope('seq')
        for stmt in node.statements:
            self.visit(stmt)
        self.table.exit_scope()

    # ─────────────────────────────────────────────────────────────────────────
    # NOVO v2026.1 — POO
    # ─────────────────────────────────────────────────────────────────────────

    def visit_ClassDecl(self, node: ClassDecl):
        # Registrar estrutura completa na tabela de símbolos
        desc = self.table.register_class(node)

        # Analisar corpo no escopo da classe
        self.table.enter_scope(f'class_{node.nome}')
        prev_classe = self._classe_atual
        self._classe_atual = node.nome

        # 'this' disponível com tipo da própria classe
        self.table.add_symbol('this', SymbolType.VARIABLE, node.nome,
                               inicializado=True)
        # 'super' disponível se há herança
        if node.superclasse:
            self.table.add_symbol('super', SymbolType.VARIABLE, node.superclasse,
                                   inicializado=True)

        # Analisar construtor
        if node.construtor:
            self._visit_constructor(node.construtor, node.nome)

        # Analisar métodos
        for metodo in node.metodos:
            self._visit_method(metodo, node.nome, desc)

        self._classe_atual = prev_classe
        self.table.exit_scope()

    def _inject_class_fields(self, class_name: str):
        """
        Injeta todos os campos da classe (e superclasses) no escopo atual
        para que possam ser referenciados sem 'this.' dentro de métodos
        e construtores.
        """
        desc = self.table.lookup_class(class_name)
        while desc:
            for f in desc._fields:
                # Só adiciona se ainda não existe no escopo (evita override acidental)
                if not self.table.lookup_local(f.nome):
                    self.table.add_symbol(
                        f.nome, SymbolType.FIELD, f.tipo_dados,
                        inicializado=True,
                        class_name=class_name
                    )
            desc = desc.superclasse

    def _visit_constructor(self, node: ConstructorDecl, class_name: str):
        self.table.enter_scope(f'ctor_{class_name}')
        # Campos da classe visíveis diretamente (sem 'this.')
        self._inject_class_fields(class_name)
        for param in node.parametros:
            self.table.add_symbol(param.nome, SymbolType.PARAMETER, param.tipo,
                                   linha=param.linha)
        prev_f, prev_r = self._em_funcao, self._tipo_retorno_funcao
        self._em_funcao = True
        self._tipo_retorno_funcao = 'void'
        self.visit(node.corpo)
        self._em_funcao, self._tipo_retorno_funcao = prev_f, prev_r
        self.table.exit_scope()

    def _visit_method(self, node: MethodDecl, class_name: str, desc: ClassDescriptor):
        # Verificar override
        if node.is_override:
            if desc.superclasse:
                entry = desc.superclasse.lookup_method(node.nome)
                if entry is None:
                    self._error(
                        f"Método '{node.nome}' marcado como @override, "
                        f"mas não existe na superclasse '{desc.superclasse.nome}'",
                        node.linha
                    )
            else:
                self._error(
                    f"Método '{node.nome}' marcado como @override, "
                    f"mas classe '{class_name}' não tem superclasse", node.linha
                )

        self.table.enter_scope(f'method_{class_name}_{node.nome}')
        # Campos da classe visíveis diretamente (sem 'this.')
        self._inject_class_fields(class_name)
        for param in node.parametros:
            self.table.add_symbol(param.nome, SymbolType.PARAMETER, param.tipo,
                                   linha=param.linha)
        prev_f, prev_r = self._em_funcao, self._tipo_retorno_funcao
        self._em_funcao = True
        self._tipo_retorno_funcao = node.tipo_retorno
        self.visit(node.corpo)
        self._em_funcao, self._tipo_retorno_funcao = prev_f, prev_r
        self.table.exit_scope()

    def visit_NewExpr(self, node: NewExpr) -> str:
        desc = self.table.lookup_class(node.class_name)
        if desc is None:
            self._error(f"Classe '{node.class_name}' não definida", node.linha)
            return 'unknown'
        if desc.is_abstract:
            self._error(f"Não é possível instanciar classe abstrata '{node.class_name}'",
                        node.linha)
        for arg in node.argumentos:
            self.visit(arg)
        return node.class_name

    def visit_MemberAccessExpr(self, node: MemberAccessExpr) -> str:
        tipo_obj = self.visit(node.obj)
        desc = self.table.lookup_class(tipo_obj)
        if desc is None:
            self._error(f"Tipo '{tipo_obj}' não é uma classe", node.linha)
            return 'unknown'
        field_layout = desc.lookup_field(node.membro)
        if field_layout is None:
            self._error(
                f"Campo '{node.membro}' não existe na classe '{tipo_obj}'", node.linha
            )
            return 'unknown'
        return field_layout.tipo_dados

    def visit_MethodCallExpr(self, node: MethodCallExpr) -> str:
        tipo_obj = self.visit(node.obj)
        desc = self.table.lookup_class(tipo_obj)
        if desc is None:
            self._error(f"Tipo '{tipo_obj}' não é uma classe", node.linha)
            return 'unknown'
        entry = desc.lookup_method(node.metodo)
        if entry is None:
            self._error(
                f"Método '{node.metodo}' não existe na classe '{tipo_obj}'", node.linha
            )
            return 'unknown'
        # Verificar arity
        if len(node.argumentos) != len(entry.param_types):
            self._error(
                f"Método '{node.metodo}' espera {len(entry.param_types)} argumentos, "
                f"recebeu {len(node.argumentos)}", node.linha
            )
            return entry.return_type
        for i, (arg, expected) in enumerate(zip(node.argumentos, entry.param_types)):
            tipo_arg = self.visit(arg)
            if tipo_arg and not self.table.is_subtype_of(tipo_arg, expected):
                self._error(
                    f"Argumento {i} de '{node.metodo}': "
                    f"esperado '{expected}', obtido '{tipo_arg}'", node.linha
                )
        return entry.return_type

    def visit_ThisExpr(self, node: ThisExpr) -> str:
        sym = self.table.lookup('this')
        if sym is None:
            self._error("'this' usado fora de contexto de classe", node.linha)
            return 'unknown'
        return sym.tipo_dados

    def visit_SuperCallExpr(self, node: SuperCallExpr) -> str:
        sym = self.table.lookup('super')
        if sym is None:
            self._error("'super' usado fora de contexto de classe com herança", node.linha)
            return 'unknown'
        desc = self.table.lookup_class(sym.tipo_dados)
        if desc is None:
            return 'unknown'
        entry = desc.lookup_method(node.metodo)
        if entry is None:
            self._error(
                f"Método '{node.metodo}' não existe na superclasse '{sym.tipo_dados}'",
                node.linha
            )
            return 'unknown'
        return entry.return_type

    # ─────────────────────────────────────────────────────────────────────────
    # NOVO v2026.1 — Paralelismo
    # ─────────────────────────────────────────────────────────────────────────

    def visit_SpawnStmt(self, node: SpawnStmt):
        if node.call_expr:
            self.visit(node.call_expr)
        if node.body:
            self.table.enter_scope('spawn_thread')
            self.visit(node.body)
            self.table.exit_scope()

    def visit_SyncStmt(self, node: SyncStmt):
        sym = self.table.lookup(node.lock_var)
        if sym is None:
            self._error(
                f"Variável de mutex '{node.lock_var}' não declarada", node.linha
            )
        self.table.enter_scope('sync_block')
        self.visit(node.corpo)
        self.table.exit_scope()

    def visit_AsyncFuncDecl(self, node: AsyncFuncDecl):
        if self.table.lookup_local(node.nome):
            self._error(f"Função async '{node.nome}' já declarada", node.linha)
            return
        param_types = [p.tipo for p in node.parametros]
        self.table.add_symbol(
            node.nome, SymbolType.FUNCTION, node.tipo_retorno,
            linha=node.linha,
            tipos_parametros=param_types,
            tipo_retorno=node.tipo_retorno,
            is_async=True
        )
        self.table.enter_scope(f'async_{node.nome}')
        for param in node.parametros:
            self.table.add_symbol(param.nome, SymbolType.PARAMETER, param.tipo,
                                   linha=param.linha)
        prev_f, prev_r = self._em_funcao, self._tipo_retorno_funcao
        self._em_funcao = True
        self._tipo_retorno_funcao = node.tipo_retorno
        self.visit(node.corpo)
        self._em_funcao, self._tipo_retorno_funcao = prev_f, prev_r
        self.table.exit_scope()

    def visit_AwaitExpr(self, node: AwaitExpr) -> str:
        tipo = self.visit(node.future_expr)
        return tipo or 'any'

    # ─────────────────────────────────────────────────────────────────────────
    # NOVO v2026.1 — Distribuição
    # ─────────────────────────────────────────────────────────────────────────

    def visit_NodeDecl(self, node: NodeDecl):
        # Já registrado na passagem 1
        pass

    def visit_RemoteFuncDecl(self, node: RemoteFuncDecl):
        func = node.func_decl
        # Registrar com flag is_remote (evitar conflito com visit_FuncDecl)
        if self.table.lookup_local(func.nome):
            self._error(f"Função remote '{func.nome}' já declarada", node.linha)
            return
        param_types = [p.tipo for p in func.parametros]
        self.table.add_symbol(
            func.nome, SymbolType.FUNCTION, func.tipo_retorno,
            linha=func.linha,
            tipos_parametros=param_types,
            tipo_retorno=func.tipo_retorno,
            is_remote=True
        )
        # Verificar serializabilidade de todos os tipos
        for param in func.parametros:
            self._check_serializable(param.tipo, f"Parâmetro '{param.nome}'", func.linha)
        self._check_serializable(func.tipo_retorno, "Tipo de retorno", func.linha)
        # Analisar corpo diretamente (sem re-registrar o símbolo via visit_FuncDecl)
        self.table.enter_scope(f'func_{func.nome}')
        for param in func.parametros:
            self.table.add_symbol(param.nome, SymbolType.PARAMETER, param.tipo,
                                   linha=param.linha)
        prev_f, prev_r = self._em_funcao, self._tipo_retorno_funcao
        self._em_funcao = True
        self._tipo_retorno_funcao = func.tipo_retorno
        self.visit(func.corpo)
        self._em_funcao, self._tipo_retorno_funcao = prev_f, prev_r
        self.table.exit_scope()

    def visit_RemoteCallStmt(self, node: RemoteCallStmt):
        # 1. Verificar que o nó existe
        if isinstance(node.alvo, Identifier):
            addr = self.table.lookup_node(node.alvo.nome)
            if addr is None:
                self._error(
                    f"Nó remoto '{node.alvo.nome}' não declarado. "
                    f"Use: node {node.alvo.nome} = \"ip:porta\";", node.linha
                )
        # 2. Verificar que a função é remote
        if isinstance(node.function_call, FunctionCall):
            sym = self.table.lookup(node.function_call.nome)
            if sym is None:
                self._error(
                    f"Função '{node.function_call.nome}' não definida", node.linha
                )
            elif not getattr(sym, 'is_remote', False):
                self._error(
                    f"Função '{node.function_call.nome}' não é marcada como 'remote'. "
                    f"Adicione 'remote' na declaração.", node.linha
                )
            # 3. Verificar serializabilidade dos argumentos
            if sym:
                for i, arg in enumerate(node.function_call.argumentos):
                    tipo_arg = self.visit(arg)
                    self._check_serializable(
                        tipo_arg, f"Argumento {i} de chamada remota", node.linha
                    )

    def visit_RemoteSpawnStmt(self, node: RemoteSpawnStmt):
        if isinstance(node.alvo, Identifier):
            if not self.table.lookup_node(node.alvo.nome):
                self._error(f"Nó remoto '{node.alvo.nome}' não declarado", node.linha)
        self.table.enter_scope('remote_spawn')
        self.visit(node.corpo)
        self.table.exit_scope()

    # ─────────────────────────────────────────────────────────────────────────
    # Utilitários internos
    # ─────────────────────────────────────────────────────────────────────────

    def _check_serializable(self, tipo: str, contexto: str, linha: int):
        if tipo in SERIALIZABLE_TYPES:
            return True
        desc = self.table.lookup_class(tipo)
        if desc and desc.is_serializable:
            return True
        self._error(
            f"{contexto}: tipo '{tipo}' não é serializável para chamada remota. "
            f"Use tipos primitivos: {sorted(SERIALIZABLE_TYPES - {'null', 'void'})}",
            linha
        )
        return False

    def _error(self, mensagem: str, linha: int = 0):
        msg = f"[Erro Semântico linha {linha}] {mensagem}"
        self.errors.append(msg)

    def _inicializar_builtins(self):
        """
        Registra funções built-in do MiniPar.
        Funções com tipos_parametros=[] aceitam qualquer número de argumentos
        (a checagem de arity só ocorre quando tipos_parametros não está vazio).
        """
        builtins = [
            # (nome, tipo_retorno, tipos_parametros)
            # lista vazia = variadic / sem checagem de arity
            ('print',  'void',   []),   # variadic
            ('input',  'string', []),   # variadic
            ('len',    'number', ['any']),
            ('str',    'string', ['any']),
            ('num',    'number', ['any']),
            ('append', 'void',   ['list', 'any']),
            ('keys',   'list',   ['dict']),
            ('values', 'list',   ['dict']),
        ]
        for nome, ret, params in builtins:
            self.table.add_symbol(
                nome, SymbolType.FUNCTION, ret,
                tipos_parametros=params,
                tipo_retorno=ret
            )
