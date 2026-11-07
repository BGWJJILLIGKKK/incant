"""Deterministic parts of the `characters` parse method (no LLM)."""

from incant.stages.parse.characters.characters import (
    _Mention,
    ambiguous,
    build_characters,
    candidates,
    clean_aliases,
    clusters_from_links,
    core_tokens,
    pool,
    windows,
)

TEXT = ("Mr. Bennet and Mrs. Bennet spoke of Lizzy, whom some call Eliza; Elizabeth Bennet laughed. "
        "Jane, Miss Bennet, smiled. Kitty coughed. Lady Lucas and Sir William Lucas called. The Colonel bowed.")


def m(name, gender="unknown", aliases=(), age="unknown", speaks=False, desc=""):
    return _Mention(name=name, aliases=list(aliases), gender=gender, gender_evidence="she" if gender == "female" else "",
                    age=age, description=desc, speaks=speaks)


def test_clean_aliases_drops_pronouns_descriptions_possessives_and_unseen():
    got = clean_aliases("Mrs. Bennet", ["his lady", "she", "Mrs. Bennet", "Lizzy's mother", "Mama", "Lady Bennet"], TEXT)
    assert got == []  # 'Mama' and 'Lady Bennet' never occur in TEXT
    assert clean_aliases("Lizzy", ["Eliza", "Elizabeth Bennet", "her sister"], TEXT) == ["Eliza", "Elizabeth Bennet"]


def test_pool_verifies_names_and_drops_groups():
    entries = pool([("0001", TEXT, m("Lizzy", "female", ["Eliza"], speaks=True)),
                    ("0002", TEXT, m("Lizzy", "female")),
                    ("0002", TEXT, m("father")),                 # not a name
                    ("0002", TEXT, m("Mr. and Mrs. Bennet")),    # group
                    ("0002", TEXT, m("Charles Darcy", "male")),  # not in text
                    ("0003", TEXT, m("Sir William Lucas", "male"))])
    by = {e.name: e for e in entries}
    assert set(by) == {"Lizzy", "Sir William Lucas"}
    assert by["Lizzy"].mentions == 2 and by["Lizzy"].speaks and by["Lizzy"].chapters == ["0001", "0002"]


def test_candidates_need_name_overlap_and_compatible_gender():
    entries = pool([("1", TEXT, m("Elizabeth Bennet", "female")), ("1", TEXT, m("Elizabeth Bennet", "female")),
                    ("1", TEXT, m("Mr. Bennet", "male")), ("1", TEXT, m("Lizzy", "female", ["Elizabeth Bennet"])),
                    ("1", TEXT, m("Mrs. Bennet", "female")), ("1", TEXT, m("Kitty", "female"))])
    idx = {e.name: i for i, e in enumerate(entries)}
    names = lambda j: {entries[i].name for i in candidates(entries, j)}  # noqa: E731
    assert names(idx["Lizzy"]) == {"Elizabeth Bennet"}              # shared alias
    assert "Mr. Bennet" not in names(idx["Mrs. Bennet"])           # same surname, opposite gender
    assert names(idx["Kitty"]) == set()
    assert core_tokens("Sir William Lucas") == {"william", "lucas"}


def test_links_refuse_gender_conflict_and_build_profiles():
    entries = pool([("0002", TEXT, m("Elizabeth Bennet", "female", age="young_adult")),
                    ("0003", TEXT, m("Elizabeth Bennet", "female")),
                    ("0002", TEXT, m("Lizzy", "female", ["Miss Bennet"])),
                    *[(c, TEXT, m("Jane", "female", ["Miss Bennet"])) for c in ("0004", "0005", "0006")],
                    ("0002", TEXT, m("Mr. Bennet", "male"))])
    idx = {e.name: i for i, e in enumerate(entries)}
    notes = []
    groups = clusters_from_links(entries, {idx["Lizzy"]: idx["Elizabeth Bennet"], idx["Mr. Bennet"]: idx["Jane"]}, notes.append)
    assert any("gender conflict" in n for n in notes)
    chars = {c.canonical_name: c for c in build_characters(groups)}
    assert set(chars) == {"Elizabeth Bennet", "Jane", "Mr. Bennet"}
    liz = chars["Elizabeth Bennet"]
    assert liz.id == "elizabeth-bennet" and "Lizzy" in liz.aliases and liz.age.value == "young_adult"
    assert liz.mentions == 3 and liz.first_chapter == "0002" and chars["Jane"].mentions == 3
    # "Miss Bennet": Jane uses it 3x, Elizabeth 1x (< half) -> stays with Jane only
    assert "Miss Bennet" in chars["Jane"].aliases and "Miss Bennet" not in liz.aliases
    assert ambiguous(list(chars.values())) == {}


