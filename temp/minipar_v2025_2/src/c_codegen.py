"""
c_codegen.py — MiniPar v2025.2
Backend C: traduz TAC para código C compilável pelo GCC.
Suporta POO (structs + vtables), paralelismo (pthreads) e
execução distribuída (sockets POSIX + cJSON).
"""

from codegen import TAC, TACOp
from symbol_table import SymbolTable, ClassDescriptor
from typing import List, Optional, Set, Dict


# ─────────────────────────────────────────────────────────────────────────────
# Cabeçalhos C gerados automaticamente
# ─────────────────────────────────────────────────────────────────────────────

HEADER_BASE = """\
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
"""

HEADER_PARALLEL = """\
#include <pthread.h>

typedef struct {
    void (*func)(void*);
    void* args;
} __thread_ctx_t;

static void* __thread_wrapper(void* ctx) {
    __thread_ctx_t* c = (__thread_ctx_t*) ctx;
    c->func(c->args);
    free(ctx);
    return NULL;
}
"""

HEADER_NETWORK = """\
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <cjson/cJSON.h>

static int __minipar_connect(const char* host, int port) {
    struct sockaddr_in addr;
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    addr.sin_family = AF_INET;
    addr.sin_port   = htons(port);
    inet_pton(AF_INET, host, &addr.sin_addr);
    if (connect(fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) return -1;
    return fd;
}

static char* __minipar_rpc(int fd, const char* func_name, cJSON* args) {
    cJSON* req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "func", func_name);
    cJSON_AddItemToObject(req, "args", args);
    char* payload = cJSON_PrintUnformatted(req);
    uint32_t len = htonl((uint32_t)strlen(payload));
    send(fd, &len, 4, 0);
    send(fd, payload, strlen(payload), 0);
    free(payload);
    cJSON_Delete(req);
    uint32_t resp_len;
    recv(fd, &resp_len, 4, MSG_WAITALL);
    resp_len = ntohl(resp_len);
    char* buf = (char*) malloc(resp_len + 1);
    recv(fd, buf, resp_len, MSG_WAITALL);
    buf[resp_len] = '\\0';
    return buf;
}

static int __active_conn = -1;
"""

HEADER_RUNTIME = """\
/* Runtime MiniPar — tipos auxiliares */
typedef union {
    double  as_number;
    char*   as_string;
    int     as_bool;
    void*   as_ptr;
} minipar_any_t;

typedef struct { void** data; int len; int cap; } minipar_list_t;
typedef struct { void** keys; void** vals; int len; } minipar_dict_t;

static void minipar_print(const char* fmt, ...) {
    /* wrapper simplificado de print */
    printf("%s\\n", fmt);
}
"""


