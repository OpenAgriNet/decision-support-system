# Changelog

## [Unreleased]

### Added
- Intent recognition: `core.intent.classify` labels a turn into `Intent(asks,
  confidence)` against a `Taxonomy`, plus the `UserTurn` envelope, the
  `LLMProvider` port, and an OpenAI-backed adapter (default model `gpt-5.6-luna`)
- Python project scaffolding: `pyproject.toml`, package layout, ruff, pytest (#57)
- Framework-boundary enforcement — `core/` cannot import `pydantic_ai` or
  `pydantic_graph`, checked by ruff and by an AST test (#57)
- pre-commit hooks and CI workflow (#57)
