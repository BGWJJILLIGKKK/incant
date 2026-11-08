"""List the license of every installed Python distribution, as markdown table rows (for THIRD_PARTY_NOTICES.md).

    uv run python tools/licenses.py                                   # this environment
    docker run --rm -i --entrypoint python incant_local - < tools/licenses.py   # everything in the image
"""

import re
from importlib.metadata import distributions

SKIP = {"incant"}


def license_of(meta) -> str:
    expr = meta.get("License-Expression")
    if expr:
        return expr.strip()
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    classifiers = [c for c in classifiers if c not in ("OSI Approved",)]
    if classifiers:
        return " / ".join(dict.fromkeys(classifiers))
    text = (meta.get("License") or "").strip()
    if text and "\n" not in text and len(text) < 60:
        return text
    if text:
        first = re.split(r"[\n.]", text)[0].strip()
        return first[:60] if first else "see package"
    return "see package"


rows = {}
for d in distributions():
    name = d.metadata["Name"]
    if not name or name.lower() in SKIP:
        continue
    rows[name.lower()] = (name, d.version, license_of(d.metadata))
for _, (name, ver, lic) in sorted(rows.items()):
    print(f"| {name} | {ver} | {lic} |")
