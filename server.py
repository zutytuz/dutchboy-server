import json
import os
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from rapidfuzz import fuzz
from datetime import datetime, timedelta

# =====================================================
# 1. APP & CONFIG
# =====================================================

app = FastAPI()

BASE_DIR = Path(__file__).parent
API_KEY = os.getenv("DUTCHBOY_API_KEY")


# =====================================================
# 2. SECURITY
# =====================================================

def check_api_key(provided_key: Optional[str]) -> None:
    if API_KEY is None:
        raise HTTPException(status_code=500, detail="Server API key not configured")

    if provided_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# =====================================================
# 3. JSON FILE HELPERS
# =====================================================

def load_json_file(filename: str, default):
    generated_files = {
        "codes.json",
        "synonyms.json",
        "formulas.json",
        "display_mapping.json",
        "display_classes.json"
    }

    if filename in generated_files:
        path = BASE_DIR / "generated" / filename
    else:
        path = BASE_DIR / filename

    if not path.exists():
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

    with open("generated/display_mapping.json", "r", encoding="utf-8") as f:
        DISPLAY_MAPPING = json.load(f)

    with open("generated/display_classes.json", "r", encoding="utf-8") as f:
        DISPLAY_CLASSES = json.load(f)
def save_json_file(filename: str, data) -> None:
    path = BASE_DIR / filename

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def enrich_display(code: str) -> dict:
    code = str(code).upper().strip()

    display_mapping_file = load_display_mapping()
    display_classes = load_display_classes()

    mapping_root = display_mapping_file.get("mapping", display_mapping_file)
    mapping = mapping_root.get(code, {})

    display_class = mapping.get("display_class", mapping.get("class", ""))
    display_order = mapping.get("position", mapping.get("order", 999999))

    style = display_classes.get(display_class, {})

    return {
        "display_class": display_class,
        "display_order": display_order,
        "rgb": style.get("rgb", []),
        "font_rgb": style.get("font_rgb", [])
    }
CODES = load_json_file("codes.json", {})
SYNONYMS = load_json_file("synonyms.json", {})
FORMULAS = load_json_file("formulas.json", [])

DISPLAY_MAPPING = load_json_file("display_mapping.json", {})
DISPLAY_CLASSES = load_json_file("display_classes.json", {})
# =====================================================
# 4. TEXT HELPERS
# =====================================================

def normalize_text(text: str) -> str:
    text = str(text).lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.replace(".", " ")
    text = text.replace("-", " ")
    text = text.replace("_", " ")
    text = " ".join(text.split())
    return text


def absmatch_score(a: str, b: str) -> float:
    na = normalize_text(a)
    nb = normalize_text(b)

    if na == nb:
        return 1.0

    if na in nb or nb in na:
        return 0.92

    return SequenceMatcher(None, na, nb).ratio()


def is_number(value) -> bool:
    try:
        float(value)
        return True
    except Exception:
        return False


def is_year(value) -> bool:
    try:
        y = to_year(value)
        return 1900 <= y <= 2100
    except Exception:
        return False


def to_year(value) -> int:
    if value is None:
        raise ValueError("No year")

    # Numeric year or Excel date serial
    if isinstance(value, (int, float)):
        if 1900 <= int(value) <= 2100:
            return int(value)

        # Excel serial date, e.g. 45291 = 2024-ish
        if 20000 <= float(value) <= 60000:
            excel_epoch = datetime(1899, 12, 30)
            dt = excel_epoch + timedelta(days=float(value))
            return dt.year

    txt = str(value).strip()

    # Simple year inside text
    match = re.search(r"(19\d{2}|20\d{2}|21\d{2})", txt)
    if match:
        return int(match.group(1))

    raise ValueError(f"Cannot extract year from {value}")


# =====================================================
# 5. FORMULA LIBRARY
# =====================================================

def load_formula_library() -> list[str]:
    return load_json_file("formulas.json", [])


def save_formula_library(formulas: list[str]) -> None:
    save_json_file("formulas.json", formulas)


def get_formula_targets() -> set[str]:
    targets = set()

    for formula in load_formula_library():
        if "=" in formula:
            left, _ = formula.split("=", 1)
            targets.add(left.strip().upper())

    return targets
def load_display_mapping() -> dict:
    return load_json_file("display_mapping.json", {})


def load_display_classes() -> dict:
    return load_json_file("display_classes.json", {})




# =====================================================
# 6. ABSMATCH ENGINE
# =====================================================

