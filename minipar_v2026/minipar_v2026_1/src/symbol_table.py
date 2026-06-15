"""
symbol_table.py — MiniPar v2026.1
Tabela de símbolos estendida com suporte a classes, VTables e nós remotos.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from enum import Enum


# ─────────────────────────────────────────────────────────────────────────────
# Enums e estruturas básicas
# ─────────────────────────────────────────────────────────────────────────────

class SymbolType(Enum):
    VARIABLE   = 'VARIABLE'
    FUNCTION   = 'FUNCTION'
    PARAMETER  = 'PARAMETER'
    CHANNEL    = 'CHANNEL'
    CLASS      = 'CLASS'      # NOVO v2026.1
    METHOD     = 'METHOD'     # NOVO v2026.1
    FIELD      = 'FIELD'      # NOVO v2026.1


@dataclass
class Symbol:
    nome: str
    tipo_simbolo: SymbolType
    tipo_dados: str
    escopo_nivel: int = 0
    linha_declarada: int = 0
    inicializado: bool = True
    tipos_parametros: List[str] = field(default_factory=list)
    tipo_retorno: str = ''
    tipo_canal: str = ''
    # ── NOVO v2026.1 ──────────────────────────────────────────────────────────
    is_remote: bool = False       # func marcada como 'remote'
    is_async: bool = False        # func marcada como 'async'
    class_name: str = ''          # para METHOD/FIELD: classe proprietária
    vtable_index: int = -1        # índice na VTable (dispatch dinâmico)
    field_offset: int = -1        # offset em bytes no layout do objeto


# ─────────────────────────────────────────────────────────────────────────────
# Estruturas de layout de classe (NOVO v2026.1)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FieldLayout:
    """Descreve um campo dentro do layout de memória de um objeto."""
    nome: str
    tipo_dados: str
    offset: int          # offset em bytes a partir do início do objeto
    is_static: bool = False


@dataclass
class VTableEntry:
    """Entrada na tabela virtual de métodos."""
    method_name: str
    label: str           # rótulo Assembly/C: "NomeDaClasse_nomeDoMetodo"
    return_type: str
    param_types: List[str]
    is_override: bool = False


class ClassDescriptor:
    """
    Descreve completamente uma classe:
    - Layout de memória (offsets de campos)
    - VTable (tabela de ponteiros de métodos virtuais)
    - Cadeia de herança
    """

    # Offset 0 = ponteiro para VTable (4 bytes em ARMv7 32-bit)
    VTABLE_PTR_SIZE = 4

    def __init__(self, nome: str, superclass_desc: Optional['ClassDescriptor'] = None):
        self.nome = nome
        self.superclasse: Optional[ClassDescriptor] = superclass_desc

        self._fields: List[FieldLayout] = []
        self._static_fields: Dict[str, FieldLayout] = {}
        self._next_offset = self.VTABLE_PTR_SIZE

        # VTable começa como cópia da superclasse (herança)
        if superclass_desc:
            self._vtable: List[VTableEntry] = list(superclass_desc._vtable)
            self._next_offset = superclass_desc.size_bytes
        else:
            self._vtable = []

        self.is_serializable: bool = False  # pode ser marcada pelo semântico
        self.is_abstract: bool = False
        self.ctor_params: List[str] = []    # tipos dos parâmetros do construtor

    # ── Campos ────────────────────────────────────────────────────────────────

    def add_field(self, nome: str, tipo_dados: str, is_static: bool = False) -> int:
        """Registra campo e retorna o offset em bytes. Campos estáticos retornam -1."""
        if is_static:
            layout = FieldLayout(nome, tipo_dados, offset=-1, is_static=True)
            self._static_fields[nome] = layout
            return -1
        offset = self._next_offset
        layout = FieldLayout(nome, tipo_dados, offset)
        self._fields.append(layout)
        self._next_offset += self._type_size(tipo_dados)
        return offset

    def lookup_field(self, nome: str) -> Optional[FieldLayout]:
        """Busca campo na classe atual e, recursivamente, em superclasses."""
        for f in self._fields:
            if f.nome == nome:
                return f
        if nome in self._static_fields:
            return self._static_fields[nome]
        if self.superclasse:
            return self.superclasse.lookup_field(nome)
        return None

    # ── Métodos / VTable ──────────────────────────────────────────────────────

    def add_or_override_method(self, method_name: str, return_type: str,
                               param_types: List[str],
                               is_override: bool = False) -> str:
        """
        Adiciona método à VTable ou substitui entrada herdada (override).
        Retorna o label Assembly gerado (NomeDaClasse_nomeDoMetodo).
        """
        label = f"{self.nome}_{method_name}"
        entry = VTableEntry(method_name, label, return_type,
                            param_types, is_override)
        # Se override: substituir na mesma posição para manter índice estável
        for i, existing in enumerate(self._vtable):
            if existing.method_name == method_name:
                self._vtable[i] = entry
                return label
        self._vtable.append(entry)
        return label

    def lookup_method(self, nome: str) -> Optional[VTableEntry]:
        """Busca método na VTable (inclui herdados)."""
        for entry in self._vtable:
            if entry.method_name == nome:
                return entry
        return None

    def get_vtable_index(self, method_name: str) -> int:
        """Índice na VTable — usado para dispatch dinâmico."""
        for i, entry in enumerate(self._vtable):
            if entry.method_name == method_name:
                return i
        return -1

    # ── Propriedades ──────────────────────────────────────────────────────────

    @property
    def size_bytes(self) -> int:
        return self._next_offset

    @property
    def vtable_label(self) -> str:
        """Label global da VTable desta classe no Assembly."""
        return f"__vtable_{self.nome}"

    @property
    def ctor_label(self) -> str:
        return f"{self.nome}___ctor"

    @staticmethod
    def _type_size(tipo: str) -> int:
        """Tamanho em bytes por tipo MiniPar (alinhado a 4 bytes para ARMv7)."""
        return {
            'number': 4,
            'bool':   4,
            'string': 4,
            'any':    8,
            'list':   4,
            'dict':   4,
        }.get(tipo, 4)  # default: ponteiro de 4 bytes (objeto ou canal)

    def __repr__(self):
        return (f"ClassDescriptor(nome={self.nome!r}, "
                f"size={self.size_bytes}B, "
                f"fields={[f.nome for f in self._fields]}, "
                f"vtable={[e.method_name for e in self._vtable]})")


# ─────────────────────────────────────────────────────────────────────────────
# Escopo e Tabela de Símbolos
# ─────────────────────────────────────────────────────────────────────────────

class Scope:
    def __init__(self, nivel: int, nome: str, pai: Optional['Scope']):
        self.nivel = nivel
        self.nome = nome
        self.pai = pai
        self.simbolos: Dict[str, Symbol] = {}

    def add(self, simbolo: Symbol) -> bool:
        if simbolo.nome in self.simbolos:
            return False
        self.simbolos[simbolo.nome] = simbolo
        return True

    def lookup_local(self, nome: str) -> Optional[Symbol]:
        return self.simbolos.get(nome)

    def lookup(self, nome: str) -> Optional[Symbol]:
        """Busca no escopo atual e em todos os escopos pai."""
        if nome in self.simbolos:
            return self.simbolos[nome]
        if self.pai:
            return self.pai.lookup(nome)
        return None

    def __repr__(self):
        return f"Scope(nivel={self.nivel}, nome={self.nome!r}, simbolos={list(self.simbolos.keys())})"


class SymbolTable:
    """
    Tabela de símbolos hierárquica estendida para MiniPar v2026.1.
    Adiciona class_registry e node_registry aos escopos existentes.
    """

    def __init__(self):
        # ── Escopos (existente v2026.1) ───────────────────────────────────────
        self.escopo_global = Scope(0, 'global', None)
        self.escopo_atual = self.escopo_global
        self.pilha_escopos: List[Scope] = [self.escopo_global]
        self._contador = 0

        # ── NOVO v2026.1 ──────────────────────────────────────────────────────
        self.class_registry: Dict[str, ClassDescriptor] = {}
        self.node_registry: Dict[str, str] = {}   # nome → "ip:porta"

    # ── Gerenciamento de escopos ──────────────────────────────────────────────

    def enter_scope(self, nome: str = 'bloco'):
        self._contador += 1
        novo = Scope(self._contador, nome, self.escopo_atual)
        self.pilha_escopos.append(novo)
        self.escopo_atual = novo

    def exit_scope(self):
        if len(self.pilha_escopos) > 1:
            self.pilha_escopos.pop()
            self.escopo_atual = self.pilha_escopos[-1]

    # ── Símbolos ──────────────────────────────────────────────────────────────

    def add_symbol(self, nome: str, tipo_simbolo: SymbolType, tipo_dados: str,
                   linha: int = 0, **kwargs) -> bool:
        simbolo = Symbol(
            nome=nome,
            tipo_simbolo=tipo_simbolo,
            tipo_dados=tipo_dados,
            escopo_nivel=self.escopo_atual.nivel,
            linha_declarada=linha,
            **kwargs
        )
        return self.escopo_atual.add(simbolo)

    def lookup_local(self, nome: str) -> Optional[Symbol]:
        return self.escopo_atual.lookup_local(nome)

    def lookup(self, nome: str) -> Optional[Symbol]:
        return self.escopo_atual.lookup(nome)

    def get_scope_level(self) -> int:
        return self.escopo_atual.nivel

    # ── Classes (NOVO v2026.1) ────────────────────────────────────────────────

    def register_class(self, class_decl) -> ClassDescriptor:
        """
        Constrói o ClassDescriptor a partir de um ClassDecl (nó de AST).
        Deve ser chamado após a primeira passagem (forward declarations).
        """
        superclass_desc = None
        if class_decl.superclasse:
            superclass_desc = self.class_registry.get(class_decl.superclasse)
            if superclass_desc is None:
                from semantic import SemanticError
                raise SemanticError(
                    f"Superclasse '{class_decl.superclasse}' não definida "
                    f"(linha {class_decl.linha})"
                )

        desc = ClassDescriptor(class_decl.nome, superclass_desc)
        desc.is_abstract = class_decl.is_abstract
        if class_decl.construtor:
            desc.ctor_params = [p.tipo for p in class_decl.construtor.parametros]

        # Registrar campos
        for campo in class_decl.campos:
            offset = desc.add_field(campo.nome, campo.tipo, is_static=campo.is_static)
            # Campos estáticos também ficam no escopo global
            if campo.is_static:
                self.add_symbol(
                    nome=f"{class_decl.nome}.{campo.nome}",
                    tipo_simbolo=SymbolType.FIELD,
                    tipo_dados=campo.tipo,
                    linha=campo.linha,
                    class_name=class_decl.nome,
                    field_offset=offset
                )

        # Registrar métodos na VTable
        for metodo in class_decl.metodos:
            param_types = [p.tipo for p in metodo.parametros]
            label = desc.add_or_override_method(
                metodo.nome, metodo.tipo_retorno, param_types,
                is_override=metodo.is_override
            )
            vtable_idx = desc.get_vtable_index(metodo.nome)
            self.add_symbol(
                nome=f"{class_decl.nome}.{metodo.nome}",
                tipo_simbolo=SymbolType.METHOD,
                tipo_dados=metodo.tipo_retorno,
                linha=metodo.linha,
                class_name=class_decl.nome,
                tipos_parametros=param_types,
                tipo_retorno=metodo.tipo_retorno,
                vtable_index=vtable_idx
            )

        # Registrar a classe como tipo no escopo global
        self.add_symbol(
            nome=class_decl.nome,
            tipo_simbolo=SymbolType.CLASS,
            tipo_dados=class_decl.nome,
            linha=class_decl.linha
        )

        self.class_registry[class_decl.nome] = desc
        return desc

    def lookup_class(self, nome: str) -> Optional[ClassDescriptor]:
        return self.class_registry.get(nome)

    def is_subtype_of(self, tipo_filho: str, tipo_pai: str) -> bool:
        """
        Verifica compatibilidade de tipos incluindo herança.
        'any' é compatível com tudo.
        """
        if tipo_filho == tipo_pai:
            return True
        if tipo_pai in ('any', 'unknown') or tipo_filho in ('any', 'unknown'):
            return True
        # Conversão implícita number ↔ string (mantido do v2026.1)
        if {tipo_filho, tipo_pai} == {'number', 'string'}:
            return True
        # Verificar cadeia de herança
        desc = self.lookup_class(tipo_filho)
        while desc:
            if desc.nome == tipo_pai:
                return True
            desc = desc.superclasse
        return False

    # ── Nós remotos (NOVO v2026.1) ────────────────────────────────────────────

    def register_node(self, nome: str, endereco: str):
        self.node_registry[nome] = endereco

    def lookup_node(self, nome: str) -> Optional[str]:
        return self.node_registry.get(nome)

    def print_table(self):
        """Debug: imprime todos os símbolos e classes registrados."""
        print("=== TABELA DE SÍMBOLOS ===")
        for scope in self.pilha_escopos:
            print(f"  Escopo '{scope.nome}' (nível {scope.nivel}):")
            for nome, sym in scope.simbolos.items():
                print(f"    {nome}: {sym.tipo_dados} [{sym.tipo_simbolo.value}]")
        if self.class_registry:
            print("\n=== REGISTRO DE CLASSES ===")
            for nome, desc in self.class_registry.items():
                print(f"  {nome}: {desc.size_bytes}B | vtable={[e.method_name for e in desc._vtable]}")
        if self.node_registry:
            print("\n=== NODOS REMOTOS ===")
            for nome, addr in self.node_registry.items():
                print(f"  {nome} → {addr}")