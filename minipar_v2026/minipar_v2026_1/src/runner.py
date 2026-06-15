"""
runner.py — MiniPar v2026.1
Executor em tempo de execução: interpreta a AST diretamente sem gerar código.
Suporta todos os construtos da v2026.1 e os novos de v2026.1
(classes, herança, spawn/sync simulados, remote como stub local).
"""

from ast_nodes import *
from symbol_table import SymbolTable, ClassDescriptor, SymbolType
from typing import Any, Dict, List, Optional
import threading
import sys


# ─────────────────────────────────────────────────────────────────────────────
# Exceções de controle de fluxo
# ─────────────────────────────────────────────────────────────────────────────

class ReturnException(Exception):
    def __init__(self, value): self.value = value

class BreakException(Exception):    pass
class ContinueException(Exception): pass


# ─────────────────────────────────────────────────────────────────────────────
# Objetos de classe em tempo de execução
# ─────────────────────────────────────────────────────────────────────────────

class MiniParObject:
    """
    Instância de uma classe MiniPar em tempo de execução.
    Armazena campos em um dicionário e carrega os métodos
    do ClassDescriptor correspondente.
    """
    def __init__(self, class_name: str, desc: ClassDescriptor):
        self.__class_name = class_name
        self.__desc = desc
        self.__fields: Dict[str, Any] = {}
        # Inicializar campos com valores padrão percorrendo toda a cadeia de herança
        # (superclasse primeiro, subclasse depois — subclasse pode sobrescrever)
        chain = []
        d = desc
        while d:
            chain.append(d)
            d = d.superclasse
        for ancestor in reversed(chain):
            for f in ancestor._fields:
                if f.nome not in self.__fields:
                    self.__fields[f.nome] = self._default(f.tipo_dados)

    def get_field(self, nome: str) -> Any:
        # Busca no objeto e nas superclasses (campos herdados)
        if nome in self.__fields:
            return self.__fields[nome]
        raise AttributeError(f"Campo '{nome}' não existe em '{self.__class_name}'")

    def set_field(self, nome: str, value: Any):
        self.__fields[nome] = value

    def get_class_name(self) -> str:
        return self.__class_name

    def get_descriptor(self) -> ClassDescriptor:
        return self.__desc

    @staticmethod
    def _default(tipo: str) -> Any:
        return {'number': 0.0, 'string': '', 'bool': False,
                'list': [], 'dict': {}, 'any': None}.get(tipo, None)

    def __repr__(self):
        return f'<{self.__class_name} {self.__fields}>'


# ─────────────────────────────────────────────────────────────────────────────
# Ambiente de execução (escopo de variáveis)
# ─────────────────────────────────────────────────────────────────────────────

class Environment:
    def __init__(self, parent: Optional['Environment'] = None):
        self._vars: Dict[str, Any] = {}
        self.parent = parent

    def get(self, name: str) -> Any:
        if name in self._vars:
            return self._vars[name]
        if self.parent:
            return self.parent.get(name)
        raise NameError(f"Variável '{name}' não definida")

    def set(self, name: str, value: Any):
        """Define no escopo mais próximo onde a variável existe."""
        if name in self._vars:
            self._vars[name] = value
        elif self.parent and self.parent._has(name):
            self.parent.set(name, value)
        else:
            self._vars[name] = value

    def define(self, name: str, value: Any):
        """Define sempre no escopo atual."""
        self._vars[name] = value

    def _has(self, name: str) -> bool:
        if name in self._vars:
            return True
        return self.parent._has(name) if self.parent else False


# ─────────────────────────────────────────────────────────────────────────────
# Funções built-in
# ─────────────────────────────────────────────────────────────────────────────

def _builtin_print(*args):
    print(' '.join(str(a) for a in args))

def _builtin_input(*args):
    prompt = args[0] if args else ''
    return input(str(prompt))

def _builtin_len(obj):
    if isinstance(obj, (list, dict, str)):
        return float(len(obj))
    return 0.0

def _builtin_str(val):
    return str(val)

