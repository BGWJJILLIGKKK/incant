from ..base import Method


class PublishMethod(Method):
    EXTENSION = ".epub"

    @classmethod
    def outputs(cls, wd):
        return [wd.root / "ebook-output.json"]
