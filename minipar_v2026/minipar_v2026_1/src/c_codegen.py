"""
c_codegen.py — MiniPar v2026.1
Backend C: traduz TAC para código C compilável pelo GCC.

ARQUITETURA (reescrita — Passo 1: núcleo procedural)
─────────────────────────────────────────────────────
Problemas corrigidos em relação à versão anterior:
  1. Parâmetros formais de função eram descartados (e "vazavam" para
     chamadas seguintes). Agora cada FUNC_BEGIN coleta seus parâmetros
     formais (PARAM imediatamente após, em quantidade igual à aridade
     conhecida pela tabela de símbolos) e os emite na assinatura.
  2. Variáveis atribuídas mais de uma vez geravam redefinição em C
     (`double i = ...;` duas vezes). Agora as declarações são *içadas*
     (hoisted) para o topo da função e o corpo usa apenas atribuições.
     Isso também elimina o erro de `goto` pulando inicialização.
  3. `print` e demais builtins não existiam em C. Agora `print` vira
     `printf` com especificador por tipo; `len/str/num` têm suporte
     mínimo coerente com o interpretador.
  4. Tipos: a assinatura (retorno e parâmetros) vem da tabela de
     símbolos, não mais "double" para tudo. `%` usa `fmod` (math.h).

POO, paralelismo e distribuição entram nos Passos 2 e 3.
"""

from codegen import TAC, TACOp
from symbol_table import SymbolTable, SymbolType
from typing import List, Optional, Set, Dict, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Cabeçalhos / runtime
# ─────────────────────────────────────────────────────────────────────────────

HEADER_BASE = """\
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
"""

HEADER_RUNTIME = r"""
/* ── Runtime MiniPar ── */

/* Formata um double como o str() do interpretador (Python):
   inteiros com ".0"; demais com a menor precisão que faz round-trip. */
static void __mp_fmt_num(char* out, int n, double v) {
    if (v == (long long) v && fabs(v) < 1e15) {
        snprintf(out, n, "%lld.0", (long long) v);
        return;
    }
    for (int p = 1; p <= 17; p++) {
        snprintf(out, n, "%.*g", p, v);
        if (strtod(out, NULL) == v) return;
    }
}

static void __mp_pnum(double v) {
    char buf[40];
    __mp_fmt_num(buf, sizeof buf, v);
    printf("%s", buf);
}

/* str(number): string recém-alocada (didático: sem free). */
static char* __mp_num_to_str(double v) {
    char* buf = (char*) malloc(40);
    __mp_fmt_num(buf, 40, v);
    return buf;
}
"""

HEADER_NET_INCLUDES = """\
#include <stdarg.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
"""

HEADER_NET_RUNTIME = r'''
/* ── Runtime distribuido (sockets + serializador proprio) ── */

/* Conecta a "ip:porta". Retorna fd (>=0) ou -1 se o servidor estiver
   indisponivel (fallback gracioso, como no interpretador). */
static int __mp_connect(const char* hostport) {
    if (!hostport) return -1;
    const char* c = strchr(hostport, ':');
    if (!c) return -1;
    char host[64]; int hl = (int)(c - hostport);
    if (hl > 63) hl = 63;
    memcpy(host, hostport, hl); host[hl] = 0;
    int port = atoi(c + 1);
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    struct sockaddr_in sa; memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET; sa.sin_port = htons(port);
    if (inet_pton(AF_INET, host, &sa.sin_addr) <= 0) { close(fd); return -1; }
    if (connect(fd, (struct sockaddr*)&sa, sizeof sa) < 0) { close(fd); return -1; }
    return fd;
}
static void __mp_disconnect(int fd) { if (fd >= 0) close(fd); }

/* Serializacao textual propria (sem libcjson). */
static char* __mp_ser_num(double v) { char* b = (char*) malloc(40); __mp_fmt_num(b, 40, v); return b; }
static char* __mp_ser_str(const char* s) { char* b = (char*) malloc((s?strlen(s):0)+1); strcpy(b, s?s:""); return b; }
static double __mp_deser_num(const char* s) { return s ? strtod(s, NULL) : 0.0; }

/* RPC: envia "func arg0 arg1 ...\n" e le a resposta. Sem servidor, retorna
   "0" — fallback local neutro (a saida observavel nao depende do retorno). */
static char* __mp_rpc(int fd, const char* func, int n, ...) {
    char req[1024]; int off = snprintf(req, sizeof req, "%s", func);
    va_list ap; va_start(ap, n);
    for (int i = 0; i < n; i++) {
        const char* a = va_arg(ap, const char*);
        off += snprintf(req + off, sizeof req - off, " %s", a ? a : "");
    }
    va_end(ap);
    off += snprintf(req + off, sizeof req - off, "\n");
    if (fd >= 0 && send(fd, req, off, 0) > 0) {
        static char resp[1024];
        int r = (int) recv(fd, resp, sizeof resp - 1, 0);
        if (r > 0) { resp[r] = 0; char* nl = strchr(resp, '\n'); if (nl) *nl = 0; return resp; }
    }
    return (char*) "0";
}
'''