def test_windows_split_on_paragraphs():
    text = "\n\n".join(["word " * 100] * 7)
    w = windows(text, 250)
    assert len(w) == 4 and all(len(x.split()) <= 250 for x in w)
    assert "\n\n".join(w) == text


def test_reference_names_are_not_characters():
    from incant.stages.parse.characters.characters import _is_bad_name

    for bad in ("your Elizabeth", "my sister Philips", "Poor Lydia", "the present Mr. Darcy", "Mr. and Mrs. Gardiner", "the Bennets",
                "the two Netherfield gentlemen", "the eldest Miss Bennet"):
        assert _is_bad_name(bad), bad
    for good in ("Elizabeth", "the Colonel", "Sir William Lucas", "Mrs. Philips", "Lady Catherine de Bourgh", "Ludwig van Beethoven"):
        assert not _is_bad_name(good), good


def test_corroborated_and_canonical_name():
    from incant.stages.parse.characters.characters import Entry, canonical_name, corroborated

    assert corroborated(Entry("George Wickham"), Entry("Mr. Wickham"))
    assert not corroborated(Entry("Lady Lucas"), Entry("Lady Catherine"))
    jane = Entry("Jane", mentions=10)
    jane.alias_counts.update({"Jane Bennet": 2, "Miss Bennet": 3})
    claims = {"Jane Bennet": {0: 2}, "Miss Bennet": {0: 3, 1: 2}}
    assert canonical_name([jane], ["Jane Bennet", "Miss Bennet"], claims) == "Jane Bennet"


def test_token_budget_limits_concurrency():
    import threading
    import time

    from incant.llm import TokenBudget

    b = TokenBudget(100)
    active, peak = [0], [0]
    lock = threading.Lock()

    def job():
        n = b.acquire(40)
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.02)
        with lock:
            active[0] -= 1
        b.release(n)

    ts = [threading.Thread(target=job) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert peak[0] == 2 and b.free == 100
    assert b.acquire(500) == 100  # oversized request is capped, runs alone


def test_roles_kept_for_speakers_and_linked_by_description():
    from incant.stages.parse.characters.characters import corroborated, is_role

    assert is_role("Scrooge's nephew") and is_role("the nephew") and is_role("the first gentleman")
    assert not is_role("his wife") and not is_role("the two Netherfield gentlemen") and not is_role("the girls")
    text = "Fred, Scrooge's nephew, came in. “Merry Christmas!” said Scrooge's nephew. The nephew laughed."
    entries = pool([("1", text, m("Fred", "male", desc="Scrooge's cheerful nephew")),
                    ("1", text, m("Fred", "male")),
                    ("2", text, m("Scrooge's nephew", "male", ["the nephew"], speaks=True)),
                    ("2", text, m("the clerk", "male"))])  # a role that never speaks is dropped
    by = {e.name: e for e in entries}
    assert set(by) == {"Fred", "Scrooge's nephew"} and by["Scrooge's nephew"].role
    assert "the nephew" in by["Scrooge's nephew"].aliases
    j = [i for i, e in enumerate(entries) if e.role][0]
    assert [entries[i].name for i in candidates(entries, j)] == ["Fred"]
    assert not corroborated(by["Scrooge's nephew"], by["Fred"])  # a role link always gets the thinking check
    chars = build_characters(clusters_from_links(entries, {j: 0}, lambda *_: None))
    assert [c.canonical_name for c in chars] == ["Fred"] and "Scrooge's nephew" in chars[0].aliases


def test_bare_titles_are_scoped_per_chapter():
    text = "The Spirit spoke. The Ghost of Christmas Past and the Ghost of Christmas Present."
    entries = pool([("0003", text, m("the Spirit", speaks=True)), ("0004", text, m("the Spirit", speaks=True)),
                    ("0003", text, m("the Ghost of Christmas Past", aliases=["the Spirit"])),
                    ("0004", text, m("the Ghost of Christmas Present", aliases=["the Spirit"]))])
    spirits = [i for i, e in enumerate(entries) if e.name == "the Spirit"]
    assert len(spirits) == 2 and all(entries[i].scoped for i in spirits)
    for i in spirits:
        e = entries[i]
        cands = {entries[c].name for c in candidates(entries, i)} | {entries[k].name for k in range(len(entries)) if i in candidates(entries, k)}
        ghost = "the Ghost of Christmas Past" if e.chapters == ["0003"] else "the Ghost of Christmas Present"
        assert ghost in cands and len(cands - {ghost}) == 0
