/* Gerado automaticamente pelo compilador MiniPar v2026.1 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>


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

/* ── Globais ── */
double numero;
double fatorial;
double i;
int main(void) {
    int t0;
    double t1;
    double t2;
    numero = 5.0;
    fatorial = 1.0;
    i = 1.0;
    __g_L0: ;
    t0 = (i <= numero);
    if (!(t0)) goto __g_L1;
    t1 = fatorial * i;
    fatorial = t1;
    t2 = i + 1.0;
    i = t2;
    goto __g_L0;
    __g_L1: ;
    printf("%s", "O fatorial de");
    printf("\n");
    __mp_pnum((double)(numero));
    printf("\n");
    printf("%s", "é");
    printf("\n");
    __mp_pnum((double)(fatorial));
    printf("\n");
    return 0;
}