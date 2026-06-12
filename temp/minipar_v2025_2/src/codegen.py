"""
codegen.py — MiniPar v2025.2
Gerador de Código Intermediário (TAC — Three-Address Code) estendido.
Novos opcodes: ALLOC_OBJ, VCALL, SPAWN_THREAD, MUTEX_*, RPC_CALL, etc.
"""

from dataclasses import dataclass
from typing import Optional, List, Any
from enum import Enum
from ast_nodes import *
from symbol_table import SymbolTable


# ─────────────────────────────────────────────────────────────────────────────
# Opcodes TAC
# ─────────────────────────────────────────────────────────────────────────────

class TACOp(Enum):
    # ── Existentes v2025.1 ────────────────────────────────────────────────────
    ASSIGN      = 'ASSIGN'
    ADD         = 'ADD'
    SUB         = 'SUB'
    MUL         = 'MUL'
    DIV         = 'DIV'
    MOD         = 'MOD'
    EQ          = 'EQ'
    NEQ         = 'NEQ'
    LT          = 'LT'
    GT          = 'GT'
    LTE         = 'LTE'
    GTE         = 'GTE'
    AND         = 'AND'
    OR          = 'OR'
    NOT         = 'NOT'
    NEG         = 'NEG'
    LABEL       = 'LABEL'
    GOTO        = 'GOTO'
    IF_FALSE    = 'IF_FALSE'
    PARAM       = 'PARAM'
    CALL        = 'CALL'
    RETURN      = 'RETURN'
    FUNC_BEGIN  = 'FUNC_BEGIN'
    FUNC_END    = 'FUNC_END'
    # ── POO — NOVO v2025.2 ────────────────────────────────────────────────────
    ALLOC_OBJ   = 'ALLOC_OBJ'       # result = ALLOC_OBJ ClassName size_bytes
    SET_VTABLE  = 'SET_VTABLE'       # SET_VTABLE ptr vtable_label
    STORE_FIELD = 'STORE_FIELD'      # STORE_FIELD ptr value offset
    LOAD_FIELD  = 'LOAD_FIELD'       # result = LOAD_FIELD ptr offset
    INIT_FIELD  = 'INIT_FIELD'       # INIT_FIELD ptr offset size (zero-fill)
    LOAD_VTABLE = 'LOAD_VTABLE'      # result = LOAD_VTABLE ptr
    VCALL       = 'VCALL'            # result = VCALL vtable_ptr vtable_index
    STATIC_CALL = 'STATIC_CALL'      # result = STATIC_CALL label num_args
    # ── Paralelismo — NOVO v2025.2 ────────────────────────────────────────────
    PAR_BEGIN     = 'PAR_BEGIN'
    PAR_END       = 'PAR_END'
    SPAWN_THREAD  = 'SPAWN_THREAD'   # result = SPAWN_THREAD func_label num_args
    THREAD_JOIN   = 'THREAD_JOIN'    # THREAD_JOIN handle
    PARAM_THREAD  = 'PARAM_THREAD'   # PARAM_THREAD value
    MUTEX_INIT    = 'MUTEX_INIT'     # MUTEX_INIT lock_name
    MUTEX_LOCK    = 'MUTEX_LOCK'     # MUTEX_LOCK lock_name
    MUTEX_UNLOCK  = 'MUTEX_UNLOCK'   # MUTEX_UNLOCK lock_name
    ASYNC_BEGIN   = 'ASYNC_BEGIN'    # result = ASYNC_BEGIN func_name
    AWAIT_FUTURE  = 'AWAIT_FUTURE'   # result = AWAIT_FUTURE future_handle
    # ── Distribuído — NOVO v2025.2 ────────────────────────────────────────────
    CONNECT_NODE    = 'CONNECT_NODE'    # result = CONNECT_NODE "ip:porta"
    RPC_CALL        = 'RPC_CALL'        # result = RPC_CALL conn_handle "func_name"
    PARAM_REMOTE    = 'PARAM_REMOTE'    # PARAM_REMOTE serialized_buf
    SERIALIZE       = 'SERIALIZE'       # result = SERIALIZE value
    DESERIALIZE     = 'DESERIALIZE'     # result = DESERIALIZE buf "tipo"
    DISCONNECT_NODE = 'DISCONNECT_NODE' # DISCONNECT_NODE conn_handle
    REMOTE_SPAWN    = 'REMOTE_SPAWN'    # result = REMOTE_SPAWN conn block_label


