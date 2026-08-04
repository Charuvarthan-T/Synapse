# Memory for Graph-RAG based Agents

## Overview

Memory for Graph-RAG based Agents is a research project that enhances repository-level AI coding assistants by combining knowledge graphs, semantic memory, and graph-based retrieval. The framework helps Large Language Models (LLMs) understand large software repositories more effectively while reducing hallucinations and improving cross-file reasoning.

## Problem Statement

Traditional LLMs struggle to understand large codebases due to limited context windows. Existing Graph-RAG approaches mainly retrieve structural information and lack persistent memory and semantic understanding. This project addresses these limitations by introducing a graph-based memory framework for repository-level reasoning.

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
