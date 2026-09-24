FROM ghcr.io/astral-sh/uv:0.11-python3.12-bookworm-slim AS build
WORKDIR /src
ENV UV_FROZEN=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app
COPY . .
RUN uv sync --no-dev --no-editable --package dafter-runtime

FROM python:3.12-slim-bookworm
COPY --from=build /app /app
ENV PATH=/app/bin:$PATH
ENTRYPOINT ["dafter-runtime"]
CMD ["start"]
