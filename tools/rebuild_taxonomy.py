import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TAXONOMY_DIR = BASE_DIR / "taxonomy"
GENERATED_DIR = BASE_DIR / "generated"

GENERATED_DIR.mkdir(exist_ok=True)

codes = {}
synonyms = {}
formulas = []
display = {}

for path in TAXONOMY_DIR.glob("*.json"):
    if path.name in {"display_classes.json", "display_mapping.json"}:
        continue

    print(f"Reading {path}...")

    with open(path, "r", encoding="utf-8") as f:
        entries = json.load(f)

    if isinstance(entries, dict):
        entries = list(entries.values())

    for entry in entries:
        code = entry.get("code")
        if not code:
            continue

        code = code.upper()

        label = entry.get("fr") or entry.get("label") or code
        codes[code] = label

        syns = entry.get("synonyms", [])
        full_syns = list(dict.fromkeys([code, label] + syns))
        synonyms[code] = full_syns

        for formula in entry.get("formulas", []):
            if formula not in formulas:
                formulas.append(formula)

        if "display" in entry:
            display[code] = entry["display"]

# Optional display files
display_classes_path = TAXONOMY_DIR / "display_classes.json"
display_mapping_path = TAXONOMY_DIR / "display_mapping.json"

display_classes = {}
display_mapping = {}

if display_classes_path.exists():
    with open(display_classes_path, "r", encoding="utf-8") as f:
        display_classes = json.load(f)

if display_mapping_path.exists():
    with open(display_mapping_path, "r", encoding="utf-8") as f:
        display_mapping = json.load(f)

# Merge display_mapping into display
for code, mapping in display_mapping.items():
    display[code.upper()] = mapping

outputs = {
    "codes.json": codes,
    "synonyms.json": synonyms,
    "formulas.json": formulas,
    "display.json": display,
    "display_classes.json": display_classes,
    "display_mapping.json": display_mapping,
}

for filename, content in outputs.items():
    out_path = GENERATED_DIR / filename
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)

print("Taxonomy rebuilt successfully.")
print(f"Codes: {len(codes)}")
print(f"Synonyms groups: {len(synonyms)}")
print(f"Formulas: {len(formulas)}")
print(f"Display entries: {len(display)}")
print(f"Display classes: {len(display_classes)}")
print(f"Display mapping entries: {len(display_mapping)}")