"""
arm_codegen.py — MiniPar v2026.1
Backend ARMv7 (Cortex-A9 / DE1-SoC) para o CPUlator.

`number` é DOUBLE (IEEE-754 64 bits) via VFPv3 — para corresponder ao
backend C. O CPUlator suporta VFPv3 (sem Thumb/Neon), então usamos:
    vadd.f64 vsub.f64 vmul.f64 vdiv.f64 vneg.f64 vcmp.f64 vcvt vldr vstr vmov

Modelo de execução
──────────────────
  • Quadro de pilha: cada nome do TAC tem um slot de 8 bytes em [fp,#-k].
  • Números vivem em registradores VFP (d0..d2 scratch); booleanos e
    ponteiros de string em GPRs (r0..r2).
  • Passagem de argumentos 100% pela PILHA (8 bytes por argumento),
    uniforme para qualquer tipo/quantidade. Retorno: número em d0,
    booleano/string em r0.
  • Saída de texto via JTAG UART MMIO (0xFF201000 / 0xFF201004).

POO / paralelismo / distribuição entram nos próximos passos.
"""

from codegen import TAC, TACOp
from symbol_table import SymbolTable, SymbolType
from typing import List, Optional, Set, Dict, Tuple

UART_LO, UART_HI = 0x1000, 0xFF20    # 0xFF201000