def lookup_label(
    query: str,
    auto_threshold: float = 0.95,
    confirm_threshold: float = 0.80,
    max_matches: int = 10
) -> dict:
    query = str(query).strip()

    if not query:
        return {
            "status": "missing_label",
            "code": "",
            "label": "",
            "score": 0,
            "needs_confirmation": True
        }

    codes = load_json_file("codes.json", {})
    synonyms = load_json_file("synonyms.json", {})

    direct_code = query.upper()

    if direct_code in codes:
        return {
            "status": "auto_match",
            "method": "direct_code",
            "query": query,
            "code": direct_code,
            "label": codes[direct_code],
            "matched_on": direct_code,
            "score": 1.0,
            "needs_confirmation": False
        }

    normalized_query = normalize_text(query)

    for code, label in codes.items():
        candidates = [code, label]
        candidates.extend(synonyms.get(code, []))

        for candidate in candidates:
            if normalized_query == normalize_text(candidate):
                return {
                    "status": "auto_match",
                    "method": "exact_or_synonym",
                    "query": query,
                    "code": code,
                    "label": label,
                    "matched_on": candidate,
                    "score": 1.0,
                    "needs_confirmation": False
                }

    matches = []

    for code, label in codes.items():
        candidates = [code, label]
        candidates.extend(synonyms.get(code, []))

        best_candidate = None
        best_score = 0

        for candidate in candidates:
            score = fuzz.ratio(normalized_query, normalize_text(candidate)) / 100

            if score > best_score:
                best_score = score
                best_candidate = candidate

        matches.append({
            "code": code,
            "label": label,
            "matched_on": best_candidate,
            "score": round(best_score, 4)
        })

    matches = sorted(matches, key=lambda x: x["score"], reverse=True)

    if not matches:
        raise HTTPException(status_code=500, detail="No codes available")

    best = matches[0]

    if best["score"] >= auto_threshold:
        status = "auto_match"
        needs_confirmation = False
    elif best["score"] >= confirm_threshold:
        status = "needs_confirmation"
        needs_confirmation = True
    else:
        status = "no_reliable_match"
        needs_confirmation = True

    return {
        "status": status,
        "method": "fuzzy_levenshtein",
        "query": query,
        "code": best["code"],
        "label": best["label"],
        "matched_on": best["matched_on"],
        "score": best["score"],
        "best_match": best,
        "needs_confirmation": needs_confirmation,
        "thresholds": {
            "auto": auto_threshold,
            "confirm": confirm_threshold
        },
        "all_matches": matches[:max_matches]
    }


def lookup_label_light(query: str, min_score: float = 0.90):
    result = lookup_label(
        query=query,
        auto_threshold=0.97,
        confirm_threshold=min_score,
        max_matches=5
    )

    method = result.get("method", "")
    score = float(result.get("score", 0))

    exact_methods = {
        "direct_code",
        "exact_or_synonym"
    }

    if result.get("status") == "auto_match" and method in exact_methods:
        return {
            "code": result.get("code"),
            "label": result.get("label"),
            "score": result.get("score", 0),
            "method": method,
            "matched_on": result.get("matched_on", "")
        }

    if result.get("status") == "auto_match" and score >= min_score:
        return {
            "code": result.get("code"),
            "label": result.get("label"),
            "score": result.get("score", 0),
            "method": method,
            "matched_on": result.get("matched_on", "")
        }

    return None


def learn_synonym(code: str, synonym: str) -> dict:
    code = str(code).strip().upper()
    synonym = str(synonym).strip()

    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    if not synonym:
        raise HTTPException(status_code=400, detail="Missing synonym")

    codes = load_json_file("codes.json", {})
    synonyms = load_json_file("synonyms.json", {})

    if code not in codes:
        raise HTTPException(status_code=404, detail=f"Unknown code: {code}")

    if code not in synonyms:
        synonyms[code] = []

    if synonym not in synonyms[code]:
        synonyms[code].append(synonym)

    save_json_file("synonyms.json", synonyms)

    return {
        "status": "ok",
        "message": "Synonym learned",
        "code": code,
        "label": codes[code],
        "synonym": synonym,
        "synonym_count": len(synonyms[code])
    }


# =====================================================
# 7. WOLFRAMZETA ENGINE
# =====================================================

def avg_all(base_var: str, values: dict) -> float:
    prefix = base_var.lower() + ".y"
    nums = []

    for key, value in values.items():
        if key.lower().startswith(prefix):
            nums.append(float(value))

    if not nums:
        raise ValueError(f"No yearly values found for {base_var}")

    return sum(nums) / len(nums)


def extract_variables(expr: str):
    cleaned = re.sub(r"'[^']*'", "", expr)
    cleaned = re.sub(r'"[^"]*"', "", cleaned)

    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]*", cleaned)

    ignored = {
        "abs", "min", "max", "round",
        "avg_all", "AVG_ALL",
        "avg", "AVG"
    }

    ignored_lower = {x.lower() for x in ignored}

    return [
        t.lower()
        for t in tokens
        if t.lower() not in ignored_lower
    ]


def safe_name(var_name: str):
    return (
        var_name
        .lower()
        .replace(".", "__dot__")
        .replace("β", "beta")
    )


def safe_expr(expr: str):
    safe = expr.lower()

    all_vars = set(extract_variables(expr))

    for v in sorted(all_vars, key=len, reverse=True):
        safe = re.sub(
            r"(?<![a-zA-Z0-9_.])" + re.escape(v) + r"(?![a-zA-Z0-9_.])",
            safe_name(v),
            safe
        )

    return safe


def build_formula_map(formulas: list[str]) -> dict:
    formula_map = {}

    for formula in formulas:
        if "=" not in formula:
            continue

        left, right = formula.split("=", 1)
        left = left.strip().lower()
        right = right.strip().lower()

        formula_map.setdefault(left, []).append({
            "raw": formula,
            "right": right
        })

    return formula_map


