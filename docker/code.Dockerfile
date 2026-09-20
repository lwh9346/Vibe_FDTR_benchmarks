# code variant: src/ only, no docs, no guides, no examples
# Agent can read src/ code but gets no README, AGENTS.md, CLAUDE.md, skills, refs, or examples
ARG BASE_TAG
FROM ${BASE_TAG}
RUN rm -rf .opencode/skills references examples \
    AGENTS.md CLAUDE.md README.md LICENSE
COPY docker/opencode.json opencode.json
