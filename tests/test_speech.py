import pytest

from incant.speech import normalize, roman_to_int


@pytest.mark.parametrize("text, want", [
    # Roman numerals: section words, regnal names, and the pronoun I left alone
    ("CHAPTER III.", "Chapter three."),
    ("Chapter I.", "Chapter one."),
    ("Henry VIII and Louis XIV", "Henry the Eighth and Louis the Fourteenth"),
    ("Elizabeth I.", "Elizabeth the First."),
    ("the letter I wrote", "the letter I wrote"),
    ("for my part I think", "for my part I think"),
    ("Mary I think so", "Mary I think so"),
    ("Malcolm X spoke", "Malcolm X spoke"),
    # caps runs / drop caps; acronyms kept
    ("MR. BENNET was among the earliest", "Mister Bennet was among the earliest"),
    ("IT is a truth universally acknowledged", "It is a truth universally acknowledged"),
    ("IRENE NORTON, *née* ADLER.", "Irene Norton, née Adler."),
    ("The FBI and the BBC.", "The FBI and the BBC."),
    # abbreviations
    ("Mr. Darcy met Dr. Watson at St. Paul’s on Baker St. today.",
     "Mister Darcy met Doctor Watson at Saint Paul’s on Baker Street today."),
    ("Mr Darcy", "Mister Darcy"),
    ("apples, pears, etc., and more", "apples, pears, et cetera, and more"),
    ("He left, &c. Then she came.", "He left, et cetera. Then she came."),
    ("He paid $3.50, etc. Then left.", "He paid three dollars and fifty cents, et cetera. Then left."),
    # numbers, years, money, times
    ("on the twentieth of March, 1888—I was", "on the twentieth of March, eighteen eighty-eight—I was"),
    ("from 1810–1820, about 3.5 miles", "from eighteen ten to eighteen twenty, about three point five miles"),
    ("the 23rd of May", "the twenty-third of May"),
    ("In the 1880s there were 1,500 men.", "In the eighteen eighties there were one thousand five hundred men."),
    ("He has £10,000 a year, and 2s. 6d. besides.", "He has ten thousand pounds a year, and two shillings and six pence besides."),
    ("We dine at 7:30 to-morrow.", "We dine at seven thirty tomorrow."),
    ("at 5:00 or 6:05", "at five o'clock or six oh five"),
    ("50% of No. 7", "fifty percent of number seven"),
    # dashes and markup
    ("the Boulanger——", "the Boulanger—"),
    ("“We are *not* here,”", "We are not here,"),
])
def test_normalize(text, want):
    assert normalize(text) == want


def test_titles_and_lone_numerals():
    assert normalize("I. A SCANDAL IN BOHEMIA", title=True) == "One. A Scandal In Bohemia"
    assert normalize("IV.", whole_span=True) == "Four."
    assert normalize("I.") == "I."  # not a whole narration span: could be the pronoun


def test_roman_to_int():
    assert [roman_to_int(r) for r in ("I", "IV", "XIV", "MCMLXXXIV", "IIII", "")] == [1, 4, 14, 1984, None, None]
