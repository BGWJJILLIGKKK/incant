"""Credits and license report: what a finished audiobook was made with, under which terms, and what is unknown.

Nothing here is required. Methods declare what is certain (`LICENSE`, `OUTPUT_TERMS`, `USES_LLM` on the class);
voices carry `license` / `source` / `consent`; the user can declare the rest in config:

    [credits]
    llm = "provider terms: Qwen license (commercial use allowed)"
    "synthesize.indextts" = "non-commercial: check your IndexTTS checkpoint's license"
    "voice.a_janelle_risa" = "consent: recorded for this project"

Terms are ranked none < attribution < provider terms < non-commercial; unknown terms are listed as gaps, never
assumed. A voice's consent is tracked apart from its terms: a recording can be free to use ("public domain") while
nobody has recorded the speaker's consent to a cloned voice, so a voice without `consent` gets a "not recorded" line
naming its provenance, whatever its terms. This is bookkeeping, not legal advice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

RANK = {"none": 0, "attribution": 1, "provider terms": 2, "non-commercial": 3}


def kind(terms: str | None) -> str | None:
    """The category of a terms string ("non-commercial: …" -> "non-commercial"), None when unknown."""
    if not terms:
        return None
    t = terms.casefold()
    if re.search(r"\bnon-?commercial\b|\bnc\b|research only", t):
        return "non-commercial"
    if t.startswith("provider") or "terms of service" in t or re.search(r"\btos\b", t):
        return "provider terms"
    if re.search(r"attribution|cc-by|credit", t):
        return "attribution"
    if re.search(r"^none\b|public domain|cc0|\bmit\b|apache|\bbsd\b|unencumbered", t):
        return "none"
    return None


@dataclass
class Item:
    what: str  # "synthesize: kokoro", "voice af_heart", "LLM qwen3.5-9b"
    license: str | None
    terms: str | None  # as declared
    note: str = ""
    consent: str = ""  # voices only: "" when consent is recorded (or not applicable), else what is missing

    @property
    def kind(self) -> str | None:
        return kind(self.terms)


@dataclass
class Report:
    items: list[Item] = field(default_factory=list)

    @property
    def gaps(self) -> list[Item]:
        return [i for i in self.items if i.kind is None]

    @property
    def weakest(self) -> str | None:
        known = [i.kind for i in self.items if i.kind]
        return max(known, key=RANK.__getitem__) if known else None

    @property
    def attributions(self) -> list[Item]:
        return [i for i in self.items if i.kind == "attribution"]

    @property
    def consent_gaps(self) -> list[Item]:
        return [i for i in self.items if i.consent]


def consent_gap(v, declared_terms: str | None) -> str:
    """What is missing about a voice's speaker consent ("" when recorded). Independent of the recording's terms."""
    if (v is not None and v.consent) or (declared_terms or "").casefold().startswith("consent:"):
        return ""
    provenance = f"voice provenance: {v.source if v is not None and v.source else 'unknown'}"
    if v is not None and v.consent is False:
        return f"recorded as not given ({provenance})"
    return f"not recorded ({provenance})"


def declared(cfg: Config) -> dict[str, str]:
    return {str(k): str(v) for k, v in ((cfg.model_extra or {}).get("credits") or {}).items()}


def method_item(cfg: Config, cls, stage: str, name: str) -> list[Item]:
    user = declared(cfg)
    out = [Item(f"{stage}: {name}", cls.LICENSE, user.get(f"{stage}.{name}") or cls.OUTPUT_TERMS)]
    if cls.USES_LLM:
        model = cfg.llm.model
        out.append(Item(f"LLM {model}", None, user.get("llm") or user.get(f"llm.{model}"),
                        note=f"via {cfg.llm.dialect} at {cfg.llm.base_url if cfg.llm.dialect != 'anthropic' else 'Anthropic API'}"))
    return out


def for_book(cfg: Config, wd, records: dict) -> Report:
    """The chain one book went through (pipeline.json records), its voices and its source."""
    from .pipeline import discover, instantiate, load_method_class

    stages = {s.name: s for s in discover()}
    rep = Report()
    meta = wd.read_meta()
    source_terms = declared(cfg).get("source")
    if source_terms is None and records.get("ingest", {}).get("method") == "gutenberg":
        source_terms = "none: Project Gutenberg text (public domain in the USA; check your country)"
    rep.items.append(Item(f"source text: {meta.source}", None, source_terms))
    seen_llm = False
    for stage, rec in records.items():
        name = rec.get("method")
        if stage not in stages or not name or name not in stages[stage].methods:
            continue
        cls = load_method_class(stages[stage], name)
        for item in method_item(cfg, cls, stage, name):
            if item.what.startswith("LLM "):
                if seen_llm:
                    continue
                seen_llm = True
            rep.items.append(item)
        if stage == "synthesize" and cls.NEEDS_VOICE_LIBRARY:
            synth = instantiate(cfg, stages[stage], name)
            lib = synth.voices().voices
            cast = wd.read_cast()
            used = sorted(set(cast.roles.values()) | ({cast.default_voice} if cast.default_voice else set())) if cast else []
            user = declared(cfg)
            for vid in used:
                v = lib.get(vid)
                terms = user.get(f"voice.{vid}") or (v.license if v and v.license else None)
                note = ", ".join(x for x in (f"source {v.source}" if v and v.source else "",
                                             "consent recorded" if v and v.consent else "") if x)
                rep.items.append(Item(f"voice {vid}", v.license if v else None, terms, note,
                                      consent=consent_gap(v, user.get(f"voice.{vid}"))))
    return rep


def text(rep: Report, title: str) -> str:
    lines = [f"Credits and terms: {title}", "=" * (len(title) + 19), "",
             "Made with (license; terms it puts on this audiobook):", ""]
    for i in rep.items:
        lines.append(f"  {i.what}")
        lines.append(f"      license: {i.license or 'unknown'}")
        lines.append(f"      terms:   {i.terms or 'unknown'}" + (f"  ({i.note})" if i.note else ""))
        if i.consent:
            lines.append(f"      consent: {i.consent}")
    lines += ["", f"Weakest known terms: {rep.weakest or 'none known'}"
              + (f" ({len(rep.gaps)} item{'s' if len(rep.gaps) != 1 else ''} unknown, below)" if rep.gaps else "")]
    if rep.attributions:
        lines += ["", "Required attributions:"] + [f"  - {i.what}: {i.terms}" for i in rep.attributions]
    if rep.gaps:
        lines += ["", "Gaps (terms not known; declare them under [credits] in your config to close them):"]
        lines += [f"  - {i.what}" for i in rep.gaps]
    if rep.consent_gaps:
        lines += ["", "Speaker consent not recorded (separate from the terms above; set the voice's `consent` field,",
                  "or declare it under [credits] as \"voice.<id>\" = \"consent: ...\", once you have it):"]
        lines += [f"  - {i.what}: {i.consent}" for i in rep.consent_gaps]
    lines += ["", "Bookkeeping from what incant was told, not legal advice."]
    return "\n".join(lines) + "\n"


def write(cfg: Config, wd, first: Path) -> Path:
    """credits.txt in the book's own output folder; <file>.credits.txt beside an -o FILE elsewhere."""
    from .pipeline import read_records
    from .stages.output.base import book_name

    folder = first.parent
    in_tree = folder.name == book_name(wd.read_meta())
    path = folder / ("credits.txt" if in_tree else f"{first.stem}.credits.txt")
    from .workdir import atomic_write

    atomic_write(path, text(for_book(cfg, wd, read_records(wd)), wd.read_meta().title))
    return path


