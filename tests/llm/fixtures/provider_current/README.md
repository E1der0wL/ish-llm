# Current Provider fixture

Synthetic data created with explicit Project Agents, typed model records and typed
Tool definitions. These archives exercise completed-response reopening, approval
before effects, effect-receipt reuse after failure, and Graph Step reuse on Resume.
They are not user data or a reader for previous development formats.

Generate from the repository root with Python 3.12.14:

```sh
.venv-linux312/bin/python -m tests.llm.fixtures.provider_current.generate
.venv-linux312/bin/python -m tests.llm.fixtures.provider_current.generate_graph
.venv-linux312/bin/python -m unittest -v tests.llm.test_provider_current
```

Each manifest records the base commit, whether source was modified, exact source
file hashes, Python version, storage versions, archive hash, and extracted file
hashes. A modified-source fixture is identified as such, not attributed to a clean
commit. The final storage cleanup regenerates these fixtures at its current source.
Tests verify archive integrity and assert that reopening never modifies the fixture.
