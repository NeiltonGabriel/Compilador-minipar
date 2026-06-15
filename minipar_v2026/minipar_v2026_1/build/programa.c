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
char* nome;
int main(void) {
    nome = "MiniPar";
    printf("%s", "Olá,");
    printf(" ");
    printf("%s", nome);
    printf("\n");
    printf("%s", "Versão: 2026.1");
    printf("\n");
    return 0;
}