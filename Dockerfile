FROM python:3.12-slim

WORKDIR /app

# Dependencies first so a code change does not reinstall scipy every build.
COPY pyproject.toml README.md ./
COPY planbrain ./planbrain
RUN pip install --no-cache-dir -e ".[dev]"

COPY tools ./tools
COPY tests ./tests
COPY docs ./docs

# Seed the demo at build time so `up` produces output in seconds, not minutes.
RUN python -m tools.seed_demo --out /app/data/local/demo.sqlite3

CMD ["python", "-m", "tools.demo"]
