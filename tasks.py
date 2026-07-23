from invoke.tasks import task

COMMON_PARAMS = dict(echo=True, pty=True)


@task
def format(ctx):
    ctx.run("uv run ruff format oolog tests tasks.py", **COMMON_PARAMS)
    ctx.run("uv run docformatter -r -i oolog tests tasks.py", **COMMON_PARAMS)


@task
def check(ctx):
    ctx.run("uv run ruff check oolog tests tasks.py --fix", **COMMON_PARAMS)
    ctx.run("uv run ty check", **COMMON_PARAMS)


@task
def test(ctx):
    ctx.run("uv run pytest -rP tests/", **COMMON_PARAMS)