@dataclass
class TAC:
    op: TACOp
    arg1: Optional[str] = None
    arg2: Optional[str] = None
    result: Optional[str] = None

    def __repr__(self):
        parts = []
        if self.result:
            parts.append(f"{self.result} =")
        parts.append(self.op.value)
        if self.arg1: parts.append(self.arg1)
        if self.arg2: parts.append(self.arg2)
        return ' '.join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Gerador de TAC
# ─────────────────────────────────────────────────────────────────────────────

OP_MAP = {
    '+': TACOp.ADD, '-': TACOp.SUB, '*': TACOp.MUL,
    '/': TACOp.DIV, '%': TACOp.MOD,
    '==': TACOp.EQ, '!=': TACOp.NEQ,
    '<':  TACOp.LT,  '>':  TACOp.GT,
    '<=': TACOp.LTE, '>=': TACOp.GTE,
    '&&': TACOp.AND, '||': TACOp.OR,
}


class CodeGenerator:

    def __init__(self, symbol_table: SymbolTable):
        self.table = symbol_table
        self.code: List[TAC] = []
        self._temp_count = 0
        self._label_count = 0
        self._func_count = 0
        self._loop_stack: List[tuple] = []   # (label_inicio, label_fim)
        self._par_thread_handles: List[str] = []
        self._type_map: dict = {}   # mapa temp → tipo (para dispatch correto)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def new_temp(self) -> str:
        t = f't{self._temp_count}'
        self._temp_count += 1
        return t

    def new_label(self) -> str:
        lbl = f'L{self._label_count}'
        self._label_count += 1
        return lbl

    def new_func_label(self, prefix: str = '__anon') -> str:
        lbl = f'{prefix}_{self._func_count}'
        self._func_count += 1
        return lbl

    def emit(self, op: TACOp, arg1=None, arg2=None, result=None) -> TAC:
        instr = TAC(op, arg1, arg2, result)
        self.code.append(instr)
        return instr

    def generate(self, node) -> Optional[str]:
        method = f'gen_{type(node).__name__}'
        gen = getattr(self, method, None)
        if gen:
            return gen(node)
        return None

    def print_code(self):
        for i, instr in enumerate(self.code):
            print(f"  {i:3d}: {instr}")

    # ─────────────────────────────────────────────────────────────────────────
    # Declarações existentes v2025.1
    # ─────────────────────────────────────────────────────────────────────────

    def gen_Program(self, node: Program):
        # Primeiro gerar declarações de nós (conectar ao iniciar)
        for decl in node.declarations:
            if isinstance(decl, NodeDecl):
                self.generate(decl)
        # Depois classes (vtables e construtores)
        for decl in node.declarations:
            if isinstance(decl, ClassDecl):
                self.generate(decl)
        # Depois funções (incluindo remote)
        for decl in node.declarations:
            if isinstance(decl, (FuncDecl, RemoteFuncDecl, AsyncFuncDecl)):
                self.generate(decl)
        # Por fim, código de nível global (variáveis e statements)
        for decl in node.declarations:
            if not isinstance(decl, (ClassDecl, FuncDecl, RemoteFuncDecl,
                                     AsyncFuncDecl, NodeDecl)):
                self.generate(decl)

    def gen_FuncDecl(self, node: FuncDecl):
        self.emit(TACOp.FUNC_BEGIN, node.nome)
        for param in node.parametros:
            self.emit(TACOp.PARAM, param.nome)
        self.generate(node.corpo)
        # Garantir RETURN no fim
        if not self.code or self.code[-1].op != TACOp.RETURN:
            self.emit(TACOp.RETURN, None)
        self.emit(TACOp.FUNC_END, node.nome)

    def gen_VarDecl(self, node: VarDecl):
        if node.inicializador:
            val = self.generate(node.inicializador)
            self.emit(TACOp.ASSIGN, val, None, node.nome)

    def gen_Block(self, node: Block):
        for stmt in node.statements:
            self.generate(stmt)

    def gen_IfStmt(self, node: IfStmt):
        cond = self.generate(node.condicao)
        lbl_else = self.new_label()
        lbl_end  = self.new_label()
        self.emit(TACOp.IF_FALSE, cond, None, lbl_else)
        self.generate(node.corpo_se)
        self.emit(TACOp.GOTO, None, None, lbl_end)
        self.emit(TACOp.LABEL, lbl_else)
        if node.corpo_senao:
            self.generate(node.corpo_senao)
        self.emit(TACOp.LABEL, lbl_end)

    def gen_WhileStmt(self, node: WhileStmt):
        lbl_start = self.new_label()
        lbl_end   = self.new_label()
        self._loop_stack.append((lbl_start, lbl_end))
        self.emit(TACOp.LABEL, lbl_start)
        cond = self.generate(node.condicao)
        self.emit(TACOp.IF_FALSE, cond, None, lbl_end)
        self.generate(node.corpo)
        self.emit(TACOp.GOTO, None, None, lbl_start)
        self.emit(TACOp.LABEL, lbl_end)
        self._loop_stack.pop()

    def gen_ReturnStmt(self, node: ReturnStmt):
        val = self.generate(node.expressao) if node.expressao else None
        self.emit(TACOp.RETURN, val)

    def gen_BreakStmt(self, node: BreakStmt):
        _, lbl_end = self._loop_stack[-1]
        self.emit(TACOp.GOTO, None, None, lbl_end)

    def gen_ContinueStmt(self, node: ContinueStmt):
        lbl_start, _ = self._loop_stack[-1]
        self.emit(TACOp.GOTO, None, None, lbl_start)

    def gen_AssignStmt(self, node: AssignStmt):
        val = self.generate(node.expressao)
        if isinstance(node.alvo, str):
            self.emit(TACOp.ASSIGN, val, None, node.alvo)
        elif isinstance(node.alvo, MemberAccessExpr):
            # obj.campo = val → STORE_FIELD
            ptr = self.generate(node.alvo.obj)
            tipo_obj = self._infer_type_name(node.alvo.obj)
            desc = self.table.lookup_class(tipo_obj)
            if desc:
                f = desc.lookup_field(node.alvo.membro)
                if f:
                    self.emit(TACOp.STORE_FIELD, ptr, val, str(f.offset))

    def gen_ExprStmt(self, node: ExprStmt):
        self.generate(node.expr)

    def gen_FunctionCall(self, node: FunctionCall) -> str:
        for arg in node.argumentos:
            v = self.generate(arg)
            self.emit(TACOp.PARAM, v)
        result = self.new_temp()
        self.emit(TACOp.CALL, node.nome, str(len(node.argumentos)), result)
        return result

    def gen_BinaryExpr(self, node: BinaryExpr) -> str:
        esq = self.generate(node.esquerda)
        dir_ = self.generate(node.direita)
        result = self.new_temp()
        op = OP_MAP.get(node.operador, TACOp.ADD)
        self.emit(op, esq, dir_, result)
        return result

    def gen_UnaryExpr(self, node: UnaryExpr) -> str:
        operando = self.generate(node.operando)
        result = self.new_temp()
        op = TACOp.NOT if node.operador == '!' else TACOp.NEG
        self.emit(op, operando, None, result)
        return result

    def gen_Identifier(self, node: Identifier) -> str:
        return node.nome

    def gen_NumberLiteral(self, node: NumberLiteral) -> str:
        return str(node.valor)

    def gen_StringLiteral(self, node: StringLiteral) -> str:
        return f'"{node.valor}"'

    def gen_BoolLiteral(self, node: BoolLiteral) -> str:
        return '1' if node.valor else '0'

    def gen_NullLiteral(self, node: NullLiteral) -> str:
        return 'null'

    def gen_IndexAccess(self, node: IndexAccess) -> str:
        obj = self.generate(node.obj)
        idx = self.generate(node.indice)
        result = self.new_temp()
        self.emit(TACOp.CALL, '__index__', '2', result)
        return result

    def gen_SeqBlock(self, node: SeqBlock):
        for stmt in node.statements:
            self.generate(stmt)

    # ─────────────────────────────────────────────────────────────────────────
    # NOVO v2025.2 — POO
    # ─────────────────────────────────────────────────────────────────────────

    def gen_ClassDecl(self, node: ClassDecl):
        desc = self.table.lookup_class(node.nome)
        if desc is None:
            return

        # 1. Emitir VTable como sequência de entradas de dados
        self.emit(TACOp.LABEL, f'__vtable_def_{node.nome}')
        for i, entry in enumerate(desc._vtable):
            self.emit(TACOp.ASSIGN, entry.label, None,
                      f'{desc.vtable_label}[{i}]')

        # 2. Emitir métodos
        for metodo in node.metodos:
            self._gen_method(metodo, node.nome)

        # 3. Emitir construtor
        if node.construtor:
            self._gen_constructor(node.construtor, desc)
        else:
            # Construtor padrão vazio
            self._gen_default_constructor(desc)

    def _gen_constructor(self, ctor: ConstructorDecl, desc: 'ClassDescriptor'):
        self.emit(TACOp.FUNC_BEGIN, desc.ctor_label)
        for param in ctor.parametros:
            self.emit(TACOp.PARAM, param.nome)

        # Alocar objeto no heap
        ptr = self.new_temp()
        self.emit(TACOp.ALLOC_OBJ, desc.nome, str(desc.size_bytes), ptr)

        # Inicializar ponteiro de VTable
        self.emit(TACOp.SET_VTABLE, ptr, desc.vtable_label)

        # Zero-fill de todos os campos
        for f in desc._fields:
            self.emit(TACOp.INIT_FIELD, ptr, str(f.offset),
                      str(desc._type_size(f.tipo_dados)))

        # Disponibilizar 'this' para o corpo
        self.emit(TACOp.ASSIGN, ptr, None, '__this__')

        # Executar corpo do construtor
        self.generate(ctor.corpo)

        self.emit(TACOp.RETURN, ptr)
        self.emit(TACOp.FUNC_END, desc.ctor_label)

    def _gen_default_constructor(self, desc: 'ClassDescriptor'):
        self.emit(TACOp.FUNC_BEGIN, desc.ctor_label)
        ptr = self.new_temp()
        self.emit(TACOp.ALLOC_OBJ, desc.nome, str(desc.size_bytes), ptr)
        self.emit(TACOp.SET_VTABLE, ptr, desc.vtable_label)
        for f in desc._fields:
            self.emit(TACOp.INIT_FIELD, ptr, str(f.offset),
                      str(desc._type_size(f.tipo_dados)))
        self.emit(TACOp.RETURN, ptr)
        self.emit(TACOp.FUNC_END, desc.ctor_label)

    def _gen_method(self, metodo: MethodDecl, class_name: str):
        label = f'{class_name}_{metodo.nome}'
        self.emit(TACOp.FUNC_BEGIN, label)
        self.emit(TACOp.PARAM, '__this__')   # 'this' é sempre o 1º param
        for param in metodo.parametros:
            self.emit(TACOp.PARAM, param.nome)
        self.generate(metodo.corpo)
        if not self.code or self.code[-1].op != TACOp.RETURN:
            self.emit(TACOp.RETURN, None)
        self.emit(TACOp.FUNC_END, label)

    def gen_NewExpr(self, node: NewExpr) -> str:
        for arg in node.argumentos:
            v = self.generate(arg)
            self.emit(TACOp.PARAM, v)
        result = self.new_temp()
        ctor_label = f'{node.class_name}___ctor'
        self.emit(TACOp.CALL, ctor_label, str(len(node.argumentos)), result)
        self._type_map[result] = node.class_name
        return result

    def gen_MemberAccessExpr(self, node: MemberAccessExpr) -> str:
        ptr = self.generate(node.obj)
        tipo_obj = self._infer_type_name(node.obj)
        desc = self.table.lookup_class(tipo_obj)
        if not desc:
            return ptr
        f = desc.lookup_field(node.membro)
        if not f:
            return ptr
        result = self.new_temp()
        self.emit(TACOp.LOAD_FIELD, ptr, str(f.offset), result)
        return result

    def gen_MethodCallExpr(self, node: MethodCallExpr) -> str:
        ptr = self.generate(node.obj)
        tipo_obj = self._infer_type_name(node.obj)
        desc = self.table.lookup_class(tipo_obj)

        # Emitir 'this' + argumentos
        self.emit(TACOp.PARAM, ptr)
        for arg in node.argumentos:
            v = self.generate(arg)
            self.emit(TACOp.PARAM, v)

        result = self.new_temp()
        total_args = len(node.argumentos) + 1  # +1 para 'this'

        if desc:
            entry = desc.lookup_method(node.metodo)
            if entry and (entry.is_override or desc.superclasse is not None):
                # Dispatch dinâmico via VTable
                vtbl = self.new_temp()
                self.emit(TACOp.LOAD_VTABLE, ptr, None, vtbl)
                idx = str(desc.get_vtable_index(node.metodo))
                self.emit(TACOp.VCALL, vtbl, idx, result)
            elif entry:
                # Dispatch estático
                self.emit(TACOp.STATIC_CALL, entry.label, str(total_args), result)
            else:
                self.emit(TACOp.CALL, f'{tipo_obj}_{node.metodo}', str(total_args), result)
        else:
            self.emit(TACOp.CALL, node.metodo, str(total_args), result)

        return result

    def gen_ThisExpr(self, node: ThisExpr) -> str:
        return '__this__'

    def gen_SuperCallExpr(self, node: SuperCallExpr) -> str:
        # Buscar o tipo de superclasse
        sym = self.table.lookup('super')
        super_type = sym.tipo_dados if sym else 'unknown'
        self.emit(TACOp.PARAM, '__this__')
        for arg in node.argumentos:
            v = self.generate(arg)
            self.emit(TACOp.PARAM, v)
        result = self.new_temp()
        label = f'{super_type}_{node.metodo}'
        self.emit(TACOp.STATIC_CALL, label, str(len(node.argumentos) + 1), result)
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # NOVO v2025.2 — Paralelismo
    # ─────────────────────────────────────────────────────────────────────────

    def gen_ParBlock(self, node: ParBlock):
        """
        Upgrade do par{} existente: cada statement vira uma thread.
        """
        self.emit(TACOp.PAR_BEGIN)
        handles = []

        for stmt in node.statements:
            block_label = self.new_func_label('__par_block')
            # Emitir o corpo como função isolada
            self.emit(TACOp.FUNC_BEGIN, block_label)
            self.generate(stmt)
            self.emit(TACOp.RETURN, None)
            self.emit(TACOp.FUNC_END, block_label)
            # Criar thread
            handle = self.new_temp()
            self.emit(TACOp.SPAWN_THREAD, block_label, '0', handle)
            handles.append(handle)

        # Join de todas as threads
        for h in handles:
            self.emit(TACOp.THREAD_JOIN, h)

        self.emit(TACOp.PAR_END)

    def gen_SpawnStmt(self, node: SpawnStmt):
        if node.call_expr:
            # spawn funcao(args);
            if isinstance(node.call_expr, FunctionCall):
                for arg in node.call_expr.argumentos:
                    v = self.generate(arg)
                    self.emit(TACOp.PARAM_THREAD, v)
                handle = self.new_temp()
                self.emit(TACOp.SPAWN_THREAD, node.call_expr.nome,
                          str(len(node.call_expr.argumentos)), handle)
        else:
            # spawn { bloco anônimo }
            block_label = self.new_func_label('__spawn_block')
            self.emit(TACOp.FUNC_BEGIN, block_label)
            self.generate(node.body)
            self.emit(TACOp.RETURN, None)
            self.emit(TACOp.FUNC_END, block_label)
            handle = self.new_temp()
            self.emit(TACOp.SPAWN_THREAD, block_label, '0', handle)

    def gen_SyncStmt(self, node: SyncStmt):
        self.emit(TACOp.MUTEX_INIT, node.lock_var)
        self.emit(TACOp.MUTEX_LOCK, node.lock_var)
        self.generate(node.corpo)
        self.emit(TACOp.MUTEX_UNLOCK, node.lock_var)

    def gen_AsyncFuncDecl(self, node: AsyncFuncDecl):
        # Async é tratada como função normal no TAC;
        # o ASYNC_BEGIN marca o início para o backend.
        self.emit(TACOp.FUNC_BEGIN, node.nome)
        future = self.new_temp()
        self.emit(TACOp.ASYNC_BEGIN, node.nome, None, future)
        for param in node.parametros:
            self.emit(TACOp.PARAM, param.nome)
        self.generate(node.corpo)
        if not self.code or self.code[-1].op != TACOp.RETURN:
            self.emit(TACOp.RETURN, None)
        self.emit(TACOp.FUNC_END, node.nome)

    def gen_AwaitExpr(self, node: AwaitExpr) -> str:
        future_handle = self.generate(node.future_expr)
        result = self.new_temp()
        self.emit(TACOp.AWAIT_FUTURE, future_handle, None, result)
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # NOVO v2025.2 — Distribuição
    # ─────────────────────────────────────────────────────────────────────────

    def gen_NodeDecl(self, node: NodeDecl):
        conn_var = f'__conn_{node.nome}'
        self.emit(TACOp.CONNECT_NODE, f'"{node.endereco}"', None, conn_var)

    def gen_RemoteFuncDecl(self, node: RemoteFuncDecl):
        func = node.func_decl

        # 1. Implementação real (executada no nó servidor)
        impl_label = f'__impl_{func.nome}'
        self.emit(TACOp.FUNC_BEGIN, impl_label)
        for param in func.parametros:
            self.emit(TACOp.PARAM, param.nome)
        self.generate(func.corpo)
        if not self.code or self.code[-1].op != TACOp.RETURN:
            self.emit(TACOp.RETURN, None)
        self.emit(TACOp.FUNC_END, impl_label)

        # 2. Stub local (mesmo nome — transparente para o chamador)
        self.emit(TACOp.FUNC_BEGIN, func.nome)
        for param in func.parametros:
            self.emit(TACOp.PARAM, param.nome)

        # Serializar argumentos
        for param in func.parametros:
            buf = self.new_temp()
            self.emit(TACOp.SERIALIZE, param.nome, None, buf)
            self.emit(TACOp.PARAM_REMOTE, buf)

        # Chamada RPC (conexão ativa configurada em runtime)
        resp_buf = self.new_temp()
        self.emit(TACOp.RPC_CALL, '__active_conn', f'"{func.nome}"', resp_buf)

        # Deserializar resultado
        result = self.new_temp()
        self.emit(TACOp.DESERIALIZE, resp_buf, f'"{func.tipo_retorno}"', result)
        self.emit(TACOp.RETURN, result)
        self.emit(TACOp.FUNC_END, func.nome)

    def gen_RemoteCallStmt(self, node: RemoteCallStmt):
        # Resolver handle de conexão
        if isinstance(node.alvo, Identifier):
            conn_handle = f'__conn_{node.alvo.nome}'
        else:
            conn_handle = self.new_temp()
            addr = self.generate(node.alvo)
            self.emit(TACOp.CONNECT_NODE, addr, None, conn_handle)

        # Serializar argumentos
        if isinstance(node.function_call, FunctionCall):
            for arg in node.function_call.argumentos:
                val = self.generate(arg)
                buf = self.new_temp()
                self.emit(TACOp.SERIALIZE, val, None, buf)
                self.emit(TACOp.PARAM_REMOTE, buf)

            result = self.new_temp()
            func_name = f'"{node.function_call.nome}"'
            self.emit(TACOp.RPC_CALL, conn_handle, func_name, result)

            if node.result_var:
                self.emit(TACOp.ASSIGN, result, None, node.result_var)
            return result

    def gen_RemoteSpawnStmt(self, node: RemoteSpawnStmt):
        if isinstance(node.alvo, Identifier):
            conn_handle = f'__conn_{node.alvo.nome}'
        else:
            conn_handle = self.new_temp()
            self.emit(TACOp.CONNECT_NODE, self.generate(node.alvo), None, conn_handle)

        block_label = self.new_func_label('__remote_block')
        self.emit(TACOp.FUNC_BEGIN, block_label)
        self.generate(node.corpo)
        self.emit(TACOp.RETURN, None)
        self.emit(TACOp.FUNC_END, block_label)

        future = self.new_temp()
        self.emit(TACOp.REMOTE_SPAWN, conn_handle, block_label, future)

    # ─────────────────────────────────────────────────────────────────────────
    # Utilitários internos
    # ─────────────────────────────────────────────────────────────────────────

    def _infer_type_name(self, node) -> str:
        """
        Tenta inferir o nome do tipo de um nó de expressão.
        Usado para encontrar o ClassDescriptor correto.
        """
        if isinstance(node, Identifier):
            sym = self.table.lookup(node.nome)
            if sym:
                return sym.tipo_dados
        if isinstance(node, NewExpr):
            return node.class_name
        if isinstance(node, ThisExpr):
            sym = self.table.lookup('this')
            return sym.tipo_dados if sym else 'unknown'
        if isinstance(node, MethodCallExpr):
            return 'unknown'
        # Para temporários, consultar _type_map
        return 'unknown'