def solve_engine(target: str, input_values: dict, formulas: list[str]) -> dict:
    target = str(target).strip().lower()
    values = {k.lower(): float(v) for k, v in input_values.items()}
    formula_map = build_formula_map(formulas)
    logs = []

    if not target:
        raise HTTPException(status_code=400, detail="Missing target")

    def formula_score(expr: str):
        needed = extract_variables(expr)

        known = 0
        missing = 0

        for v in needed:
            if v in values:
                known += 1
            else:
                missing += 1

        if missing == 0:
            return 10000 - len(needed)

        return known - (missing * 10) - (len(needed) * 0.01)

    def resolve(var_name: str, resolving=None):
        if resolving is None:
            resolving = set()

        var_name = var_name.lower()

        if var_name in values:
            return values[var_name]

        if var_name in resolving:
            raise Exception(f"Loop detected on {var_name}")

        if var_name not in formula_map:
            raise Exception(f"No formula available for {var_name}")

        resolving.add(var_name)

        candidates = sorted(
            formula_map[var_name],
            key=lambda f: formula_score(f["right"]),
            reverse=True
        )

        failed = []

        for candidate in candidates:
            right = candidate["right"]
            needed_vars = extract_variables(right)

            try:
                local_values = dict(values)

                for dep in needed_vars:
                    if dep not in local_values:
                        local_values[dep] = resolve(dep, resolving)

                safe_values = {
                    safe_name(k): v
                    for k, v in local_values.items()
                }

                def AVG_ALL(base_var):
                    return avg_all(str(base_var), values)

                result = eval(
                    safe_expr(right),
                    {
                        "__builtins__": {},
                        "abs": abs,
                        "min": min,
                        "max": max,
                        "round": round,
                        "AVG_ALL": AVG_ALL,
                        "avg_all": AVG_ALL
                    },
                    safe_values
                )

                values[var_name] = float(result)

                logs.append({
                    "variable": var_name,
                    "formula": candidate["raw"],
                    "dependencies": needed_vars,
                    "score": formula_score(right),
                    "result": result
                })

                resolving.remove(var_name)
                return float(result)

            except Exception as e:
                missing = []

                for dep in needed_vars:
                    if dep not in values and dep not in formula_map:
                        missing.append(dep)

                failed.append({
                    "formula": candidate["raw"],
                    "dependencies": needed_vars,
                    "missing": missing,
                    "reason": str(e)
                })

        resolving.remove(var_name)

        raise Exception({
            "message": f"Unable to solve {var_name}",
            "failed_formulas": failed
        })

    result = resolve(target)

    return {
        "target": target,
        "result": result,
        "logs": logs,
        "values": values
    }


# =====================================================
# 8. YEAR STRUCTURING
# =====================================================

def structure_year_rows(
    rows: list,
    auto_threshold: float = 0.95,
    confirm_threshold: float = 0.80
) -> dict:
    if not rows:
        raise HTTPException(status_code=400, detail="Missing rows")

    years = []

    for row in rows:
        year = row.get("year")

        if year is None:
            continue

        try:
            year_int = int(year)
            years.append(year_int)
        except Exception:
            continue

    years = sorted(list(set(years)))

    if not years:
        raise HTTPException(status_code=400, detail="No valid years found")

    year_mapping = {
        str(year): f"Y{i + 1}"
        for i, year in enumerate(years)
    }

    values = {}
    structured_rows = []
    needs_confirmation = []

    for row in rows:
        label_input = str(row.get("label", "")).strip()
        year = row.get("year")
        value = row.get("value")

        if not label_input or year is None or value is None:
            continue

        try:
            year_int = int(year)
            numeric_value = float(value)
        except Exception:
            continue

        if str(year_int) not in year_mapping:
            continue

        match = lookup_label(
            label_input,
            auto_threshold=auto_threshold,
            confirm_threshold=confirm_threshold,
            max_matches=5
        )

        if match["status"] != "auto_match":
            needs_confirmation.append({
                "original_label": label_input,
                "year": year_int,
                "value": numeric_value,
                "match": match
            })
            continue

        code = match["code"].lower()
        y_code = year_mapping[str(year_int)].lower()
        final_key = f"{code}.{y_code}"

        values[final_key] = numeric_value

        if year_int == years[-1]:
            values[code] = numeric_value

        structured_rows.append({
            "original_label": label_input,
            "official_code": match["code"],
            "official_label": match["label"],
            "match_method": match.get("method", ""),
            "score": match.get("score", 0),
            "year": year_int,
            "mapped_year": year_mapping[str(year_int)],
            "key": final_key,
            "value": numeric_value
        })

    return {
        "year_mapping": year_mapping,
        "values": values,
        "rows": structured_rows,
        "needs_confirmation": needs_confirmation,
        "summary": {
            "input_rows": len(rows),
            "structured_rows": len(structured_rows),
            "needs_confirmation": len(needs_confirmation),
            "years_detected": len(years)
        }
    }


def structure_and_solve_engine(
    target: str,
    rows: list,
    auto_threshold: float = 0.95,
    confirm_threshold: float = 0.80
) -> dict:
    target = str(target).strip()

    if not target:
        raise HTTPException(status_code=400, detail="Missing target")

    if not rows:
        raise HTTPException(status_code=400, detail="Missing rows")

    structured = structure_year_rows(
        rows,
        auto_threshold=auto_threshold,
        confirm_threshold=confirm_threshold
    )

    if structured.get("needs_confirmation"):
        return {
            "status": "needs_confirmation",
            "message": "Some labels need confirmation before solving",
            "structure": structured
        }

    values = structured.get("values", {})

    codes = load_json_file("codes.json", {})
    target_code = target.upper()
    formula_targets = get_formula_targets()

    if target_code not in codes and target_code not in formula_targets:
        lookup_result = lookup_label(target)

        if lookup_result.get("status") != "auto_match":
            return {
                "status": "target_needs_confirmation",
                "message": "Target needs confirmation before solving",
                "target_match": lookup_result,
                "structure": structured
            }

        target_code = lookup_result.get("code", target).upper()

    solve_result = solve_engine(
        target=target_code.lower(),
        input_values=values,
        formulas=load_formula_library()
    )

    return {
        "status": "solved",
        "target": target_code,
        "structure": structured,
        "solve": solve_result
    }
