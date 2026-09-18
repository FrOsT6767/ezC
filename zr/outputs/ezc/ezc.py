#!/usr/bin/env python3
"""ezC v0.1 compiler: source -> x86-64 System V assembly -> Linux ELF.

This module is intentionally self-contained so each compiler phase can be read
in one place.  It is a compiler, not an interpreter: no ezC program is ever
executed by Python.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Diagnostics and lexer


@dataclass
class Token:
    kind: str
    text: str
    path: str
    line: int
    column: int
    source_line: str


class CompileError(Exception):
    def __init__(self, token: Token, message: str):
        self.token, self.message = token, message
        super().__init__(message)

    def pretty(self) -> str:
        caret = " " * (max(self.token.column - 1, 0)) + "^"
        return (f"{self.token.path}:{self.token.line}:{self.token.column}: "
                f"error: {self.message}\n  {self.token.source_line}\n  {caret}")


KEYWORDS = {
    "module", "use", "extern", "fn", "struct", "enum", "impl", "let", "var",
    "return", "if", "else", "while", "true", "false", "cast",
}
MULTI = ("->", "==", "!=", "<=", ">=", "&&", "||")
SINGLE = set("(){}[],:;.+-*/%<>=!&")


def lex(path: Path, text: str) -> list[Token]:
    tokens: list[Token] = []
    i = line = 0
    col = 1
    lines = text.splitlines() or [""]

    def source_line() -> str:
        return lines[line] if line < len(lines) else ""

    def add(kind: str, value: str, start_line: int, start_col: int, src: str) -> None:
        tokens.append(Token(kind, value, str(path), start_line + 1, start_col, src))

    while i < len(text):
        c = text[i]
        if c in " \t\r":
            i += 1
            col += 1
            continue
        if c == "\n":
            i += 1
            line += 1
            col = 1
            continue
        if text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
                col += 1
            continue
        start_line, start_col, src = line, col, source_line()
        if c.isalpha() or c == "_":
            j = i + 1
            while j < len(text) and (text[j].isalnum() or text[j] == "_"):
                j += 1
            value = text[i:j]
            add(value if value in KEYWORDS else "IDENT", value, start_line, start_col, src)
            col += j - i
            i = j
            continue
        if c.isdigit():
            j = i + 1
            while j < len(text) and text[j].isdigit():
                j += 1
            add("INT", text[i:j], start_line, start_col, src)
            col += j - i
            i = j
            continue
        if c == '"':
            j = i + 1
            escaped = False
            while j < len(text):
                if text[j] == "\n":
                    raise CompileError(Token("STRING", "", str(path), start_line + 1, start_col, src),
                                       "newline inside string literal")
                if text[j] == '"' and not escaped:
                    break
                if text[j] == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False
                j += 1
            if j >= len(text):
                raise CompileError(Token("STRING", "", str(path), start_line + 1, start_col, src),
                                   "unterminated string literal")
            raw = text[i + 1:j]
            try:
                value = bytes(raw, "utf-8").decode("unicode_escape")
            except UnicodeDecodeError:
                raise CompileError(Token("STRING", raw, str(path), start_line + 1, start_col, src),
                                   "invalid string escape")
            add("STRING", value, start_line, start_col, src)
            col += j + 1 - i
            i = j + 1
            continue
        op = next((x for x in MULTI if text.startswith(x, i)), None)
        if op:
            add(op, op, start_line, start_col, src)
            i += len(op)
            col += len(op)
            continue
        if c in SINGLE:
            add(c, c, start_line, start_col, src)
            i += 1
            col += 1
            continue
        raise CompileError(Token("?", c, str(path), start_line + 1, start_col, src),
                           f"unexpected character {c!r}")
    tokens.append(Token("EOF", "", str(path), line + 1, col, source_line()))
    return tokens


# ---------------------------------------------------------------------------
# AST.  Parser nodes contain no execution logic; semantic analysis fills in
# resolved `ty`, offsets, bindings and function targets afterwards.


@dataclass
class TypeExpr:
    token: Token
    kind: str
    name: str | None = None
    element: TypeExpr | None = None
    length: int | None = None


@dataclass
class Param:
    token: Token
    name: str
    annotation: TypeExpr


@dataclass
class ModuleDecl:
    token: Token
    name: str


@dataclass
class UseDecl:
    token: Token
    filename: str


@dataclass
class StructDecl:
    token: Token
    name: str
    fields: list[tuple[Token, str, TypeExpr]]


@dataclass
class EnumDecl:
    token: Token
    name: str
    variants: list[tuple[Token, str]]


@dataclass
class FuncDecl:
    token: Token
    name: str
    params: list[Param]
    ret: TypeExpr
    body: list[Any] | None
    external: bool = False
    owner: str | None = None


@dataclass
class ImplDecl:
    token: Token
    owner: str
    methods: list[FuncDecl]


@dataclass
class Program:
    path: Path
    items: list[Any]


@dataclass
class LetStmt:
    token: Token
    name: str
    mutable: bool
    annotation: TypeExpr | None
    value: Any


@dataclass
class ReturnStmt:
    token: Token
    value: Any | None


@dataclass
class IfStmt:
    token: Token
    condition: Any
    then_body: list[Any]
    else_body: list[Any] | None


@dataclass
class WhileStmt:
    token: Token
    condition: Any
    body: list[Any]


@dataclass
class AssignStmt:
    token: Token
    target: Any
    value: Any


@dataclass
class ExprStmt:
    token: Token
    value: Any


@dataclass
class IntExpr:
    token: Token
    value: int


@dataclass
class BoolExpr:
    token: Token
    value: bool


@dataclass
class StringExpr:
    token: Token
    value: str


@dataclass
class NameExpr:
    token: Token
    name: str


@dataclass
class UnaryExpr:
    token: Token
    op: str
    value: Any


@dataclass
class BinaryExpr:
    token: Token
    left: Any
    op: str
    right: Any


@dataclass
class CallExpr:
    token: Token
    callee: Any
    args: list[Any]


@dataclass
class MemberExpr:
    token: Token
    object: Any
    name: str


@dataclass
class IndexExpr:
    token: Token
    object: Any
    index: Any


@dataclass
class CastExpr:
    token: Token
    target: TypeExpr
    value: Any


@dataclass
class StructExpr:
    token: Token
    name: str
    values: list[tuple[Token, str, Any]]


@dataclass
class ArrayExpr:
    token: Token
    values: list[Any]


# ---------------------------------------------------------------------------
# Recursive-descent / Pratt parser


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens, self.pos = tokens, 0

    @property
    def current(self) -> Token:
        return self.tokens[self.pos]

    def match(self, *kinds: str) -> Token | None:
        if self.current.kind in kinds:
            token = self.current
            self.pos += 1
            return token
        return None

    def expect(self, kind: str, what: str | None = None) -> Token:
        token = self.match(kind)
        if token is None:
            got = "end of file" if self.current.kind == "EOF" else repr(self.current.text)
            raise CompileError(self.current, f"expected {what or repr(kind)}, got {got}")
        return token

    def program(self, path: Path) -> Program:
        items = []
        while self.current.kind != "EOF":
            if self.match("module"):
                start = self.tokens[self.pos - 1]
                parts = [self.expect("IDENT", "module name").text]
                while self.match("."):
                    parts.append(self.expect("IDENT", "module name").text)
                self.expect(";")
                items.append(ModuleDecl(start, ".".join(parts)))
            elif self.match("use"):
                start = self.tokens[self.pos - 1]
                filename = self.expect("STRING", "quoted module filename")
                self.expect(";")
                items.append(UseDecl(start, filename.text))
            elif self.match("extern"):
                start = self.tokens[self.pos - 1]
                items.append(self.parse_function(start, external=True))
            elif self.match("fn"):
                items.append(self.parse_function(self.tokens[self.pos - 1]))
            elif self.match("struct"):
                items.append(self.parse_struct(self.tokens[self.pos - 1]))
            elif self.match("enum"):
                items.append(self.parse_enum(self.tokens[self.pos - 1]))
            elif self.match("impl"):
                items.append(self.parse_impl(self.tokens[self.pos - 1]))
            else:
                raise CompileError(self.current, "expected a declaration")
        return Program(path, items)

    def parse_type(self) -> TypeExpr:
        token = self.current
        if self.match("*"):
            return TypeExpr(token, "pointer", element=self.parse_type())
        if self.match("["):
            element = self.parse_type()
            self.expect(";")
            length = int(self.expect("INT", "array length").text)
            self.expect("]")
            return TypeExpr(token, "array", element=element, length=length)
        name = self.expect("IDENT", "type name")
        return TypeExpr(name, "name", name=name.text)

    def parse_function(self, start: Token, external: bool = False, owner: str | None = None) -> FuncDecl:
        if external:
            self.expect("fn")
        name = self.expect("IDENT", "function name")
        self.expect("(")
        params: list[Param] = []
        if self.current.kind != ")":
            while True:
                p = self.expect("IDENT", "parameter name")
                self.expect(":")
                params.append(Param(p, p.text, self.parse_type()))
                if not self.match(","):
                    break
        self.expect(")")
        self.expect("->")
        ret = self.parse_type()
        if external:
            self.expect(";")
            return FuncDecl(start, name.text, params, ret, None, True, owner)
        return FuncDecl(start, name.text, params, ret, self.parse_block(), False, owner)

    def parse_struct(self, start: Token) -> StructDecl:
        name = self.expect("IDENT", "struct name")
        self.expect("{")
        fields = []
        while self.current.kind != "}":
            field = self.expect("IDENT", "field name")
            self.expect(":")
            fields.append((field, field.text, self.parse_type()))
            if not self.match(","):
                self.match(";")
        self.expect("}")
        return StructDecl(start, name.text, fields)

    def parse_enum(self, start: Token) -> EnumDecl:
        name = self.expect("IDENT", "enum name")
        self.expect("{")
        variants = []
        while self.current.kind != "}":
            value = self.expect("IDENT", "enum variant")
            variants.append((value, value.text))
            if not self.match(","):
                self.match(";")
        self.expect("}")
        return EnumDecl(start, name.text, variants)

    def parse_impl(self, start: Token) -> ImplDecl:
        owner = self.expect("IDENT", "struct name after impl")
        self.expect("{")
        methods = []
        while self.current.kind != "}":
            marker = self.expect("fn", "fn in impl block")
            methods.append(self.parse_function(marker, owner=owner.text))
        self.expect("}")
        return ImplDecl(start, owner.text, methods)

    def parse_block(self) -> list[Any]:
        self.expect("{")
        body = []
        while self.current.kind != "}":
            if self.current.kind == "EOF":
                raise CompileError(self.current, "expected '}' before end of file")
            body.append(self.parse_statement())
        self.expect("}")
        return body

    def parse_statement(self) -> Any:
        if (token := self.match("let", "var")):
            name = self.expect("IDENT", "variable name")
            annotation = None
            if self.match(":"):
                annotation = self.parse_type()
            self.expect("=")
            value = self.expression()
            self.expect(";")
            return LetStmt(token, name.text, token.kind == "var", annotation, value)
        if (token := self.match("return")):
            value = None if self.current.kind == ";" else self.expression()
            self.expect(";")
            return ReturnStmt(token, value)
        if (token := self.match("if")):
            condition = self.expression()
            then_body = self.parse_block()
            else_body = self.parse_block() if self.match("else") else None
            return IfStmt(token, condition, then_body, else_body)
        if (token := self.match("while")):
            condition = self.expression()
            return WhileStmt(token, condition, self.parse_block())
        value = self.expression()
        if self.match("="):
            assigned = self.expression()
            self.expect(";")
            return AssignStmt(value.token, value, assigned)
        self.expect(";")
        return ExprStmt(value.token, value)

    PRECEDENCE = {"||": 1, "&&": 2, "==": 3, "!=": 3, "<": 4, "<=": 4,
                  ">": 4, ">=": 4, "+": 5, "-": 5, "*": 6, "/": 6, "%": 6}

    def expression(self, minimum: int = 0) -> Any:
        left = self.prefix()
        while True:
            prec = self.PRECEDENCE.get(self.current.kind, -1)
            if prec < minimum:
                break
            op = self.current
            self.pos += 1
            right = self.expression(prec + 1)
            left = BinaryExpr(op, left, op.kind, right)
        return left

    def prefix(self) -> Any:
        token = self.current
        if self.match("INT"):
            value: Any = IntExpr(token, int(token.text))
        elif self.match("STRING"):
            value = StringExpr(token, token.text)
        elif self.match("true", "false"):
            value = BoolExpr(token, token.kind == "true")
        elif self.match("IDENT"):
            value = NameExpr(token, token.text)
            if self.match("{"):
                fields = []
                while self.current.kind != "}":
                    name = self.expect("IDENT", "struct field name")
                    self.expect(":")
                    fields.append((name, name.text, self.expression()))
                    if not self.match(","):
                        break
                self.expect("}")
                value = StructExpr(token, token.text, fields)
        elif self.match("("):
            value = self.expression()
            self.expect(")")
        elif self.match("["):
            values = []
            if self.current.kind != "]":
                while True:
                    values.append(self.expression())
                    if not self.match(","):
                        break
            self.expect("]")
            value = ArrayExpr(token, values)
        elif self.match("cast"):
            self.expect("[")
            target = self.parse_type()
            self.expect("]")
            self.expect("(")
            inner = self.expression()
            self.expect(")")
            value = CastExpr(token, target, inner)
        elif self.match("-", "!", "*", "&"):
            value = UnaryExpr(token, token.kind, self.expression(7))
        else:
            raise CompileError(token, "expected an expression")
        while True:
            if self.match("("):
                args = []
                if self.current.kind != ")":
                    while True:
                        args.append(self.expression())
                        if not self.match(","):
                            break
                self.expect(")")
                value = CallExpr(token, value, args)
            elif self.match("."):
                member = self.expect("IDENT", "field or method name")
                value = MemberExpr(member, value, member.text)
            elif self.match("["):
                index = self.expression()
                self.expect("]")
                value = IndexExpr(token, value, index)
            else:
                return value


# ---------------------------------------------------------------------------
# Types and semantic analysis



@dataclass(eq=False)
class CType:
    kind: str
    name: str
    element: CType | None = None
    length: int | None = None
    fields: dict[str, CType] = field(default_factory=dict)
    offsets: dict[str, int] = field(default_factory=dict)
    variants: dict[str, int] = field(default_factory=dict)
    size: int = 8

    def display(self) -> str:
        if self.kind == "pointer":
            return "*" + self.element.display()
        if self.kind == "array":
            return f"[{self.element.display()}; {self.length}]"
        return self.name

    @property
    def aggregate(self) -> bool:
        return self.kind in ("struct", "array")


@dataclass
class Binding:
    ty: CType
    mutable: bool
    offset: int


@dataclass
class Function:
    decl: FuncDecl
    params: list[CType]
    ret: CType
    symbol: str


class Analyzer:
    def __init__(self, programs: list[Program]):
        self.programs = programs
        self.types: dict[str, CType] = {
            n: CType("primitive", n) for n in ("i64", "i32", "u64", "u8", "usize", "bool", "void")
        }
        self.string = CType("string", "string")
        self.functions: dict[str, Function] = {}
        self.methods: dict[tuple[str, str], Function] = {}
        self.scopes: list[dict[str, Binding]] = []
        self.current_function: Function | None = None
        self.next_offset = 0

    def error(self, token: Token, message: str) -> None:
        raise CompileError(token, message)

    def resolve_type(self, node: TypeExpr) -> CType:
        if node.kind == "name":
            ty = self.types.get(node.name or "")
            if ty is None:
                self.error(node.token, f"unknown type '{node.name}'")
            return ty
        if node.kind == "pointer":
            base = self.resolve_type(node.element)
            return CType("pointer", "pointer", element=base)
        if node.kind == "array":
            base = self.resolve_type(node.element)
            if not node.length or node.length < 1:
                self.error(node.token, "array length must be positive")
            return CType("array", "array", element=base, length=node.length, size=base.size * node.length)
        raise AssertionError(node.kind)

    @staticmethod
    def same(a: CType, b: CType) -> bool:
        if a.kind != b.kind or a.name != b.name:
            return False
        if a.kind == "pointer":
            return Analyzer.same(a.element, b.element)
        if a.kind == "array":
            return a.length == b.length and Analyzer.same(a.element, b.element)
        return True

    @staticmethod
    def integer(ty: CType) -> bool:
        return ty.name in ("i64", "i32", "u64", "u8", "usize")

    def compatible(self, expected: CType, actual: CType, expr: Any) -> bool:
        if self.same(expected, actual):
            return True
        if self.integer(expected) and self.integer(actual):
            return True
        if actual.kind == "string" and expected.kind == "pointer" and expected.element.name == "u8":
            return True
        return False

    def require(self, token: Token, expected: CType, actual: CType, expr: Any, context: str) -> None:
        if not self.compatible(expected, actual, expr):
            self.error(token, f"{context}: expected {expected.display()}, got {actual.display()}")

    def collect(self) -> None:
        # Names first, so recursive pointers and cross-references have a type.
        for program in self.programs:
            for item in program.items:
                if isinstance(item, StructDecl):
                    if item.name in self.types:
                        self.error(item.token, f"duplicate type '{item.name}'")
                    self.types[item.name] = CType("struct", item.name, size=0)
                elif isinstance(item, EnumDecl):
                    if item.name in self.types:
                        self.error(item.token, f"duplicate type '{item.name}'")
                    self.types[item.name] = CType("enum", item.name)
        for program in self.programs:
            for item in program.items:
                if isinstance(item, StructDecl):
                    ty = self.types[item.name]
                    offset = 0
                    for field_token, name, annotation in item.fields:
                        if name in ty.fields:
                            self.error(field_token, f"duplicate field '{name}' in {item.name}")
                        field_ty = self.resolve_type(annotation)
                        ty.fields[name] = field_ty
                        ty.offsets[name] = offset
                        offset += field_ty.size
                    ty.size = max(offset, 1)
                elif isinstance(item, EnumDecl):
                    ty = self.types[item.name]
                    for number, (variant_token, name) in enumerate(item.variants):
                        if name in ty.variants:
                            self.error(variant_token, f"duplicate enum variant '{name}'")
                        ty.variants[name] = number
        for program in self.programs:
            for item in program.items:
                candidates = [item] if isinstance(item, FuncDecl) else item.methods if isinstance(item, ImplDecl) else []
                for decl in candidates:
                    if decl.owner and self.types.get(decl.owner, CType("", "")).kind != "struct":
                        self.error(decl.token, f"impl target '{decl.owner}' is not a struct")
                    params = [self.resolve_type(p.annotation) for p in decl.params]
                    ret = self.resolve_type(decl.ret)
                    for param, ty in zip(decl.params, params):
                        if ty.aggregate:
                            self.error(param.token, "aggregate parameters must be passed as pointers")
                    if ret.aggregate:
                        self.error(decl.token, "aggregate returns are not supported; return a pointer instead")
                    symbol = f"{decl.owner}__{decl.name}" if decl.owner else decl.name
                    fn = Function(decl, params, ret, symbol)
                    if decl.owner:
                        key = (decl.owner, decl.name)
                        if key in self.methods:
                            self.error(decl.token, f"duplicate method '{decl.owner}.{decl.name}'")
                        self.methods[key] = fn
                    else:
                        if decl.name in self.functions:
                            self.error(decl.token, f"duplicate function '{decl.name}'")
                        self.functions[decl.name] = fn

    def analyze(self) -> None:
        self.collect()
        if "main" not in self.functions or self.functions["main"].decl.external:
            token = next((p.items[0].token for p in self.programs if p.items), Token("", "", "<input>", 1, 1, ""))
            self.error(token, "a non-extern fn main() -> i32 is required")
        main = self.functions["main"]
        if main.params or main.ret.name not in ("i32", "i64"):
            self.error(main.decl.token, "main must have no parameters and return i32 or i64")
        for fn in list(self.functions.values()) + list(self.methods.values()):
            if not fn.decl.external:
                self.check_function(fn)

    def push_scope(self) -> None:
        self.scopes.append({})

    def pop_scope(self) -> None:
        self.scopes.pop()

    def lookup(self, token: Token, name: str) -> Binding:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        self.error(token, f"unknown variable '{name}'")
        raise AssertionError

    def allocate(self, ty: CType) -> int:
        self.next_offset += max(ty.size, 8)
        return self.next_offset

    def check_function(self, fn: Function) -> None:
        self.current_function, self.next_offset = fn, 0
        self.scopes = []
        self.push_scope()
        if len(fn.params) > 6:
            self.error(fn.decl.token, "functions currently accept at most six parameters")
        for param, ty in zip(fn.decl.params, fn.params):
            binding = Binding(ty, False, self.allocate(ty))
            self.scopes[-1][param.name] = binding
            param.binding = binding
        self.check_body(fn.decl.body or [])
        fn.frame_size = ((self.next_offset + 15) // 16) * 16
        self.pop_scope()
        self.current_function = None

    def check_body(self, body: list[Any]) -> None:
        self.push_scope()
        for statement in body:
            self.check_statement(statement)
        self.pop_scope()

    def check_statement(self, node: Any) -> None:
        if isinstance(node, LetStmt):
            value_ty = self.check_expr(node.value)
            ty = self.resolve_type(node.annotation) if node.annotation else value_ty
            self.require(node.token, ty, value_ty, node.value, "initializer type mismatch")
            binding = Binding(ty, node.mutable, self.allocate(ty))
            self.scopes[-1][node.name] = binding
            node.binding, node.ty = binding, ty
        elif isinstance(node, ReturnStmt):
            expected = self.current_function.ret
            if expected.name == "void":
                if node.value is not None:
                    self.error(node.token, "void function cannot return a value")
            elif node.value is None:
                self.error(node.token, f"function must return {expected.display()}")
            else:
                self.require(node.token, expected, self.check_expr(node.value), node.value, "return type mismatch")
        elif isinstance(node, IfStmt):
            self.require(node.token, self.types["bool"], self.check_expr(node.condition), node.condition,
                         "if condition")
            self.check_body(node.then_body)
            if node.else_body is not None:
                self.check_body(node.else_body)
        elif isinstance(node, WhileStmt):
            self.require(node.token, self.types["bool"], self.check_expr(node.condition), node.condition,
                         "while condition")
            self.check_body(node.body)
        elif isinstance(node, AssignStmt):
            target_ty = self.check_lvalue(node.target, assignment=True)
            self.require(node.token, target_ty, self.check_expr(node.value), node.value, "assignment type mismatch")
        elif isinstance(node, ExprStmt):
            self.check_expr(node.value)
        else:
            raise AssertionError(type(node))

    def check_lvalue(self, node: Any, assignment: bool = False) -> CType:
        ty = self.check_expr(node)
        if not getattr(node, "lvalue", False):
            self.error(node.token, "expression is not assignable")
        if assignment and not getattr(node, "mutable", False):
            self.error(node.token, "cannot assign through immutable storage; declare it with var")
        return ty

    def check_expr(self, node: Any) -> CType:
        if hasattr(node, "ty"):
            return node.ty
        if isinstance(node, IntExpr):
            node.ty = self.types["i64"]
        elif isinstance(node, BoolExpr):
            node.ty = self.types["bool"]
        elif isinstance(node, StringExpr):
            node.ty = self.string
        elif isinstance(node, NameExpr):
            node.binding = self.lookup(node.token, node.name)
            node.ty, node.lvalue, node.mutable = node.binding.ty, True, node.binding.mutable
        elif isinstance(node, UnaryExpr):
            if node.op == "&":
                base = self.check_lvalue(node.value)
                node.ty = CType("pointer", "pointer", element=base)
            elif node.op == "*":
                pointer = self.check_expr(node.value)
                if pointer.kind != "pointer":
                    self.error(node.token, f"cannot dereference {pointer.display()}")
                node.ty, node.lvalue, node.mutable = pointer.element, True, True
            elif node.op == "!":
                self.require(node.token, self.types["bool"], self.check_expr(node.value), node.value, "!")
                node.ty = self.types["bool"]
            else:
                inner = self.check_expr(node.value)
                if not self.integer(inner):
                    self.error(node.token, f"unary {node.op} requires an integer")
                node.ty = inner
        elif isinstance(node, BinaryExpr):
            left, right = self.check_expr(node.left), self.check_expr(node.right)
            if node.op in ("+", "-", "*", "/", "%"):
                if not self.integer(left) or not self.integer(right):
                    self.error(node.token, f"operator {node.op} requires integer operands")
                node.ty = left
            elif node.op in ("<", "<=", ">", ">=", "==", "!="):
                if not self.compatible(left, right, node.right):
                    self.error(node.token, "comparison operands have incompatible types")
                node.ty = self.types["bool"]
            else:
                self.require(node.token, self.types["bool"], left, node.left, f"left operand of {node.op}")
                self.require(node.token, self.types["bool"], right, node.right, f"right operand of {node.op}")
                node.ty = self.types["bool"]
        elif isinstance(node, MemberExpr):
            # Enum members are values (Direction.Up), not field access.
            if isinstance(node.object, NameExpr) and node.object.name in self.types and self.types[node.object.name].kind == "enum":
                enum = self.types[node.object.name]
                if node.name not in enum.variants:
                    self.error(node.token, f"enum {enum.name} has no variant '{node.name}'")
                node.ty, node.enum_value = enum, enum.variants[node.name]
            else:
                base = self.check_expr(node.object)
                struct = base.element if base.kind == "pointer" else base
                if struct.kind != "struct":
                    self.error(node.token, f"{base.display()} has no fields")
                if node.name not in struct.fields:
                    self.error(node.token, f"{struct.name} has no field '{node.name}'")
                node.ty, node.field_offset = struct.fields[node.name], struct.offsets[node.name]
                node.lvalue = True
                node.mutable = getattr(node.object, "mutable", False) or base.kind == "pointer"
        elif isinstance(node, IndexExpr):
            base = self.check_expr(node.object)
            element = base.element if base.kind in ("array", "pointer") else None
            if element is None:
                self.error(node.token, f"cannot index {base.display()}")
            index = self.check_expr(node.index)
            if not self.integer(index):
                self.error(node.index.token, "array index must be an integer")
            node.ty, node.element_size = element, element.size
            node.lvalue, node.mutable = True, getattr(node.object, "mutable", False) or base.kind == "pointer"
        elif isinstance(node, CastExpr):
            target, source = self.resolve_type(node.target), self.check_expr(node.value)
            if not ((target.kind == "pointer" and (source.kind == "pointer" or self.integer(source))) or
                    (self.integer(target) and (source.kind == "pointer" or self.integer(source)))):
                self.error(node.token, f"cannot cast {source.display()} to {target.display()}")
            node.ty = target
        elif isinstance(node, StructExpr):
            ty = self.types.get(node.name)
            if ty is None or ty.kind != "struct":
                self.error(node.token, f"'{node.name}' is not a struct type")
            received = set()
            for field_token, name, value in node.values:
                if name not in ty.fields:
                    self.error(field_token, f"{ty.name} has no field '{name}'")
                if name in received:
                    self.error(field_token, f"field '{name}' initialized twice")
                received.add(name)
                self.require(field_token, ty.fields[name], self.check_expr(value), value,
                             f"field '{name}' type mismatch")
            missing = set(ty.fields) - received
            if missing:
                self.error(node.token, f"struct literal missing field '{sorted(missing)[0]}'")
            node.ty = ty
        elif isinstance(node, ArrayExpr):
            if not node.values:
                self.error(node.token, "empty array needs an explicit initializer design (not supported in v0.1)")
            element = self.check_expr(node.values[0])
            for value in node.values[1:]:
                self.require(value.token, element, self.check_expr(value), value, "array element type mismatch")
            node.ty = CType("array", "array", element=element, length=len(node.values), size=element.size * len(node.values))
        elif isinstance(node, CallExpr):
            self.check_call(node)
        else:
            raise AssertionError(type(node))
        return node.ty

    def check_call(self, node: CallExpr) -> None:
        if isinstance(node.callee, NameExpr) and node.callee.name == "println":
            if len(node.args) != 1:
                self.error(node.token, "println expects exactly one string")
            got = self.check_expr(node.args[0])
            if got.kind != "string":
                self.error(node.args[0].token, "println currently accepts a string value")
            node.builtin, node.ty = "println", self.types["void"]
            return
        fn: Function | None = None
        implicit: Any | None = None
        if isinstance(node.callee, NameExpr):
            fn = self.functions.get(node.callee.name)
            if fn is None:
                self.error(node.callee.token, f"unknown function '{node.callee.name}'")
        elif isinstance(node.callee, MemberExpr):
            object_ty = self.check_expr(node.callee.object)
            owner = object_ty.element if object_ty.kind == "pointer" else object_ty
            fn = self.methods.get((owner.name, node.callee.name)) if owner.kind == "struct" else None
            if fn is None:
                self.error(node.callee.token, f"{owner.display()} has no method '{node.callee.name}'")
            implicit = node.callee.object
            expected = fn.params[0] if fn.params else None
            if expected is None:
                self.error(node.callee.token, "methods must declare self as their first parameter")
            if expected.kind == "pointer" and self.same(expected.element, owner) and object_ty.kind != "pointer":
                node.receiver_address = True
            elif not self.compatible(expected, object_ty, implicit):
                self.error(node.callee.token, f"receiver type mismatch: expected {expected.display()}, got {object_ty.display()}")
        else:
            self.error(node.token, "only named functions and struct methods can be called")
        all_args = ([implicit] if implicit is not None else []) + node.args
        if len(all_args) != len(fn.params):
            self.error(node.token, f"{fn.decl.name} expects {len(fn.params)} argument(s), got {len(all_args)}")
        if len(all_args) > 6:
            self.error(node.token, "calls currently accept at most six arguments")
        for index, (arg, expected) in enumerate(zip(all_args, fn.params)):
            actual = self.check_expr(arg)
            self.require(arg.token, expected, actual, arg, f"argument {index + 1} to {fn.decl.name}")
        node.fn, node.implicit, node.ty = fn, implicit, fn.ret


# ---------------------------------------------------------------------------
# Direct x86-64 System V backend.  It emits GAS Intel syntax.  `cc` is only
# used to assemble/link the output; ezC source is never passed to a C frontend.


class Emitter:
    ARG_REGS = ("rdi", "rsi", "rdx", "rcx", "r8", "r9")

    def __init__(self, analyzer: Analyzer):
        self.analyzer = analyzer
        self.lines: list[str] = []
        self.strings: dict[str, str] = {}
        self.labels = 0
        self.function: Function | None = None
        self.return_label = ""

    def line(self, text: str = "") -> None:
        self.lines.append(text)

    def label(self, prefix: str) -> str:
        self.labels += 1
        return f".L_{prefix}_{self.labels}"

    def compile(self) -> str:
        # Discover string labels while generating text, then put rodata first.
        text: list[str] = []
        old = self.lines
        self.lines = text
        self.line(".intel_syntax noprefix")
        self.line(".text")
        self.line(".extern puts")
        for fn in list(self.analyzer.functions.values()) + list(self.analyzer.methods.values()):
            if not fn.decl.external:
                self.emit_function(fn)
        body = self.lines
        self.lines = [".intel_syntax noprefix"]
        if self.strings:
            self.line(".section .rodata")
            for value, name in self.strings.items():
                self.line(f"{name}:")
                self.line(f"  .asciz {json.dumps(value)}")
        self.lines.extend(body[1:])
        return "\n".join(self.lines) + "\n"

    def string_label(self, value: str) -> str:
        if value not in self.strings:
            self.strings[value] = f".LC{len(self.strings)}"
        return self.strings[value]

    def mem(self, offset: int) -> str:
        return f"QWORD PTR [rbp-{offset}]"

    def emit_function(self, fn: Function) -> None:
        self.function = fn
        self.return_label = self.label(f"return_{fn.symbol}")
        self.line(f".globl {fn.symbol}")
        self.line(f"{fn.symbol}:")
        self.line("  push rbp")
        self.line("  mov rbp, rsp")
        if fn.frame_size:
            self.line(f"  sub rsp, {fn.frame_size}")
        for param, register in zip(fn.decl.params, self.ARG_REGS):
            self.line(f"  mov {self.mem(param.binding.offset)}, {register}")
        for statement in fn.decl.body or []:
            self.emit_stmt(statement)
        self.line("  mov rax, 0")
        self.line(f"{self.return_label}:")
        self.line("  leave")
        self.line("  ret")
        self.line()

    def emit_stmt(self, node: Any) -> None:
        if isinstance(node, LetStmt):
            self.line(f"  lea rcx, [rbp-{node.binding.offset}]")
            if node.ty.aggregate:
                self.emit_aggregate_into("rcx", node.value)
            else:
                self.emit_expr(node.value)
                self.line("  mov QWORD PTR [rcx], rax")
        elif isinstance(node, ReturnStmt):
            if node.value is not None:
                self.emit_expr(node.value)
            self.line(f"  jmp {self.return_label}")
        elif isinstance(node, ExprStmt):
            self.emit_expr(node.value)
        elif isinstance(node, AssignStmt):
            self.emit_lvalue(node.target)
            self.line("  push rax")
            if getattr(node.target, "ty").aggregate:
                self.emit_expr(node.value)
                self.line("  pop rcx")
                self.copy_aggregate("rcx", "rax", node.target.ty.size)
            else:
                self.emit_expr(node.value)
                self.line("  pop rcx")
                self.line("  mov QWORD PTR [rcx], rax")
        elif isinstance(node, IfStmt):
            otherwise = self.label("else")
            done = self.label("ifend")
            self.emit_expr(node.condition)
            self.line("  cmp rax, 0")
            self.line(f"  je {otherwise}")
            for statement in node.then_body:
                self.emit_stmt(statement)
            self.line(f"  jmp {done}")
            self.line(f"{otherwise}:")
            for statement in node.else_body or []:
                self.emit_stmt(statement)
            self.line(f"{done}:")
        elif isinstance(node, WhileStmt):
            begin, done = self.label("while"), self.label("while_end")
            self.line(f"{begin}:")
            self.emit_expr(node.condition)
            self.line("  cmp rax, 0")
            self.line(f"  je {done}")
            for statement in node.body:
                self.emit_stmt(statement)
            self.line(f"  jmp {begin}")
            self.line(f"{done}:")
        else:
            raise AssertionError(type(node))

    def copy_aggregate(self, destination: str, source: str, size: int) -> None:
        for offset in range(0, size, 8):
            self.line(f"  mov rdx, QWORD PTR [{source}+{offset}]")
            self.line(f"  mov QWORD PTR [{destination}+{offset}], rdx")

    def emit_aggregate_into(self, destination: str, node: Any) -> None:
        # Child expressions may issue calls and therefore clobber every
        # caller-saved register.  Keep the parent destination on the stack
        # while recursively initializing its fields.
        self.line(f"  push {destination}")
        if isinstance(node, StructExpr):
            for _, name, value in node.values:
                self.line("  mov r11, QWORD PTR [rsp]")
                self.line(f"  lea rdi, [r11+{node.ty.offsets[name]}]")
                self.emit_value_into("rdi", value)
        elif isinstance(node, ArrayExpr):
            for index, value in enumerate(node.values):
                self.line("  mov r11, QWORD PTR [rsp]")
                self.line(f"  lea rdi, [r11+{index * node.ty.element.size}]")
                self.emit_value_into("rdi", value)
        else:
            self.line(f"  push {destination}")
            self.emit_expr(node)
            self.line("  pop rcx")
            self.copy_aggregate("rcx", "rax", node.ty.size)
        self.line("  add rsp, 8")

    def emit_value_into(self, destination: str, node: Any) -> None:
        if node.ty.aggregate:
            self.emit_aggregate_into(destination, node)
        else:
            self.line(f"  push {destination}")
            self.emit_expr(node)
            self.line("  pop rcx")
            self.line("  mov QWORD PTR [rcx], rax")

    def emit_lvalue(self, node: Any) -> None:
        if isinstance(node, NameExpr):
            self.line(f"  lea rax, [rbp-{node.binding.offset}]")
        elif isinstance(node, MemberExpr):
            if node.object.ty.kind == "pointer":
                self.emit_expr(node.object)
            else:
                self.emit_lvalue(node.object)
            if node.field_offset:
                self.line(f"  add rax, {node.field_offset}")
        elif isinstance(node, IndexExpr):
            if node.object.ty.kind == "pointer":
                self.emit_expr(node.object)
            else:
                self.emit_lvalue(node.object)
            self.line("  push rax")
            self.emit_expr(node.index)
            if node.element_size != 1:
                self.line(f"  imul rax, {node.element_size}")
            self.line("  pop rcx")
            self.line("  add rax, rcx")
        elif isinstance(node, UnaryExpr) and node.op == "*":
            self.emit_expr(node.value)
        else:
            raise AssertionError(f"not lvalue: {type(node)}")

    def emit_expr(self, node: Any) -> None:
        if isinstance(node, IntExpr):
            self.line(f"  mov rax, {node.value}")
        elif isinstance(node, BoolExpr):
            self.line(f"  mov rax, {1 if node.value else 0}")
        elif isinstance(node, StringExpr):
            self.line(f"  lea rax, [rip+{self.string_label(node.value)}]")
        elif isinstance(node, NameExpr):
            self.emit_lvalue(node)
            if not node.ty.aggregate:
                self.line("  mov rax, QWORD PTR [rax]")
        elif isinstance(node, MemberExpr):
            if hasattr(node, "enum_value"):
                self.line(f"  mov rax, {node.enum_value}")
            else:
                self.emit_lvalue(node)
                if not node.ty.aggregate:
                    self.line("  mov rax, QWORD PTR [rax]")
        elif isinstance(node, IndexExpr):
            self.emit_lvalue(node)
            if not node.ty.aggregate:
                self.line("  mov rax, QWORD PTR [rax]")
        elif isinstance(node, UnaryExpr):
            if node.op == "&":
                self.emit_lvalue(node.value)
            elif node.op == "*":
                self.emit_lvalue(node)
                if not node.ty.aggregate:
                    self.line("  mov rax, QWORD PTR [rax]")
            elif node.op == "-":
                self.emit_expr(node.value)
                self.line("  neg rax")
            else:
                self.emit_expr(node.value)
                self.line("  cmp rax, 0")
                self.line("  sete al")
                self.line("  movzx rax, al")
        elif isinstance(node, BinaryExpr):
            if node.op in ("&&", "||"):
                self.emit_boolean(node)
                return
            self.emit_expr(node.left)
            self.line("  push rax")
            self.emit_expr(node.right)
            self.line("  pop rcx")
            if node.op == "+":
                self.line("  add rax, rcx")
            elif node.op == "*":
                self.line("  imul rax, rcx")
            elif node.op == "-":
                self.line("  sub rcx, rax")
                self.line("  mov rax, rcx")
            elif node.op in ("/", "%"):
                self.line("  mov r8, rax")
                self.line("  mov rax, rcx")
                self.line("  cqo")
                self.line("  idiv r8")
                if node.op == "%":
                    self.line("  mov rax, rdx")
            else:
                self.line("  cmp rcx, rax")
                code = {"==": "sete", "!=": "setne", "<": "setl", "<=": "setle", ">": "setg", ">=": "setge"}[node.op]
                self.line(f"  {code} al")
                self.line("  movzx rax, al")
        elif isinstance(node, CastExpr):
            self.emit_expr(node.value)
        elif isinstance(node, CallExpr):
            if getattr(node, "builtin", None) == "println":
                self.emit_call("puts", [(node.args[0], False)])
            else:
                args: list[tuple[Any, bool]] = []
                if node.implicit is not None:
                    args.append((node.implicit, bool(getattr(node, "receiver_address", False))))
                args.extend((arg, False) for arg in node.args)
                self.emit_call(node.fn.symbol, args)
        elif isinstance(node, (StructExpr, ArrayExpr)):
            raise AssertionError("aggregate literals must be written into aggregate storage")
        else:
            raise AssertionError(type(node))

    def emit_boolean(self, node: BinaryExpr) -> None:
        short, done = self.label("bool_short"), self.label("bool_done")
        self.emit_expr(node.left)
        self.line("  cmp rax, 0")
        self.line(f"  {'je' if node.op == '&&' else 'jne'} {short}")
        self.emit_expr(node.right)
        self.line("  cmp rax, 0")
        self.line("  setne al")
        self.line("  movzx rax, al")
        self.line(f"  jmp {done}")
        self.line(f"{short}:")
        self.line(f"  mov rax, {0 if node.op == '&&' else 1}")
        self.line(f"{done}:")

    def emit_call(self, symbol: str, args: list[tuple[Any, bool]]) -> None:
        for node, by_address in reversed(args):
            if by_address:
                self.emit_lvalue(node)
            else:
                self.emit_expr(node)
            self.line("  push rax")
        for register in self.ARG_REGS[:len(args)]:
            self.line(f"  pop {register}")
        # Expressions can temporarily push values while preparing this call.
        # Align independently of that nesting, then restore the exact stack
        # pointer after return.  This is required by the System V ABI.
        self.line("  mov r11, rsp")
        self.line("  and rsp, -16")
        self.line("  sub rsp, 16")
        self.line("  mov QWORD PTR [rsp], r11")
        self.line(f"  call {symbol}")
        self.line("  mov r11, QWORD PTR [rsp]")
        self.line("  mov rsp, r11")


# ---------------------------------------------------------------------------
# Driver and module loader


def parse_file(path: Path) -> Program:
    try:
        return Parser(lex(path, path.read_text(encoding="utf-8"))).program(path)
    except OSError as exc:
        token = Token("", "", str(path), 1, 1, "")
        raise CompileError(token, str(exc))


def load_modules(entry: Path) -> list[Program]:
    loaded: dict[Path, Program] = {}
    visiting: set[Path] = set()

    def visit(path: Path) -> None:
        path = path.resolve()
        if path in loaded:
            return
        if path in visiting:
            token = Token("", "", str(path), 1, 1, "")
            raise CompileError(token, "cyclic module import")
        visiting.add(path)
        program = parse_file(path)
        for item in program.items:
            if isinstance(item, UseDecl):
                dependency = path.parent / item.filename
                if not dependency.exists():
                    raise CompileError(item.token, f"module file '{item.filename}' does not exist")
                visit(dependency)
        visiting.remove(path)
        loaded[path] = program

    visit(entry)
    return list(loaded.values())


def compile_source(source: Path) -> tuple[str, Analyzer]:
    analyzer = Analyzer(load_modules(source))
    analyzer.analyze()
    return Emitter(analyzer).compile(), analyzer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile ezC to a native x86-64 Linux executable.")
    parser.add_argument("source", type=Path, help="entry .ezc source file")
    parser.add_argument("-o", "--output", type=Path, default=Path("a.out"), help="output executable")
    parser.add_argument("--emit-asm", type=Path, help="also write generated assembly to this path")
    parser.add_argument("--check", action="store_true", help="parse and type-check only")
    args = parser.parse_args(argv)
    try:
        assembly, _ = compile_source(args.source)
        if args.emit_asm:
            args.emit_asm.write_text(assembly, encoding="utf-8")
        if args.check:
            return 0
        asm_path = args.output.with_suffix(args.output.suffix + ".s")
        asm_path.write_text(assembly, encoding="utf-8")
        result = subprocess.run(["cc", "-x", "assembler", "-no-pie", str(asm_path), "-o", str(args.output)],
                                text=True, capture_output=True)
        if result.returncode:
            sys.stderr.write(result.stderr)
            return result.returncode
        return 0
    except CompileError as exc:
        sys.stderr.write(exc.pretty() + "\n")
        return 1
    except OSError as exc:
        sys.stderr.write(f"ezc: unable to run assembler/linker: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
