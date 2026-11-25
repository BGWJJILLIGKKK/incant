from ..base import Method


class DraftMethod(Method):
    @classmethod
    def outputs(cls, wd):
        return [wd.root / "ebook.json"]