def build_values_for_requested_year(rows: list, requested_year: int, year_mapping: dict) -> dict:
    values = {}

    requested_year = int(requested_year)

    for row in rows:
        code = str(row.get("detected_code", "")).lower()
        year = int(row.get("real_year") or row.get("year"))
        value = float(row.get("value"))

        mapped_year = year_mapping[str(year)].lower()

        # Toujours garder les valeurs annuelles
        values[f"{code}.{mapped_year}"] = value

        # Valeur courante = valeur de l'année demandée
        if year == requested_year:
            values[code] = value

    return values


def filter_rows_until_year(rows: list, requested_year: int) -> list:
    requested_year = int(requested_year)

    return [
        row for row in rows
        if int(row.get("real_year") or row.get("year")) <= requested_year
    ]


def get_requested_mapped_year(requested_year: int, year_mapping: dict) -> str:
    requested_year = str(int(requested_year))

    if requested_year not in year_mapping:
        raise HTTPException(
            status_code=400,
            detail=f"Requested year {requested_year} not found in extracted data"
        )

    return year_mapping[requested_year]
def auto_structure_and_solve_engine(
    target: str,
    cells: list,
    year=None,
    auto_threshold: float = 0.95,
    confirm_threshold: float = 0.80
):
    if not target:
        raise HTTPException(status_code=400, detail="Missing target")

    if not cells:
        raise HTTPException(status_code=400, detail="Missing cells")

    auto_structured = auto_structure_cells(cells)
    extracted_rows = auto_structured.get("extracted_rows", [])

    if not extracted_rows:
        return {
            "status": "no_rows_extracted",
            "message": "No usable rows could be extracted from the grid",
            "auto_structure": auto_structured
        }

    year_mapping = auto_structured.get("year_mapping", {})

    # Si aucune année demandée : comportement actuel = dernière année globale
    if year is None or str(year).strip() == "":
        available_years = sorted([
            int(row.get("real_year") or row.get("year"))
            for row in extracted_rows
        ])
        requested_year = available_years[-1]
    else:
        requested_year = int(year)

    requested_mapped_year = get_requested_mapped_year(requested_year, year_mapping)

    rows_until_year = filter_rows_until_year(extracted_rows, requested_year)

    values = build_values_for_requested_year(
        rows=rows_until_year,
        requested_year=requested_year,
        year_mapping=year_mapping
    )

    solve_result = solve_engine(
        target=target,
        input_values=values,
        formulas=load_formula_library()
    )

    return {
        "status": "solved",
        "target": target,
        "requested_year": requested_year,
        "requested_mapped_year": requested_mapped_year,
        "auto_structure": auto_structured,
        "solve": solve_result
    }
# =====================================================
# 9. AUTO TABLE STRUCTURING V2
# Multi-tableaux + mapping années réel -> Y
# =====================================================

def build_grid_from_cells(cells: list) -> dict:
    grid = {}

    for cell in cells:
        try:
            r = int(cell.get("row"))
            c = int(cell.get("col"))
            v = cell.get("value")
        except Exception:
            continue

        if v is None or str(v).strip() == "":
            continue

        grid[(r, c)] = v

    return grid


def detect_table_blocks(grid: dict, max_row_gap: int = 1, max_col_gap: int = 1) -> list:
    """
    Détecte des blocs de cellules non vides.
    L'idée : si deux zones sont séparées par au moins une ligne/colonne vide,
    elles deviennent deux blocs différents.
    """

    if not grid:
        return []

    points = set(grid.keys())
    visited = set()
    blocks = []
    block_id = 1

    for start in sorted(points):
        if start in visited:
            continue

        stack = [start]
        component = []
        visited.add(start)

        while stack:
            r, c = stack.pop()
            component.append((r, c))

            for rr in range(r - max_row_gap - 1, r + max_row_gap + 2):
                for cc in range(c - max_col_gap - 1, c + max_col_gap + 2):
                    candidate = (rr, cc)

                    if candidate in points and candidate not in visited:
                        if abs(rr - r) <= max_row_gap + 1 and abs(cc - c) <= max_col_gap + 1:
                            visited.add(candidate)
                            stack.append(candidate)

        rows = [p[0] for p in component]
        cols = [p[1] for p in component]

        blocks.append({
            "block_id": block_id,
            "min_row": min(rows),
            "max_row": max(rows),
            "min_col": min(cols),
            "max_col": max(cols),
            "cell_count": len(component),
            "cells": component
        })

        block_id += 1

    return blocks


def get_block_grid(grid: dict, block: dict) -> dict:
    block_grid = {}

    for key in block["cells"]:
        if key in grid:
            block_grid[key] = grid[key]

    return block_grid


def detect_year_cells_in_grid(grid: dict) -> list:
    year_cells = []

    for (r, c), v in grid.items():
        if is_year(v):
            year_cells.append({
                "row": r,
                "col": c,
                "year": to_year(v)
            })

    return year_cells


