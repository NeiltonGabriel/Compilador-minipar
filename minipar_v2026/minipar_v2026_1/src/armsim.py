"""Mini-simulador ARMv7 — cobre o subconjunto emitido por arm_codegen.py.
Objetivo: validar a lógica do assembly gerado (saída via JTAG UART)."""
import re, sys

MASK = 0xFFFFFFFF
def u32(x): return x & MASK
def s32(x):
    x &= MASK
    return x - (1 << 32) if x & 0x80000000 else x

class Sim:
    def __init__(self, asm):
        self.mem = {}                # endereço -> byte
        self._wordfix = []
        self.labels = {}
        self.instrs = []             # (addr, line)
        self.reg = [0]*16            # r0..r15
        self.N=self.Z=self.C=self.V=0
        self.d=[0.0]*16
        self.sreg=[0]*32
        self.fp=(0,0,0,0)
        import struct as _st; self._st=_st
        self.out = []
        self._assemble(asm)

    # ---- montagem (duas passagens simplificadas) ----
    def _assemble(self, asm):
        lines = [l.split('@')[0].rstrip() for l in asm.splitlines()]
        lines = [l for l in lines if l.strip()]
        DATA, TEXT = 0x1000, 0x100000
        addr = DATA; section='data'
        # passagem 1: dados + labels
        text_lines=[]
        for l in lines:
            s=l.strip()
            if s in ('.data',): section='data'; continue
            if s.startswith('.section .rodata'): section='rodata'; continue
            if s=='.text': section='text'; addr=TEXT; continue
            if s.startswith('.global') or s=='.end':
                continue
            if s.startswith('.align'):
                if section!='text':
                    n=int(s.split()[1]) if len(s.split())>1 else 2
                    al=1<<n; addr=(addr+al-1)&~(al-1)
                continue
            m=re.match(r'^(\.?\w[\w\.]*):\s*(.*)$', s)
            label=None; rest=s
            if m and (m.group(2)=='' or m.group(2).startswith('.')):
                label=m.group(1); rest=m.group(2).strip()
            elif m and section=='text':
                label=m.group(1); rest=m.group(2).strip()
            if label: self.labels[label]=addr
            if not rest: continue
            if section in ('data','rodata'):
                if rest.startswith('.space'):
                    addr+=int(rest.split()[1])
                elif rest.startswith('.word'):
                    tok=rest.split(None,1)[1].strip()
                    try:
                        val=int(tok,0); self._stw(addr,val)
                    except ValueError:
                        self._wordfix.append((addr,tok))
                    addr+=4
                elif rest.startswith('.double'):
                    val=float(rest.split(None,1)[1]); self._std(addr,val); addr+=8
                elif rest.startswith('.asciz'):
                    sx=re.search(r'"(.*)"', rest).group(1)
                    sx=sx.encode().decode('unicode_escape')
                    for ch in sx: self.mem[addr]=ord(ch)&0xFF; addr+=1
                    self.mem[addr]=0; addr+=1
            else:
                text_lines.append((addr,rest)); addr+=4
        for a,lbl in self._wordfix:
            self._stw(a, self.labels[lbl])
        for a,ins in text_lines:
            self.instrs.append((a,ins))
        self.addr2idx={a:i for i,(a,_) in enumerate(self.instrs)}

    def _std(self,a,v):
        for i,b in enumerate(self._st.pack('<d',float(v))): self.mem[a+i]=b
    def _ldd(self,a):
        return self._st.unpack('<d', bytes(self.mem.get(a+i,0) for i in range(8)))[0]
    def didx(self,r): return int(r[1:])
    def _stw(self,a,v):
        v=u32(v)
        for i in range(4): self.mem[a+i]=(v>>(8*i))&0xFF
    def _ldw(self,a):
        return sum(self.mem.get(a+i,0)<<(8*i) for i in range(4))

    # ---- operandos ----
    def rget(self,r):
        return self.reg[int(r[1:])] if r!='sp' and r!='fp' and r!='lr' and r!='pc' \
            else self.reg[{'sp':13,'fp':11,'lr':14,'pc':15}[r]]
    def rset(self,r,v):
        idx = int(r[1:]) if r not in ('sp','fp','lr','pc') else {'sp':13,'fp':11,'lr':14,'pc':15}[r]
        self.reg[idx]=u32(v)
    def val(self,tok):
        tok=tok.strip()
        if tok.startswith('#'): return u32(int(tok[1:],0))
        if tok.startswith('='):
            return self.labels[tok[1:]]
        return self.rget(tok)

    def cond_ok(self,c):
        return {'':True,'eq':self.Z,'ne':not self.Z,'ge':self.N==self.V,
                'lt':self.N!=self.V,'gt':(not self.Z) and self.N==self.V,
                'le':self.Z or self.N!=self.V,'pl':not self.N,'mi':self.N,
                'hs':self.C,'cs':self.C,'lo':not self.C,'cc':not self.C}[c]

    def setflags_sub(self,a,b):
        a=u32(a); b=u32(b); r=u32(a-b)
        self.Z=int(r==0); self.N=int((r>>31)&1)
        self.C=int(a>=b); self.V=int(((a^b)&(a^r))>>31 & 1)

    # ---- execução ----
    def run(self,maxsteps=2_000_000):
        self.reg[15]=self.instrs[0][0]
        steps=0
        while steps<maxsteps:
            steps+=1
            pc=self.reg[15]
            if pc not in self.addr2idx: break
            _,ins=self.instrs[self.addr2idx[pc]]
            self.reg[15]=pc+4
            if self._exec(ins, pc): break
        else:
            raise RuntimeError("limite de passos (loop?)")
        return bytes(self.out).decode('utf-8', errors='replace')

    def _exec(self, ins, pc):
        parts=ins.replace(',',' ').split()
        op=parts[0]
        # mnemônico + condição/sufixo
        base=op; cond=''; setf=False
        for c in ('eq','ne','ge','lt','gt','le','pl','mi','hs','cs','lo','cc'):
            if op.endswith(c) and op not in ('b',) and len(op)>len(c):
                base=op[:-len(c)]; cond=c; break
        if base.endswith('s') and base not in ('bls','push','rsbs') and base in ('adds','subs','lsls','muls'):
            setf=True; base=base[:-1]
        a=parts[1:]

        if base=='b' and not cond:
            self.reg[15]=self.labels[a[0]]; return self.labels[a[0]]==pc  # halt se b p/ si
        if base=='b' and cond:
            if self.cond_ok(cond): self.reg[15]=self.labels[a[0]]
            return False
        if base=='blx':
            self.reg[14]=pc+4; self.reg[15]=self.rget(a[0]); return False
        if base=='bl':
            self.reg[14]=pc+4; self.reg[15]=self.labels[a[0]]; return False

        if base in ('mov','movw') :
            if self.cond_ok(cond): self.rset(a[0], self.val(a[1]))
            return False
        if base=='movt':
            if self.cond_ok(cond):
                self.rset(a[0], (self.rget(a[0])&0xFFFF)|((self.val(a[1])&0xFFFF)<<16))
            return False
        if base in ('add','sub','mul','and','orr','eor','rsb','lsl','lsr'):
            if not self.cond_ok(cond): return False
            x=self.val(a[1]); y=self.val(a[2])
            if base=='add': r=x+y
            elif base=='sub': r=x-y
            elif base=='mul': r=x*y
            elif base=='and': r=x&y
            elif base=='orr': r=x|y
            elif base=='eor': r=x^y
            elif base=='rsb': r=y-x
            elif base=='lsl': r=x<<(y&31)
            elif base=='lsr': r=(u32(x)>>(y&31))
            if setf and base in ('sub','add'):
                if base=='sub': self.setflags_sub(x,y)
            if op.startswith('lsls'):
                sh=y&31; self.C=int((x>>(32-sh))&1) if sh else self.C
                r=x<<sh
            if op.startswith('subs'):
                self.setflags_sub(x,y); r=x-y
            self.rset(a[0], r); return False
        if base=='cmp':
            self.setflags_sub(self.val(a[0]), self.val(a[1])); return False
        if base=='push':
            regs=self._reglist(ins)
            for rr in reversed(regs):
                self.reg[13]-=4; self._stw(self.reg[13], self.rget(rr))
            return False
        if base=='pop':
            regs=self._reglist(ins)
            ret=False
            for rr in regs:
                v=self._ldw(self.reg[13]); self.reg[13]+=4
                if rr=='pc': self.reg[15]=v; ret=False
                else: self.rset(rr,v)
            return False
        if base in ('ldr','ldrb','str','strb'):
            return self._mem(base, ins, pc)
        if base=='bx':
            self.reg[15]=self.rget(a[0]); return False
        if base.startswith('vpush'):
            for rr in reversed(self._reglist(ins)):
                self.reg[13]-=8; self._std(self.reg[13], self.d[self.didx(rr)])
            return False
        if base.startswith('vpop'):
            for rr in self._reglist(ins):
                self.d[self.didx(rr)]=self._ldd(self.reg[13]); self.reg[13]+=8
            return False
        if op.startswith('vmrs'):
            self.N,self.Z,self.C,self.V=self.fp; return False
        if op.startswith('vldr'):
            m=re.match(r'vldr\s+(\w+)\s*,\s*\[(\w+)(?:\s*,\s*#(-?\d+))?\]',ins)
            self.d[self.didx(m.group(1))]=self._ldd(u32(self.rget(m.group(2))+int(m.group(3) or 0))); return False
        if op.startswith('vstr'):
            m=re.match(r'vstr\s+(\w+)\s*,\s*\[(\w+)(?:\s*,\s*#(-?\d+))?\]',ins)
            self._std(u32(self.rget(m.group(2))+int(m.group(3) or 0)), self.d[self.didx(m.group(1))]); return False
        if op.startswith('vmov.f64'):
            self.d[self.didx(a[0])]=self.d[self.didx(a[1])]; return False
        if op.startswith('vmov'):
            x,y=a[0],a[1]
            if x[0]=='s': self.sreg[int(x[1:])]=u32(self.rget(y))
            else: self.rset(x, self.sreg[int(y[1:])])
            return False
        if op.startswith('vadd.f64'): self.d[self.didx(a[0])]=self.d[self.didx(a[1])]+self.d[self.didx(a[2])]; return False
        if op.startswith('vsub.f64'): self.d[self.didx(a[0])]=self.d[self.didx(a[1])]-self.d[self.didx(a[2])]; return False
        if op.startswith('vmul.f64'): self.d[self.didx(a[0])]=self.d[self.didx(a[1])]*self.d[self.didx(a[2])]; return False
        if op.startswith('vdiv.f64'): self.d[self.didx(a[0])]=self.d[self.didx(a[1])]/self.d[self.didx(a[2])]; return False
        if op.startswith('vneg.f64'): self.d[self.didx(a[0])]=-self.d[self.didx(a[1])]; return False
        if op.startswith('vcmp.f64'):
            x=self.d[self.didx(a[0])]
            y=0.0 if a[1].startswith('#') else self.d[self.didx(a[1])]
            if x==y: self.fp=(0,1,1,0)
            elif x<y: self.fp=(1,0,0,0)
            else: self.fp=(0,0,1,0)
            return False
        if op.startswith('vcvt.s32.f64'):
            v=self.d[self.didx(a[1])]
            iv=int(v)                      # trunc p/ zero
            if iv>2**31-1: iv=2**31-1
            if iv<-2**31: iv=-2**31
            self.sreg[int(a[0][1:])]=u32(iv); return False
        if op.startswith('vcvt.f64.s32'):
            self.d[self.didx(a[0])]=float(s32(self.sreg[int(a[1][1:])])); return False
        # ignora diretivas que sobraram
        return False

    def _reglist(self,ins):
        inside=ins[ins.index('{')+1:ins.index('}')]
        return [r.strip() for r in inside.split(',')]

    def _mem(self, base, ins, pc):
        # forma:  op rD, =label    |  op rD, [rn]  | op rD, [rn, #imm]
        m=re.match(r'\w+\s+(\w+)\s*,\s*(.*)$', ins)
        rd=m.group(1); src=m.group(2).strip()
        if src.startswith('='):
            self.rset(rd, self.labels[src[1:]]); return False
        mreg=re.match(r'\[(\w+)\s*,\s*(\w+)\]$', src)
        mimm=re.match(r'\[(\w+)(?:\s*,\s*#(-?\d+))?\]$', src)
        if mreg and not mimm:
            addr=u32(self.rget(mreg.group(1))+self.rget(mreg.group(2)))
        else:
            rn=mimm.group(1); off=int(mimm.group(2) or 0)
            addr=u32(self.rget(rn)+off)
        if base=='ldr': self.rset(rd, self._ldw(addr))
        elif base=='ldrb': self.rset(rd, self.mem.get(addr,0)&0xFF)
        elif base=='str':
            if addr==0xFF201000: self.out.append(self.rget(rd)&0xFF)
            else: self._stw(addr, self.rget(rd))
        elif base=='strb': self.mem[addr]=self.rget(rd)&0xFF
        return False

    # leitura do control register do UART (WSPACE != 0)
    def _ldw(self,a):
        if a==0xFF201004: return 0xFFFF0000   # WSPACE alto
        return sum(self.mem.get(a+i,0)<<(8*i) for i in range(4))


if __name__=='__main__':
    asm=open(sys.argv[1]).read()
    sys.stdout.write(Sim(asm).run())