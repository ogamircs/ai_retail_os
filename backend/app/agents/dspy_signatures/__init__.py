"""DSPy Signatures (Track 7 D1).

Each module exposes one `dspy.Signature` subclass + a thin
`dspy.Module` wrapper. The Signature defines I/O fields; the Module
composes the LLM call. The `compiled_to_markdown(module)` helper in
`app.agents.dspy_compile` turns a compiled Module into the
`prompts/<slug>/v<n+1>.md` body that the prompt registry resolves at
runtime — that's the bridge between DSPy's optimizer and our existing
prompt-versioning gate (Track 4 M4).

Imports here are lazy at the call site: `import dspy` only fires when
the optimizer / adapter actually runs. The cockpit demo path stays
unaffected when `dspy-ai` isn't installed (the `[dspy]` extra is
opt-in by design).
"""
