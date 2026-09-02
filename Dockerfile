FROM python:3.12-slim

WORKDIR /app

# Dependencies first so a code change does not reinstall scipy every build.
COPY pyproject.toml README.md ./
COPY planbrain ./planbrain
RUN pip install --no-cache-dir -e ".[dev]"

COPY tools ./tools
COPY tests ./tests
COPY docs ./docs
# The suite reads these, so leaving them out does not shrink the run -- it
# breaks collection, which is how the offline job failed from the day
# test_bundle_manifest.py was written. Nobody saw it because there was no
# remote for CI to run on. Skipping those tests here instead would be worse:
# it would leave "the whole test suite runs with no network interface" true
# only of the tests that happened to be copied.
COPY packaging ./packaging
COPY datasets ./datasets
COPY desktop ./desktop
COPY .github ./.github
COPY Dockerfile ./

# Seed the demo at build time so `up` produces output in seconds, not minutes.
RUN python -m tools.seed_demo --out /app/data/local/demo.sqlite3

CMD ["python", "-m", "tools.demo"]
