"""
arm_codegen.py — MiniPar v2025.2
Backend ARMv7: traduz TAC para Assembly ARMv7 compatível com o CPUlator.

Convenções AAPCS respeitadas:
  r0–r3  → argumentos e valor de retorno
  r4–r11 → callee-saved (preservados entre chamadas)
  r12    → scratch / dispatch de vtable  (caller-saved)
  r13    → sp (stack pointer)
  r14    → lr (link register)
  r15    → pc (program counter)
  fp     → r11 (frame pointer)

POO:      implementado realmente (heap bump, vtable em .rodata, blx r12).
Threads:  execução sequencial simulada no CPUlator (comentários explicativos).
Rede:     stubs que retornam 0 (CPUlator não tem sockets).
"""

from codegen import TAC, TACOp
from symbol_table import SymbolTable, ClassDescriptor
from typing import List, Optional, Set, Dict


class ArmCodeGenerator:
    """
    Traduz lista de instruções TAC para código Assembly ARMv7.
    """

    def __init__(self, symbol_table: SymbolTable):
        self.table = symbol_table

        # Seções de saída
        self._sec_data:   List[str] = []
        self._sec_rodata: List[str] = []
        self._sec_text:   List[str] = []

        # Estado interno
        self._reg_map:        Dict[str, str] = {}   # nome TAC → registrador
        self._next_reg:       int = 4               # r4..r11 callee-saved
        self._string_literals: Dict[str, str] = {}  # valor → label
        self._str_counter:    int = 0
        self._global_vars:    Set[str] = set()
        self._emitted_globals: Set[str] = set()
        self._emitted_vtables: Set[str] = set()
        self._in_function:    bool = False
        self._func_prefix:    str = ''
        self._current_func:   str = ''
        self._pending_params: List[str] = []
        self._pending_tparams: List[str] = []
        self._heap_declared:  bool = False
        self._par_depth:      int = 0

    # ─────────────────────────────────────────────────────────────────────────
    # API pública
    # ─────────────────────────────────────────────────────────────────────────

    def generate(self, tac_code: List[TAC]) -> str:
        """Traduz lista de TAC e retorna o arquivo .s completo."""
        # Pré-passagem: coletar variáveis globais e strings
        self._pre_pass(tac_code)

        # Emitir VTables de classes
        self._emit_all_vtables()

        # Seção .data
        self._build_data_section()

        # Seção .rodata (strings)
        self._build_rodata_section()

        # Seção .text
        self._sec_text.append('    .text')
        self._sec_text.append('    .global main')
        self._sec_text.append('    .global _start')
        self._sec_text.append('    .align 2')
        self._sec_text.append('')
        self._sec_text.append('_start:')
        self._sec_text.append('    bl main')
        self._sec_text.append('    mov r7, #1        @ exit syscall')
        self._sec_text.append('    svc #0            @ make syscall')
        self._sec_text.append('')

        # Traduzir instruções
        for instr in tac_code:
            self._translate(instr)

        # Montar arquivo
        parts = []
        if self._sec_data:
            parts += self._sec_data + ['']
        if self._sec_rodata:
            parts += self._sec_rodata + ['']
        parts += self._sec_text
        parts.append('    .end')
        return '\n'.join(parts)

    # ─────────────────────────────────────────────────────────────────────────
    # Pré-passagem
    # ─────────────────────────────────────────────────────────────────────────

    def _pre_pass(self, tac_code: List[TAC]):
        in_func = False
        for instr in tac_code:
            if instr.op == TACOp.FUNC_BEGIN:
                in_func = True
            elif instr.op == TACOp.FUNC_END:
                in_func = False
            elif not in_func and instr.op == TACOp.ASSIGN:
                # Variável global
                if instr.result and not instr.result.startswith('t'):
                    self._global_vars.add(instr.result)
            # Coletar strings literais
            for val in (instr.arg1, instr.arg2, instr.result):
                if val and isinstance(val, str) and \
                        val.startswith('"') and val.endswith('"'):
                    self._register_string(val[1:-1])

    def _pre_pass_vtable_labels(self, tac_code: List[TAC]):
        for instr in tac_code:
            if instr.op == TACOp.LABEL and instr.arg1 \
                    and instr.arg1.startswith('__vtable_def_'):
                class_name = instr.arg1[len('__vtable_def_'):]
                self._emit_vtable(class_name)

    # ─────────────────────────────────────────────────────────────────────────
    # Seção .data
    # ─────────────────────────────────────────────────────────────────────────

    def _build_data_section(self):
        self._sec_data.append('    .data')
        # Variáveis globais
        for var in sorted(self._global_vars):
            self._sec_data.append(f'{var}:')
            self._sec_data.append('    .word 0')
        # Heap simulado (declarado uma vez)
        if not self._heap_declared:
            self._sec_data.append('    @ Heap simulado 64KB para CPUlator')
            self._sec_data.append('__heap_base:')
            self._sec_data.append('    .space 65536')
            self._sec_data.append('__heap_ptr:')
            self._sec_data.append('    .word __heap_base')
            self._heap_declared = True

    # ─────────────────────────────────────────────────────────────────────────
    # Seção .rodata
    # ─────────────────────────────────────────────────────────────────────────

    def _build_rodata_section(self):
        if not self._string_literals and not self._emitted_vtables:
            return
        self._sec_rodata.append('    .section .rodata')
        self._sec_rodata.append('    .align 2')
        for val, label in self._string_literals.items():
            self._sec_rodata.append(f'{label}:')
            escaped = val.replace('\\', '\\\\').replace('"', '\\"')
            self._sec_rodata.append(f'    .asciz "{escaped}"')
        self._sec_rodata.append('')

    def _emit_all_vtables(self):
        for class_name in self.table.class_registry:
            self._emit_vtable(class_name)

    def _emit_vtable(self, class_name: str):
        if class_name in self._emitted_vtables:
            return
        self._emitted_vtables.add(class_name)
        desc = self.table.lookup_class(class_name)
        if not desc or not desc._vtable:
            return
        self._sec_rodata.append(f'    @ VTable de {class_name}')
        self._sec_rodata.append(f'__vtable_{class_name}:')
        for entry in desc._vtable:
            self._sec_rodata.append(f'    .word {entry.label}')
        self._sec_rodata.append('')

    def _register_string(self, val: str) -> str:
        if val not in self._string_literals:
            label = f'.STR{self._str_counter}'
            self._str_counter += 1
            self._string_literals[val] = label
        return self._string_literals[val]

    # ─────────────────────────────────────────────────────────────────────────
    # Tradução de instruções TAC → ARM
    # ─────────────────────────────────────────────────────────────────────────

    def _translate(self, instr: TAC):
        op = instr.op
        txt = self._sec_text

        # ── Controle de funções ───────────────────────────────────────────────
        if op == TACOp.FUNC_BEGIN:
            self._open_func(instr.arg1)
            return
        if op == TACOp.FUNC_END:
            self._close_func()
            return

        # ── Existentes v2025.1 ────────────────────────────────────────────────
        if op == TACOp.LABEL:
            lbl = instr.arg1
            if not lbl.startswith('__vtable_def_'):
                full = f'{self._func_prefix}{lbl}' if self._in_function else lbl
                txt.append(f'{full}:')
            return

        if op == TACOp.GOTO:
            lbl = f'{self._func_prefix}{instr.result}'
            txt.append(f'    b {lbl}')

        elif op == TACOp.IF_FALSE:
            reg = self._get_reg(instr.arg1)
            lbl = f'{self._func_prefix}{instr.result}'
            txt.append(f'    cmp {reg}, #0')
            txt.append(f'    beq {lbl}')

        elif op == TACOp.ASSIGN:
            r_dest = self._get_reg(instr.result)
            self._load_value(instr.arg1, r_dest)
            # Se variável global, salvar na memória
            if instr.result in self._global_vars:
                txt.append(f'    ldr r12, ={instr.result}')
                txt.append(f'    str {r_dest}, [r12]')

        elif op in (TACOp.ADD, TACOp.SUB, TACOp.MUL,
                    TACOp.DIV, TACOp.MOD):
            r1  = self._get_reg(instr.arg1)
            r2  = self._get_reg(instr.arg2)
            rd  = self._get_reg(instr.result)
            if op == TACOp.ADD:
                txt.append(f'    add {rd}, {r1}, {r2}')
            elif op == TACOp.SUB:
                txt.append(f'    sub {rd}, {r1}, {r2}')
            elif op == TACOp.MUL:
                txt.append(f'    mul {rd}, {r1}, {r2}')
            elif op == TACOp.DIV:
                # ARMv7: usar __aeabi_idiv (divisão inteira via ABI)
                txt.append(f'    mov r0, {r1}')
                txt.append(f'    mov r1, {r2}')
                txt.append(f'    bl __aeabi_idiv')
                txt.append(f'    mov {rd}, r0')
            elif op == TACOp.MOD:
                txt.append(f'    mov r0, {r1}')
                txt.append(f'    mov r1, {r2}')
                txt.append(f'    bl __aeabi_idivmod')
                txt.append(f'    mov {rd}, r1')   # resto em r1

        elif op in (TACOp.EQ, TACOp.NEQ, TACOp.LT,
                    TACOp.GT, TACOp.LTE, TACOp.GTE):
            r1 = self._get_reg(instr.arg1)
            r2 = self._get_reg(instr.arg2)
            rd = self._get_reg(instr.result)
            cond = {
                TACOp.EQ: 'eq', TACOp.NEQ: 'ne',
                TACOp.LT: 'lt', TACOp.GT:  'gt',
                TACOp.LTE:'le', TACOp.GTE: 'ge',
            }[op]
            txt.append(f'    cmp {r1}, {r2}')
            txt.append(f'    mov{cond} {rd}, #1')
            txt.append(f'    mov{self._invert_cond(cond)} {rd}, #0')

        elif op in (TACOp.AND, TACOp.OR):
            r1 = self._get_reg(instr.arg1)
            r2 = self._get_reg(instr.arg2)
            rd = self._get_reg(instr.result)
            arm_op = 'and' if op == TACOp.AND else 'orr'
            txt.append(f'    {arm_op} {rd}, {r1}, {r2}')

        elif op == TACOp.NOT:
            r1 = self._get_reg(instr.arg1)
            rd = self._get_reg(instr.result)
            txt.append(f'    cmp {r1}, #0')
            txt.append(f'    moveq {rd}, #1')
            txt.append(f'    movne {rd}, #0')

        elif op == TACOp.NEG:
            r1 = self._get_reg(instr.arg1)
            rd = self._get_reg(instr.result)
            txt.append(f'    rsb {rd}, {r1}, #0')

        elif op == TACOp.PARAM:
            self._pending_params.append(instr.arg1)

        elif op == TACOp.CALL:
            # Colocar argumentos em r0..r3
            for i, arg in enumerate(self._pending_params[:4]):
                ri = f'r{i}'
                reg = self._get_reg(arg)
                if reg != ri:
                    txt.append(f'    mov {ri}, {reg}')
            if len(self._pending_params) > 4:
                # Argumentos extras na stack (simplificado)
                for arg in reversed(self._pending_params[4:]):
                    reg = self._get_reg(arg)
                    txt.append(f'    push {{{reg}}}')
            self._pending_params = []
            txt.append(f'    bl {instr.arg1}')
            if instr.result:
                rd = self._get_reg(instr.result)
                if rd != 'r0':
                    txt.append(f'    mov {rd}, r0')

        elif op == TACOp.RETURN:
            if instr.arg1:
                rv = self._get_reg(instr.arg1)
                if rv != 'r0':
                    txt.append(f'    mov r0, {rv}')
            txt.append(f'    sub sp, fp, #4')
            txt.append(f'    pop {{fp, pc}}')

        # ── POO ───────────────────────────────────────────────────────────────

        elif op == TACOp.ALLOC_OBJ:
            size   = int(instr.arg2)
            result = self._get_reg(instr.result)
            txt.append(f'    @ ALLOC_OBJ {instr.arg1} ({size} bytes) — heap bump')
            txt.append(f'    ldr r0, =__heap_ptr')
            txt.append(f'    ldr r1, [r0]')
            txt.append(f'    mov {result}, r1')
            txt.append(f'    add r1, r1, #{size}')
            txt.append(f'    str r1, [r0]')

        elif op == TACOp.SET_VTABLE:
            ptr_reg  = self._get_reg(instr.arg1)
            vtl_lbl  = instr.arg2
            txt.append(f'    @ SET_VTABLE {vtl_lbl}')
            txt.append(f'    ldr r1, ={vtl_lbl}')
            txt.append(f'    str r1, [{ptr_reg}, #0]')

        elif op == TACOp.INIT_FIELD:
            ptr    = self._get_reg(instr.arg1)
            offset = int(instr.arg2)
            txt.append(f'    @ INIT_FIELD offset={offset}')
            txt.append(f'    mov r1, #0')
            txt.append(f'    str r1, [{ptr}, #{offset}]')

        elif op == TACOp.STORE_FIELD:
            ptr    = self._get_reg(instr.arg1)
            val    = self._get_reg(instr.arg2)
            offset = int(instr.result)
            txt.append(f'    @ STORE_FIELD offset={offset}')
            txt.append(f'    str {val}, [{ptr}, #{offset}]')

        elif op == TACOp.LOAD_FIELD:
            ptr    = self._get_reg(instr.arg1)
            offset = int(instr.arg2)
            result = self._get_reg(instr.result)
            txt.append(f'    @ LOAD_FIELD offset={offset}')
            txt.append(f'    ldr {result}, [{ptr}, #{offset}]')

        elif op == TACOp.LOAD_VTABLE:
            ptr    = self._get_reg(instr.arg1)
            result = self._get_reg(instr.result)
            txt.append(f'    @ LOAD_VTABLE (offset 0 = vtable ptr)')
            txt.append(f'    ldr {result}, [{ptr}, #0]')

        elif op == TACOp.VCALL:
            vtbl_reg = self._get_reg(instr.arg1)
            idx      = int(instr.arg2)
            result   = self._get_reg(instr.result)
            txt.append(f'    @ VCALL vtable[{idx}]')
            # Colocar argumentos (this + args) em r0..r3
            for i, arg in enumerate(self._pending_params[:4]):
                ri = f'r{i}'
                reg = self._get_reg(arg)
                if reg != ri:
                    txt.append(f'    mov {ri}, {reg}')
            self._pending_params = []
            txt.append(f'    ldr r12, [{vtbl_reg}, #{idx * 4}]')
            txt.append(f'    blx r12')
            if result != 'r0':
                txt.append(f'    mov {result}, r0')

        elif op == TACOp.STATIC_CALL:
            label  = instr.arg1
            result = self._get_reg(instr.result)
            # Colocar argumentos em r0..r3
            for i, arg in enumerate(self._pending_params[:4]):
                ri = f'r{i}'
                reg = self._get_reg(arg)
                if reg != ri:
                    txt.append(f'    mov {ri}, {reg}')
            self._pending_params = []
            txt.append(f'    bl {label}')
            if result != 'r0':
                txt.append(f'    mov {result}, r0')

        # ── Paralelismo (sequencial simulado no CPUlator) ─────────────────────

        elif op == TACOp.PAR_BEGIN:
            self._par_depth += 1
            txt.append(f'    @ PAR_BEGIN — CPUlator: execução sequencial simulada')
            txt.append(f'    @ (Para paralelismo real use o backend C+pthreads)')

        elif op == TACOp.PAR_END:
            self._par_depth = max(0, self._par_depth - 1)
            txt.append(f'    @ PAR_END')

        elif op == TACOp.PARAM_THREAD:
            self._pending_tparams.append(instr.arg1)

        elif op == TACOp.SPAWN_THREAD:
            func_label = instr.arg1
            result     = instr.result
            txt.append(f'    @ SPAWN_THREAD {func_label} — chamada direta no CPUlator')
            for i, arg in enumerate(self._pending_tparams[:4]):
                ri  = f'r{i}'
                reg = self._get_reg(arg)
                if reg != ri:
                    txt.append(f'    mov {ri}, {reg}')
            self._pending_tparams = []
            txt.append(f'    bl {func_label}')
            if result:
                rd = self._get_reg(result)
                txt.append(f'    mov {rd}, #0  @ handle simulado')

        elif op == TACOp.THREAD_JOIN:
            txt.append(f'    @ THREAD_JOIN {instr.arg1} — no-op (sequencial)')

        elif op == TACOp.MUTEX_INIT:
            txt.append(f'    @ MUTEX_INIT {instr.arg1} — no-op no CPUlator')
            txt.append(f'    @ (Em hardware: LDREX/STREX para mutex ARMv7)')

        elif op == TACOp.MUTEX_LOCK:
            txt.append(f'    @ MUTEX_LOCK {instr.arg1} — no-op (single-thread)')

        elif op == TACOp.MUTEX_UNLOCK:
            txt.append(f'    @ MUTEX_UNLOCK {instr.arg1} — no-op')

        elif op == TACOp.ASYNC_BEGIN:
            txt.append(f'    @ ASYNC_BEGIN {instr.arg1} — execução síncrona no CPUlator')

        elif op == TACOp.AWAIT_FUTURE:
            txt.append(f'    @ AWAIT_FUTURE {instr.arg1} — no-op (já executou)')
            if instr.result:
                rd = self._get_reg(instr.result)
                txt.append(f'    mov {rd}, r0  @ resultado disponível em r0')

        # ── Distribuição (stubs simulados no CPUlator) ────────────────────────

        elif op == TACOp.CONNECT_NODE:
            result = self._get_reg(instr.result)
            txt.append(f'    @ CONNECT_NODE {instr.arg1}')
            txt.append(f'    @ CPUlator sem rede — stub retorna handle=1')
            txt.append(f'    mov {result}, #1')

        elif op == TACOp.SERIALIZE:
            val    = self._get_reg(instr.arg1)
            result = self._get_reg(instr.result)
            txt.append(f'    @ SERIALIZE — pass-through no CPUlator')
            if val != result:
                txt.append(f'    mov {result}, {val}')

        elif op == TACOp.PARAM_REMOTE:
            self._pending_params.append(instr.arg1)

        elif op == TACOp.RPC_CALL:
            result = self._get_reg(instr.result) if instr.result else 'r0'
            txt.append(f'    @ RPC_CALL {instr.arg2} em {instr.arg1}')
            txt.append(f'    @ CPUlator sem rede — stub retorna 0')
            txt.append(f'    mov {result}, #0')
            self._pending_params = []

        elif op == TACOp.DESERIALIZE:
            val    = self._get_reg(instr.arg1)
            result = self._get_reg(instr.result)
            txt.append(f'    @ DESERIALIZE {instr.arg2} — pass-through')
            if val != result:
                txt.append(f'    mov {result}, {val}')

        elif op == TACOp.DISCONNECT_NODE:
            txt.append(f'    @ DISCONNECT_NODE {instr.arg1} — no-op no CPUlator')

        elif op == TACOp.REMOTE_SPAWN:
            result = self._get_reg(instr.result) if instr.result else 'r0'
            txt.append(f'    @ REMOTE_SPAWN {instr.arg2} em {instr.arg1}')
            txt.append(f'    @ CPUlator sem rede — stub')
            txt.append(f'    mov {result}, #0')

    # ─────────────────────────────────────────────────────────────────────────
    # Gerenciamento de funções
    # ─────────────────────────────────────────────────────────────────────────

    def _open_func(self, name: str):
        self._in_function = True
        self._current_func = name
        self._func_prefix = f'{name}_'
        self._reg_map = {}
        self._next_reg = 4
        self._pending_params = []

        self._sec_text.append('')
        self._sec_text.append(f'{name}:')
        self._sec_text.append(f'    push {{r4, r5, r6, r7, fp, lr}}')
        self._sec_text.append(f'    add fp, sp, #20')

    def _close_func(self):
        # RETURN já foi emitido pela instrução RETURN; garantir epilogo padrão
        self._sec_text.append(f'    @ func end: {self._current_func}')
        self._in_function = False
        self._current_func = ''
        self._func_prefix = ''

    # ─────────────────────────────────────────────────────────────────────────
    # Alocação de registradores
    # ─────────────────────────────────────────────────────────────────────────

    def _get_reg(self, var: Optional[str]) -> str:
        """
        Retorna o registrador associado a uma variável TAC.
        Aloca r4..r11 em ordem crescente; usa r4 como fallback se esgotado.
        """
        if var is None:
            return 'r0'

        # Literais numéricos inline (não precisam de registrador fixo)
        try:
            int(var)
            return var   # retorna o literal; _load_value gerará movw
        except ValueError:
            pass
        try:
            float(var)
            return var
        except ValueError:
            pass

        if var in self._reg_map:
            return self._reg_map[var]

        # Alocar próximo registrador callee-saved
        if self._next_reg <= 11:
            reg = f'r{self._next_reg}'
            self._next_reg += 1
        else:
            # Esgotado: reusar r4 (simplificação — projetos maiores usariam spilling)
            reg = 'r4'

        self._reg_map[var] = reg
        return reg

    def _load_value(self, val: Optional[str], dest_reg: str):
        """Emite instruções para carregar um valor em dest_reg."""
        txt = self._sec_text
        if val is None:
            txt.append(f'    mov {dest_reg}, #0')
            return

        # String literal
        if isinstance(val, str) and val.startswith('"') and val.endswith('"'):
            inner = val[1:-1]
            label = self._register_string(inner)
            txt.append(f'    ldr {dest_reg}, ={label}')
            return

        # Inteiro/booleano pequeno (≤ 255 → MOV; maior → MOVW)
        try:
            n = int(float(val))
            if 0 <= n <= 255:
                txt.append(f'    mov {dest_reg}, #{n}')
            elif -255 <= n < 0:
                txt.append(f'    mvn {dest_reg}, #{-n - 1}')
            else:
                # MOVW/MOVT para 32-bit
                low  = n & 0xFFFF
                high = (n >> 16) & 0xFFFF
                txt.append(f'    movw {dest_reg}, #{low}')
                if high:
                    txt.append(f'    movt {dest_reg}, #{high}')
            return
        except (ValueError, TypeError):
            pass

        # Variável: usar registrador já alocado ou carregar da memória global
        if val in self._global_vars:
            txt.append(f'    ldr r12, ={val}')
            txt.append(f'    ldr {dest_reg}, [r12]')
            return

        src_reg = self._reg_map.get(val)
        if src_reg and src_reg != dest_reg:
            txt.append(f'    mov {dest_reg}, {src_reg}')
        elif not src_reg:
            # Variável ainda não vista — tratar como 0
            txt.append(f'    mov {dest_reg}, #0  @ variável "{val}" não inicializada')

    # ─────────────────────────────────────────────────────────────────────────
    # Utilitários
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _invert_cond(cond: str) -> str:
        inv = {'eq':'ne','ne':'eq','lt':'ge','ge':'lt','gt':'le','le':'gt'}
        return inv.get(cond, 'al')
