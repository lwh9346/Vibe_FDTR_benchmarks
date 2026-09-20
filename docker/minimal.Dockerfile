# minimal variant: bare metal, no fdtr CLI, no fdtr package, no docs, guides, skills, refs, examples
# Agent must implement everything from scratch using only numpy/scipy/matplotlib
ARG BASE_TAG
FROM ${BASE_TAG}
RUN rm -rf .opencode/skills references examples src/ \
    AGENTS.md CLAUDE.md README.md LICENSE \
    .venv/bin/fdtr \
    .venv/lib/python3.14/site-packages/fdtr/ \
    .venv/lib/python3.14/site-packages/fdtr_toolkit-*.dist-info/ \
    pyproject.toml uv.lock
# 已非 uv project，关闭继承自 full 的 UV_NO_SYNC，避免 `uv run` 每次告警
ENV UV_NO_SYNC=0
COPY docker/opencode.json opencode.json
