# ezC

**ezC** is a deliberately small, statically typed language for native x86-64
Linux programs.  Its first implementation is a real compiler: it lexes,
parses and type-checks source into an AST, emits System V x86-64 assembly, and
invokes the platform linker to produce an ELF executable.  It is not an
interpreter and it does not translate ezC into C.

The language is intended to be easy to read, explicit about ownership and
memory, and close enough to C's execution model for command-line tools and
small games.

## Design in one minute

```ezc
extern fn puts(text: *u8) -> i32;

struct Vec2 { x: i64, y: i64 }

impl Vec2 {
    fn add(self: &Vec2, other: &Vec2) -> i64 {
        return self.x + other.x;
    }
}

fn main() -> i32 {
    let a = Vec2 { x: 20, y: 1 };
    let b = Vec2 { x: 22, y: 2 };
    println("answer follows");
    return a.add(&b) - 42;
}
```

* `let` is immutable; `var` is mutable.
* Types are inferred from initializers where unambiguous, or can be written as
  `name: Type`.
* Values live in predictable stack storage unless allocated explicitly.
  There is no garbage collector or hidden heap allocation.
* `&value` takes an address, `*pointer` dereferences it, and C functions are
  declared with `extern fn`.
* Structs are plain data. `impl` attaches statically dispatched methods; there
  are no classes, inheritance, virtual dispatch, or object headers.

Read [the language and compiler architecture](docs/DESIGN.md) before extending
the compiler.  It documents the intentional first-version boundaries.

## Quick start

```sh
python3 ezc.py examples/hello.ezc -o hello
./hello
python3 -m unittest discover -s tests -v
```

The compiler needs Python 3.11+ and a C toolchain (`cc`) to assemble and link
the emitted assembly.  The compiled program itself has no Python dependency.

## Layout

* `ezc.py` — compiler driver, lexer, parser, AST, type checker and x86-64 backend.
* `examples/` — executable language examples.
* `tests/` — compiler and end-to-end tests.
* `docs/DESIGN.md` — syntax, type system, ABI and roadmap.

## Supported in v0.1

Functions; integer and boolean arithmetic; `if` and `while`; structs and
methods; tagged integer enums; fixed-size stack arrays; pointers; C calls with
up to six scalar/pointer arguments; module declarations and file-based imports;
useful source locations in diagnostics.

The current ABI intentionally limits user function parameters and returns to
scalar/pointer values.  Aggregate values are stack-resident and passed by
reference.  Floating point, slices, generics, separate object-file builds and
cross-module symbol resolution are planned next; this keeps the initial backend
small and inspectable without pretending those difficult parts are finished.
