# Memory for Graph-RAG based Agents

## Overview

Memory for Graph-RAG based Agents is a research project that enhances repository-level AI coding assistants by combining knowledge graphs, semantic memory, and graph-based retrieval. The framework helps Large Language Models (LLMs) understand large software repositories more effectively while reducing hallucinations and improving cross-file reasoning.

## Problem Statement

Traditional LLMs struggle to understand large codebases due to limited context windows. Existing Graph-RAG approaches mainly retrieve structural information and lack persistent memory and semantic understanding. This project addresses these limitations by introducing a graph-based memory framework for repository-level reasoning.

## VS Code Extension: Synapse

🧠 **Synapse for VS Code** lives in [`vscode-extension/`](vscode-extension/). It
bundles this repository's engine and brings its features into the editor:
graph-validated answers (bidirectional reasoning), weighted and community-aware
search, pre-execution hallucination checks for AI-written code, semantic graph
enrichment, and tools for GitHub Copilot's agent mode. It needs no API key: it
uses GitHub Copilot, Claude Code or Codex through your existing sign-in.

Download the `.vsix` from [Releases](https://github.com/Charuvarthan-T/Synapse/releases).
See [`vscode-extension/README.md`](vscode-extension/README.md) for installation,
usage, development and the release process.

## Getting Started

Follow this guide to clone the repository and get a working local setup.

### Prerequisites

- **Python 3.10+** (the project is tested against 3.10–3.14)
- **[uv](https://docs.astral.sh/uv/)** — used to manage the virtual environment and dependencies from `uv.lock`
  - Install with `pip install uv`, or see the [uv installation docs](https://docs.astral.sh/uv/getting-started/installation/) for platform-specific installers
- **Git**

### 1. Clone the repository

```bash
git clone https://github.com/Charuvarthan-T/Synapse.git
cd Synapse
```

### 2. Install dependencies

`uv sync` creates a `.venv` and installs the exact dependency versions pinned in `uv.lock`:

```bash
uv sync
```

To also pull in optional integrations (Neo4j, FalkorDB, PDF/office parsing, LLM backends such as OpenAI/Anthropic/Ollama, video transcription, etc.), install the extras you need, e.g.:

```bash
uv sync --extra neo4j --extra pdf --extra anthropic
# or install everything at once
uv sync --extra all
```

Available extras are listed under `[project.optional-dependencies]` in [pyproject.toml](pyproject.toml).

### 3. Run the CLI

Use `uv run` so commands execute inside the project's managed virtual environment:

```bash
uv run graphify --help
```

Common commands:

```bash
# Full extraction (AST + semantic LLM) for the current repository
uv run graphify extract .

# Incrementally update the graph after code changes (AST-only, no API cost)
uv run graphify update .

# Query the graph
uv run graphify explain "SomeClass"
uv run graphify path "NodeA" "NodeB"

# Install the graphify skill/config for your AI coding assistant
uv run graphify install --platform claude
```

### 4. Run the test suite

```bash
uv run pytest -q
```

### 5. Development tooling

```bash
uv run ruff check .      # lint
uv run pyright           # type-check
uv run pre-commit install  # enable pre-commit hooks (formatting/lint checks on commit)
```

> **Note (Windows users):** A small number of tests assume POSIX path separators, UTF-8 as the default text encoding, or short path lengths, and may fail on Windows for environment reasons unrelated to the code under test (e.g. `test_watch.py`, `test_obsidian_filename_cap.py`, `test_merge_chunks_validation.py`). Tests requiring optional LLM SDKs (e.g. `test_ollama_retry_cap.py`) also require the matching extra (`uv sync --extra openai`) to run.

## Features

- Repository knowledge graph generation
- Hierarchical community-aware retrieval
- Relationship-aware weighted graph traversal
- Semantic code graph using LLM-generated relationships
- Bidirectional LLM–Graph reasoning
- Persistent graph memory for AI agents

## Project Architecture

```
Repository
      │
      ▼
 Tree-sitter Parsing
      │
      ▼
   AST Generation
      │
      ▼
 Repository Knowledge Graph
      │
      ▼
 Community Detection
      │
      ▼
 Weighted Graph Retrieval (PPR)
      │
      ▼
 Large Language Model
      │
      ▼
 Repository-Aware Response
```

## Technologies Used

- Python
- Tree-sitter
- Graphify
- Neo4j
- Cypher
- Python-igraph
- Personalized PageRank (PPR)
- Leiden Community Detection

## Datasets

- SWE-Bench Lite
- CrossCodeEval

## Current Progress

- Implemented weighted graph retrieval
- Added community-aware retrieval
- Generated semantic code relationships
- Conducted preliminary retrieval evaluation

## Future Work

- Support multiple programming languages
- Dynamic graph updates
- Graph-based hallucination detection
- Multi-agent collaboration
- Large-scale repository evaluation

## Team Members

- Nandu Manoj
- Naren Sundar L
- Vatturu Pardheev
- Charuvarthan T

## Supervisor

**Ms. Rema M**

## References

1. REPOGRAPH (ICLR 2025)
2. Codebase-Memory (2026)
3. LocAgent (ACL 2025)
4. Think-on-Graph (ICLR 2024)
5. HippoRAG (NeurIPS 2024)
6. MemoTime (WWW 2026)

## License

This project is developed for academic and research purposes.