class ArmCodeGenerator:

    def __init__(self, symbol_table: SymbolTable):
        self.table = symbol_table
        self._data:   List[str] = []
        self._rodata: List[str] = []
        self._text:   List[str] = []

        self._strings: Dict[str, str] = {}
        self._str_count = 0
        self._floats: Dict[str, str] = {}     # repr do double -> label
        self._flt_count = 0
        self._global_vars: Set[str] = set()
        self._kind: Dict[str, str] = {}       # nome -> 'num'|'bool'|'str'
        self._sig: Dict[str, List[str]] = {}
        self._cur_func = ''
        self._slots: Dict[str, int] = {}
        self._pending: List[str] = []

        self.uses_parallel = False
        self.uses_network = False
        self.uses_oop = False
        self._uses_len = False
        self._uses_str = False
        self._uses_num = False

        # POO
        self._obj_class: Dict[str, str] = {}     # nome -> classe
        self._arm_field: Dict[str, Dict[int, tuple]] = {}  # classe -> {descoffset:(armoff,kind)}
        self._arm_size: Dict[str, int] = {}      # classe -> tamanho em bytes (ARM)

    # ─────────────────────────────────────────────────────────────────────────
    # API pública
    # ─────────────────────────────────────────────────────────────────────────

    def generate(self, tac_code: List[TAC]) -> str:
        self._build_layouts()
        self._pre_pass(tac_code)
        funcs, main_instrs = self._split(tac_code)

        self._emit_prelude()
        self._emit_function('main', [], main_instrs)
        for name, formals, instrs in funcs:
            self._emit_function(name, formals, instrs)
        self._emit_runtime()

        out: List[str] = ['    .data', '    .align 3',
                          '__mp_stack: .space 8192', '__mp_stack_top:']
        out.append('    .align 2')
        out.append('__mp_outbuf: .word 0')   # 0=UART; !=0 => buffer (builtins)
        if self.uses_oop or self._uses_str:
            out.append('    .align 3')
            out.append('__mp_heap: .space 65536')
            out.append('__mp_heap_ptr: .word __mp_heap')
        for g in sorted(self._global_vars):
            out.append('    .align 3')
            out.append(f'{g}: .space 8')
        out.append('')
        if self._strings or self._floats or self._arm_field:
            out.append('    .section .rodata')
            for val, lbl in self._strings.items():
                esc = val.replace('\\', '\\\\').replace('"', '\\"')
                out.append('    .align 2')
                out.append(f'{lbl}: .asciz "{esc}"')
            for rep, lbl in self._floats.items():
                out.append('    .align 3')
                out.append(f'{lbl}: .double {rep}')
            # vtables
            for cls in self._arm_field:
                desc = self.table.lookup_class(cls)
                if desc and desc._vtable:
                    out.append('    .align 2')
                    out.append(f'__vtable_{cls}:')
                    for e in desc._vtable:
                        out.append(f'    .word {e.label}')
            out.append('')
        out.extend(self._text)
        out.append('    .end')
        return '\n'.join(out)

    # ── Layout de objetos (bytes ARM: number=8, ponteiro/bool=4) ───────────────

    def _build_layouts(self):
        for cls in self.table.class_registry:
            desc = self.table.lookup_class(cls)
            chain = []
            d = desc
            while d:
                chain.append(d); d = d.superclasse
            fields: Dict[int, tuple] = {}
            off = 4   # offset 0 = ponteiro de vtable
            for anc in reversed(chain):
                for f in anc._fields:
                    k = self._kind_of_type(f.tipo_dados)
                    fields[f.offset] = (off, k)   # descoffset -> (armoff, kind)
                    off += 8 if k == 'num' else 4
            self._arm_field[cls] = fields
            self._arm_size[cls] = off

    def _field_info(self, cls: Optional[str], descoff):
        """(arm_offset, kind) de um campo a partir do offset do descritor."""
        if not cls or cls not in self._arm_field:
            return None
        try:
            o = int(descoff)
        except (ValueError, TypeError):
            return None
        return self._arm_field[cls].get(o)

    # ─────────────────────────────────────────────────────────────────────────
    # Pré-passagens
    # ─────────────────────────────────────────────────────────────────────────

    def _pre_pass(self, tac_code: List[TAC]):
        depth = 0
        for instr in tac_code:
            if instr.op == TACOp.FUNC_BEGIN:
                depth += 1
            elif instr.op == TACOp.FUNC_END:
                depth -= 1
            elif depth == 0 and instr.op == TACOp.ASSIGN and instr.result:
                r = instr.result
                if not self._is_temp(r) and '[' not in r:
                    self._global_vars.add(r)
            for v in (instr.arg1, instr.arg2, instr.result):
                if v and isinstance(v, str) and self._is_str_lit(v):
                    self._register_string(v[1:-1])
        # assinaturas (formais)
        n = len(tac_code)
        for i, instr in enumerate(tac_code):
            if instr.op == TACOp.FUNC_BEGIN:
                run = []
                j = i + 1
                while j < n and tac_code[j].op == TACOp.PARAM:
                    run.append(tac_code[j].arg1); j += 1
                self._sig[instr.arg1] = run[:self._arity(instr.arg1, len(run))]
        # inferência de "kind" (num/bool/str/obj) — passagem linear
        for instr in tac_code:
            op, r = instr.op, instr.result
            if op == TACOp.FUNC_BEGIN:
                self._set_formal_kinds(instr.arg1)
                continue
            if op == TACOp.FUNC_END:
                continue
            if op == TACOp.ASSIGN:
                self._kind[r] = self._kind_of_val(instr.arg1)
                if instr.arg1 in self._obj_class:
                    self._obj_class[r] = self._obj_class[instr.arg1]
            elif op in (TACOp.ADD, TACOp.SUB, TACOp.MUL, TACOp.DIV,
                        TACOp.MOD, TACOp.NEG):
                self._kind[r] = 'num'
            elif op in (TACOp.EQ, TACOp.NEQ, TACOp.LT, TACOp.GT, TACOp.LTE,
                        TACOp.GTE, TACOp.AND, TACOp.OR, TACOp.NOT):
                self._kind[r] = 'bool'
            elif op == TACOp.CALL and r is not None:
                if instr.arg1 == 'len':
                    self._uses_len = True
                elif instr.arg1 == 'str':
                    self._uses_str = True
                elif instr.arg1 == 'num':
                    self._uses_num = True
                self._kind[r] = self._call_kind(instr.arg1)
                cls = self._ctor_class(instr.arg1)
                if cls:
                    self.uses_oop = True
                    self._obj_class[r] = cls
            elif op == TACOp.ALLOC_OBJ:
                self.uses_oop = True
                self._kind[r] = 'obj'
                self._obj_class[r] = instr.arg1
            elif op in (TACOp.SET_VTABLE, TACOp.STORE_FIELD, TACOp.INIT_FIELD):
                self.uses_oop = True
            elif op == TACOp.LOAD_VTABLE:
                self.uses_oop = True
                self._kind[r] = 'obj'
            elif op == TACOp.LOAD_FIELD:
                self.uses_oop = True
                fi = self._field_info(self._obj_class.get(instr.arg1), instr.arg2)
                fld = self._field_decl(self._obj_class.get(instr.arg1), instr.arg2)
                self._kind[r] = fi[1] if fi else 'num'
                if fld and self.table.lookup_class(fld.tipo_dados):
                    self._obj_class[r] = fld.tipo_dados
            elif op == TACOp.VCALL:
                self.uses_oop = True
                ret = self._vcall_ret(instr.arg2)
                self._kind[r] = self._kind_of_type(ret)
                if self.table.lookup_class(ret):
                    self._obj_class[r] = ret
            elif op == TACOp.STATIC_CALL:
                me = self._method_entry(instr.arg1)
                ret = me[1].return_type if me else 'number'
                self._kind[r] = self._kind_of_type(ret)
                if self.table.lookup_class(ret):
                    self._obj_class[r] = ret

    def _set_formal_kinds(self, name):
        formals = self._sig.get(name, [])
        cls = self._ctor_class(name)
        if cls:
            d = self.table.lookup_class(cls)
            types = d.ctor_params if d else []
            for p, t in zip(formals, types):
                self._kind[p] = self._kind_of_type(t)
                if self.table.lookup_class(t):
                    self._obj_class[p] = t
            return
        me = self._method_entry(name)
        if me:
            mcls, e = me
            if formals:
                self._kind[formals[0]] = 'obj'      # __this__
                self._obj_class[formals[0]] = mcls
            for p, t in zip(formals[1:], e.param_types):
                self._kind[p] = self._kind_of_type(t)
                if self.table.lookup_class(t):
                    self._obj_class[p] = t
            return
        for p, t in zip(formals, self._ptypes(name)):
            self._kind[p] = self._kind_of_type(t)
            if self.table.lookup_class(t):
                self._obj_class[p] = t

    def _ctor_class(self, name):
        return name[:-len('___ctor')] if name.endswith('___ctor') else None

    def _method_entry(self, label):
        for cls, desc in self.table.class_registry.items():
            for e in desc._vtable:
                if e.label == label:
                    return (cls, e)
        return None

    def _field_decl(self, cls, descoff):
        if not cls:
            return None
        try:
            o = int(descoff)
        except (ValueError, TypeError):
            return None
        d = self.table.lookup_class(cls)
        while d:
            for f in d._fields:
                if f.offset == o:
                    return f
            d = d.superclasse
        return None

    def _vcall_ret(self, arg2):
        if not arg2 or '@' not in arg2:
            return 'number'
        idx_s, cls = arg2.split('@', 1)
        desc = self.table.lookup_class(cls)
        if desc and idx_s.isdigit() and int(idx_s) < len(desc._vtable):
            return desc._vtable[int(idx_s)].return_type
        return 'number'

    def _ptypes(self, name):
        sym = self.table.lookup(name)
        return sym.tipos_parametros if sym else []

    def _kind_of_type(self, t):
        if t in ('string',):
            return 'str'
        if t == 'bool':
            return 'bool'
        if self.table.lookup_class(t):
            return 'obj'
        return 'num'

    def _call_kind(self, name):
        if name in ('str', 'input'):
            return 'str'
        if name in ('len', 'num'):
            return 'num'
        if name.endswith('___ctor'):
            return 'obj'
        sym = self.table.lookup(name)
        return self._kind_of_type(sym.tipo_retorno) if sym else 'num'

    def _kind_of_val(self, v):
        if v is None:
            return 'num'
        if self._is_str_lit(v):
            return 'str'
        if self._is_number(v):
            return 'num'
        if v == 'null':
            return 'str'
        return self._kind.get(v, 'num')

    def _split(self, tac_code):
        funcs, main_instrs = [], []
        n = len(tac_code); cur = None; i = 0
        while i < n:
            instr = tac_code[i]
            if instr.op == TACOp.FUNC_BEGIN:
                name = instr.arg1
                formals = self._sig.get(name, [])
                cur = (name, formals, []); i += 1
                skipped = 0
                while i < n and skipped < len(formals) and tac_code[i].op == TACOp.PARAM:
                    i += 1; skipped += 1
                continue
            if instr.op == TACOp.FUNC_END:
                funcs.append(cur); cur = None; i += 1; continue
            (cur[2] if cur else main_instrs).append(instr); i += 1
        return funcs, main_instrs

    def _arity(self, name, leading):
        if name == 'main':
            return 0
        # blocos-closure de thread (par/spawn) e impl. remota não têm formais
        # declarados via PARAM imediato (os PARAM seguintes são de chamadas).
        if name.startswith(('__par_block', '__spawn_block', '__remote_block')):
            return 0
        sym = self.table.lookup(name)
        if sym and sym.tipo_simbolo == SymbolType.FUNCTION:
            return len(sym.tipos_parametros)
        return leading

    # ─────────────────────────────────────────────────────────────────────────
    # Emissão de função
    # ─────────────────────────────────────────────────────────────────────────

    def _emit_function(self, name, formals, instrs):
        self._cur_func = name
        self._set_formal_kinds(name)   # kinds/obj_class corretos desta função
        self._slots = self._collect_slots(formals, instrs)
        frame = ((len(self._slots) * 8) + 7) & ~7
        t = self._text
        t.append('')
        t.append(f'{name}:')
        t.append('    push {fp, lr}')
        t.append('    mov fp, sp')
        if frame:
            t.append(f'    sub sp, sp, #{frame}')
        # parâmetros chegam na pilha do chamador: [fp,#8], [fp,#16], ...
        for idx, p in enumerate(formals):
            src = 8 + 8 * idx
            if self._kind.get(p) == 'num':
                t.append(f'    vldr d0, [fp, #{src}]')
                t.append(f'    vstr d0, [fp, #{self._slots[p]}]')
            else:
                t.append(f'    ldr r0, [fp, #{src}]')
                t.append(f'    str r0, [fp, #{self._slots[p]}]')
        self._pending = []
        for instr in instrs:
            self._emit_instr(instr)
        if not t[-1].strip().startswith('pop {fp, pc}'):
            t.append('    mov sp, fp')
            t.append('    pop {fp, pc}')

    def _collect_slots(self, formals, instrs):
        slots: Dict[str, int] = {}

        def add(nm):
            if nm is None or self._is_literal(nm) or nm in self._global_vars:
                return
            if nm not in slots:
                slots[nm] = -8 * (len(slots) + 1)

        def add_formal(nm):
            # Parâmetros sempre recebem slot local, mesmo que haja um global
            # de mesmo nome (o parâmetro faz shadowing do global na função).
            if nm is None or self._is_literal(nm):
                return
            if nm not in slots:
                slots[nm] = -8 * (len(slots) + 1)

        for p in formals:
            add_formal(p)
        for ins in instrs:
            op = ins.op
            if op == TACOp.ASSIGN:
                add(ins.arg1); add(ins.result)
            elif op in (TACOp.ADD, TACOp.SUB, TACOp.MUL, TACOp.DIV, TACOp.MOD,
                        TACOp.EQ, TACOp.NEQ, TACOp.LT, TACOp.GT, TACOp.LTE,
                        TACOp.GTE, TACOp.AND, TACOp.OR):
                add(ins.arg1); add(ins.arg2); add(ins.result)
            elif op in (TACOp.NOT, TACOp.NEG):
                add(ins.arg1); add(ins.result)
            elif op == TACOp.IF_FALSE:
                add(ins.arg1)
            elif op == TACOp.PARAM:
                add(ins.arg1)
            elif op == TACOp.CALL:
                add(ins.result)
            elif op == TACOp.RETURN:
                add(ins.arg1)
            elif op == TACOp.ALLOC_OBJ:
                add(ins.result)
            elif op == TACOp.SET_VTABLE:
                add(ins.arg1)
            elif op == TACOp.LOAD_VTABLE:
                add(ins.arg1); add(ins.result)
            elif op == TACOp.LOAD_FIELD:
                add(ins.arg1); add(ins.result)
            elif op == TACOp.STORE_FIELD:
                add(ins.arg1); add(ins.arg2)
            elif op in (TACOp.VCALL, TACOp.STATIC_CALL):
                add(ins.arg1); add(ins.result)
            # ── Passo 3: paralelismo / distribuição ──
            elif op == TACOp.PARAM_THREAD:
                add(ins.arg1)
            elif op == TACOp.SPAWN_THREAD:
                add(ins.result)
            elif op in (TACOp.CONNECT_NODE, TACOp.SERIALIZE, TACOp.RPC_CALL,
                        TACOp.DESERIALIZE, TACOp.REMOTE_SPAWN,
                        TACOp.ASYNC_BEGIN, TACOp.AWAIT_FUTURE):
                add(ins.result)
        return slots

    # ─────────────────────────────────────────────────────────────────────────
    # Tradução
    # ─────────────────────────────────────────────────────────────────────────

    def _emit_instr(self, instr):
        op = instr.op
        t = self._text

        if op == TACOp.LABEL and instr.arg1 and instr.arg1.startswith('__vtable_def_'):
            return
        if op == TACOp.ASSIGN and instr.result and '[' in instr.result:
            return

        if op == TACOp.ASSIGN:
            if instr.arg1 in self._obj_class:
                self._obj_class[instr.result] = self._obj_class[instr.arg1]
            if self._kind.get(instr.result) == 'num':
                self._load_num(instr.arg1, 'd0'); self._store_num(instr.result, 'd0')
            else:
                self._load_int(instr.arg1, 'r0'); self._store_int(instr.result, 'r0')

        elif op in (TACOp.ADD, TACOp.SUB, TACOp.MUL, TACOp.DIV):
            self._load_num(instr.arg1, 'd0'); self._load_num(instr.arg2, 'd1')
            asm = {TACOp.ADD: 'vadd.f64', TACOp.SUB: 'vsub.f64',
                   TACOp.MUL: 'vmul.f64', TACOp.DIV: 'vdiv.f64'}[op]
            t.append(f'    {asm} d2, d0, d1')
            self._store_num(instr.result, 'd2')

        elif op == TACOp.MOD:
            # fmod(a,b) = a - trunc(a/b)*b   (semântica do C)
            self._load_num(instr.arg1, 'd0'); self._load_num(instr.arg2, 'd1')
            t += ['    vdiv.f64 d2, d0, d1',
                  '    vcvt.s32.f64 s8, d2',     # trunc p/ inteiro
                  '    vcvt.f64.s32 d4, s8',
                  '    vmul.f64 d4, d4, d1',
                  '    vsub.f64 d2, d0, d4']
            self._store_num(instr.result, 'd2')

        elif op == TACOp.NEG:
            self._load_num(instr.arg1, 'd0')
            t.append('    vneg.f64 d2, d0')
            self._store_num(instr.result, 'd2')

        elif op in (TACOp.EQ, TACOp.NEQ, TACOp.LT, TACOp.GT, TACOp.LTE, TACOp.GTE):
            self._load_num(instr.arg1, 'd0'); self._load_num(instr.arg2, 'd1')
            cond = {TACOp.EQ: 'eq', TACOp.NEQ: 'ne', TACOp.LT: 'lt',
                    TACOp.GT: 'gt', TACOp.LTE: 'le', TACOp.GTE: 'ge'}[op]
            t += ['    vcmp.f64 d0, d1',
                  '    vmrs APSR_nzcv, fpscr',
                  '    mov r2, #0',
                  f'    mov{cond} r2, #1']
            self._store_int(instr.result, 'r2')

        elif op in (TACOp.AND, TACOp.OR):
            self._load_int(instr.arg1, 'r0'); self._load_int(instr.arg2, 'r1')
            t += ['    cmp r0, #0', '    movne r0, #1',
                  '    cmp r1, #0', '    movne r1, #1',
                  f'    {"and" if op == TACOp.AND else "orr"} r2, r0, r1']
            self._store_int(instr.result, 'r2')

        elif op == TACOp.NOT:
            self._load_int(instr.arg1, 'r0')
            t += ['    cmp r0, #0', '    mov r2, #0', '    moveq r2, #1']
            self._store_int(instr.result, 'r2')

        elif op == TACOp.LABEL:
            t.append(f'{self._lbl(instr.arg1)}:')

        elif op == TACOp.GOTO:
            t.append(f'    b {self._lbl(instr.result)}')

        elif op == TACOp.IF_FALSE:
            self._load_int(instr.arg1, 'r0')
            t.append('    cmp r0, #0')
            t.append(f'    beq {self._lbl(instr.result)}')

        elif op == TACOp.PARAM:
            self._pending.append(instr.arg1)

        elif op == TACOp.CALL:
            self._emit_call(instr)

        elif op == TACOp.RETURN:
            if instr.arg1 is not None:
                if self._kind_of_val(instr.arg1) == 'num':
                    self._load_num(instr.arg1, 'd0')
                else:
                    self._load_int(instr.arg1, 'r0')
            t.append('    mov sp, fp')
            t.append('    pop {fp, pc}')

        # ── POO ────────────────────────────────────────────────────────────────

        elif op == TACOp.ALLOC_OBJ:
            cls = instr.arg1
            size = self._arm_size.get(cls, 8)
            self._obj_class[instr.result] = cls
            # heap bump (memória já zerada: .space em .data)
            t += ['    ldr r1, =__mp_heap_ptr',
                  '    ldr r0, [r1]',
                  f'    add r2, r0, #{size}',
                  '    str r2, [r1]']        # r0 = ponteiro do objeto
            self._store_int(instr.result, 'r0')

        elif op == TACOp.SET_VTABLE:
            self._load_int(instr.arg1, 'r0')
            t.append(f'    ldr r1, ={instr.arg2}')
            t.append('    str r1, [r0, #0]')

        elif op == TACOp.INIT_FIELD:
            pass   # heap já vem zerado

        elif op == TACOp.STORE_FIELD:
            ptr, val, off = instr.arg1, instr.arg2, instr.result
            fi = self._field_info(self._obj_class.get(ptr), off)
            self._load_int(ptr, 'r3')
            if fi and fi[1] == 'num':
                self._load_num(val, 'd0')
                t.append(f'    vstr d0, [r3, #{fi[0]}]')
            else:
                armoff = fi[0] if fi else int(off)
                self._load_int(val, 'r0')
                t.append(f'    str r0, [r3, #{armoff}]')

        elif op == TACOp.LOAD_FIELD:
            ptr, off, res = instr.arg1, instr.arg2, instr.result
            fi = self._field_info(self._obj_class.get(ptr), off)
            self._load_int(ptr, 'r3')
            if fi and fi[1] == 'num':
                t.append(f'    vldr d0, [r3, #{fi[0]}]')
                self._store_num(res, 'd0')
            else:
                armoff = fi[0] if fi else int(off)
                t.append(f'    ldr r0, [r3, #{armoff}]')
                self._store_int(res, 'r0')

        elif op == TACOp.LOAD_VTABLE:
            self._load_int(instr.arg1, 'r0')
            t.append('    ldr r0, [r0, #0]')
            self._store_int(instr.result, 'r0')

        elif op == TACOp.VCALL:
            self._emit_vcall(instr)

        elif op == TACOp.STATIC_CALL:
            self._emit_static_call(instr)

        # ── Passo 3: execução SEQUENCIAL (sem SO/threads no bare-metal) ──────
        elif op in (TACOp.PAR_BEGIN, TACOp.PAR_END, TACOp.THREAD_JOIN,
                    TACOp.MUTEX_INIT, TACOp.MUTEX_LOCK, TACOp.MUTEX_UNLOCK):
            t.append(f'    @ {op.value} (no-op: execução sequencial)')

        elif op == TACOp.PARAM_THREAD:
            self._pending.append(instr.arg1)

        elif op == TACOp.SPAWN_THREAD:
            # sem threads: chama o bloco/função diretamente, em ordem.
            args = list(self._pending)
            self._pending = []
            for a in reversed(args):
                t.append('    sub sp, sp, #8')
                if self._kind_of_val(a) == 'num':
                    self._load_num(a, 'd0'); t.append('    vstr d0, [sp]')
                else:
                    self._load_int(a, 'r0'); t.append('    str r0, [sp]')
            t.append(f'    bl {instr.arg1}')
            if args:
                t.append(f'    add sp, sp, #{8 * len(args)}')
            # handle de thread é irrelevante no modo sequencial

        # ── Passo 3: rede — STUB (CPUlator não tem pilha de rede) ────────────
        elif op == TACOp.PARAM_REMOTE:
            pass   # argumentos remotos descartados no stub

        elif op in (TACOp.CONNECT_NODE, TACOp.SERIALIZE, TACOp.RPC_CALL,
                    TACOp.REMOTE_SPAWN, TACOp.ASYNC_BEGIN, TACOp.AWAIT_FUTURE):
            self._pending = []
            t.append(f'    @ {op.value} (stub de rede)')
            if instr.result:
                t.append('    mov r0, #0')
                self._store_int(instr.result, 'r0')

        elif op == TACOp.DESERIALIZE:
            t.append('    @ DESERIALIZE (stub de rede)')
            if instr.result:
                if self._kind.get(instr.result) == 'num':
                    self._zero_num('d0'); self._store_num(instr.result, 'd0')
                else:
                    t.append('    mov r0, #0'); self._store_int(instr.result, 'r0')

        elif op == TACOp.DISCONNECT_NODE:
            t.append('    @ DISCONNECT_NODE (stub de rede)')

        else:
            t.append(f'    @ op não traduzida: {op.value}')

    def _emit_call(self, instr):
        name = instr.arg1
        args = list(self._pending)
        self._pending = []
        t = self._text

        if name == 'print':
            self._emit_print(args)
            return
        if name == 'len':
            self._load_int(args[0], 'r0')
            t.append('    bl __mp_strlen')
            t.append('    vmov s0, r0')
            t.append('    vcvt.f64.s32 d0, s0')
            self._store_num(instr.result, 'd0')
            return
        if name == 'str':
            self._load_num(args[0], 'd0')
            t.append('    bl __mp_num_to_str')
            self._store_int(instr.result, 'r0')
            return
        if name == 'num':
            self._load_int(args[0], 'r0')
            t.append('    bl __mp_str_to_num')
            self._store_num(instr.result, 'd0')
            return
        if name in ('input', 'append', 'keys', 'values'):
            t.append(f'    @ builtin {name}() — nao suportado no ARM (stub)')
            if instr.result:
                if self._kind.get(instr.result) == 'num':
                    self._zero_num('d0'); self._store_num(instr.result, 'd0')
                else:
                    t.append('    mov r0, #0'); self._store_int(instr.result, 'r0')
            return

        # empilha argumentos (8 bytes cada, em ordem reversa → arg0 em [fp,#8])
        for a in reversed(args):
            t.append('    sub sp, sp, #8')
            if self._kind_of_val(a) == 'num':
                self._load_num(a, 'd0'); t.append('    vstr d0, [sp]')
            else:
                self._load_int(a, 'r0');  t.append('    str r0, [sp]')
        t.append(f'    bl {name}')
        if args:
            t.append(f'    add sp, sp, #{8 * len(args)}')
        if instr.result:
            if self._kind.get(instr.result) == 'num':
                self._store_num(instr.result, 'd0')
            else:
                self._store_int(instr.result, 'r0')

    def _push_args(self, args):
        t = self._text
        for a in reversed(args):
            t.append('    sub sp, sp, #8')
            if self._kind_of_val(a) == 'num':
                self._load_num(a, 'd0'); t.append('    vstr d0, [sp]')
            else:
                self._load_int(a, 'r0');  t.append('    str r0, [sp]')

    def _store_result(self, result):
        if not result:
            return
        if self._kind.get(result) == 'num':
            self._store_num(result, 'd0')
        else:
            self._store_int(result, 'r0')

    def _emit_vcall(self, instr):
        t = self._text
        vtbl = instr.arg1
        args = list(self._pending)
        self._pending = []
        idx = int(instr.arg2.split('@')[0]) if '@' in (instr.arg2 or '') else 0
        self._push_args(args)
        self._load_int(vtbl, 'r0')                 # base da vtable
        t.append(f'    ldr r12, [r0, #{idx * 4}]')  # ponteiro do método
        t.append('    blx r12')
        if args:
            t.append(f'    add sp, sp, #{8 * len(args)}')
        self._store_result(instr.result)

    def _emit_static_call(self, instr):
        t = self._text
        label = instr.arg1
        args = list(self._pending)
        self._pending = []
        self._push_args(args)
        t.append(f'    bl {label}')
        if args:
            t.append(f'    add sp, sp, #{8 * len(args)}')
        self._store_result(instr.result)

    def _emit_print(self, args):
        t = self._text
        for i, a in enumerate(args):
            if i > 0:
                t.append('    mov r0, #32'); t.append('    bl __mp_putchar')
            if self._kind_of_val(a) == 'str':
                self._load_int(a, 'r0'); t.append('    bl __mp_print_str')
            elif self._kind_of_val(a) == 'bool':
                self._load_int(a, 'r0')
                t.append('    vmov s0, r0'); t.append('    vcvt.f64.s32 d0, s0')
                t.append('    bl __mp_print_double')
            else:
                self._load_num(a, 'd0'); t.append('    bl __mp_print_double')
        t.append('    mov r0, #10'); t.append('    bl __mp_putchar')

    # ─────────────────────────────────────────────────────────────────────────
    # Carga / armazenamento
    # ─────────────────────────────────────────────────────────────────────────

    def _load_num(self, val, dreg):
        t = self._text
        if val is None or val == 'null':
            self._zero_num(dreg); return
        if self._is_number(val):
            lbl = self._register_float(float(val))
            t.append(f'    ldr r12, ={lbl}')
            t.append(f'    vldr {dreg}, [r12]'); return
        if val in self._slots:
            t.append(f'    vldr {dreg}, [fp, #{self._slots[val]}]'); return
        if val in self._global_vars:
            t.append(f'    ldr r12, ={val}')
            t.append(f'    vldr {dreg}, [r12]'); return
        self._zero_num(dreg)

    def _store_num(self, name, dreg):
        t = self._text
        if not name or self._is_literal(name):
            return
        if name in self._slots:
            t.append(f'    vstr {dreg}, [fp, #{self._slots[name]}]'); return
        if name in self._global_vars:
            t.append(f'    ldr r12, ={name}')
            t.append(f'    vstr {dreg}, [r12]'); return

    def _zero_num(self, dreg):
        lbl = self._register_float(0.0)
        self._text.append(f'    ldr r12, ={lbl}')
        self._text.append(f'    vldr {dreg}, [r12]')

    def _load_int(self, val, reg):
        t = self._text
        if val is None or val == 'null':
            t.append(f'    mov {reg}, #0'); return
        if self._is_str_lit(val):
            lbl = self._register_string(val[1:-1])
            t.append(f'    ldr {reg}, ={lbl}'); return
        if self._is_number(val):
            self._mov_imm(reg, int(float(val))); return
        if val in self._slots:
            t.append(f'    ldr {reg}, [fp, #{self._slots[val]}]'); return
        if val in self._global_vars:
            t.append(f'    ldr r12, ={val}')
            t.append(f'    ldr {reg}, [r12]'); return
        t.append(f'    mov {reg}, #0')

    def _store_int(self, name, reg):
        t = self._text
        if not name or self._is_literal(name):
            return
        if name in self._slots:
            t.append(f'    str {reg}, [fp, #{self._slots[name]}]'); return
        if name in self._global_vars:
            t.append(f'    ldr r12, ={name}')
            t.append(f'    str {reg}, [r12]'); return

    def _mov_imm(self, reg, value):
        t = self._text
        v = value & 0xFFFFFFFF
        if 0 <= value <= 255:
            t.append(f'    mov {reg}, #{value}')
        else:
            t.append(f'    movw {reg}, #{v & 0xFFFF}')
            if (v >> 16) & 0xFFFF:
                t.append(f'    movt {reg}, #{(v >> 16) & 0xFFFF}')

    # ─────────────────────────────────────────────────────────────────────────
    # Prelúdio e runtime
    # ─────────────────────────────────────────────────────────────────────────

    def _emit_prelude(self):
        self._text += [
            '    .text', '    .global _start', '    .global main',
            '    .align 2', '',
            '_start:',
            '    ldr sp, =__mp_stack_top',
            '    bl main',
            '__mp_halt:', '    b __mp_halt',
        ]

    def _emit_runtime(self):
        t = self._text
        t.append('')
        t.append('@ ── Runtime ──────────────────────────────────────────────')
        t += ['', '__mp_putchar:',
              '    push {r4, r5, lr}',
              '    ldr r4, =__mp_outbuf', '    ldr r5, [r4]',
              '    cmp r5, #0', '    beq __mp_pc_uart',
              '    strb r0, [r5]', '    add r5, r5, #1', '    str r5, [r4]',
              '    pop {r4, r5, pc}',
              '__mp_pc_uart:',
              f'    movw r4, #{UART_LO}', f'    movt r4, #{UART_HI}',
              '__mp_pc_wait:',
              '    ldr r5, [r4, #4]', '    lsr r5, r5, #16',
              '    cmp r5, #0', '    beq __mp_pc_wait',
              '    str r0, [r4]', '    pop {r4, r5, pc}']
        t += ['', '__mp_print_str:',
              '    push {r4, lr}', '    mov r4, r0',
              '__mp_ps_loop:',
              '    ldrb r0, [r4]', '    cmp r0, #0', '    beq __mp_ps_done',
              '    bl __mp_putchar', '    add r4, r4, #1', '    b __mp_ps_loop',
              '__mp_ps_done:', '    pop {r4, pc}']
        # imprime inteiro sem sinal (r0 >= 0)
        t += ['', '__mp_print_uint:',
              '    push {r4, r5, r6, lr}', '    mov r4, r0',
              '    cmp r4, #0', '    bne __mp_pu_nz',
              '    mov r0, #48', '    bl __mp_putchar',
              '    pop {r4, r5, r6, pc}',
              '__mp_pu_nz:',
              '    sub sp, sp, #16', '    mov r5, sp', '    mov r6, #0',
              '__mp_pu_loop:',
              '    mov r0, r4', '    mov r1, #10', '    bl __mp_divmod',
              '    add r1, r1, #48', '    strb r1, [r5, r6]', '    add r6, r6, #1',
              '    mov r4, r0', '    cmp r4, #0', '    bne __mp_pu_loop',
              '__mp_pu_out:',
              '    sub r6, r6, #1', '    ldrb r0, [r5, r6]', '    bl __mp_putchar',
              '    cmp r6, #0', '    bne __mp_pu_out',
              '    add sp, sp, #16', '    pop {r4, r5, r6, pc}']
        # imprime double (d0): parte inteira + "." + dígitos fracionários (trim)
        t += ['', '__mp_print_double:',
              '    vpush {d8, d9, d10}',
              '    push {r4, r5, r6, r7, lr}',
              '    vmov.f64 d8, d0',
              '    vcmp.f64 d8, #0.0', '    vmrs APSR_nzcv, fpscr',
              '    bpl __mp_pd_pos',
              '    mov r0, #45', '    bl __mp_putchar',
              '    vneg.f64 d8, d8',
              '__mp_pd_pos:',
              '    vcvt.s32.f64 s0, d8', '    vmov r0, s0',
              '    bl __mp_print_uint',
              '    vcvt.f64.s32 d9, s0', '    vsub.f64 d10, d8, d9',
              '    mov r0, #10', '    vmov s0, r0', '    vcvt.f64.s32 d9, s0',
              '    sub sp, sp, #24', '    mov r5, sp', '    mov r6, #0',
              '    mov r7, #16',
              '__mp_pd_loop:',
              '    cmp r7, #0', '    beq __mp_pd_trim',
              '    vmul.f64 d10, d10, d9',
              '    vcvt.s32.f64 s0, d10', '    vmov r0, s0',
              '    add r0, r0, #48', '    strb r0, [r5, r6]', '    add r6, r6, #1',
              '    vcvt.f64.s32 d8, s0', '    vsub.f64 d10, d10, d8',
              '    sub r7, r7, #1',
              '    vcmp.f64 d10, #0.0', '    vmrs APSR_nzcv, fpscr',
              '    bne __mp_pd_loop',
              '__mp_pd_trim:',
              '    cmp r6, #0', '    beq __mp_pd_dot0',
              '    sub r0, r6, #1', '    ldrb r1, [r5, r0]',
              '    cmp r1, #48', '    bne __mp_pd_emit',
              '    mov r6, r0', '    b __mp_pd_trim',
              '__mp_pd_dot0:',
              '    mov r0, #46', '    bl __mp_putchar',
              '    mov r0, #48', '    bl __mp_putchar',
              '    b __mp_pd_done',
              '__mp_pd_emit:',
              '    mov r0, #46', '    bl __mp_putchar', '    mov r4, #0',
              '__mp_pd_pr:',
              '    cmp r4, r6', '    bge __mp_pd_done',
              '    ldrb r0, [r5, r4]', '    bl __mp_putchar', '    add r4, r4, #1',
              '    b __mp_pd_pr',
              '__mp_pd_done:',
              '    add sp, sp, #24',
              '    pop {r4, r5, r6, r7, lr}',
              '    vpop {d8, d9, d10}', '    bx lr']
        # divisão inteira (usada só na impressão de dígitos)
        t += ['', '__mp_divmod:',
              '    push {r4, r5, r6, r7, lr}',
              '    cmp r1, #0', '    beq __mp_dm_zero',
              '    mov r4, #0', '    mov r5, #0',
              '    cmp r0, #0', '    bpl __mp_dm_np',
              '    rsb r0, r0, #0', '    eor r4, r4, #1', '    mov r5, #1',
              '__mp_dm_np:',
              '    cmp r1, #0', '    bpl __mp_dm_dp',
              '    rsb r1, r1, #0', '    eor r4, r4, #1',
              '__mp_dm_dp:',
              '    mov r2, #0', '    mov r3, #0', '    mov r6, #32',
              '__mp_dm_loop:',
              '    lsls r0, r0, #1', '    lsl r3, r3, #1', '    orrcs r3, r3, #1',
              '    lsl r2, r2, #1', '    cmp r3, r1',
              '    subhs r3, r3, r1', '    orrhs r2, r2, #1',
              '    subs r6, r6, #1', '    bne __mp_dm_loop',
              '    mov r0, r2', '    mov r1, r3',
              '    cmp r4, #0', '    rsbne r0, r0, #0',
              '    cmp r5, #0', '    rsbne r1, r1, #0',
              '    pop {r4, r5, r6, r7, pc}',
              '__mp_dm_zero:',
              '    mov r0, #0', '    mov r1, #0', '    pop {r4, r5, r6, r7, pc}']

        # ── Builtins (len / str / num) ──────────────────────────────────────
        if self._uses_len:
            t += ['', '__mp_strlen:',
                  '    mov r1, #0',
                  '__mp_sl_loop:',
                  '    ldrb r2, [r0, r1]', '    cmp r2, #0', '    beq __mp_sl_done',
                  '    add r1, r1, #1', '    b __mp_sl_loop',
                  '__mp_sl_done:', '    mov r0, r1', '    bx lr']
        if self._uses_str:
            # str(number): formata d0 em um buffer no heap (reusa __mp_print_double
            # via redirecionamento do __mp_putchar). Retorna ponteiro em r0.
            t += ['', '__mp_num_to_str:',
                  '    push {r4, r5, lr}',
                  '    ldr r4, =__mp_heap_ptr', '    ldr r5, [r4]',
                  '    add r1, r5, #40', '    str r1, [r4]',
                  '    ldr r4, =__mp_outbuf', '    str r5, [r4]',
                  '    bl __mp_print_double',
                  '    ldr r4, =__mp_outbuf', '    ldr r1, [r4]',
                  '    mov r2, #0', '    strb r2, [r1]', '    str r2, [r4]',
                  '    mov r0, r5', '    pop {r4, r5, pc}']
        if self._uses_num:
            # num(string): converte string -> double (d0). Suporta sinal, parte
            # inteira e fracionaria. s0 e o scratch int<->float (convencao do
            # codegen); acumula em d4, base 10 em d5, digito em d6, casa em d7.
            t += ['', '__mp_str_to_num:',
                  '    push {r4, r5, r6, lr}',
                  '    mov r4, r0', '    mov r6, #0',
                  '    ldrb r1, [r4]', '    cmp r1, #45', '    bne __mp_sn_init',
                  '    mov r6, #1', '    add r4, r4, #1',
                  '__mp_sn_init:',
                  '    mov r1, #0', '    vmov s0, r1', '    vcvt.f64.s32 d4, s0',
                  '    mov r1, #10', '    vmov s0, r1', '    vcvt.f64.s32 d5, s0',
                  '__mp_sn_int:',
                  '    ldrb r1, [r4]', '    cmp r1, #48', '    blt __mp_sn_dot',
                  '    cmp r1, #57', '    bgt __mp_sn_dot',
                  '    sub r1, r1, #48',
                  '    vmul.f64 d4, d4, d5',
                  '    vmov s0, r1', '    vcvt.f64.s32 d6, s0',
                  '    vadd.f64 d4, d4, d6', '    add r4, r4, #1', '    b __mp_sn_int',
                  '__mp_sn_dot:',
                  '    cmp r1, #46', '    bne __mp_sn_fin', '    add r4, r4, #1',
                  '    mov r1, #1', '    vmov s0, r1', '    vcvt.f64.s32 d7, s0',
                  '__mp_sn_frac:',
                  '    ldrb r1, [r4]', '    cmp r1, #48', '    blt __mp_sn_fin',
                  '    cmp r1, #57', '    bgt __mp_sn_fin',
                  '    sub r1, r1, #48',
                  '    vmul.f64 d7, d7, d5',
                  '    vmov s0, r1', '    vcvt.f64.s32 d6, s0',
                  '    vdiv.f64 d6, d6, d7', '    vadd.f64 d4, d4, d6',
                  '    add r4, r4, #1', '    b __mp_sn_frac',
                  '__mp_sn_fin:',
                  '    cmp r6, #0', '    beq __mp_sn_ret', '    vneg.f64 d4, d4',
                  '__mp_sn_ret:',
                  '    vmov.f64 d0, d4', '    pop {r4, r5, r6, pc}']

    # ─────────────────────────────────────────────────────────────────────────
    # Utilitários
    # ─────────────────────────────────────────────────────────────────────────

    def _lbl(self, lbl):
        return f'{self._cur_func}_{lbl}'

    def _register_string(self, val):
        if val not in self._strings:
            self._strings[val] = f'.STR{self._str_count}'; self._str_count += 1
        return self._strings[val]

    def _register_float(self, v):
        rep = repr(float(v))
        if rep not in self._floats:
            self._floats[rep] = f'.LCD{self._flt_count}'; self._flt_count += 1
        return self._floats[rep]

    @staticmethod
    def _is_str_lit(val):
        return isinstance(val, str) and len(val) >= 2 \
            and val.startswith('"') and val.endswith('"')

    @staticmethod
    def _is_number(val):
        try:
            float(val); return True
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _is_temp(name):
        return len(name) >= 2 and name[0] == 't' and name[1:].isdigit()

    def _is_literal(self, name):
        return self._is_number(name) or self._is_str_lit(name) or name == 'null'
