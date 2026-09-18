# Compiling and running ezC

## What the compiler does

When you run the following command, ezC parses and type-checks the source,
creates x86-64 assembly, and asks the Linux toolchain only to assemble and link
that assembly into an ELF binary.

```sh
cd /home/p1lot/Documents/Codex/2026-09-18/zr/outputs/ezc
python3 ezc.py examples/hello.ezc -o hello
./hello
```

Expected output:

```text
Hello from ezC!
```

The compiler also leaves `hello.s` beside the binary.  This is useful when
learning or auditing the backend: it is the x86-64 assembly produced by ezC,
not generated C code.

There is also a two-file module example.  `use` is resolved relative to the
importing source file, so compile the entry file:

```sh
python3 ezc.py examples/modules/main.ezc -o module-demo
./module-demo
```

Use `--emit-asm` to choose a different assembly file and `--check` to stop
after parsing/type checking:

```sh
python3 ezc.py examples/structs.ezc --emit-asm structs.s --check
python3 ezc.py examples/structs.ezc -o structs
./structs
```

## The seven requested starting examples

| Need | Source | What it demonstrates |
|---|---|---|
| 1. Hello world | `examples/hello.ezc` | `main`, string literals, `println` |
| 2. Function | `examples/functions.ezc` | typed parameters, inferred local type, return value |
| 3. Structures | `examples/structs.ezc` | plain data, `impl`, static method dispatch |
| 4. Arrays | `examples/arrays.ezc` | fixed-size stack arrays and indexing |
| 5. Memory | `examples/memory.ezc` | explicit `malloc`, `free`, pointer cast and dereference |
| 6. Performance | `examples/performance.ezc` | a million-iteration native integer loop |
| C interoperability | `examples/c_interop.ezc` | fixed-signature C function declaration |

For the performance example, compile and time the binary normally.  The loop
executes as code emitted by the backend; no Python process stays alive after
the compiler has exited.

```sh
python3 ezc.py examples/performance.ezc -o performance
time ./performance
```

## Testing the compiler

Run the full suite:

```sh
python3 -m unittest discover -s tests -v
```

Or, when `make` is installed:

```sh
make test
```

The test suite compiles and runs the language examples, verifies that the
output begins with the ELF magic bytes, checks imported modules, checks the
direct assembly backend, and verifies a source-located type error.

## Writing a small program

The following rules are enough to begin:

* Begin at `fn main() -> i32` and finish with `return 0;`.
* Use `let` when a local should not change; use `var` when it will change.
* State function parameter and return types.  Let ordinary initializers infer
  their type unless an explicit type conveys something important.
* Use `[T; count]` for a fixed-size array and `[a, b, c]` as its literal.
* Take an address with `&value` and access a pointer with `*pointer`.
* Declare non-variadic C functions with `extern fn` before calling them.

If a mistake is made, the compiler prints its file, line, the relevant source
line and a caret.  For example, writing to a `let` binding reports that a
mutable `var` binding is required.
