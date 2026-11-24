from ....ebook import publish
from ..base import PublishMethod


class EpubPublish(PublishMethod):
    """Write EPUB 3 with a table of contents and the imported cover when supported."""

    LICENSE = "project code"
    OUTPUT_TERMS = "none"

    def run(self, ctx):
        path = publish(ctx.need_wd(), ctx.out_dir or self.cfg.paths.output_dir, ctx.out)
        ctx.log(f"  ebook → {path}")
        return path
