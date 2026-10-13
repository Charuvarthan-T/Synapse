# Language-aware structural validation

Python pre-execution checks still use the `ast` module. Other languages listed in `graphify/lang_structural.py` `VALIDATION_LEVELS` are parsed with Tree-sitter when the grammar package is installed. Imports and calls are read from syntax-tree nodes.

A name the graph does not contain stays `UNKNOWN`. A Tree-sitter `ERROR` node is reported as a syntax error and is not a hallucination. An unknown suffix or a missing grammar adds a note and applies no checks. String search is not used as a substitute for a syntax tree.
