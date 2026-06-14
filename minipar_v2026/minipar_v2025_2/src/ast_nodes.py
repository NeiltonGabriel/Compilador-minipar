"""
ast_nodes.py — MiniPar v2026.1
Nós da Árvore Sintática Abstrata.
Todos os nós v2026.1 são preservados; novos nós são adicionados ao final.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Any


# ─────────────────────────────────────────────────────────────────────────────
# Nós existentes v2026.1 (inalterados)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Program:
    declarations: List[Any]

@dataclass
class VarDecl:
    nome: str
    tipo: str
    inicializador: Optional[Any]
    linha: int = 0
    coluna: int = 0

@dataclass
class FuncDecl:
    tipo_retorno: str
    nome: str
    parametros: List[Any]
    corpo: Any
    linha: int = 0
    coluna: int = 0

@dataclass
class Block:
    statements: List[Any]
    linha: int = 0

@dataclass
class IfStmt:
    condicao: Any
    corpo_se: Any
    corpo_senao: Optional[Any]
    linha: int = 0

@dataclass
class WhileStmt:
    condicao: Any
    corpo: Any
    linha: int = 0

@dataclass
class ReturnStmt:
    expressao: Optional[Any]
    linha: int = 0

@dataclass
class BreakStmt:
    linha: int = 0

@dataclass
class ContinueStmt:
    linha: int = 0

@dataclass
class ExprStmt:
    expr: Any
    linha: int = 0

@dataclass
class AssignStmt:
    alvo: str
    expressao: Any
    linha: int = 0

@dataclass
class BinaryExpr:
    esquerda: Any
    operador: str
    direita: Any
    linha: int = 0

@dataclass
class UnaryExpr:
    operador: str
    operando: Any
    linha: int = 0

@dataclass
class FunctionCall:
    nome: str
    argumentos: List[Any]
    linha: int = 0

@dataclass
class Identifier:
    nome: str
    linha: int = 0

@dataclass
class NumberLiteral:
    valor: float
    linha: int = 0

@dataclass
class StringLiteral:
    valor: str
    linha: int = 0

@dataclass
class BoolLiteral:
    valor: bool
    linha: int = 0

@dataclass
class ListLiteral:
    elementos: List[Any]
    linha: int = 0

@dataclass
class DictLiteral:
    pares: List[Any]   # list of (key_expr, val_expr)
    linha: int = 0

@dataclass
class IndexAccess:
    obj: Any
    indice: Any
    linha: int = 0

@dataclass
class MethodCallOld:
    """Chamada de método v2026.1: obj.metodo(args) como statement separado."""
    obj: str
    metodo: str
    argumentos: List[Any]
    linha: int = 0

@dataclass
class ParBlock:
    statements: List[Any]
    linha: int = 0

@dataclass
class SeqBlock:
    statements: List[Any]
    linha: int = 0

@dataclass
class ChannelDecl:
    tipo_canal: str   # 's_channel' ou 'c_channel'
    nome: str
    argumentos: List[Any]
    linha: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Novos nós v2026.1 — POO
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FieldDecl:
    """Atributo de classe: var number x;"""
    nome: str
    tipo: str
    inicializador: Optional[Any]
    is_static: bool = False
    linha: int = 0
    coluna: int = 0

@dataclass
class MethodDecl:
    """Método de classe."""
    nome: str
    tipo_retorno: str
    parametros: List[Any]     # List[VarDecl]
    corpo: Any                # Block
    is_static: bool = False
    is_override: bool = False
    linha: int = 0
    coluna: int = 0

@dataclass
class ConstructorDecl:
    """Construtor: func NomeDaClasse(...) { ... }"""
    class_name: str
    parametros: List[Any]
    corpo: Any
    linha: int = 0
    coluna: int = 0

@dataclass
class ClassDecl:
    """Declaração completa de uma classe."""
    nome: str
    superclasse: Optional[str]
    campos: List[FieldDecl]
    metodos: List[MethodDecl]
    construtor: Optional[ConstructorDecl]
    is_abstract: bool = False
    linha: int = 0
    coluna: int = 0

@dataclass
class NewExpr:
    """Instanciamento: new MinhaClasse(args)"""
    class_name: str
    argumentos: List[Any]
    linha: int = 0
    coluna: int = 0

@dataclass
class MemberAccessExpr:
    """Acesso a campo: obj.campo"""
    obj: Any
    membro: str
    linha: int = 0
    coluna: int = 0

@dataclass
class MethodCallExpr:
    """Chamada de método: obj.metodo(args)"""
    obj: Any
    metodo: str
    argumentos: List[Any]
    linha: int = 0
    coluna: int = 0

@dataclass
class ThisExpr:
    """Referência ao objeto atual."""
    linha: int = 0
    coluna: int = 0

@dataclass
class SuperCallExpr:
    """Chamada a método da superclasse: super.metodo(args)"""
    metodo: str
    argumentos: List[Any]
    linha: int = 0
    coluna: int = 0

@dataclass
class NullLiteral:
    """Valor nulo de referência."""
    linha: int = 0
    coluna: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Novos nós v2026.1 — Paralelismo Avançado
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SpawnStmt:
    """
    spawn funcao(args);       → call_expr definida, body=None
    spawn { bloco anônimo }   → call_expr=None, body definido
    """
    call_expr: Optional[Any]
    body: Optional[Any]
    linha: int = 0
    coluna: int = 0

@dataclass
class SyncStmt:
    """sync(lock) { ... } — bloco sincronizado sobre mutex."""
    lock_var: str
    corpo: Any
    linha: int = 0
    coluna: int = 0

@dataclass
class AsyncFuncDecl:
    """async func tipo nome(params) { ... }"""
    nome: str
    tipo_retorno: str
    parametros: List[Any]
    corpo: Any
    linha: int = 0
    coluna: int = 0

@dataclass
class AwaitExpr:
    """await <expressão> — aguarda resolução de um future."""
    future_expr: Any
    linha: int = 0
    coluna: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Novos nós v2026.1 — Execução Distribuída
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NodeDecl:
    """node servidor = "192.168.1.10:7000";"""
    nome: str
    endereco: str
    linha: int = 0
    coluna: int = 0

@dataclass
class RemoteFuncDecl:
    """remote func ... — gera stub local + implementação remota."""
    func_decl: Any          # FuncDecl original
    linha: int = 0
    coluna: int = 0

@dataclass
class RemoteCallStmt:
    """remote on <alvo> funcao(args);"""
    alvo: Any               # Identifier (nome do node) ou StringLiteral
    function_call: Any      # FunctionCall
    result_var: Optional[str] = None
    linha: int = 0
    coluna: int = 0

@dataclass
class RemoteSpawnStmt:
    """spawn on <alvo> { ... } — executa bloco em nó remoto."""
    alvo: Any
    corpo: Any
    linha: int = 0
    coluna: int = 0