def detect_label_cells_in_grid(grid: dict) -> list:
    label_cells = []

    IGNORED_STRUCTURE_CODES = {
        "ETAT.BILAN",
        "ETAT.CDR",
        "ETAT.TFT"
    }

    for (r, c), v in grid.items():

        if is_year(v):
            continue

        if is_number(v):
            continue

        match = lookup_label_light(str(v))

        if match and match.get("code") not in IGNORED_STRUCTURE_CODES:
            label_cells.append({
                "row": r,
                "col": c,
                "raw": str(v),
                "match": match
            })

    return label_cells

def score_orientation(year_cells: list, label_cells: list) -> dict:
    year_rows = {}
    year_cols = {}
    label_rows = {}
    label_cols = {}

    for y in year_cells:
        year_rows[y["row"]] = year_rows.get(y["row"], 0) + 1
        year_cols[y["col"]] = year_cols.get(y["col"], 0) + 1

    for l in label_cells:
        label_rows[l["row"]] = label_rows.get(l["row"], 0) + 1
        label_cols[l["col"]] = label_cols.get(l["col"], 0) + 1

    best_year_row = max(year_rows, key=year_rows.get)
    best_year_col = max(year_cols, key=year_cols.get)
    best_label_row = max(label_rows, key=label_rows.get)
    best_label_col = max(label_cols, key=label_cols.get)

    horizontal_score = year_rows[best_year_row] + label_cols[best_label_col]
    vertical_score = year_cols[best_year_col] + label_rows[best_label_row]

    if horizontal_score >= vertical_score:
        return {
            "orientation": "labels_as_rows_years_as_columns",
            "horizontal_score": horizontal_score,
            "vertical_score": vertical_score,
            "header_year_row": best_year_row,
            "label_col": best_label_col
        }

    return {
        "orientation": "labels_as_columns_years_as_rows",
        "horizontal_score": horizontal_score,
        "vertical_score": vertical_score,
        "year_col": best_year_col,
        "header_label_row": best_label_row
    }


def extract_horizontal_rows(
    grid: dict,
    year_cells: list,
    label_cells: list,
    header_year_row: int,
    label_col: int,
    block_id: int = None
) -> list:

    years_by_col = {}

    for y in year_cells:
        if y["row"] == header_year_row:
            years_by_col[y["col"]] = y["year"]

    labels_by_row = {}

    for l in label_cells:
        if l["col"] == label_col:
            labels_by_row[l["row"]] = l

    extracted_rows = []

    for data_row, label_data in labels_by_row.items():
        for data_col, year in years_by_col.items():
            value = grid.get((data_row, data_col))

            if value is None:
                continue

            try:
                numeric_value = float(value)
            except Exception:
                continue

            extracted_rows.append({
                "label": label_data["raw"],
                "detected_code": label_data["match"]["code"],
                "detected_label": label_data["match"]["label"],
                "year": year,
                "value": numeric_value,
                "source_block": block_id,
                "source_row": data_row,
                "source_col": data_col
            })

    return extracted_rows


def extract_vertical_rows(
    grid: dict,
    year_cells: list,
    label_cells: list,
    year_col: int,
    header_label_row: int,
    block_id: int = None
) -> list:

    years_by_row = {}

    for y in year_cells:
        if y["col"] == year_col:
            years_by_row[y["row"]] = y["year"]

    labels_by_col = {}

    for l in label_cells:
        if l["row"] == header_label_row:
            labels_by_col[l["col"]] = l

    extracted_rows = []

    for data_row, year in years_by_row.items():
        for data_col, label_data in labels_by_col.items():
            value = grid.get((data_row, data_col))

            if value is None:
                continue

            try:
                numeric_value = float(value)
            except Exception:
                continue

            extracted_rows.append({
                "label": label_data["raw"],
                "detected_code": label_data["match"]["code"],
                "detected_label": label_data["match"]["label"],
                "year": year,
                "value": numeric_value,
                "source_block": block_id,
                "source_row": data_row,
                "source_col": data_col
            })

    return extracted_rows


