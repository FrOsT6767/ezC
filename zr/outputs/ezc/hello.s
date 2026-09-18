.intel_syntax noprefix
.section .rodata
.LC0:
  .asciz "Hello from ezC!"
.text
.extern puts
.globl main
main:
  push rbp
  mov rbp, rsp
  lea rax, [rip+.LC0]
  push rax
  pop rdi
  mov r11, rsp
  and rsp, -16
  sub rsp, 16
  mov QWORD PTR [rsp], r11
  call puts
  mov r11, QWORD PTR [rsp]
  mov rsp, r11
  mov rax, 0
  jmp .L_return_main_1
  mov rax, 0
.L_return_main_1:
  leave
  ret

