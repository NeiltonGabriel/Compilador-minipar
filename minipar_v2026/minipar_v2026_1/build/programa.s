    .data
    .align 3
__mp_stack: .space 8192
__mp_stack_top:
    .align 2
__mp_outbuf: .word 0
    .align 3
fatorial: .space 8
    .align 3
i: .space 8
    .align 3
numero: .space 8

    .section .rodata
    .align 2
.STR0: .asciz "O fatorial de"
    .align 2
.STR1: .asciz "é"
    .align 3
.LCD0: .double 5.0
    .align 3
.LCD1: .double 1.0

    .text
    .global _start
    .global main
    .align 2

_start:
    ldr sp, =__mp_stack_top
    bl main
__mp_halt:
    b __mp_halt

main:
    push {fp, lr}
    mov fp, sp
    sub sp, sp, #56
    ldr r12, =.LCD0
    vldr d0, [r12]
    ldr r12, =numero
    vstr d0, [r12]
    ldr r12, =.LCD1
    vldr d0, [r12]
    ldr r12, =fatorial
    vstr d0, [r12]
    ldr r12, =.LCD1
    vldr d0, [r12]
    ldr r12, =i
    vstr d0, [r12]
main_L0:
    ldr r12, =i
    vldr d0, [r12]
    ldr r12, =numero
    vldr d1, [r12]
    vcmp.f64 d0, d1
    vmrs APSR_nzcv, fpscr
    mov r2, #0
    movle r2, #1
    str r2, [fp, #-8]
    ldr r0, [fp, #-8]
    cmp r0, #0
    beq main_L1
    ldr r12, =fatorial
    vldr d0, [r12]
    ldr r12, =i
    vldr d1, [r12]
    vmul.f64 d2, d0, d1
    vstr d2, [fp, #-16]
    vldr d0, [fp, #-16]
    ldr r12, =fatorial
    vstr d0, [r12]
    ldr r12, =i
    vldr d0, [r12]
    ldr r12, =.LCD1
    vldr d1, [r12]
    vadd.f64 d2, d0, d1
    vstr d2, [fp, #-24]
    vldr d0, [fp, #-24]
    ldr r12, =i
    vstr d0, [r12]
    b main_L0
main_L1:
    ldr r0, =.STR0
    bl __mp_print_str
    mov r0, #10
    bl __mp_putchar
    ldr r12, =numero
    vldr d0, [r12]
    bl __mp_print_double
    mov r0, #10
    bl __mp_putchar
    ldr r0, =.STR1
    bl __mp_print_str
    mov r0, #10
    bl __mp_putchar
    ldr r12, =fatorial
    vldr d0, [r12]
    bl __mp_print_double
    mov r0, #10
    bl __mp_putchar
    mov sp, fp
    pop {fp, pc}

@ ── Runtime ──────────────────────────────────────────────

__mp_putchar:
    push {r4, r5, lr}
    ldr r4, =__mp_outbuf
    ldr r5, [r4]
    cmp r5, #0
    beq __mp_pc_uart
    strb r0, [r5]
    add r5, r5, #1
    str r5, [r4]
    pop {r4, r5, pc}
__mp_pc_uart:
    movw r4, #4096
    movt r4, #65312
__mp_pc_wait:
    ldr r5, [r4, #4]
    lsr r5, r5, #16
    cmp r5, #0
    beq __mp_pc_wait
    str r0, [r4]
    pop {r4, r5, pc}

__mp_print_str:
    push {r4, lr}
    mov r4, r0
__mp_ps_loop:
    ldrb r0, [r4]
    cmp r0, #0
    beq __mp_ps_done
    bl __mp_putchar
    add r4, r4, #1
    b __mp_ps_loop
__mp_ps_done:
    pop {r4, pc}

__mp_print_uint:
    push {r4, r5, r6, lr}
    mov r4, r0
    cmp r4, #0
    bne __mp_pu_nz
    mov r0, #48
    bl __mp_putchar
    pop {r4, r5, r6, pc}
__mp_pu_nz:
    sub sp, sp, #16
    mov r5, sp
    mov r6, #0
__mp_pu_loop:
    mov r0, r4
    mov r1, #10
    bl __mp_divmod
    add r1, r1, #48
    strb r1, [r5, r6]
    add r6, r6, #1
    mov r4, r0
    cmp r4, #0
    bne __mp_pu_loop
__mp_pu_out:
    sub r6, r6, #1
    ldrb r0, [r5, r6]
    bl __mp_putchar
    cmp r6, #0
    bne __mp_pu_out
    add sp, sp, #16
    pop {r4, r5, r6, pc}

__mp_print_double:
    vpush {d8, d9, d10}
    push {r4, r5, r6, r7, lr}
    vmov.f64 d8, d0
    vcmp.f64 d8, #0.0
    vmrs APSR_nzcv, fpscr
    bpl __mp_pd_pos
    mov r0, #45
    bl __mp_putchar
    vneg.f64 d8, d8
__mp_pd_pos:
    vcvt.s32.f64 s0, d8
    vmov r0, s0
    bl __mp_print_uint
    vcvt.f64.s32 d9, s0
    vsub.f64 d10, d8, d9
    mov r0, #10
    vmov s0, r0
    vcvt.f64.s32 d9, s0
    sub sp, sp, #24
    mov r5, sp
    mov r6, #0
    mov r7, #16
__mp_pd_loop:
    cmp r7, #0
    beq __mp_pd_trim
    vmul.f64 d10, d10, d9
    vcvt.s32.f64 s0, d10
    vmov r0, s0
    add r0, r0, #48
    strb r0, [r5, r6]
    add r6, r6, #1
    vcvt.f64.s32 d8, s0
    vsub.f64 d10, d10, d8
    sub r7, r7, #1
    vcmp.f64 d10, #0.0
    vmrs APSR_nzcv, fpscr
    bne __mp_pd_loop
__mp_pd_trim:
    cmp r6, #0
    beq __mp_pd_dot0
    sub r0, r6, #1
    ldrb r1, [r5, r0]
    cmp r1, #48
    bne __mp_pd_emit
    mov r6, r0
    b __mp_pd_trim
__mp_pd_dot0:
    mov r0, #46
    bl __mp_putchar
    mov r0, #48
    bl __mp_putchar
    b __mp_pd_done
__mp_pd_emit:
    mov r0, #46
    bl __mp_putchar
    mov r4, #0
__mp_pd_pr:
    cmp r4, r6
    bge __mp_pd_done
    ldrb r0, [r5, r4]
    bl __mp_putchar
    add r4, r4, #1
    b __mp_pd_pr
__mp_pd_done:
    add sp, sp, #24
    pop {r4, r5, r6, r7, lr}
    vpop {d8, d9, d10}
    bx lr

__mp_divmod:
    push {r4, r5, r6, r7, lr}
    cmp r1, #0
    beq __mp_dm_zero
    mov r4, #0
    mov r5, #0
    cmp r0, #0
    bpl __mp_dm_np
    rsb r0, r0, #0
    eor r4, r4, #1
    mov r5, #1
__mp_dm_np:
    cmp r1, #0
    bpl __mp_dm_dp
    rsb r1, r1, #0
    eor r4, r4, #1
__mp_dm_dp:
    mov r2, #0
    mov r3, #0
    mov r6, #32
__mp_dm_loop:
    lsls r0, r0, #1
    lsl r3, r3, #1
    orrcs r3, r3, #1
    lsl r2, r2, #1
    cmp r3, r1
    subhs r3, r3, r1
    orrhs r2, r2, #1
    subs r6, r6, #1
    bne __mp_dm_loop
    mov r0, r2
    mov r1, r3
    cmp r4, #0
    rsbne r0, r0, #0
    cmp r5, #0
    rsbne r1, r1, #0
    pop {r4, r5, r6, r7, pc}
__mp_dm_zero:
    mov r0, #0
    mov r1, #0
    pop {r4, r5, r6, r7, pc}
    .end