def auto_structure_single_block(grid: dict, block: dict) -> dict:
    block_id = block["block_id"]
    block_grid = get_block_grid(grid, block)

    year_cells = detect_year_cells_in_grid(block_grid)
    label_cells = detect_label_cells_in_grid(block_grid)

    if not year_cells:
        return {
            "block_id": block_id,
            "status": "ignored",
            "reason": "no_year_detected",
            "bounds": {
                "min_row": block["min_row"],
                "max_row": block["max_row"],
                "min_col": block["min_col"],
                "max_col": block["max_col"]
            },
            "cell_count": block["cell_count"]
        }

    if not label_cells:
        return {
            "block_id": block_id,
            "status": "ignored",
            "reason": "no_financial_label_detected",
            "bounds": {
                "min_row": block["min_row"],
                "max_row": block["max_row"],
                "min_col": block["min_col"],
                "max_col": block["max_col"]
            },
            "cell_count": block["cell_count"],
            "year_cells": year_cells
        }

    orientation_info = score_orientation(year_cells, label_cells)
    orientation = orientation_info["orientation"]

    if orientation == "labels_as_rows_years_as_columns":
        extracted_rows = extract_horizontal_rows(
            grid=block_grid,
            year_cells=year_cells,
            label_cells=label_cells,
            header_year_row=orientation_info["header_year_row"],
            label_col=orientation_info["label_col"],
            block_id=block_id
        )
    else:
        extracted_rows = extract_vertical_rows(
            grid=block_grid,
            year_cells=year_cells,
            label_cells=label_cells,
            year_col=orientation_info["year_col"],
            header_label_row=orientation_info["header_label_row"],
            block_id=block_id
        )

    if not extracted_rows:
        return {
            "block_id": block_id,
            "status": "ignored",
            "reason": "no_rows_extracted",
            "orientation": orientation,
            "bounds": {
                "min_row": block["min_row"],
                "max_row": block["max_row"],
                "min_col": block["min_col"],
                "max_col": block["max_col"]
            },
            "cell_count": block["cell_count"],
            "year_cells": year_cells,
            "label_cells": label_cells,
            "summary": {
                "years_detected": len(year_cells),
                "labels_detected": len(label_cells),
                "horizontal_score": orientation_info["horizontal_score"],
                "vertical_score": orientation_info["vertical_score"]
            }
        }

    return {
        "block_id": block_id,
        "status": "structured",
        "orientation": orientation,
        "bounds": {
            "min_row": block["min_row"],
            "max_row": block["max_row"],
            "min_col": block["min_col"],
            "max_col": block["max_col"]
        },
        "cell_count": block["cell_count"],
        "year_cells": year_cells,
        "label_cells": label_cells,
        "extracted_rows": extracted_rows,
        "summary": {
            "years_detected": len(year_cells),
            "labels_detected": len(label_cells),
            "rows_extracted": len(extracted_rows),
            "horizontal_score": orientation_info["horizontal_score"],
            "vertical_score": orientation_info["vertical_score"]
        }
    }


def build_global_year_mapping(extracted_rows: list) -> dict:
    years = sorted(list(set(int(row["year"]) for row in extracted_rows)))

    year_mapping = {
        str(year): f"Y{i + 1}"
        for i, year in enumerate(years)
    }

    reverse_year_mapping = {
        f"Y{i + 1}": str(year)
        for i, year in enumerate(years)
    }

    return {
        "year_mapping": year_mapping,
        "reverse_year_mapping": reverse_year_mapping
    }


def add_mapped_years_to_rows(extracted_rows: list, year_mapping: dict) -> list:
    enriched = []

    for row in extracted_rows:
        real_year = int(row["year"])
        mapped_year = year_mapping[str(real_year)]

        new_row = dict(row)
        new_row["real_year"] = real_year
        new_row["mapped_year"] = mapped_year
        new_row["key"] = f"{str(row['detected_code']).lower()}.{mapped_year.lower()}"

        enriched.append(new_row)

    return enriched


def detect_conflicts(extracted_rows: list) -> list:
    seen = {}
    conflicts = []

    for row in extracted_rows:
        code = str(row.get("detected_code", "")).upper()
        year = int(row.get("year"))
        value = float(row.get("value"))
        key = f"{code}.{year}"

        if key not in seen:
            seen[key] = row
            continue

        previous = seen[key]
        previous_value = float(previous.get("value"))

        if abs(previous_value - value) > 0.000001:
            conflicts.append({
                "code": code,
                "year": year,
                "key": key,
                "values": [
                    previous_value,
                    value
                ],
                "sources": [
                    {
                        "block": previous.get("source_block"),
                        "row": previous.get("source_row"),
                        "col": previous.get("source_col")
                    },
                    {
                        "block": row.get("source_block"),
                        "row": row.get("source_row"),
                        "col": row.get("source_col")
                    }
                ]
            })

    return conflicts


def rows_to_facts(extracted_rows: list) -> list:
    facts = []

    for row in extracted_rows:
        code = str(row.get("detected_code", "")).upper()
        display_info = enrich_display(code)

        fact = {
            "code": code,
            "label": row.get("detected_label", ""),
            "real_year": row.get("real_year", row.get("year")),
            "mapped_year": row.get("mapped_year", ""),
            "key": row.get("key", ""),
            "value": row.get("value"),
            "source_block": row.get("source_block"),
            "source_row": row.get("source_row"),
            "source_col": row.get("source_col")
        }

        fact.update(display_info)
        facts.append(fact)

    return facts


