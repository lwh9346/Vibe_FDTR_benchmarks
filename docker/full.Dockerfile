FROM node:22-alpine

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_NO_SYNC=1

RUN apk add --no-cache \
    python3 python3-dev py3-pip build-base gfortran openblas-dev \
    curl git bash ripgrep

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

COPY Vibe_FDTR/pyproject.toml Vibe_FDTR/uv.lock ./
COPY Vibe_FDTR/src/ ./src/
# opencode 扫描工作目录下的 .opencode/skills 作为 project skills，直接放到位
COPY Vibe_FDTR/.claude/skills/ ./.opencode/skills/
COPY Vibe_FDTR/references/ ./references/
COPY Vibe_FDTR/examples/ ./examples/
COPY Vibe_FDTR/README.md Vibe_FDTR/LICENSE ./

RUN uv sync --no-editable \
    && npm install -g opencode-ai \
    && rm -rf /usr/local/lib/node_modules/opencode-ai/node_modules \
    && apk del build-base gcc g++ gfortran python3-dev py3-pip openblas-dev \
    && rm -rf /root/.npm /root/.cache /var/cache/apk/* /tmp/*

COPY docker/opencode.json ./opencode.json
COPY Vibe_FDTR/AGENTS.md Vibe_FDTR/CLAUDE.md ./

ENV OPENCODE_MODEL="" \
    OPENCODE_SMALL_MODEL="" \
    ANTHROPIC_API_KEY="" \
    OPENAI_API_KEY="" \
    DEEPSEEK_API_KEY="" \
    OPENROUTER_API_KEY="" \
    GEMINI_API_KEY="" \
    GROQ_API_KEY="" \
    CEREBRAS_API_KEY="" \
    MOONSHOT_API_KEY="" \
    FIREWORKS_API_KEY="" \
    TOGETHER_API_KEY="" \
    XAI_API_KEY="" \
    VERTEX_API_KEY="" \
    HUGGINGFACE_API_KEY=""

WORKDIR /app
CMD ["bash"]
