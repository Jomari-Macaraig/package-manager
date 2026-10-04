"""The four narrow LLM jobs of pipeline v2 (extract, strategy, check, notes).

Each module owns its prompt, versions, answer schema, user-message builder,
code checks and fallback. packing.v2 runs them.
"""