def auto_structure_cells(cells: list) -> dict:
    """
    V2 :
    - détecte plusieurs blocs/tableaux ;
    - structure chaque bloc séparément ;
    - conserve les années locales ;
    - crée un mapping global 2022 -> Y1 ;
    - retourne des facts prêts pour la suite.
    """

    if not cells:
        raise HTTPException(status_code=400, detail="Missing cells")

    grid = build_grid_from_cells(cells)

    if not grid:
        raise HTTPException(status_code=400, detail="No usable cells")

    blocks = detect_table_blocks(grid)
    structured_blocks = []
    ignored_blocks = []
    all_extracted_rows = []

    for block in blocks:
        block_result = auto_structure_single_block(grid, block)

        if block_result["status"] == "structured":
            structured_blocks.append(block_result)
            all_extracted_rows.extend(block_result.get("extracted_rows", []))
        else:
            ignored_blocks.append(block_result)

    if not all_extracted_rows:
        return {
            "status": "no_usable_rows",
            "mode": "multi_block_v2",
            "blocks_detected": len(blocks),
            "blocks_structured": 0,
            "blocks_ignored": len(ignored_blocks),
            "year_mapping": {},
            "reverse_year_mapping": {},
            "blocks": [],
            "ignored_blocks": ignored_blocks,
            "extracted_rows": [],
            "facts": [],
            "conflicts": [],
            "summary": {
                "cells_received": len(cells),
                "rows_extracted": 0,
                "facts_created": 0,
                "conflicts_detected": 0
            }
        }

    mappings = build_global_year_mapping(all_extracted_rows)
    year_mapping = mappings["year_mapping"]
    reverse_year_mapping = mappings["reverse_year_mapping"]

    enriched_rows = add_mapped_years_to_rows(all_extracted_rows, year_mapping)
    conflicts = detect_conflicts(enriched_rows)
    facts = rows_to_facts(enriched_rows)

    return {
        "status": "structured",
        "mode": "multi_block_v2",
        "blocks_detected": len(blocks),
        "blocks_structured": len(structured_blocks),
        "blocks_ignored": len(ignored_blocks),
        "year_mapping": year_mapping,
        "reverse_year_mapping": reverse_year_mapping,
        "blocks": structured_blocks,
        "ignored_blocks": ignored_blocks,
        "extracted_rows": enriched_rows,
        "facts": facts,
        "conflicts": conflicts,
        "summary": {
            "cells_received": len(cells),
            "rows_extracted": len(enriched_rows),
            "facts_created": len(facts),
            "conflicts_detected": len(conflicts)
        }
    }


# =====================================================
# STRUCTURE YEAR ROWS V2
# Valeur courante = dernière année disponible par variable
# =====================================================

def structure_year_rows(
    rows: list,
    auto_threshold: float = 0.95,
    confirm_threshold: float = 0.80
) -> dict:
    if not rows:
        raise HTTPException(status_code=400, detail="Missing rows")

    years = []

    for row in rows:
        year = row.get("year")

        if year is None:
            continue

        try:
            years.append(int(year))
        except Exception:
            continue

    years = sorted(list(set(years)))

    if not years:
        raise HTTPException(status_code=400, detail="No valid years found")

    year_mapping = {
        str(year): f"Y{i + 1}"
        for i, year in enumerate(years)
    }

    reverse_year_mapping = {
        f"Y{i + 1}": str(year)
        for i, year in enumerate(years)
    }

    values = {}
    structured_rows = []
    needs_confirmation = []

    latest_by_code = {}

    for row in rows:
        label_input = str(row.get("label", "")).strip()
        year = row.get("year")
        value = row.get("value")

        if not label_input or year is None or value is None:
            continue

        try:
            year_int = int(year)
            numeric_value = float(value)
        except Exception:
            continue

        if str(year_int) not in year_mapping:
            continue

        match = lookup_label(
            label_input,
            auto_threshold=auto_threshold,
            confirm_threshold=confirm_threshold,
            max_matches=5
        )

        if match["status"] != "auto_match":
            needs_confirmation.append({
                "original_label": label_input,
                "year": year_int,
                "value": numeric_value,
                "match": match
            })
            continue

        code = match["code"].lower()
        mapped_year = year_mapping[str(year_int)]
        y_code = mapped_year.lower()
        final_key = f"{code}.{y_code}"

        values[final_key] = numeric_value

        if code not in latest_by_code or year_int > latest_by_code[code]["year"]:
            latest_by_code[code] = {
                "year": year_int,
                "value": numeric_value
            }

        structured_rows.append({
            "original_label": label_input,
            "official_code": match["code"],
            "official_label": match["label"],
            "match_method": match.get("method", ""),
            "score": match.get("score", 0),
            "year": year_int,
            "mapped_year": mapped_year,
            "key": final_key,
            "value": numeric_value
        })

    for code, latest in latest_by_code.items():
        values[code] = latest["value"]

    return {
        "year_mapping": year_mapping,
        "reverse_year_mapping": reverse_year_mapping,
        "values": values,
        "rows": structured_rows,
        "needs_confirmation": needs_confirmation,
        "latest_by_code": latest_by_code,
        "summary": {
            "input_rows": len(rows),
            "structured_rows": len(structured_rows),
            "needs_confirmation": len(needs_confirmation),
            "years_detected": len(years)
        }
    }

# =====================================================
# 10. BASIC ENDPOINTS
# =====================================================

@app.get("/")
def home():
    return {"status": "ok", "message": "DutchBoy public server is alive"}


@app.get("/ping")
def ping(x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)
    return {"status": "ok", "message": "DutchBoy server is alive"}


@app.get("/calculate_get_urlkey")
def calculate_get_urlkey(a: float = 0, b: float = 0, api_key: str = ""):
    check_api_key(api_key)
    return {"result": a + b, "formula": "a + b"}


