FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /uvx /bin/
WORKDIR /src
ENV UV_FROZEN=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app
COPY . .
RUN uv sync --no-dev --no-editable --package dafter-runtime

FROM python:3.12-slim-bookworm
COPY --from=build /app /app
ENV PATH=/app/bin:$PATH
ENTRYPOINT ["dafter-runtime"]
CMD ["start"]
