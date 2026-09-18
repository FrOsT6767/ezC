# ezC language and compiler design

## Goals

ezC favors a short learning curve and a C-like cost model:

| Decision | Reason |
|---|---|
| Static typing with local inference | catches errors before the binary runs without annotating ordinary local code |
| Values on the stack by default | lifetime and allocation cost remain visible and predictable |
| No GC / no exceptions | no surprise pauses or invisible control paths |
| Plain structs + `impl` | offers organization and methods without class hierarchies or dynamic dispatch |
| Native System V x86-64 code | enables Linux ELF programs and ordinary C ABI calls |
| Diagnostics retain line/column spans | compiler errors point at the source construct that caused them |

## Source model

One source file may begin with `module name;`.  Imports are intentionally
file-based:

```ezc
module game.main;
use "math.ezc";
```

`use` files are parsed and type-checked before the importing file.  Version
0.1 reserves this syntax and validates/imports the files, while its code
generator emits one combined assembly unit.  This is a practical first module
system, not a textual preprocessor: every source has a separate AST and module
name, duplicate function names are rejected, and the future object-file mode
can use the same module graph.

### Declarations

```ezc
extern fn puts(text: *u8) -> i32;

fn max(a: i64, b: i64) -> i64 { if a > b { return a; } return b; }

struct Player { hp: i64, alive: bool }

enum Direction { Up, Right, Down, Left }

impl Player {
    fn damage(self: &Player, amount: i64) -> i64 {
        self.hp = self.hp - amount;
        return self.hp;
    }
}
```

Every function has a return type.  Parameters are explicit.  `self` is only a
normal parameter name, but an `impl T` method is recorded as `T.method` and a
receiver expression such as `player.damage(5)` is lowered to a static call with
the receiver as its first argument.

### Types

* `i64`, `i32`, `u64`, `u8`, `usize`, `bool`, `void`
* `*T` — raw pointer to `T`; `&expr` produces one; `*expr` reads/writes through one
* `Name` — a declared struct or enum
* `[T; N]` — fixed-size stack array

In the current backend integer-like values all occupy an eight-byte machine
slot.  The type checker still distinguishes their names and rejects unrelated
assignments.  This is intentionally conservative while the backend is small.

```ezc
let score = 10;                    // inferred i64
let ready = true;                  // inferred bool
let p: *i64 = &score;              // explicit pointer type
let tiles: [i64; 4] = [1, 2, 3, 4];
```

`let` cannot be assigned again.  Use `var` for mutation.  Fields and array
elements are mutable when reached through mutable storage.  Array indexing in
v0.1 is intentionally unchecked, like C, so an index must be validated by the
program when safety is needed.

### Expressions and control flow

The compiler supports arithmetic, comparisons, `&&`, `||`, unary `!`/`-`,
function calls, field/index access, addresses and dereference.  `if` and
`while` use braces, with no mandatory parenthesis:

```ezc
var n = 4;
while n > 0 { n = n - 1; }
if n == 0 { println("done"); } else { println("unexpected"); }
```

Struct and array literals are direct aggregate initialization:

```ezc
let p = Point { x: 2, y: 3 };
let samples = [3, 5, 8];
```

### Memory and C interoperability

ezC does not have `new`, a GC or reference-counting.  A user can use a C
allocator explicitly:

```ezc
extern fn malloc(size: usize) -> *u8;
extern fn free(memory: *u8) -> void;

let raw = malloc(8);
let number: *i64 = cast[*i64](raw);
*number = 42;
free(raw);
```

This is a direct System V call: the first six arguments are placed in
`rdi`, `rsi`, `rdx`, `rcx`, `r8`, `r9`; integer/pointer results are read from
`rax`.  C declarations must describe non-variadic functions in this version.
`println("literal")` is a tiny convenience that calls libc `puts`.

## Compiler pipeline

```
ezC text
  -> Lexer (tokens with SourceSpan)
  -> Parser (typed AST nodes, no execution)
  -> Semantic analysis (names, types, mutability, layouts)
  -> x86-64 backend (.intel_syntax assembly)
  -> cc assembler/linker
  -> Linux x86-64 ELF
```

The compiler is intentionally a single readable Python module at this stage.
Its **phases are not mixed**: parser nodes contain source spans; semantic
analysis attaches resolved types/layouts; code generation only consumes those
resolved nodes.  That separation makes diagnostics trustworthy and enables a
future LLVM backend without reimplementing parsing or type checking.

The backend owns stack-frame layout and emits System V function prologues,
register argument moves, calls, branches, arithmetic, fields and arrays.  It
uses `cc` solely as the platform assembler/linker; it never asks a C compiler
to translate the ezC program.

## Intentional v0.1 boundaries and roadmap

The project is usable now for its demonstrated CLI scope, but does not claim
to be a full C replacement.  Next milestones are: register-width-aware integer
layout; f32/f64/SSE calling convention; slices and bounds checks selectable by
build mode; aggregate returns/parameters; object files and incremental module
builds; debug info; then an optional LLVM IR backend for optimization levels.

LLVM is not a shortcut for a language front end: lexing, parsing, name lookup,
type checking, diagnostics, layout, modules and ownership decisions remain
ezC's work.  A direct backend makes all of that tangible before LLVM is added
where its optimizers become worth the dependency.