@app.post("/calculate")
def calculate(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    values = data.get("values", {})
    a = float(values.get("a", 0))
    b = float(values.get("b", 0))

    return {"result": a + b, "formula": "a + b"}


# =====================================================
# 11. FORMULA ENDPOINTS
# =====================================================

@app.get("/formulas")
def get_formulas(api_key: str = ""):
    check_api_key(api_key)

    formulas = load_formula_library()

    return {
        "count": len(formulas),
        "formulas": formulas
    }


@app.get("/formulas/clear_urlkey")
def clear_formulas_urlkey(api_key: str = ""):
    check_api_key(api_key)

    save_formula_library([])

    return {
        "status": "ok",
        "message": "Formula library cleared",
        "count": 0
    }


@app.get("/formulas/add_urlkey")
def add_formula_urlkey(formula: str = "", api_key: str = ""):
    check_api_key(api_key)

    formula = formula.strip()

    if not formula:
        raise HTTPException(status_code=400, detail="Missing formula")

    if "=" not in formula:
        raise HTTPException(status_code=400, detail="Formula must contain '='")

    formulas = load_formula_library()

    if formula not in formulas:
        formulas.append(formula)
        save_formula_library(formulas)

    return {
        "status": "ok",
        "message": "Formula added",
        "formula": formula,
        "count": len(formulas)
    }


@app.post("/formulas/save")
def save_formulas(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    formulas = data.get("formulas", [])
    cleaned = []

    for f in formulas:
        f = str(f).strip()

        if f and "=" in f:
            cleaned.append(f)

    save_formula_library(cleaned)

    return {
        "status": "ok",
        "message": "Formula library saved",
        "count": len(cleaned)
    }


# =====================================================
# 12. ABSMATCH ENDPOINTS
# =====================================================

@app.post("/absmatch/lookup")
def absmatch_lookup(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    query = str(data.get("query", "")).strip()
    auto_threshold = float(data.get("auto_threshold", 0.95))
    confirm_threshold = float(data.get("confirm_threshold", 0.80))

    if not query:
        raise HTTPException(status_code=400, detail="Missing query")

    return lookup_label(
        query=query,
        auto_threshold=auto_threshold,
        confirm_threshold=confirm_threshold,
        max_matches=10
    )


@app.post("/absmatch/confirm")
def absmatch_confirm(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    return learn_synonym(
        code=data.get("code", ""),
        synonym=data.get("synonym", "")
    )


# =====================================================
# 13. SOLVE ENDPOINT
# =====================================================

@app.post("/solve")
def solve(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    target = data.get("target", "")
    values = data.get("values", {})
    formulas = data.get("formulas") or load_formula_library()

    try:
        return solve_engine(
            target=target,
            input_values=values,
            formulas=formulas
        )

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )


# =====================================================
# 14. INPUT STRUCTURE ENDPOINTS
# =====================================================

@app.post("/inputs/structure_years")
def structure_years(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    return structure_year_rows(
        rows=data.get("rows", []),
        auto_threshold=float(data.get("auto_threshold", 0.95)),
        confirm_threshold=float(data.get("confirm_threshold", 0.80))
    )


@app.post("/inputs/structure_and_solve")
def structure_and_solve(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    return structure_and_solve_engine(
        target=str(data.get("target", "")).strip(),
        rows=data.get("rows", []),
        auto_threshold=float(data.get("auto_threshold", 0.95)),
        confirm_threshold=float(data.get("confirm_threshold", 0.80))
    )


@app.post("/inputs/auto_structure")
def auto_structure(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    return auto_structure_cells(
        cells=data.get("cells", [])
    )


@app.post("/inputs/auto_structure_and_solve")
def auto_structure_and_solve(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    return auto_structure_and_solve_engine(
        target=str(data.get("target", "")).strip(),
        cells=data.get("cells", []),
        year=data.get("year", None),
        auto_threshold=float(data.get("auto_threshold", 0.95)),
        confirm_threshold=float(data.get("confirm_threshold", 0.80))
    )


# =====================================================
# 15. DASHBOARD
# =====================================================

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>DutchBoy Dashboard</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                background: #f4f6f8;
                padding: 40px;
            }
            .card {
                max-width: 520px;
                margin: auto;
                background: white;
                padding: 30px;
                border-radius: 16px;
                box-shadow: 0 8px 24px rgba(0,0,0,0.08);
            }
            h1 {
                margin-top: 0;
            }
            input {
                width: 100%;
                padding: 10px;
                margin: 8px 0 16px 0;
                border: 1px solid #ccc;
                border-radius: 8px;
            }
            button {
                width: 100%;
                padding: 12px;
                border: none;
                border-radius: 8px;
                background: #1f2937;
                color: white;
                font-size: 16px;
                cursor: pointer;
            }
            button:hover {
                background: #374151;
            }
            pre {
                background: #111827;
                color: #e5e7eb;
                padding: 16px;
                border-radius: 8px;
                overflow-x: auto;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>DutchBoy Dashboard</h1>
            <p>Test simple du cerveau Python public.</p>

            <label>API Key</label>
            <input id="api_key" type="password" placeholder="Entre ta clé API">

            <label>A</label>
            <input id="a" type="number" value="5">

            <label>B</label>
            <input id="b" type="number" value="7">

            <button onclick="calculate()">Calculate</button>

            <h3>Résultat</h3>
            <pre id="result">En attente...</pre>
        </div>

        <script>
            async function calculate() {
                const apiKey = document.getElementById("api_key").value;
                const a = document.getElementById("a").value;
                const b = document.getElementById("b").value;

                const url = `/calculate_get_urlkey?a=${a}&b=${b}&api_key=${encodeURIComponent(apiKey)}`;

                try {
                    const response = await fetch(url);
                    const data = await response.json();
                    document.getElementById("result").textContent =
                        JSON.stringify(data, null, 2);
                } catch (err) {
                    document.getElementById("result").textContent =
                        "Erreur : " + err;
                }
            }
        </script>
    </body>
    </html>
    """