class CCodeGenerator:
    """
    Traduz uma lista de instruções TAC para um arquivo .c completo,
    pronto para ser compilado pelo GCC.
    """

    def __init__(self, symbol_table: SymbolTable):
        self.table = symbol_table

        # Seções do arquivo C
        self._structs:    List[str] = []   # typedef struct { ... }
        self._vtables:    List[str] = []   # static void* __vtable_X[] = { ... }
        self._globals:    List[str] = []   # variáveis e mutexes globais
        self._functions:  List[str] = []   # funções geradas
        self._main_body:  List[str] = []   # corpo do main()

        # Estado interno
        self._emitted_structs:   Set[str] = set()
        self._emitted_vtables:   Set[str] = set()
        self._emitted_mutexes:   Set[str] = set()
        self._in_function:       bool = False
        self._current_func_lines: List[str] = []
        self._current_func_name:  str = ''
        self._pending_params:    List[str] = []     # PARAM pendentes para CALL
        self._pending_rparams:   List[str] = []     # PARAM_REMOTE pendentes
        self._pending_tparams:   List[str] = []     # PARAM_THREAD pendentes
        self._par_stack:         List[tuple] = []   # (array_name, count_var)
        self._par_counter:       int = 0
        self._var_types:         Dict[str, str] = {}  # nome → tipo C

        # Feature flags (detectadas durante a passagem)
        self.uses_parallel   = False
        self.uses_network    = False
        self.uses_oop        = False

    # ─────────────────────────────────────────────────────────────────────────
    # API pública
    # ─────────────────────────────────────────────────────────────────────────

    def generate(self, tac_code: List[TAC]) -> str:
        """
        Recebe lista de instruções TAC e retorna o código C completo como string.
        """
        # Pré-passagem: detectar features usadas
        self._detect_features(tac_code)

        # Emitir todas as structs/vtables de classes conhecidas
        if self.uses_oop:
            self._emit_all_class_definitions()

        # Traduzir instruções TAC
        for instr in tac_code:
            self._translate(instr)

        # Fechar função pendente se necessário
        if self._in_function:
            self._close_function()

        return self._assemble_file()

    # ─────────────────────────────────────────────────────────────────────────
    # Pré-passagem: detectar features
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_features(self, tac_code: List[TAC]):
        par_ops = {TACOp.PAR_BEGIN, TACOp.SPAWN_THREAD, TACOp.MUTEX_INIT,
                   TACOp.THREAD_JOIN, TACOp.ASYNC_BEGIN}
        net_ops = {TACOp.CONNECT_NODE, TACOp.RPC_CALL,
                   TACOp.SERIALIZE, TACOp.DESERIALIZE, TACOp.REMOTE_SPAWN}
        oop_ops = {TACOp.ALLOC_OBJ, TACOp.VCALL, TACOp.LOAD_VTABLE,
                   TACOp.STORE_FIELD, TACOp.LOAD_FIELD, TACOp.STATIC_CALL}
        for instr in tac_code:
            if instr.op in par_ops:  self.uses_parallel = True
            if instr.op in net_ops:  self.uses_network  = True
            if instr.op in oop_ops:  self.uses_oop      = True

    # ─────────────────────────────────────────────────────────────────────────
    # Definições de classes (structs + vtables)
    # ─────────────────────────────────────────────────────────────────────────

    def _emit_all_class_definitions(self):
        """Emite structs e vtables para todas as classes no registry."""
        # Ordenar topologicamente (superclasse primeiro)
        ordered = self._topo_sort_classes()
        for class_name in ordered:
            self._emit_struct(class_name)
        # Vtables depois (referenciam nomes de funções já declarados)
        for class_name in ordered:
            self._emit_vtable_global(class_name)

    def _topo_sort_classes(self) -> List[str]:
        """Ordena classes colocando superclasses antes de subclasses."""
        visited = []
        def visit(name):
            if name in visited:
                return
            desc = self.table.lookup_class(name)
            if desc and desc.superclasse:
                visit(desc.superclasse.nome)
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
        lines = [f'/* Classe {class_name} */']
        lines.append(f'typedef struct {class_name} {{')
        lines.append(f'    void** __vtable;  /* ponteiro para VTable */')
        for f in desc._fields:
            c_type = self._mp_type_to_c(f.tipo_dados)
            lines.append(f'    {c_type} {f.nome};')
        lines.append(f'}} {class_name};')
        lines.append('')
        self._structs.extend(lines)

    def _emit_vtable_global(self, class_name: str):
        if class_name in self._emitted_vtables:
            return
        self._emitted_vtables.add(class_name)
        desc = self.table.lookup_class(class_name)
        if not desc or not desc._vtable:
            return
        # Forward-declare os métodos antes da vtable
        for entry in desc._vtable:
            ret = self._mp_type_to_c(entry.return_type)
            self._structs.append(f'static {ret} {entry.label}();  /* forward decl */')
        lines = [f'/* VTable de {class_name} */']
        lines.append(f'static void* __vtable_{class_name}[] = {{')
        for entry in desc._vtable:
            lines.append(f'    (void*) {entry.label},')
        lines.append('};')
        lines.append('')
        self._vtables.extend(lines)

    # ─────────────────────────────────────────────────────────────────────────
    # Tradução de instruções TAC
    # ─────────────────────────────────────────────────────────────────────────

    def _translate(self, instr: TAC):
        op = instr.op

        # ── Controle de funções ───────────────────────────────────────────────
        if op == TACOp.FUNC_BEGIN:
            self._open_function(instr.arg1)
            return
        if op == TACOp.FUNC_END:
            self._close_function()
            return

        # Destino das linhas: dentro de função ou no main
        dest = self._current_func_lines if self._in_function else self._main_body

        # ── Existentes v2025.1 ────────────────────────────────────────────────
        if op == TACOp.ASSIGN:
            c_type = self._infer_c_type(instr.arg1)
            dest.append(f'    {c_type} {instr.result} = {instr.arg1};')

        elif op in (TACOp.ADD, TACOp.SUB, TACOp.MUL, TACOp.DIV, TACOp.MOD,
                    TACOp.EQ,  TACOp.NEQ, TACOp.LT,  TACOp.GT,
                    TACOp.LTE, TACOp.GTE, TACOp.AND, TACOp.OR):
            c_op = {
                TACOp.ADD:'+'  , TACOp.SUB:'-'  , TACOp.MUL:'*'  ,
                TACOp.DIV:'/'  , TACOp.MOD:'%'  ,
                TACOp.EQ: '==' , TACOp.NEQ:'!=' ,
                TACOp.LT: '<'  , TACOp.GT: '>'  ,
                TACOp.LTE:'<=' , TACOp.GTE:'>=' ,
                TACOp.AND:'&&' , TACOp.OR: '||' ,
            }[op]
            c_type = self._infer_c_type(instr.arg1)
            dest.append(
                f'    {c_type} {instr.result} = {instr.arg1} {c_op} {instr.arg2};'
            )

        elif op == TACOp.NOT:
            dest.append(f'    int {instr.result} = !{instr.arg1};')

        elif op == TACOp.NEG:
            c_type = self._infer_c_type(instr.arg1)
            dest.append(f'    {c_type} {instr.result} = -{instr.arg1};')

        elif op == TACOp.LABEL:
            dest.append(f'    {instr.arg1}:;')

        elif op == TACOp.GOTO:
            dest.append(f'    goto {instr.result};')

        elif op == TACOp.IF_FALSE:
            dest.append(f'    if (!({instr.arg1})) goto {instr.result};')

        elif op == TACOp.PARAM:
            self._pending_params.append(instr.arg1)

        elif op == TACOp.CALL:
            func_name = instr.arg1
            args_str  = ', '.join(self._pending_params)
            self._pending_params = []
            c_type = 'double'   # padrão; refinado pelo semântico em projetos maiores
            if instr.result:
                dest.append(f'    {c_type} {instr.result} = {func_name}({args_str});')
            else:
                dest.append(f'    {func_name}({args_str});')

        elif op == TACOp.RETURN:
            if instr.arg1:
                dest.append(f'    return {instr.arg1};')
            else:
                dest.append('    return;')

        # ── POO ───────────────────────────────────────────────────────────────

        elif op == TACOp.ALLOC_OBJ:
            class_name = instr.arg1
            size       = instr.arg2
            result     = instr.result
            self._emit_struct(class_name)
            dest.append(
                f'    {class_name}* {result} = '
                f'({class_name}*) malloc({size});'
            )

        elif op == TACOp.SET_VTABLE:
            ptr   = instr.arg1
            vtlbl = instr.arg2
            dest.append(f'    {ptr}->__vtable = (void**) {vtlbl};')

        elif op == TACOp.INIT_FIELD:
            ptr    = instr.arg1
            offset = instr.arg2
            size   = instr.result
            dest.append(
                f'    memset((char*){ptr} + {offset}, 0, {size});'
                f'  /* INIT_FIELD offset={offset} */'
            )

        elif op == TACOp.STORE_FIELD:
            ptr    = instr.arg1
            val    = instr.arg2
            offset = instr.result
            # Tentar usar nome de campo em vez de offset bruto
            field_name = self._offset_to_field_name(ptr, offset)
            if field_name:
                dest.append(f'    {ptr}->{field_name} = {val};')
            else:
                dest.append(
                    f'    *((double*)((char*){ptr} + {offset})) = {val};'
                    f'  /* STORE_FIELD offset={offset} */'
                )

        elif op == TACOp.LOAD_FIELD:
            ptr    = instr.arg1
            offset = instr.arg2
            result = instr.result
            field_name = self._offset_to_field_name(ptr, offset)
            if field_name:
                dest.append(f'    double {result} = {ptr}->{field_name};')
            else:
                dest.append(
                    f'    double {result} = '
                    f'*((double*)((char*){ptr} + {offset}));'
                )

        elif op == TACOp.LOAD_VTABLE:
            ptr    = instr.arg1
            result = instr.result
            dest.append(f'    void** {result} = {ptr}->__vtable;')

        elif op == TACOp.VCALL:
            vtbl   = instr.arg1
            idx    = instr.arg2
            result = instr.result
            args_str = ', '.join(self._pending_params)
            self._pending_params = []
            dest.append(
                f'    double {result} = '
                f'((double(*)(void*)) {vtbl}[{idx}])({args_str});'
            )

        elif op == TACOp.STATIC_CALL:
            label  = instr.arg1
            result = instr.result
            args_str = ', '.join(self._pending_params)
            self._pending_params = []
            dest.append(f'    double {result} = {label}({args_str});')

        # ── Paralelismo ───────────────────────────────────────────────────────

        elif op == TACOp.PAR_BEGIN:
            self.uses_parallel = True
            arr  = f'__par_threads_{self._par_counter}'
            cnt  = f'__par_count_{self._par_counter}'
            self._par_counter += 1
            self._par_stack.append((arr, cnt))
            dest.append(f'    pthread_t {arr}[64];')
            dest.append(f'    int {cnt} = 0;')

        elif op == TACOp.PAR_END:
            if self._par_stack:
                arr, cnt = self._par_stack.pop()
                dest.append(f'    /* PAR_END: join de todas as threads */')
                dest.append(f'    for (int __i = 0; __i < {cnt}; __i++)')
                dest.append(f'        pthread_join({arr}[__i], NULL);')

        elif op == TACOp.PARAM_THREAD:
            self._pending_tparams.append(instr.arg1)

        elif op == TACOp.SPAWN_THREAD:
            self.uses_parallel = True
            func_label = instr.arg1
            result     = instr.result
            args       = list(self._pending_tparams)
            self._pending_tparams = []

            ctx = f'__ctx_{result}' if result else '__ctx_anon'
            dest.append(f'    __thread_ctx_t* {ctx} = malloc(sizeof(__thread_ctx_t));')
            dest.append(f'    {ctx}->func = (void(*)(void*)) {func_label};')
            dest.append(f'    {ctx}->args = NULL;')

            if self._par_stack:
                arr, cnt = self._par_stack[-1]
                dest.append(
                    f'    pthread_create(&{arr}[{cnt}++], NULL, __thread_wrapper, {ctx});'
                )
                if result:
                    dest.append(f'    pthread_t {result} = {arr}[{cnt}-1];')
            else:
                dest.append(f'    pthread_t {result};' if result else '')
                dest.append(
                    f'    pthread_create(&{result}, NULL, __thread_wrapper, {ctx});'
                )

        elif op == TACOp.THREAD_JOIN:
            dest.append(f'    pthread_join({instr.arg1}, NULL);')

        elif op == TACOp.MUTEX_INIT:
            lock = instr.arg1
            if lock not in self._emitted_mutexes:
                self._emitted_mutexes.add(lock)
                self._globals.append(
                    f'pthread_mutex_t __mutex_{lock} = PTHREAD_MUTEX_INITIALIZER;'
                )

        elif op == TACOp.MUTEX_LOCK:
            dest.append(f'    pthread_mutex_lock(&__mutex_{instr.arg1});')

        elif op == TACOp.MUTEX_UNLOCK:
            dest.append(f'    pthread_mutex_unlock(&__mutex_{instr.arg1});')

        elif op == TACOp.ASYNC_BEGIN:
            # No backend C, funções async são funções normais;
            # o future é o handle de thread
            dest.append(f'    /* ASYNC {instr.arg1} — executando sincrono */')

        elif op == TACOp.AWAIT_FUTURE:
            dest.append(f'    /* AWAIT {instr.arg1} */')
            if instr.result:
                dest.append(f'    double {instr.result} = 0;  /* resultado do future */')

        # ── Distribuição ──────────────────────────────────────────────────────

        elif op == TACOp.CONNECT_NODE:
            self.uses_network = True
            addr_raw = instr.arg1.strip('"')
            result   = instr.result
            if ':' in addr_raw:
                host, port = addr_raw.rsplit(':', 1)
                dest.append(
                    f'    int {result} = __minipar_connect("{host}", {port});'
                )
            else:
                dest.append(f'    int {result} = -1;  /* endereço inválido */')

        elif op == TACOp.SERIALIZE:
            self.uses_network = True
            val    = instr.arg1
            result = instr.result
            # Tenta inferir tipo; padrão: number
            dest.append(f'    cJSON* {result} = cJSON_CreateNumber({val});')

        elif op == TACOp.PARAM_REMOTE:
            self._pending_rparams.append(instr.arg1)

        elif op == TACOp.RPC_CALL:
            self.uses_network = True
            conn      = instr.arg1
            func_name = instr.arg2.strip('"')
            result    = instr.result
            arr_name  = f'__rpc_args_{result}'
            dest.append(f'    cJSON* {arr_name} = cJSON_CreateArray();')
            for buf in self._pending_rparams:
                dest.append(f'    cJSON_AddItemToArray({arr_name}, {buf});')
            self._pending_rparams = []
            dest.append(
                f'    char* __rpc_resp_{result} = '
                f'__minipar_rpc({conn}, "{func_name}", {arr_name});'
            )
            dest.append(
                f'    cJSON* {result} = cJSON_Parse(__rpc_resp_{result});'
            )
            dest.append(f'    free(__rpc_resp_{result});')

        elif op == TACOp.DESERIALIZE:
            buf    = instr.arg1
            tipo   = instr.arg2.strip('"')
            result = instr.result
            if tipo == 'number':
                dest.append(
                    f'    double {result} = cJSON_GetNumberValue({buf});'
                )
            elif tipo == 'string':
                dest.append(
                    f'    char* {result} = cJSON_GetStringValue({buf});'
                )
            elif tipo == 'bool':
                dest.append(
                    f'    int {result} = cJSON_IsTrue({buf});'
                )
            else:
                dest.append(
                    f'    void* {result} = NULL;  /* tipo "{tipo}" sem deserializer */'
                )

        elif op == TACOp.DISCONNECT_NODE:
            dest.append(f'    close({instr.arg1});')

        elif op == TACOp.REMOTE_SPAWN:
            self.uses_network = True
            conn         = instr.arg1
            block_label  = instr.arg2
            result       = instr.result
            dest.append(f'    /* REMOTE_SPAWN: {block_label} em {conn} */')
            dest.append(
                f'    cJSON* __rs_args_{result} = cJSON_CreateObject();'
            )
            dest.append(
                f'    cJSON_AddStringToObject('
                f'__rs_args_{result}, "block", "{block_label}");'
            )
            dest.append(
                f'    char* __rs_resp_{result} = '
                f'__minipar_rpc({conn}, "__spawn", __rs_args_{result});'
            )
            if result:
                dest.append(f'    int {result} = (__rs_resp_{result} != NULL);')

        # Opcodes de VTable (apenas dados, não geram código inline)
        elif op in (TACOp.LABEL,):
            dest.append(f'    {instr.arg1}:;')

    # ─────────────────────────────────────────────────────────────────────────
    # Gerenciamento de funções
    # ─────────────────────────────────────────────────────────────────────────

    def _open_function(self, name: str):
        self._in_function = True
        self._current_func_name = name
        self._current_func_lines = []
        self._pending_params = []

    def _close_function(self):
        name  = self._current_func_name
        lines = self._current_func_lines

        # Determinar assinatura: construtor retorna ponteiro, main retorna int
        if name == 'main':
            sig = 'int main(void)'
        elif name.endswith('___ctor'):
            class_name = name[:-7]   # remove '___ctor'
            sig = f'static void* {name}()'
        elif name.startswith('__impl_') or name.startswith('__par_block') \
                or name.startswith('__spawn_block') \
                or name.startswith('__remote_block'):
            sig = f'static void {name}(void* __args)'
        else:
            # Função genérica — retorno double por padrão
            sig = f'static double {name}()'

        func_code = [f'{sig} {{']
        func_code.extend(lines)
        func_code.append('}')
        func_code.append('')
        self._functions.extend(func_code)

        self._in_function = False
        self._current_func_lines = []
        self._current_func_name = ''

    # ─────────────────────────────────────────────────────────────────────────
    # Montagem do arquivo C completo
    # ─────────────────────────────────────────────────────────────────────────

    def _assemble_file(self) -> str:
        parts = []

        # 1. Cabeçalhos
        parts.append('/* Gerado automaticamente pelo compilador MiniPar v2025.2 */')
        parts.append(HEADER_BASE)
        parts.append(HEADER_RUNTIME)
        if self.uses_parallel:
            parts.append(HEADER_PARALLEL)
        if self.uses_network:
            parts.append(HEADER_NETWORK)

        # 2. Structs de classes
        if self._structs:
            parts.append('/* ── Definições de classes ── */')
            parts.append('\n'.join(self._structs))

        # 3. VTables
        if self._vtables:
            parts.append('/* ── VTables ── */')
            parts.append('\n'.join(self._vtables))

        # 4. Variáveis globais e mutexes
        if self._globals:
            parts.append('/* ── Globais ── */')
            parts.append('\n'.join(self._globals))
            parts.append('')

        # 5. Funções
        if self._functions:
            parts.append('/* ── Funções ── */')
            parts.append('\n'.join(self._functions))

        # 6. main()
        if self._main_body:
            parts.append('int main(void) {')
            parts.extend(self._main_body)
            parts.append('    return 0;')
            parts.append('}')

        return '\n'.join(parts)

    # ─────────────────────────────────────────────────────────────────────────
    # Utilitários
    # ─────────────────────────────────────────────────────────────────────────

    def _mp_type_to_c(self, tipo: str) -> str:
        """Converte tipo MiniPar para tipo C."""
        return {
            'number': 'double',
            'string': 'char*',
            'bool':   'int',
            'void':   'void',
            'any':    'minipar_any_t',
            'list':   'minipar_list_t*',
            'dict':   'minipar_dict_t*',
            'null':   'void*',
        }.get(tipo, f'{tipo}*')   # tipos de classe → ponteiro

    def _infer_c_type(self, val: Optional[str]) -> str:
        """Inferência básica de tipo C a partir do valor TAC."""
        if val is None:
            return 'double'
        if val.startswith('"') and val.endswith('"'):
            return 'const char*'
        try:
            float(val)
            return 'double'
        except (ValueError, TypeError):
            pass
        if val in ('0', '1', 'null'):
            return 'int'
        return 'double'   # padrão seguro

    def _offset_to_field_name(self, ptr: str, offset: str) -> Optional[str]:
        """
        Tenta encontrar o nome do campo a partir do offset.
        Consulta o ClassDescriptor associado ao ponteiro.
        """
        # Heurística: percorrer todos os ClassDescriptors procurando o offset
        try:
            off_int = int(offset)
        except (ValueError, TypeError):
            return None
        for desc in self.table.class_registry.values():
            for f in desc._fields:
                if f.offset == off_int:
                    return f.nome
        return None
