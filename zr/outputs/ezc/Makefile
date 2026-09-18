PYTHON ?= python3
Ezc := $(PYTHON) ezc.py

.PHONY: test examples build clean

test:
	$(PYTHON) -m unittest discover -s tests -v

examples: | build
	$(Ezc) examples/hello.ezc -o build/hello
	$(Ezc) examples/functions.ezc -o build/functions
	$(Ezc) examples/structs.ezc -o build/structs
	$(Ezc) examples/arrays.ezc -o build/arrays
	$(Ezc) examples/memory.ezc -o build/memory
	$(Ezc) examples/performance.ezc -o build/performance

build:
	mkdir -p build

clean:
	rm -rf build