# Conjunto de builtins reconhecidos (espelha runner.BUILTINS / semantic).
BUILTINS = {'print', 'input', 'len', 'str', 'num', 'append', 'keys', 'values'}


class CCodeGenerator:

    def __init__(self, symbol_table: SymbolTable):
        self.table = symbol_table

        # Seções do arquivo final
        self._protos:     List[str] = []
        self._globals:    List[str] = []   # variáveis globais (escopo de arquivo)
        self._functions:  List[str] = []
        self._main_decls: List[str] = []
        self._main_stmts: List[str] = []

        # Assinaturas: nome -> (ret_ctype, [(param_nome, param_ctype), ...])
        self._sig: Dict[str, Tuple[str, List[Tuple[str, str]]]] = {}
        self._formals: Dict[str, List[str]] = {}

        # Estado de tradução
        self._in_function = False
        self._cur_name = ''
        self._cur_ret = 'void'
        self._cur_formals: Set[str] = set()
        self._cur_decls: Dict[str, str] = {}     # nome -> ctype (içadas)
        self._cur_stmts: List[str] = []
        self._skip_params = 0                     # quantos PARAM ainda são formais
        self._pending: List[str] = []             # PARAM acumulados p/ CALL

        # Tipos rastreados por nome (para escolher %s vs %g e tipos de decl)
        self._ctype: Dict[str, str] = {}
        # Variáveis globais (escopo de arquivo) e seus tipos
        self._global_vars: Dict[str, str] = {}

        # Feature flags (mantidas para o backend.py escolher flags do GCC)
        self.uses_parallel = False
        self.uses_network  = False
        self.uses_oop      = False

        # POO: nome -> nome da classe (para resolver campos e despacho)
        self._obj_class: Dict[str, str] = {}
        self._structs: List[str] = []
        self._vtables: List[str] = []
        self._emitted_structs: Set[str] = set()

        # Paralelismo / distribuicao (Passo 3)
        self._pending_thread: List[str] = []   # PARAM_THREAD acumulados
        self._pending_remote: List[str] = []   # PARAM_REMOTE acumulados
        self._thread_helpers: List[str] = []   # structs+wrappers de spawn c/ args
        self._thread_ctr = 0
        self._mutexes: Set[str] = set()        # nomes de locks -> mutex global
        self._conn_globals: Set[str] = set()   # handles de conexao (file-scope)

    # ─────────────────────────────────────────────────────────────────────────
    # API pública
    # ─────────────────────────────────────────────────────────────────────────

    def generate(self, tac_code: List[TAC]) -> str:
        self._detect_features(tac_code)
        self._collect_signatures(tac_code)
        self._infer_types(tac_code)
        self._collect_globals(tac_code)
        if self.uses_oop:
            self._emit_all_classes()

        for instr in tac_code:
            self._translate(instr)
        if self._in_function:
            self._close_function()

        return self._assemble_file()

    # ─────────────────────────────────────────────────────────────────────────
    # Pré-passagens
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_features(self, tac_code: List[TAC]):
        par = {TACOp.PAR_BEGIN, TACOp.SPAWN_THREAD, TACOp.MUTEX_INIT,
               TACOp.THREAD_JOIN, TACOp.ASYNC_BEGIN}
        net = {TACOp.CONNECT_NODE, TACOp.RPC_CALL, TACOp.SERIALIZE,
               TACOp.DESERIALIZE, TACOp.REMOTE_SPAWN}
        oop = {TACOp.ALLOC_OBJ, TACOp.VCALL, TACOp.LOAD_VTABLE,
               TACOp.STORE_FIELD, TACOp.LOAD_FIELD, TACOp.STATIC_CALL}
        for instr in tac_code:
            if instr.op in par: self.uses_parallel = True
            if instr.op in net: self.uses_network = True
            if instr.op in oop: self.uses_oop = True
            if instr.op in (TACOp.MUTEX_INIT, TACOp.MUTEX_LOCK,
                            TACOp.MUTEX_UNLOCK):
                self._mutexes.add(self._mtx_id(instr.arg1))
            if instr.op == TACOp.CONNECT_NODE and instr.result:
                self._conn_globals.add(instr.result)

    def _is_block_fn(self, name: str) -> bool:
        # Blocos-closure de thread (assinatura void*(void*)). __impl_* NÃO entra
        # aqui: é a implementação local (real) de uma função remota.
        return name.startswith(('__par_block', '__spawn_block', '__remote_block'))

    def _collect_signatures(self, tac_code: List[TAC]):
        n = len(tac_code)
        for i, instr in enumerate(tac_code):
            if instr.op != TACOp.FUNC_BEGIN:
                continue
            name = instr.arg1
            # Sequência de PARAM imediatamente após FUNC_BEGIN
            run = []
            j = i + 1
            while j < n and tac_code[j].op == TACOp.PARAM:
                run.append(tac_code[j].arg1)
                j += 1
            arity = self._expected_arity(name, len(run))
            formals = run[:arity]
            self._formals[name] = formals
            self._sig[name] = self._build_signature(name, formals)

    def _expected_arity(self, name: str, leading: int) -> int:
        if name == 'main' or self._is_block_fn(name):
            return 0
        sym = self.table.lookup(name)
        if sym and sym.tipo_simbolo == SymbolType.FUNCTION:
            return len(sym.tipos_parametros)
        cls = self._ctor_class(name)
        if cls:
            d = self.table.lookup_class(cls)
            return len(d.ctor_params) if d else leading
        me = self._method_entry(name)
        if me:
            return len(me[1].param_types) + 1   # +1 para 'this'
        # Sem informação confiável: assume que toda a sequência inicial é formal.
        return leading

    def _ctor_class(self, name: str) -> Optional[str]:
        if name.endswith('___ctor'):
            return name[:-len('___ctor')]
        return None

    def _method_entry(self, label: str):
        for cls, desc in self.table.class_registry.items():
            for e in desc._vtable:
                if e.label == label:
                    return (cls, e)
        return None

    def _build_signature(self, name: str,
                          formals: List[str]) -> Tuple[str, List[Tuple[str, str]]]:
        if name == 'main':
            return ('int', [])
        if self._is_block_fn(name):
            return ('void*', [('__args', 'void*')])
        cls = self._ctor_class(name)
        if cls:
            d = self.table.lookup_class(cls)
            ptypes = [self._c_type(t) for t in (d.ctor_params if d else [])]
            while len(ptypes) < len(formals):
                ptypes.append('double')
            return (f'{cls}*', list(zip(formals, ptypes)))
        me = self._method_entry(name)
        if me:
            mcls, e = me
            ptypes = [f'{mcls}*'] + [self._c_type(t) for t in e.param_types]
            while len(ptypes) < len(formals):
                ptypes.append('double')
            return (self._c_type(e.return_type), list(zip(formals, ptypes)))
        sym = self.table.lookup(name)
        if sym and sym.tipo_simbolo == SymbolType.FUNCTION:
            ret = self._c_type(sym.tipo_retorno)
            ptypes = [self._c_type(t) for t in sym.tipos_parametros]
            while len(ptypes) < len(formals):
                ptypes.append('double')
            return (ret, list(zip(formals, ptypes)))
        return ('double', [(p, 'double') for p in formals])

    def _infer_types(self, tac_code: List[TAC]):
        """Propaga tipos C para todo result, resolvendo temporários. Permite
        decidir %s vs %g, o tipo das declarações içadas e o tipo dos globais."""
        in_func = False
        for instr in tac_code:
            op = instr.op
            if op == TACOp.FUNC_BEGIN:
                in_func = True
                for p, ct in self._sig.get(instr.arg1, ('', []))[1]:
                    self._ctype[p] = ct
                    if ct.endswith('*') and self.table.lookup_class(ct[:-1]):
                        self._obj_class[p] = ct[:-1]
                continue
            if op == TACOp.FUNC_END:
                in_func = False
                continue
            r = instr.result
            if op == TACOp.ASSIGN:
                self._ctype[r] = self._value_ctype(instr.arg1)
                if instr.arg1 in self._obj_class:
                    self._obj_class[r] = self._obj_class[instr.arg1]
            elif op in (TACOp.ADD, TACOp.SUB, TACOp.MUL, TACOp.DIV,
                        TACOp.MOD, TACOp.NEG):
                self._ctype[r] = 'double'
            elif op in (TACOp.EQ, TACOp.NEQ, TACOp.LT, TACOp.GT, TACOp.LTE,
                        TACOp.GTE, TACOp.AND, TACOp.OR, TACOp.NOT):
                self._ctype[r] = 'int'
            elif op == TACOp.CALL and r is not None:
                self._ctype[r] = self._call_ret_ctype(instr.arg1, instr.arg2)
                cls = self._ctor_class(instr.arg1)
                if cls:
                    self._obj_class[r] = cls
            elif op == TACOp.ALLOC_OBJ:
                self._ctype[r] = f'{instr.arg1}*'
                self._obj_class[r] = instr.arg1
            elif op == TACOp.LOAD_VTABLE:
                self._ctype[r] = 'void**'
            elif op == TACOp.LOAD_FIELD:
                cls = self._obj_class.get(instr.arg1)
                fld = self._field_by_offset(cls, instr.arg2)
                if fld:
                    self._ctype[r] = self._c_type(fld.tipo_dados)
                    if self.table.lookup_class(fld.tipo_dados):
                        self._obj_class[r] = fld.tipo_dados
                else:
                    self._ctype[r] = 'double'
            elif op == TACOp.VCALL:
                ret, _, _ = self._vcall_info(instr.arg2)
                self._ctype[r] = self._c_type(ret)
                if self.table.lookup_class(ret):
                    self._obj_class[r] = ret
            elif op == TACOp.STATIC_CALL:
                me = self._method_entry(instr.arg1)
                self._ctype[r] = self._c_type(me[1].return_type) if me else 'double'

    def _call_ret_ctype(self, name: str, first_arg: Optional[str]) -> str:
        if name in BUILTINS:
            if name in ('str', 'input'):
                return 'char*'
            return 'double'   # len/num/etc.
        return self._sig.get(name, ('double', []))[0]

    # ── POO: helpers de resolução ──────────────────────────────────────────────

    def _field_by_offset(self, class_name: Optional[str], offset: Optional[str]):
        """Encontra o FieldLayout pelo offset, percorrendo a cadeia de herança."""
        if not class_name or offset is None:
            return None
        try:
            off = int(offset)
        except (ValueError, TypeError):
            return None
        d = self.table.lookup_class(class_name)
        while d:
            for f in d._fields:
                if f.offset == off:
                    return f
            d = d.superclasse
        return None

    def _vcall_info(self, arg2: Optional[str]):
        """Decodifica 'idx@Classe' → (return_type, [param_types], classe)."""
        if not arg2 or '@' not in arg2:
            return ('number', [], None)
        idx_s, cls = arg2.split('@', 1)
        desc = self.table.lookup_class(cls)
        if desc and idx_s.isdigit():
            idx = int(idx_s)
            if 0 <= idx < len(desc._vtable):
                e = desc._vtable[idx]
                return (e.return_type, e.param_types, cls)
        return ('number', [], cls)

    # ── POO: emissão de structs e vtables ──────────────────────────────────────

    def _emit_all_classes(self):
        ordered = self._topo_sort_classes()
        # forward typedefs (permite campos do tipo de outra classe)
        for c in ordered:
            self._structs.append(f'typedef struct {c} {c};')
        self._structs.append('')
        for c in ordered:
            self._emit_struct(c)
        # forward-decl dos métodos antes das vtables
        for c in ordered:
            desc = self.table.lookup_class(c)
            if not desc:
                continue
            for e in desc._vtable:
                ret = self._c_type(e.return_type)
                self._structs.append(f'static {ret} {e.label}();')
        self._structs.append('')
        for c in ordered:
            self._emit_vtable(c)

    def _topo_sort_classes(self) -> List[str]:
        visited: List[str] = []

        def visit(name):
            if name in visited:
                return
            d = self.table.lookup_class(name)
            if d and d.superclasse:
                visit(d.superclasse.nome)
            visited.append(name)

        for name in self.table.class_registry:
            visit(name)
        return visited

    def _emit_struct(self, class_name: str):
        if class_name in self._emitted_structs:
            return
        self._emitted_structs.add(class_name)
        desc = self.table.lookup_class(class_name)
        if not desc:
            return
        lines = [f'struct {class_name} {{',
                 '    void** __vtable;']
        # campos de toda a cadeia (superclasse primeiro), p/ casar offsets
        chain = []
        d = desc
        while d:
            chain.append(d); d = d.superclasse
        for anc in reversed(chain):
            for f in anc._fields:
                lines.append(f'    {self._c_type(f.tipo_dados)} {f.nome};')
        lines.append('};')
        lines.append('')
        self._structs.extend(lines)

    def _emit_vtable(self, class_name: str):
        desc = self.table.lookup_class(class_name)
        if not desc or not desc._vtable:
            return
        self._vtables.append(f'static void* __vtable_{class_name}[] = {{')
        for e in desc._vtable:
            self._vtables.append(f'    (void*) {e.label},')
        self._vtables.append('};')

    def _collect_globals(self, tac_code: List[TAC]):
        """Variáveis de usuário atribuídas em nível global viram globais de
        arquivo (visíveis dentro de funções, como no interpretador)."""
        depth = 0
        for instr in tac_code:
            if instr.op == TACOp.FUNC_BEGIN:
                depth += 1
            elif instr.op == TACOp.FUNC_END:
                depth -= 1
            elif depth == 0 and instr.op == TACOp.ASSIGN and instr.result:
                r = instr.result
                if self._is_temp(r) or '[' in r:
                    continue
                ct = self._ctype.get(r, 'double')
                # se em qualquer atribuição o RHS for string, a global é char*
                if self._global_vars.get(r) == 'char*':
                    ct = 'char*'
                self._global_vars[r] = ct
        # Handles de conexao sao file-scope (visiveis nos blocos de thread).
        for c in self._conn_globals:
            self._global_vars.setdefault(c, 'int')

    # ─────────────────────────────────────────────────────────────────────────
    # Tradução
    # ─────────────────────────────────────────────────────────────────────────

    def _translate(self, instr: TAC):
        op = instr.op

        if op == TACOp.FUNC_BEGIN:
            self._open_function(instr.arg1)
            return
        if op == TACOp.FUNC_END:
            self._close_function()
            return

        # Ignorar pseudo-instruções de definição de vtable (tratadas no Passo 2)
        if op == TACOp.LABEL and instr.arg1 and instr.arg1.startswith('__vtable_def_'):
            return
        if op == TACOp.ASSIGN and instr.result and '[' in instr.result:
            return  # entradas de vtable: __vtable_X[i] = ...

        if op == TACOp.ASSIGN:
            ct = self._value_ctype(instr.arg1)
            self._set_type(instr.result, ct)
            self._declare(instr.result, ct)
            if instr.arg1 in self._obj_class:
                self._obj_class[instr.result] = self._obj_class[instr.arg1]
            self._stmt(f'{instr.result} = {instr.arg1};')

        elif op in (TACOp.ADD, TACOp.SUB, TACOp.MUL, TACOp.DIV):
            c = {TACOp.ADD: '+', TACOp.SUB: '-',
                 TACOp.MUL: '*', TACOp.DIV: '/'}[op]
            self._set_type(instr.result, 'double')
            self._declare(instr.result, 'double')
            self._stmt(f'{instr.result} = {instr.arg1} {c} {instr.arg2};')

        elif op == TACOp.MOD:
            self._set_type(instr.result, 'double')
            self._declare(instr.result, 'double')
            self._stmt(f'{instr.result} = fmod({instr.arg1}, {instr.arg2});')

        elif op in (TACOp.EQ, TACOp.NEQ, TACOp.LT, TACOp.GT,
                    TACOp.LTE, TACOp.GTE, TACOp.AND, TACOp.OR):
            c = {TACOp.EQ: '==', TACOp.NEQ: '!=', TACOp.LT: '<', TACOp.GT: '>',
                 TACOp.LTE: '<=', TACOp.GTE: '>=',
                 TACOp.AND: '&&', TACOp.OR: '||'}[op]
            self._set_type(instr.result, 'int')
            self._declare(instr.result, 'int')
            self._stmt(f'{instr.result} = ({instr.arg1} {c} {instr.arg2});')

        elif op == TACOp.NOT:
            self._set_type(instr.result, 'int')
            self._declare(instr.result, 'int')
            self._stmt(f'{instr.result} = !({instr.arg1});')

        elif op == TACOp.NEG:
            self._set_type(instr.result, 'double')
            self._declare(instr.result, 'double')
            self._stmt(f'{instr.result} = -({instr.arg1});')

        elif op == TACOp.LABEL:
            self._stmt(f'{self._label(instr.arg1)}: ;')

        elif op == TACOp.GOTO:
            self._stmt(f'goto {self._label(instr.result)};')

        elif op == TACOp.IF_FALSE:
            self._stmt(f'if (!({instr.arg1})) goto {self._label(instr.result)};')

        elif op == TACOp.PARAM:
            if self._skip_params > 0:      # parâmetro formal — já está na assinatura
                self._skip_params -= 1
            else:
                self._pending.append(instr.arg1)

        elif op == TACOp.CALL:
            self._gen_call(instr)

        elif op == TACOp.RETURN:
            if instr.arg1 is not None:
                self._stmt(f'return {instr.arg1};')
            elif self._cur_ret not in ('void', ''):
                self._stmt(f'return 0;')   # função tipada sem valor explícito
            else:
                self._stmt('return;')

        # ── POO ────────────────────────────────────────────────────────────────

        elif op == TACOp.ALLOC_OBJ:
            cls = instr.arg1
            self._set_type(instr.result, f'{cls}*')
            self._declare(instr.result, f'{cls}*')
            self._obj_class[instr.result] = cls
            # calloc zera o objeto (campos não inicializados ficam 0/NULL)
            self._stmt(f'{instr.result} = ({cls}*) calloc(1, sizeof({cls}));')

        elif op == TACOp.SET_VTABLE:
            self._stmt(f'{instr.arg1}->__vtable = (void**) {instr.arg2};')

        elif op == TACOp.INIT_FIELD:
            pass   # calloc já zerou o objeto

        elif op == TACOp.STORE_FIELD:
            ptr, val, off = instr.arg1, instr.arg2, instr.result
            fld = self._field_by_offset(self._obj_class.get(ptr), off)
            if fld:
                self._stmt(f'{ptr}->{fld.nome} = {val};')
            else:
                self._stmt(f'*((double*)((char*){ptr} + {off})) = {val};')

        elif op == TACOp.LOAD_FIELD:
            ptr, off, res = instr.arg1, instr.arg2, instr.result
            fld = self._field_by_offset(self._obj_class.get(ptr), off)
            ct = self._c_type(fld.tipo_dados) if fld else 'double'
            self._set_type(res, ct)
            self._declare(res, ct)
            if fld and self.table.lookup_class(fld.tipo_dados):
                self._obj_class[res] = fld.tipo_dados
            if fld:
                self._stmt(f'{res} = {ptr}->{fld.nome};')
            else:
                self._stmt(f'{res} = *((double*)((char*){ptr} + {off}));')

        elif op == TACOp.LOAD_VTABLE:
            self._declare(instr.result, 'void**')
            self._stmt(f'{instr.result} = {instr.arg1}->__vtable;')

        elif op == TACOp.VCALL:
            self._gen_vcall(instr)

        elif op == TACOp.STATIC_CALL:
            self._gen_static_call(instr)

        # ── Paralelismo (pthreads) ──────────────────────────────────────────
        elif op in (TACOp.PAR_BEGIN, TACOp.PAR_END):
            self._stmt('/* par { */' if op == TACOp.PAR_BEGIN else '/* } par */')

        elif op == TACOp.PARAM_THREAD:
            self._pending_thread.append(instr.arg1)

        elif op == TACOp.SPAWN_THREAD:
            self._gen_spawn(instr)

        elif op == TACOp.THREAD_JOIN:
            self._stmt(f'pthread_join({instr.arg1}, NULL);')

        elif op == TACOp.MUTEX_INIT:
            pass   # mutexes sao inicializados estaticamente (file-scope)

        elif op == TACOp.MUTEX_LOCK:
            self._stmt(f'pthread_mutex_lock(&__mtx_{self._mtx_id(instr.arg1)});')

        elif op == TACOp.MUTEX_UNLOCK:
            self._stmt(f'pthread_mutex_unlock(&__mtx_{self._mtx_id(instr.arg1)});')

        elif op in (TACOp.ASYNC_BEGIN, TACOp.AWAIT_FUTURE):
            # async/await (future): forma simplificada — executa de modo síncrono.
            if instr.result:
                self._set_type(instr.result, 'double')
                self._declare(instr.result, 'double')
                self._stmt(f'{instr.result} = 0;')

        # ── Distribuição (sockets) ──────────────────────────────────────────
        elif op == TACOp.CONNECT_NODE:
            res = instr.result
            self._set_type(res, 'int')
            self._declare(res, 'int')
            self._stmt(f'{res} = __mp_connect({instr.arg1});')

        elif op == TACOp.DISCONNECT_NODE:
            self._stmt(f'__mp_disconnect({instr.arg1});')

        elif op == TACOp.SERIALIZE:
            res = instr.result
            self._set_type(res, 'char*')
            self._declare(res, 'char*')
            if self._is_string(instr.arg1):
                self._stmt(f'{res} = __mp_ser_str({instr.arg1});')
            else:
                self._stmt(f'{res} = __mp_ser_num({instr.arg1});')

        elif op == TACOp.PARAM_REMOTE:
            self._pending_remote.append(instr.arg1)

        elif op == TACOp.RPC_CALL:
            self._gen_rpc(instr)

        elif op == TACOp.DESERIALIZE:
            res, buf = instr.result, instr.arg1
            tipo = (instr.arg2 or '').strip('"')
            if tipo == 'string':
                self._set_type(res, 'char*')
                self._declare(res, 'char*')
                self._stmt(f'{res} = {buf};')
            else:
                self._set_type(res, 'double')
                self._declare(res, 'double')
                self._stmt(f'{res} = __mp_deser_num({buf});')

        elif op == TACOp.REMOTE_SPAWN:
            # dispara um bloco remoto: tratado como fire-and-forget (stub).
            if instr.result:
                self._set_type(instr.result, 'int')
                self._declare(instr.result, 'int')
                self._stmt(f'{instr.result} = 0;')

        else:
            self._stmt(f'/* op não traduzida: {op.value} */')

    # ── Chamadas ─────────────────────────────────────────────────────────────

    def _gen_call(self, instr: TAC):
        name = instr.arg1
        args = list(self._pending)
        self._pending = []

        if name in BUILTINS:
            self._gen_builtin(name, args, instr.result)
            return

        ret = self._sig.get(name, ('double', []))[0]
        call = f'{name}({", ".join(args)})'
        if ret in ('void', '') or instr.result is None:
            self._stmt(f'{call};')
        else:
            self._set_type(instr.result, ret)
            self._declare(instr.result, ret)
            cls = self._ctor_class(name)
            if cls:
                self._obj_class[instr.result] = cls
            self._stmt(f'{instr.result} = {call};')

    def _gen_spawn(self, instr: TAC):
        """SPAWN_THREAD: cria uma thread pthread. Sem argumentos (blocos de par),
        chama diretamente o bloco; com argumentos (spawn f(args)), gera um
        struct + wrapper que captura os args e invoca a função."""
        func = instr.arg1
        args = list(self._pending_thread)
        self._pending_thread = []
        handle = instr.result or f'__h{self._thread_ctr}'
        self._declare(handle, 'pthread_t')
        if not args:
            self._stmt(f'pthread_create(&{handle}, NULL, {func}, NULL);')
            return
        n = self._thread_ctr
        self._thread_ctr += 1
        actypes = [self._value_ctype(a) for a in args]
        struct = f'__targ_{n}'
        wrap   = f'__twrap_{n}'
        fields = '\n'.join(f'    {ct} a{i};' for i, ct in enumerate(actypes))
        callas = ', '.join(f'__a->a{i}' for i in range(len(args)))
        self._thread_helpers.append(
            f'typedef struct {{\n{fields}\n}} {struct};\n'
            f'static void* {wrap}(void* __p) {{\n'
            f'    {struct}* __a = ({struct}*) __p;\n'
            f'    {func}({callas});\n'
            f'    free(__a); return NULL;\n}}')
        self._stmt(f'{struct}* __ta_{n} = ({struct}*) malloc(sizeof({struct}));')
        for i, a in enumerate(args):
            self._stmt(f'__ta_{n}->a{i} = {a};')
        self._stmt(f'pthread_create(&{handle}, NULL, {wrap}, __ta_{n});')

    def _gen_rpc(self, instr: TAC):
        """RPC_CALL conn "func": envia os PARAM_REMOTE acumulados e recebe a
        resposta serializada (char*)."""
        conn = instr.arg1
        func = instr.arg2            # já vem como "nome" (literal C)
        args = list(self._pending_remote)
        self._pending_remote = []
        arglist = ''.join(f', {a}' for a in args)
        call = f'__mp_rpc({conn}, {func}, {len(args)}{arglist})'
        res = instr.result
        if res:
            self._set_type(res, 'char*')
            self._declare(res, 'char*')
            self._stmt(f'{res} = {call};')
        else:
            self._stmt(f'{call};')

    def _gen_vcall(self, instr: TAC):
        vtbl = instr.arg1
        args = list(self._pending)
        self._pending = []
        ret, ptypes, cls = self._vcall_info(instr.arg2)
        idx = int(instr.arg2.split('@')[0]) if '@' in (instr.arg2 or '') else 0
        ret_c = self._c_type(ret)
        cast_params = [f'{cls}*'] + [self._c_type(t) for t in ptypes]
        cast = f'{ret_c} (*)({", ".join(cast_params)})'
        call = f'(({cast}) {vtbl}[{idx}])({", ".join(args)})'
        if ret_c in ('void', '') or instr.result is None:
            self._stmt(f'{call};')
        else:
            self._set_type(instr.result, ret_c)
            self._declare(instr.result, ret_c)
            if self.table.lookup_class(ret):
                self._obj_class[instr.result] = ret
            self._stmt(f'{instr.result} = {call};')

    def _gen_static_call(self, instr: TAC):
        label = instr.arg1
        args = list(self._pending)
        self._pending = []
        me = self._method_entry(label)
        ret = me[1].return_type if me else 'number'
        ret_c = self._c_type(ret)
        call = f'{label}({", ".join(args)})'
        if ret_c in ('void', '') or instr.result is None:
            self._stmt(f'{call};')
        else:
            self._set_type(instr.result, ret_c)
            self._declare(instr.result, ret_c)
            if self.table.lookup_class(ret):
                self._obj_class[instr.result] = ret
            self._stmt(f'{instr.result} = {call};')

    def _gen_builtin(self, name: str, args: List[str], result: Optional[str]):
        if name == 'print':
            for i, a in enumerate(args):
                if i > 0:
                    self._stmt('printf(" ");')
                if self._is_string(a):
                    self._stmt(f'printf("%s", {a});')
                else:
                    self._stmt(f'__mp_pnum((double)({a}));')
            self._stmt(r'printf("\n");')

        elif name == 'len':
            self._declare(result, 'double'); self._set_type(result, 'double')
            a = args[0] if args else '0'
            if self._is_string(a):
                self._stmt(f'{result} = (double) strlen({a});')
            else:
                self._stmt(f'{result} = 0; /* len() de lista/dict: Passo 3 */')

        elif name == 'str':
            self._declare(result, 'char*'); self._set_type(result, 'char*')
            a = args[0] if args else '""'
            if self._is_string(a):
                self._stmt(f'{result} = (char*)({a});')
            else:
                self._stmt(f'{result} = __mp_num_to_str((double)({a}));')

        elif name == 'num':
            self._declare(result, 'double'); self._set_type(result, 'double')
            a = args[0] if args else '0'
            if self._is_string(a):
                self._stmt(f'{result} = atof({a});')
            else:
                self._stmt(f'{result} = (double)({a});')

        elif name == 'input':
            self._declare(result, 'char*'); self._set_type(result, 'char*')
            if args and self._is_string(args[0]):
                self._stmt(f'printf("%s", {args[0]});')
            self._stmt(f'{result} = (char*) malloc(256);')
            self._stmt(f'if (!fgets({result}, 256, stdin)) {result}[0] = 0;')
            self._stmt(f'{result}[strcspn({result}, "\\n")] = 0;')

        else:
            # append/keys/values: dependem de lista/dict (Passo 3)
            if result is not None:
                self._declare(result, 'double'); self._set_type(result, 'double')
                self._stmt(f'{result} = 0; /* {name}(): Passo 3 */')
            else:
                self._stmt(f'/* {name}(): Passo 3 */')

    # ─────────────────────────────────────────────────────────────────────────
    # Gerência de funções
    # ─────────────────────────────────────────────────────────────────────────

    def _open_function(self, name: str):
        self._in_function = True
        self._cur_name = name
        self._cur_ret, params = self._sig.get(name, ('double', []))
        self._cur_formals = {p for p, _ in params}
        self._cur_decls = {}
        self._cur_stmts = []
        self._skip_params = (0 if name == 'main' or self._is_block_fn(name)
                             else len(params))
        self._pending = []
        # tipos dos parâmetros ficam disponíveis para %s/%g e chamadas
        for p, ct in params:
            self._ctype[p] = ct
            if ct.endswith('*') and self.table.lookup_class(ct[:-1]):
                self._obj_class[p] = ct[:-1]

    def _close_function(self):
        name = self._cur_name
        ret, params = self._sig.get(name, ('double', []))
        plist = ', '.join(f'{ct} {p}' for p, ct in params) or 'void'
        lines = [f'{self._storage(name)}{ret} {name}({plist}) {{']
        for var, ct in self._cur_decls.items():
            lines.append(f'    {ct} {var};')
        lines.extend('    ' + s for s in self._cur_stmts)
        # garantir retorno em funções tipadas
        if ret not in ('void', '') and (not self._cur_stmts or
                                         not self._cur_stmts[-1].startswith('return')):
            lines.append('    return 0;')
        lines.append('}')
        lines.append('')
        self._functions.extend(lines)
        self._in_function = False
        self._skip_params = 0
        self._pending = []

    def _storage(self, name: str) -> str:
        return '' if name == 'main' else 'static '

    # ── helpers de emissão ────────────────────────────────────────────────────

    def _declare(self, name: Optional[str], ctype: str):
        if not name or self._is_literal(name):
            return
        if self._in_function:
            if name in self._cur_formals:
                return
            if name in self._global_vars:
                return   # global de escopo de arquivo: não sombrear localmente
            self._cur_decls.setdefault(name, ctype)
        else:
            if name in self._global_vars:
                return   # já declarada em escopo de arquivo
            if not any(n == name for n, _ in self._main_decls):
                self._main_decls.append((name, ctype))

    def _stmt(self, line: str):
        (self._cur_stmts if self._in_function else self._main_stmts).append(line)

    def _label(self, lbl: str) -> str:
        # rótulos de função são locais; prefixar evita colisão entre funções
        return f'{self._cur_name}_{lbl}' if self._in_function else f'__g_{lbl}'

    def _set_type(self, name: Optional[str], ctype: str):
        if name and not self._is_literal(name):
            self._ctype[name] = ctype

    # ─────────────────────────────────────────────────────────────────────────
    # Montagem do arquivo
    # ─────────────────────────────────────────────────────────────────────────

    def _assemble_file(self) -> str:
        parts = ['/* Gerado automaticamente pelo compilador MiniPar v2026.1 */',
                 HEADER_BASE]
        if self.uses_parallel:
            parts.append('#include <pthread.h>')
        if self.uses_network:
            parts.append(HEADER_NET_INCLUDES)
        parts.append(HEADER_RUNTIME)
        if self.uses_network:
            parts.append(HEADER_NET_RUNTIME)
        if self._mutexes:
            parts.append('/* ── Mutexes ── */')
            parts.append('\n'.join(
                f'static pthread_mutex_t __mtx_{m} = PTHREAD_MUTEX_INITIALIZER;'
                for m in sorted(self._mutexes)))
        if self.uses_network:
            parts.append('static int __active_conn = -1;  '
                         '/* conexao corrente p/ proxies remotos */')

        # structs de classes (inclui forward-decls de métodos)
        if self._structs:
            parts.append('/* ── Classes ── */')
            parts.append('\n'.join(self._structs))

        # protótipos de funções (exceto métodos/ctor já declarados acima)
        protos = []
        for name, (ret, params) in self._sig.items():
            if name == 'main' or self._method_entry(name):
                continue
            plist = ', '.join(ct for _, ct in params) or 'void'
            protos.append(f'static {ret} {name}({plist});')
        if protos:
            parts.append('/* ── Protótipos ── */')
            parts.append('\n'.join(protos))

        if self._thread_helpers:
            parts.append('/* ── Threads: structs + wrappers de spawn ── */')
            parts.append('\n'.join(self._thread_helpers))

        # vtables (referenciam os métodos já declarados)
        if self._vtables:
            parts.append('/* ── VTables ── */')
            parts.append('\n'.join(self._vtables))

        if self._global_vars:
            parts.append('/* ── Globais ── */')
            parts.append('\n'.join(f'{ct} {nome};'
                                   for nome, ct in self._global_vars.items()))

        if self._functions:
            parts.append('/* ── Funções ── */')
            parts.append('\n'.join(self._functions))

        # main
        main_lines = ['int main(void) {']
        for var, ct in self._main_decls:
            main_lines.append(f'    {ct} {var};')
        main_lines.extend('    ' + s for s in self._main_stmts)
        main_lines.append('    return 0;')
        main_lines.append('}')
        parts.append('\n'.join(main_lines))

        return '\n'.join(parts)

    # ─────────────────────────────────────────────────────────────────────────
    # Tipos / utilitários
    # ─────────────────────────────────────────────────────────────────────────

    def _c_type(self, mp_type: str) -> str:
        return {
            'number': 'double', 'string': 'char*', 'bool': 'int',
            'void': 'void', 'any': 'double', 'null': 'void*',
        }.get(mp_type, f'{mp_type}*')   # tipos de classe → ponteiro (Passo 2)

    def _value_ctype(self, val: Optional[str]) -> str:
        if val is None:
            return 'double'
        if self._is_string(val):
            return 'char*'
        if self._is_number(val):
            return 'double'
        if val == 'null':
            return 'void*'
        return self._ctype.get(val, 'double')

    def _is_string(self, val: Optional[str]) -> bool:
        """True se o valor é uma string em C (literal "..." ou variável char*)."""
        if val is None:
            return False
        if self._is_string_literal(val):
            return True
        return self._ctype.get(val, '') in ('char*', 'const char*')

    @staticmethod
    def _is_string_literal(val: str) -> bool:
        return len(val) >= 2 and val.startswith('"') and val.endswith('"')

    @staticmethod
    def _is_number(val: str) -> bool:
        try:
            float(val); return True
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _is_temp(name: str) -> bool:
        return len(name) >= 2 and name[0] == 't' and name[1:].isdigit()

    @staticmethod
    def _mtx_id(name: Optional[str]) -> str:
        return ''.join(ch if ch.isalnum() else '_' for ch in (name or 'lock'))

    def _is_literal(self, name: str) -> bool:
        """Literal *sintático* (constante), não variável tipada como string."""
        return self._is_number(name) or self._is_string_literal(name) or name == 'null'