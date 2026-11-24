"""Dashboard operations for the independent ebook review draft."""

import hashlib
from urllib.parse import quote

from ..ebook import EbookDraft, publish, read_draft, save_draft


def ebook_data(wd):
    draft = read_draft(wd)
    return {"draft": draft.model_dump(), "revision": revision(draft)}


def revision(draft):
    return hashlib.sha256(draft.model_dump_json().encode()).hexdigest()


def save_ebook(dashboard, wd, body):
    if body.get("revision") != revision(read_draft(wd)):
        # Imported lazily because Dashboard installs these handlers.
        from .daemon import BusyError

        raise BusyError("This draft changed since you opened it. Copy your edits before reloading.")
    draft = EbookDraft.model_validate(body.get("draft"))
    if not draft.title.strip() or any(not chapter.title.strip() for chapter in draft.chapters):
        raise ValueError("Book and chapter titles must contain text.")
    result = {"draft": draft.model_dump(), "revision": revision(draft)}
    if body.get("publish") is True:
        target = dashboard._output_path(dashboard.cfg.paths.output_dir / "ebooks" / f"{wd.root.name}.epub")
        path = publish(wd, dashboard.cfg.paths.output_dir, target, draft=draft)
        result.update(download=f"/ebook-exports/{quote(wd.root.name, safe='')}/0", filename=path.name)
    save_draft(wd, draft)
    return result
