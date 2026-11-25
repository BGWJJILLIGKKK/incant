from ....ebook import ensure_draft
from ..base import DraftMethod


class Manuscript(DraftMethod):
    """Split shared text into editable chapters while keeping the saved draft on repeat runs."""

    LICENSE = "project code"
    OUTPUT_TERMS = "none"

    def run(self, ctx):
        wd = ctx.need_wd()
        draft = ensure_draft(wd)
        ctx.log(f"  {len(draft.chapters)} ebook chapters → {self.outputs(wd)[0]}")
        return draft
