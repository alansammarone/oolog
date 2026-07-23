from invoke.tasks import task

COMMON_PARAMS = dict(echo=True, pty=True)
CI_PARAMS = dict(echo=True, pty=False, echo_format="{command}")
SOURCES = "oolog tests tasks.py"


@task
def format(ctx):
    ctx.run(f"uv run ruff format {SOURCES}", **COMMON_PARAMS)
    ctx.run(f"uv run docformatter -r -i {SOURCES}", **COMMON_PARAMS)


@task
def check(ctx):
    ctx.run(f"uv run ruff check {SOURCES} --fix", **COMMON_PARAMS)
    ctx.run("uv run ty check", **COMMON_PARAMS)


@task
def test(ctx):
    ctx.run("uv run pytest -rP tests/", **COMMON_PARAMS)


@task
def ci(ctx):
    """Verify format, lint, types and tests without modifying any file."""
    ctx.run(f"uv run ruff format --check {SOURCES}", **CI_PARAMS)
    ctx.run(f"uv run docformatter -r -c -d {SOURCES}", **CI_PARAMS)
    ctx.run(f"uv run ruff check {SOURCES}", **CI_PARAMS)
    ctx.run("uv run ty check", **CI_PARAMS)
    ctx.run("uv run pytest -rP tests/", **CI_PARAMS)
