# Two stages, because the dashboard needs node to build and nothing to run.
#
# Untested: docker is not installed on the machine this was written on. The stages are
# ordinary and the commands are the ones the Makefile runs, but "it should work" is the
# honest description until someone runs it.

FROM node:24-slim AS dashboard
WORKDIR /web
# Copied separately so a source change does not reinstall the world.
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


FROM python:3.13-slim
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Dependencies before source, for the same reason.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project --no-dev

COPY src/ ./src/
COPY --from=dashboard /web/dist ./web/dist
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

# Binds to every interface, because inside a container localhost is unreachable from
# outside it. The port mapping in compose is what decides who can actually reach this.
CMD ["sillage", "serve", "--host", "0.0.0.0", "--port", "8000"]
