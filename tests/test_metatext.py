"""Author's notes / site text at chapter edges (parse metatext option)."""

import json

from incant.config import load_config
from incant.pipeline import discover, instantiate
from incant.schemas import BookMeta
from incant.stages.parse.chapters import build_chapters
from incant.stages.parse.metatext import MetatextSettings, strip_metatext
from incant.workdir import WorkDir

STORY = [
    "Ember had lived on the mountain for three hundred years, and in all that time no one had climbed it twice.",
    "“You came back,” she said, uncoiling from the warm stone. “Nobody comes back.”",
    "The knight lowered his visor. He had decided long ago to follow it wherever the road went, and he did.",
    "They talked until the stars wheeled overhead, and in the morning he was gone again.",
] * 5
WEB = [
    "This is a story about a knight and a dragon. Enjoy! :P",
    "For those of you who are regular readers, yes, this is a sequel to The Long Climb.",
    "* * *",
    "The Second Climb",
    *STORY,
    "* * *",
    "~Thanks for reading! This one was commissioned by a friend.",
    "If you enjoyed it, please leave a comment below and fave the story.",
    "Click here to send the author a tip",
]


def test_rules_remove_edge_notes_only():
    s = MetatextSettings(metatext="rules")
    [kept], removed, _ = strip_metatext([WEB], "The Second Climb by Someone - Site", "rules", s)
    assert kept == STORY  # notes, title line and the scene breaks around the story are gone
    assert [r.where for r in removed] == ["start"] * 4 + ["end"] * 4
    assert "follow it" in kept[2]  # plain prose with a call-to-action word stays


def test_old_novels_addressing_the_reader_are_kept():
    novel = ["Gentle reader, may you never feel what I then felt! May your eyes never shed such tears.",
             *STORY, "Reader, I married him."]
    [kept], removed, _ = strip_metatext([novel], "Jane Eyre", "rules", MetatextSettings())
    assert kept == novel and not removed


def test_share_limit_keeps_a_chapter_that_would_mostly_vanish():
    notes = ["~ A long note about writing this story, and how much I enjoyed writing it for you readers. :)"] * 8
    [kept], removed, log = strip_metatext([notes + STORY[:2]], "T", "rules", MetatextSettings(metatext_max_share=0.3))
    assert kept == notes + STORY[:2] and not removed and "kept as is" in log[0]


class FakeLLM:
    def __init__(self, label_all=True):
        self.label_all = label_all

    def chat_json(self, system, user, schema, max_tokens=0):
        nums = [int(x) for x in __import__("re").findall(r"^\[(\d+)\]", user, flags=8)]
        return schema(not_story=nums)  # labels everything it was shown


def test_llm_labels_need_a_cue():
    """The LLM proposes, but long plain narration is never removed on its word alone."""
    [kept], removed, _ = strip_metatext([WEB], "The Second Climb", "llm", MetatextSettings(metatext="llm"), FakeLLM())
    assert kept == STORY and len(removed) == 8


def make_book(tmp_path, fmt):
    wd = WorkDir(tmp_path / "work" / "b")
    wd.root.mkdir(parents=True)
    wd.write_meta(BookMeta(slug="b", title="The Second Climb", source="x", source_format=fmt))
    wd.text_path.write_text("\n\n".join(WEB) + "\n")
    return wd


def test_auto_cleans_web_pages_not_books(tmp_path):
    wd = make_book(tmp_path, "url")
    cl, notes = build_chapters(wd, MetatextSettings())
    text = wd.read_chapter_text(cl.chapters[0].file)
    assert text.startswith("Ember had lived") and "Click here" not in text
    assert "removed" in " ".join(notes) and "Click here to send" in (wd.root / "removed.txt").read_text()
    assert cl.chapters[0].word_count == sum(len(p.split()) for p in STORY)

    wd2 = make_book(tmp_path / "2", "txt")
    cl, _ = build_chapters(wd2, MetatextSettings())
    assert "Click here" in wd2.read_chapter_text(cl.chapters[0].file) and not (wd2.root / "removed.txt").exists()
    cl, _ = build_chapters(wd2, MetatextSettings(metatext="rules"))  # opted in for a book
    assert "Click here" not in wd2.read_chapter_text(cl.chapters[0].file)
    cl, _ = build_chapters(wd2, MetatextSettings(metatext="off"))
    assert not (wd2.root / "removed.txt").exists()


def test_default_option_keeps_parse_fingerprints(tmp_path):
    """Adding the option must not re-parse existing books; changing it must."""
    (tmp_path / "incant.toml").write_text("")
    stage = {s.name: s for s in discover()}["parse"]
    base = instantiate(load_config(tmp_path / "incant.toml"), stage, "basic").fingerprint()
    assert base == "d3c75f1f2798454a"  # the fingerprint before metatext existed
    (tmp_path / "incant.toml").write_text('[parse.basic]\nmetatext = "llm"\n')
    assert instantiate(load_config(tmp_path / "incant.toml"), stage, "basic").fingerprint() != base
    json.dumps(MetatextSettings().metatext_fingerprint())  # serializable
