from incant.casting import auto_cast
from incant.schemas import Cast, Character, Span, SpanKind, Voice


def ch(cid, gender, age="adult"):
    return Character(id=cid, canonical_name=cid, gender=gender, age=age)


def lines(chapter, *speakers):
    return [Span(id=f"{chapter}-{i:05d}", chapter_id=chapter, para=i, kind=SpanKind.dialogue, text="“x”", speaker=s)
            for i, s in enumerate(speakers)]


VOICES = {v.id: v for v in [
    Voice(id="narr", gender="female", age="adult"),
    Voice(id="f_adult", gender="female", age="adult"),
    Voice(id="f_teen", gender="female", age="teen"),
    Voice(id="m_adult", gender="male", age="adult"),
    Voice(id="m_older", gender="male", age="older"),
    Voice(id="untagged"),
]}


def test_gender_age_and_narrator_separation():
    chars = [ch("mum", "female", "adult"), ch("girl", "female", "teen"), ch("dad", "male", "adult"), ch("grandpa", "male", "elderly")]
    spans = {"1": lines("1", "mum", "mum", "mum", "dad", "dad", "girl", "grandpa")}
    cast = Cast(roles={"narrator": "narr"}, default_voice="narr")
    auto_cast(cast, chars, spans, VOICES)
    assert cast.roles["mum"] == "f_adult" and cast.roles["girl"] == "f_teen"
    assert cast.roles["dad"] == "m_adult" and cast.roles["grandpa"] == "m_older"
    assert "untagged" not in cast.roles.values()


def test_existing_roles_kept_and_scene_partners_dont_share():
    chars = [ch("a", "male"), ch("b", "male"), ch("c", "male")]
    # a and b talk in chapter 1; c only in chapter 2
    spans = {"1": lines("1", "a", "a", "a", "b", "b"), "2": lines("2", "c")}
    cast = Cast(roles={"narrator": "narr", "a": "m_older"})
    new = auto_cast(cast, chars, spans, VOICES, main_unique=1)
    assert cast.roles["a"] == "m_older"  # manual role untouched
    assert cast.roles["b"] == "m_adult"
    # pool exhausted for c: shares with b (no common chapters), never with main character a
    assert cast.roles["c"] == "m_adult"
    assert ("c", "m_adult", "shared with b") in new


def test_auto_roles_recast_but_manual_edits_kept():
    chars = [ch("a", "male"), ch("b", "female")]
    spans = {"1": lines("1", "a", "b")}
    cast = Cast(roles={"narrator": "narr"})
    auto_cast(cast, chars, spans, VOICES)
    assert cast.auto == {"a": "m_adult", "b": "f_adult"}
    cast.roles["b"] = "f_teen"  # hand edit
    better = dict(VOICES, m_new=Voice(id="m_new", gender="male", age="adult", tone="neutral"))
    auto_cast(cast, chars, spans, better)
    assert cast.roles["b"] == "f_teen" and "b" not in cast.auto  # manual kept
    assert cast.roles["a"] == "m_new" and cast.auto["a"] == "m_new"  # auto recomputed with new voice
