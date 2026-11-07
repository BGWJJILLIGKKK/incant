from __future__ import annotations

from pathlib import Path

from ...workdir import WorkDir
from ..base import Context, Method


class OutputMethod(Method):
    """Must return the path(s) written. ctx.out overrides the default location."""

    EXTENSION = ".out"

    @classmethod
    def outputs(cls, wd: WorkDir) -> list[Path]:
        return [wd.out_dir]

    def default_path(self, ctx: Context) -> Path:
        wd = ctx.need_wd()
        name = f"{wd.read_meta().slug}{self.EXTENSION}"
        return ctx.out or (ctx.out_dir / name if ctx.out_dir else wd.out_dir / name)

    def run(self, ctx: Context) -> Path:
        raise NotImplementedError