def licenses_table(cfg: Config, stages) -> str:
    """`incant --licenses`: every method's known terms, and whether a chain of known-permissive methods
    exists for every stage."""
    from .pipeline import load_method_class

    lines, permissive = [], {}
    for st in stages:
        lines.append(f"{st.order}. {st.name}")
        permissive[st.name] = []
        for m in st.methods:
            cls = load_method_class(st, m)
            items = method_item(cfg, cls, st.name, m)
            kinds = [i.kind for i in items]
            label = "unknown" if None in kinds else max(kinds, key=RANK.__getitem__)
            if label == "none":
                permissive[st.name].append(m)
            extra = f"  + {items[1].what}: {items[1].terms or 'unknown'}" if len(items) > 1 else ""
            lines.append(f"     {m:<11} {label:<15} {cls.LICENSE or 'license unknown'}{extra}")
    lines.append("")
    pipeline_stages = [st for st in stages if not getattr(st.module, "PRE", False)]
    missing = [st.name for st in pipeline_stages if not permissive.get(st.name)]
    if missing:
        lines.append("Gap: no method with known permissive terms for: " + ", ".join(missing))
    else:
        def pick(st):
            ms = permissive[st.name]
            if cfg.pipeline.get(st.name) == "auto" and len(ms) == len(st.methods):
                return "auto"
            for want in (cfg.pipeline.get(st.name), getattr(st.module, "DEFAULT", None)):
                if want in ms:
                    return want
            return ms[0]

        chain = " ".join(f"{st.name}={pick(st)}" for st in pipeline_stages)
        lines.append(f"A chain with known permissive terms exists, e.g.: {chain}")
    lines.append("Unknown = not declared (method class or [credits] in config); bookkeeping, not legal advice.")
    return "\n".join(lines)