def _builtin_num(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0

def _builtin_append(lst, val):
    if isinstance(lst, list):
        lst.append(val)

def _builtin_keys(d):
    return list(d.keys()) if isinstance(d, dict) else []

def _builtin_values(d):
    return list(d.values()) if isinstance(d, dict) else []


import math as _math

def _make_math_builtins():
    fns = {}
    unary = {
        'math_exp': _math.exp, 'math_sqrt': _math.sqrt, 'math_sin': _math.sin,
        'math_cos': _math.cos, 'math_tan': _math.tan, 'math_log': _math.log,
        'math_log10': _math.log10, 'math_floor': _math.floor,
        'math_ceil': _math.ceil, 'math_abs': abs,
    }
    for nome, f in unary.items():
        fns[nome] = (lambda g: (lambda x: float(g(float(x)))))(f)
    fns['math_pow'] = lambda b, e: float(float(b) ** float(e))
    fns['math_pi'] = lambda: _math.pi
    fns['math_e'] = lambda: _math.e
    return fns

# Gráficos turtle: delega ao módulo turtle do Python; em ambiente headless
# (sem display) degrada graciosamente para no-op, deixando o programa terminar.
_turtle_state = {'mod': None, 'failed': False}
def _turtle_call(method, *args):
    if _turtle_state['failed']:
        return None
    try:
        if _turtle_state['mod'] is None:
            import turtle as _t
            _turtle_state['mod'] = _t
        getattr(_turtle_state['mod'], method)(*args)
    except Exception:
        _turtle_state['failed'] = True
    return None

def _make_turtle_builtins():
    nomes = ['up', 'down', 'goto', 'forward', 'backward', 'left', 'right',
             'speed', 'hideturtle', 'showturtle', 'tracer', 'update', 'done',
             'fillcolor', 'pencolor', 'begin_fill', 'end_fill', 'penup',
             'pendown', 'setpos', 'circle', 'dot', 'width', 'clear', 'reset',
             'setheading', 'home', 'bgcolor', 'color', 'goto']
    fns = {}
    for n in nomes:
        fns['turtle_' + n] = (lambda m: (lambda *a: _turtle_call(m, *a)))(n)
    return fns


BUILTINS: Dict[str, Any] = {
    'print':   _builtin_print,
    'input':   _builtin_input,
    'len':     _builtin_len,
    'str':     _builtin_str,
    'num':     _builtin_num,
    'append':  _builtin_append,
    'keys':    _builtin_keys,
    'values':  _builtin_values,
}
BUILTINS.update(_make_math_builtins())
BUILTINS.update(_make_turtle_builtins())


# ─────────────────────────────────────────────────────────────────────────────
# Runner principal
# ─────────────────────────────────────────────────────────────────────────────

class Runner:
    """
    Interpretador de AST MiniPar v2026.1.
    Executa diretamente sem geração de código intermediário.
    """

    def __init__(self, symbol_table: Optional[SymbolTable] = None):
        self.symbol_table = symbol_table
        # Ambiente global com built-ins
        self._global_env = Environment()
        for name, fn in BUILTINS.items():
            self._global_env.define(name, fn)
        # Registro de funções/classes declaradas
        self._functions: Dict[str, Any] = {}   # nome → FuncDecl/AsyncFuncDecl
        self._classes:   Dict[str, ClassDecl] = {}
        self._nodes:     Dict[str, str] = {}    # nome → "ip:porta"
        self._remote_funcs: Dict[str, Any] = {}

    # ─────────────────────────────────────────────────────────────────────────
    # API pública
    # ─────────────────────────────────────────────────────────────────────────

    def run(self, program: Program):
        """Executa um programa MiniPar."""
        # Passagem 1: registrar declarações de alto nível
        for decl in program.declarations:
            if isinstance(decl, FuncDecl):
                self._functions[decl.nome] = decl
                self._global_env.define(decl.nome, decl)
            elif isinstance(decl, AsyncFuncDecl):
                self._functions[decl.nome] = decl
                self._global_env.define(decl.nome, decl)
            elif isinstance(decl, ClassDecl):
                self._classes[decl.nome] = decl
            elif isinstance(decl, NodeDecl):
                self._nodes[decl.nome] = decl.endereco
            elif isinstance(decl, RemoteFuncDecl):
                self._functions[decl.func_decl.nome] = decl.func_decl
                self._remote_funcs[decl.func_decl.nome] = decl.func_decl
                self._global_env.define(decl.func_decl.nome, decl.func_decl)

        # Passagem 2: executar código de nível global diretamente no _global_env
        # Isso garante que variáveis globais (ex: 'contador', 'lock') sejam
        # visíveis para funções chamadas de dentro de par{}, sync{}, spawn, etc.
        for decl in program.declarations:
            if not isinstance(decl, (FuncDecl, AsyncFuncDecl, ClassDecl,
                                     NodeDecl, RemoteFuncDecl)):
                try:
                    self._exec(decl, self._global_env)
                except ReturnException:
                    pass

    # ─────────────────────────────────────────────────────────────────────────
    # Execução de nós
    # ─────────────────────────────────────────────────────────────────────────

    def _exec(self, node, env: Environment):
        method = f'_exec_{type(node).__name__}'
        handler = getattr(self, method, None)
        if handler:
            return handler(node, env)
        # Nós desconhecidos: tentar avaliar como expressão
        return self._eval(node, env)

    # ── Statements existentes v2026.1 ─────────────────────────────────────────

    def _exec_VarDecl(self, node: VarDecl, env: Environment):
        value = self._eval(node.inicializador, env) if node.inicializador else None
        env.define(node.nome, value)

    def _exec_Block(self, node: Block, env: Environment):
        local_env = Environment(parent=env)
        for stmt in node.statements:
            self._exec(stmt, local_env)

    def _exec_IfStmt(self, node: IfStmt, env: Environment):
        cond = self._eval(node.condicao, env)
        if self._truthy(cond):
            self._exec(node.corpo_se, Environment(parent=env))
        elif node.corpo_senao:
            self._exec(node.corpo_senao, Environment(parent=env))

    def _exec_WhileStmt(self, node: WhileStmt, env: Environment):
        while self._truthy(self._eval(node.condicao, env)):
            try:
                self._exec(node.corpo, Environment(parent=env))
            except BreakException:
                break
            except ContinueException:
                continue

    def _exec_ReturnStmt(self, node: ReturnStmt, env: Environment):
        value = self._eval(node.expressao, env) if node.expressao else None
        raise ReturnException(value)

    def _exec_BreakStmt(self, node: BreakStmt, env: Environment):
        raise BreakException()

    def _exec_ContinueStmt(self, node: ContinueStmt, env: Environment):
        raise ContinueException()

    def _exec_AssignStmt(self, node: AssignStmt, env: Environment):
        value = self._eval(node.expressao, env)
        if isinstance(node.alvo, str):
            # Se 'this' está no escopo e o nome é um campo do objeto,
            # redireciona para o objeto (permite 'x = val' em vez de 'this.x = val')
            try:
                this_obj = env.get('this')
                if isinstance(this_obj, MiniParObject):
                    try:
                        this_obj.get_field(node.alvo)   # existe o campo?
                        this_obj.set_field(node.alvo, value)
                        return
                    except AttributeError:
                        pass   # não é campo — atribuição normal de variável
            except NameError:
                pass
            env.set(node.alvo, value)
        elif isinstance(node.alvo, MemberAccessExpr):
            obj = self._eval(node.alvo.obj, env)
            if isinstance(obj, MiniParObject):
                obj.set_field(node.alvo.membro, value)
            else:
                raise RuntimeError(f"Atribuição a campo de não-objeto: {type(obj)}")
        elif isinstance(node.alvo, IndexAccess):
            container = self._eval(node.alvo.obj, env)
            idx = self._eval(node.alvo.indice, env)
            if isinstance(container, list):
                container[int(idx)] = value
            elif isinstance(container, dict):
                container[idx] = value
            else:
                raise RuntimeError(
                    f"Atribuição por índice inválida em {type(container)}")

    def _exec_ExprStmt(self, node: ExprStmt, env: Environment):
        self._eval(node.expr, env)

    def _exec_SeqBlock(self, node: SeqBlock, env: Environment):
        local = Environment(parent=env)
        for stmt in node.statements:
            self._exec(stmt, local)

    def _exec_ChannelDecl(self, node: ChannelDecl, env: Environment):
        # Canais v2026.1 — mantido como stub no runner
        pass

    # ── NOVO v2026.1: Paralelismo ──────────────────────────────────────────────

    def _exec_ParBlock(self, node: ParBlock, env: Environment):
        """
        Executa statements do bloco par em threads Python reais.
        Cada thread recebe o env do chamador como pai para enxergar
        variáveis globais e do escopo externo.
        """
        threads = []
        errors  = []
        lock    = threading.Lock()

        def run_stmt(stmt):
            # Cada thread tem seu próprio escopo filho do env externo
            local_env = Environment(parent=env)
            try:
                self._exec(stmt, local_env)
            except ReturnException:
                pass
            except Exception as e:
                with lock:
                    errors.append(e)

        for stmt in node.statements:
            t = threading.Thread(target=run_stmt, args=(stmt,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        if errors:
            raise errors[0]

    def _exec_SpawnStmt(self, node: SpawnStmt, env: Environment):
        """Cria thread para função ou bloco."""
        def run():
            try:
                if node.call_expr:
                    self._eval(node.call_expr, env)
                elif node.body:
                    self._exec(node.body, Environment(parent=env))
            except ReturnException:
                pass

        t = threading.Thread(target=run, daemon=True)
        t.start()
        # Armazenar handle não é necessário aqui; spawn é fire-and-forget

    def _exec_SyncStmt(self, node: SyncStmt, env: Environment):
        """Bloco sincronizado: usa um Lock Python associado à variável."""
        lock_key = f'__lock_{node.lock_var}'
        if not hasattr(self, '_locks'):
            self._locks: Dict[str, threading.Lock] = {}
        if lock_key not in self._locks:
            self._locks[lock_key] = threading.Lock()
        with self._locks[lock_key]:
            self._exec(node.corpo, Environment(parent=env))

    # ── NOVO v2026.1: Distribuição ────────────────────────────────────────────

    def _exec_NodeDecl(self, node: NodeDecl, env: Environment):
        self._nodes[node.nome] = node.endereco

    def _exec_RemoteCallStmt(self, node: RemoteCallStmt, env: Environment):
        """
        Executa chamada remota. No runner, tenta conectar via socket TCP real.
        Se não conseguir, executa a implementação local como fallback.
        """
        func_call = node.function_call
        if not isinstance(func_call, FunctionCall):
            return

        # Tentar execução via socket
        if isinstance(node.alvo, Identifier):
            addr = self._nodes.get(node.alvo.nome)
        else:
            addr = self._eval(node.alvo, env)

        if addr:
            result = self._rpc_call(addr, func_call.nome,
                                    [self._eval(a, env) for a in func_call.argumentos])
        else:
            # Fallback: executar localmente
            result = self._eval(func_call, env)

        if node.result_var:
            env.set(node.result_var, result)

    def _exec_RemoteSpawnStmt(self, node: RemoteSpawnStmt, env: Environment):
        """Executa bloco remotamente ou localmente como fallback."""
        # No runner: executa em thread local como simulação
        def run():
            try:
                self._exec(node.corpo, Environment(parent=env))
            except ReturnException:
                pass
        t = threading.Thread(target=run, daemon=True)
        t.start()

    def _rpc_call(self, address: str, func_name: str, args: list) -> Any:
        """
        Faz chamada RPC via TCP.
        Protocolo: JSON com length-prefix de 4 bytes (big-endian).
        """
        import socket, json, struct
        try:
            host, port_str = address.rsplit(':', 1)
            port = int(port_str)
            payload = json.dumps({'func': func_name, 'args': args}).encode()
            with socket.create_connection((host, port), timeout=5) as s:
                s.sendall(struct.pack('>I', len(payload)) + payload)
                raw_len = s.recv(4)
                if len(raw_len) < 4:
                    return None
                resp_len = struct.unpack('>I', raw_len)[0]
                resp_data = b''
                while len(resp_data) < resp_len:
                    chunk = s.recv(resp_len - len(resp_data))
                    if not chunk:
                        break
                    resp_data += chunk
                response = json.loads(resp_data.decode())
                return response.get('result')
        except (ConnectionRefusedError, OSError, json.JSONDecodeError) as e:
            # Fallback: executar localmente se a função existir
            if func_name in self._functions:
                fn_decl = self._functions[func_name]
                return self._call_function(fn_decl, args, Environment(parent=self._global_env))
            print(f'[runner] RPC falhou ({address}): {e}. Sem fallback local.', file=sys.stderr)
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Avaliação de expressões
    # ─────────────────────────────────────────────────────────────────────────

    def _eval(self, node, env: Environment) -> Any:
        if node is None:
            return None
        method = f'_eval_{type(node).__name__}'
        handler = getattr(self, method, None)
        if handler:
            return handler(node, env)
        raise RuntimeError(f'Runner: avaliador não definido para {type(node).__name__}')

    # ── Literais ──────────────────────────────────────────────────────────────

    def _eval_NumberLiteral(self, node: NumberLiteral, env): return node.valor
    def _eval_StringLiteral(self, node: StringLiteral, env): return node.valor
    def _eval_BoolLiteral(self, node: BoolLiteral, env):     return node.valor
    def _eval_NullLiteral(self, node: NullLiteral, env):     return None

    def _eval_ListLiteral(self, node: ListLiteral, env):
        return [self._eval(e, env) for e in node.elementos]

    def _eval_DictLiteral(self, node: DictLiteral, env):
        return {self._eval(k, env): self._eval(v, env) for k, v in node.pares}

    # ── Identificadores e acesso ──────────────────────────────────────────────

    def _eval_Identifier(self, node: Identifier, env):
        try:
            return env.get(node.nome)
        except NameError:
            pass
        # Fallback: campo do objeto 'this' (permite usar 'cor' em vez de 'this.cor')
        try:
            this_obj = env.get('this')
            if isinstance(this_obj, MiniParObject):
                try:
                    return this_obj.get_field(node.nome)
                except AttributeError:
                    pass
        except NameError:
            pass
        raise NameError(f"[linha {node.linha}] Variável '{node.nome}' não definida")

    def _eval_IndexAccess(self, node: IndexAccess, env):
        obj = self._eval(node.obj, env)
        idx = self._eval(node.indice, env)
        if isinstance(obj, list):
            return obj[int(idx)]
        if isinstance(obj, dict):
            return obj[idx]
        if isinstance(obj, str):
            return obj[int(idx)]
        raise RuntimeError(f"Indexação inválida em {type(obj)}")

    # ── Operadores ────────────────────────────────────────────────────────────

    def _eval_BinaryExpr(self, node: BinaryExpr, env):
        # Short-circuit para && e ||
        if node.operador == '&&':
            l = self._eval(node.esquerda, env)
            return l and self._eval(node.direita, env)
        if node.operador == '||':
            l = self._eval(node.esquerda, env)
            return l or self._eval(node.direita, env)
        l = self._eval(node.esquerda, env)
        r = self._eval(node.direita, env)
        op = node.operador
        if   op == '+':  return l + r
        elif op == '-':  return l - r
        elif op == '*':  return l * r
        elif op == '/':
            if r == 0:
                raise ZeroDivisionError(f"[linha {node.linha}] Divisão por zero")
            return l / r
        elif op == '%':  return l % r
        elif op == '==': return l == r
        elif op == '!=': return l != r
        elif op == '<':  return l < r
        elif op == '>':  return l > r
        elif op == '<=': return l <= r
        elif op == '>=': return l >= r
        raise RuntimeError(f"Operador desconhecido: {op}")

    def _eval_UnaryExpr(self, node: UnaryExpr, env):
        val = self._eval(node.operando, env)
        if node.operador == '!':  return not self._truthy(val)
        if node.operador == '-':  return -val
        raise RuntimeError(f"Operador unário desconhecido: {node.operador}")

    # ── Chamadas de função ────────────────────────────────────────────────────

    def _eval_FunctionCall(self, node: FunctionCall, env):
        # 1. Tentar no ambiente (built-ins e funções lambda)
        try:
            fn = env.get(node.nome)
        except NameError:
            fn = None

        args = [self._eval(a, env) for a in node.argumentos]

        # 2. Função nativa Python (built-in)
        if callable(fn) and not isinstance(fn, (FuncDecl, AsyncFuncDecl)):
            return fn(*args)

        # 3. FuncDecl registrada
        if isinstance(fn, (FuncDecl, AsyncFuncDecl)):
            return self._call_function(fn, args, env)

        # 4. Buscar no registro de funções
        if node.nome in self._functions:
            return self._call_function(self._functions[node.nome], args, env)

        raise NameError(f"[linha {node.linha}] Função '{node.nome}' não definida")

    def _call_function(self, decl, args: list, call_env: Environment) -> Any:
        """
        Executa um FuncDecl ou AsyncFuncDecl com os argumentos fornecidos.
        O escopo da função tem como pai o escopo global (não o do chamador),
        exceto para funções anônimas de thread que precisam do escopo externo.
        """
        params = decl.parametros if hasattr(decl, 'parametros') else []
        # Funções normais: escopo filho do global (fechamento léxico básico)
        local_env = Environment(parent=self._global_env)
        for param, arg in zip(params, args):
            local_env.define(param.nome, arg)
        try:
            self._exec(decl.corpo, local_env)
            return None
        except ReturnException as ret:
            return ret.value

    # ── NOVO v2026.1: POO ─────────────────────────────────────────────────────

    def _eval_NewExpr(self, node: NewExpr, env):
        class_decl = self._classes.get(node.class_name)
        if class_decl is None:
            raise RuntimeError(f"[linha {node.linha}] Classe '{node.class_name}' não definida")

        # Construir ClassDescriptor para o objeto (usa o do symbol_table se disponível)
        if self.symbol_table:
            desc = self.symbol_table.lookup_class(node.class_name)
        else:
            desc = self._build_runtime_descriptor(class_decl)

        obj = MiniParObject(node.class_name, desc)
        args = [self._eval(a, env) for a in node.argumentos]

        # Executar construtor se existir
        if class_decl.construtor:
            ctor_env = Environment(parent=self._global_env)
            ctor_env.define('this', obj)
            for param, arg in zip(class_decl.construtor.parametros, args):
                ctor_env.define(param.nome, arg)
            try:
                self._exec(class_decl.construtor.corpo, ctor_env)
            except ReturnException:
                pass

        return obj

    def _eval_MemberAccessExpr(self, node: MemberAccessExpr, env):
        obj = self._eval(node.obj, env)
        if isinstance(obj, MiniParObject):
            return obj.get_field(node.membro)
        raise RuntimeError(
            f"[linha {node.linha}] Acesso a membro em não-objeto: {type(obj)}"
        )

    def _eval_MethodCallExpr(self, node: MethodCallExpr, env):
        obj = self._eval(node.obj, env)

        # Métodos nativos de listas/dicionários (sintaxe x.metodo(...)).
        if isinstance(obj, list):
            args = [self._eval(a, env) for a in node.argumentos]
            m = node.metodo
            if m == 'append':
                obj.append(args[0]); return None
            if m == 'pop':
                return obj.pop(int(args[0])) if args else obj.pop()
            if m in ('size', 'length', 'len'):
                return float(len(obj))
            if m == 'insert':
                obj.insert(int(args[0]), args[1]); return None
            if m == 'remove':
                if args[0] in obj: obj.remove(args[0])
                return None
            if m == 'contains':
                return args[0] in obj
            if m == 'clear':
                obj.clear(); return None
            raise RuntimeError(
                f"[linha {node.linha}] Método de lista '{m}' não suportado")
        if isinstance(obj, dict):
            args = [self._eval(a, env) for a in node.argumentos]
            m = node.metodo
            if m == 'keys':   return list(obj.keys())
            if m == 'values': return list(obj.values())
            if m in ('size', 'length', 'len'): return float(len(obj))
            if m == 'get':    return obj.get(args[0])
            return None

        if not isinstance(obj, MiniParObject):
            # Canais e outros valores dinâmicos: no-op gracioso (avalia args).
            for a in node.argumentos:
                self._eval(a, env)
            return None

        # Buscar método na hierarquia de classes
        method_decl = self._find_method(obj.get_class_name(), node.metodo)
        if method_decl is None:
            raise RuntimeError(
                f"[linha {node.linha}] Método '{node.metodo}' não encontrado "
                f"em '{obj.get_class_name()}'"
            )

        args = [self._eval(a, env) for a in node.argumentos]
        method_env = Environment(parent=self._global_env)
        method_env.define('this', obj)

        # Disponibilizar 'super' se há herança
        class_decl = self._classes.get(obj.get_class_name())
        if class_decl and class_decl.superclasse:
            super_proxy = _SuperProxy(obj, class_decl.superclasse, self)
            method_env.define('super', super_proxy)

        for param, arg in zip(method_decl.parametros, args):
            method_env.define(param.nome, arg)

        try:
            self._exec(method_decl.corpo, method_env)
            return None
        except ReturnException as ret:
            return ret.value

    def _eval_ThisExpr(self, node: ThisExpr, env):
        try:
            return env.get('this')
        except NameError:
            raise RuntimeError(f"[linha {node.linha}] 'this' fora de contexto de classe")

    def _eval_SuperCallExpr(self, node: SuperCallExpr, env):
        try:
            super_proxy = env.get('super')
        except NameError:
            raise RuntimeError(f"[linha {node.linha}] 'super' fora de contexto de herança")
        if isinstance(super_proxy, _SuperProxy):
            return super_proxy.call(node.metodo,
                                    [self._eval(a, env) for a in node.argumentos])
        raise RuntimeError("'super' inválido")

    def _eval_AwaitExpr(self, node: AwaitExpr, env):
        # No runner, await avalia a expressão normalmente (síncrono)
        return self._eval(node.future_expr, env)

    # ─────────────────────────────────────────────────────────────────────────
    # Utilitários
    # ─────────────────────────────────────────────────────────────────────────

    def _find_method(self, class_name: str,
                     method_name: str) -> Optional[MethodDecl]:
        """Busca método na classe e recursivamente nas superclasses."""
        class_decl = self._classes.get(class_name)
        if class_decl is None:
            return None
        for m in class_decl.metodos:
            if m.nome == method_name:
                return m
        if class_decl.superclasse:
            return self._find_method(class_decl.superclasse, method_name)
        return None

    def _truthy(self, val: Any) -> bool:
        if val is None:     return False
        if isinstance(val, bool):   return val
        if isinstance(val, float):  return val != 0.0
        if isinstance(val, int):    return val != 0
        if isinstance(val, str):    return len(val) > 0
        if isinstance(val, list):   return len(val) > 0
        if isinstance(val, dict):   return len(val) > 0
        return True

    def _build_runtime_descriptor(self, class_decl: ClassDecl) -> ClassDescriptor:
        """Constrói ClassDescriptor minimal para uso no runner sem symbol_table."""
        super_desc = None
        if class_decl.superclasse and class_decl.superclasse in self._classes:
            super_decl = self._classes[class_decl.superclasse]
            super_desc = self._build_runtime_descriptor(super_decl)
        desc = ClassDescriptor(class_decl.nome, super_desc)
        for campo in class_decl.campos:
            desc.add_field(campo.nome, campo.tipo, campo.is_static)
        for metodo in class_decl.metodos:
            param_types = [p.tipo for p in metodo.parametros]
            desc.add_or_override_method(
                metodo.nome, metodo.tipo_retorno, param_types,
                is_override=metodo.is_override
            )
        return desc


class _SuperProxy:
    """Proxy para chamadas via 'super.metodo()'."""

    def __init__(self, obj: MiniParObject, super_class_name: str, runner: Runner):
        self._obj = obj
        self._super_class = super_class_name
        self._runner = runner

    def call(self, method_name: str, args: list) -> Any:
        method = self._runner._find_method(self._super_class, method_name)
        if method is None:
            raise RuntimeError(
                f"Método '{method_name}' não encontrado na superclasse '{self._super_class}'"
            )
        method_env = Environment(parent=self._runner._global_env)
        method_env.define('this', self._obj)
        for param, arg in zip(method.parametros, args):
            method_env.define(param.nome, arg)
        try:
            self._runner._exec(method.corpo, method_env)
            return None
        except ReturnException as ret:
            return ret.value
