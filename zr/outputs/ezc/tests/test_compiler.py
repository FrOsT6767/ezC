import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPILER = ROOT / "ezc.py"
EXAMPLES = ROOT / "examples"


class EzCCompilerTests(unittest.TestCase):
    def compile_and_run(self, source: Path) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "program"
            compiled = subprocess.run(
                [sys.executable, str(COMPILER), str(source), "-o", str(binary)],
                text=True, capture_output=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            self.assertTrue(binary.exists())
            magic = binary.read_bytes()[:4]
            self.assertEqual(magic, b"\x7fELF", "compiler must produce a native ELF binary")
            return subprocess.run([str(binary)], text=True, capture_output=True)

    def test_examples_compile_to_native_binaries(self) -> None:
        expected = {
            "hello.ezc": "Hello from ezC!",
            "functions.ezc": "function call returned 42",
            "structs.ezc": "struct field and method work",
            "arrays.ezc": "stack array indexing works",
            "memory.ezc": "explicit malloc/free works",
            "enums.ezc": "enum values are static integer tags",
            "c_interop.ezc": "C library call from ezC",
            "performance.ezc": "one million native loop iterations completed",
        }
        for filename, output in expected.items():
            with self.subTest(filename=filename):
                ran = self.compile_and_run(EXAMPLES / filename)
                self.assertEqual(ran.returncode, 0, ran.stderr)
                self.assertIn(output, ran.stdout)

    def test_module_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "math.ezc").write_text(
                "module demo.math;\nfn twice(x: i64) -> i64 { return x + x; }\n",
                encoding="utf-8",
            )
            main = folder / "main.ezc"
            main.write_text(
                "module demo.main;\nuse \"math.ezc\";\n"
                "fn main() -> i32 { if twice(21) == 42 { return 0; } return 1; }\n",
                encoding="utf-8",
            )
            ran = self.compile_and_run(main)
            self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_diagnostic_has_source_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "broken.ezc"
            source.write_text(
                "fn main() -> i32 {\n  let value = 1;\n  value = 2;\n  return 0;\n}\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(COMPILER), str(source), "--check"],
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cannot assign through immutable storage", result.stderr)
            self.assertIn("broken.ezc:3:", result.stderr)
            self.assertIn("value = 2", result.stderr)

    def test_assembly_is_direct_backend_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assembly = Path(temporary) / "hello.s"
            result = subprocess.run(
                [sys.executable, str(COMPILER), str(EXAMPLES / "hello.ezc"),
                 "--emit-asm", str(assembly), "--check"],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = assembly.read_text(encoding="utf-8")
            self.assertIn(".intel_syntax noprefix", output)
            self.assertIn("call puts", output)
            self.assertIn("main:", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
