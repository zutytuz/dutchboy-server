import json
import os

TAXONOMY_FOLDER = "taxonomy"
GENERATED_FOLDER = "generated"

codes = {}
synonyms = {}
formulas = []

for filename in os.listdir(TAXONOMY_FOLDER):

    if not filename.endswith(".json"):
        continue

    path = os.path.join(TAXONOMY_FOLDER, filename)

    print(f"Reading {path}...")

    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read().strip()

    if content == "":
        entries = []
    else:
        try:
            entries = json.loads(content)
        except json.JSONDecodeError as e:
            print("\nJSON ERROR")
            print(f"File: {path}")
            print(f"Line: {e.lineno}")
            print(f"Column: {e.colno}")
            print(f"Message: {e.msg}")
            print("\nFirst 200 chars:")
            print(repr(content[:200]))
            raise

    if not isinstance(entries, list):
        raise ValueError(f"{path} must contain a JSON list []")

    for entry in entries:

        code = entry["code"]

        codes[code] = entry["fr"]

        synonyms[code] = entry.get("synonyms", [])

        for formula in entry.get("formulas", []):
            if formula not in formulas:
                formulas.append(formula)

os.makedirs(GENERATED_FOLDER, exist_ok=True)

with open(os.path.join(GENERATED_FOLDER, "codes.json"), "w", encoding="utf-8") as f:
    json.dump(codes, f, ensure_ascii=False, indent=2)

with open(os.path.join(GENERATED_FOLDER, "synonyms.json"), "w", encoding="utf-8") as f:
    json.dump(synonyms, f, ensure_ascii=False, indent=2)

with open(os.path.join(GENERATED_FOLDER, "formulas.json"), "w", encoding="utf-8") as f:
    json.dump(formulas, f, ensure_ascii=False, indent=2)

print("Taxonomy rebuilt successfully.")
print(f"Codes: {len(codes)}")
print(f"Synonyms groups: {len(synonyms)}")
print(f"Formulas: {len(formulas)}")