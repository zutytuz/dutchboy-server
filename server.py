import unicodedata
from difflib import SequenceMatcher

import json
from pathlib import Path


import os
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

app = FastAPI()

API_KEY = os.getenv("DUTCHBOY_API_KEY")

def load_json_file(filename: str, default):
    path = Path(__file__).parent / filename
    if not path.exists():
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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
def check_api_key(provided_key: Optional[str]) -> None:
    if API_KEY is None:
        raise HTTPException(status_code=500, detail="Server API key not configured")

    if provided_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

@app.get("/formulas/clear_urlkey")
def clear_formulas_urlkey(api_key: str = ""):
    check_api_key(api_key)
    save_formula_library([])
    return {
        "status": "ok",
        "message": "Formula library cleared",
        "count": 0
    }

@app.post("/absmatch/lookup")
def absmatch_lookup(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    from rapidfuzz import fuzz

    query = str(data.get("query", "")).strip()

    auto_threshold = float(data.get("auto_threshold", 0.95))
    confirm_threshold = float(data.get("confirm_threshold", 0.80))

    if not query:
        raise HTTPException(status_code=400, detail="Missing query")

    codes = load_json_file("codes.json", {})
    synonyms = load_json_file("synonyms.json", {})

    normalized_query = normalize_text(query)
    matches = []
    # 0. Direct code match
    direct_code = query.strip().upper()

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
    # 1. Exact match / synonym match
    for code, label in codes.items():
        candidates = [code, label]
        candidates.extend(synonyms.get(code, []))

        for candidate in candidates:
            normalized_candidate = normalize_text(candidate)

            if normalized_query == normalized_candidate:
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

    # 2. Fuzzy matching avec Levenshtein-like scoring
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
        "best_match": best,
        "needs_confirmation": needs_confirmation,
        "thresholds": {
            "auto": auto_threshold,
            "confirm": confirm_threshold
        },
        "all_matches": matches[:10]
    }

@app.post("/absmatch/confirm")
def absmatch_confirm(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    code = str(data.get("code", "")).strip().upper()
    synonym = str(data.get("synonym", "")).strip()

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

    path = Path(__file__).parent / "synonyms.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(synonyms, f, indent=2, ensure_ascii=False)

    return {
        "status": "ok",
        "message": "Synonym learned",
        "code": code,
        "label": codes[code],
        "synonym": synonym,
        "synonym_count": len(synonyms[code])
    }

@app.get("/")
def home():
    return {"status": "ok", "message": "DutchBoy public server is alive"}

def load_formula_library():
    path = Path(__file__).parent / "formulas.json"

    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
def save_formula_library(formulas: list[str]):
    path = Path(__file__).parent / "formulas.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(formulas, f, indent=2, ensure_ascii=False)
@app.get("/formulas")
def get_formulas(api_key: str = ""):
    check_api_key(api_key)
    formulas = load_formula_library()
    return {"count": len(formulas), "formulas": formulas}


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
@app.post("/solve")
def solve(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    import re

    target = data.get("target", "").strip().lower()
    values = {k.lower(): float(v) for k, v in data.get("values", {}).items()}
    formulas = data.get("formulas") or load_formula_library()

    if not target:
        raise HTTPException(status_code=400, detail="Missing target")

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

    logs = []

    def extract_variables(expr: str):
        import re

        # Enlever les chaînes entre guillemets simples ou doubles
        cleaned = re.sub(r"'[^']*'", "", expr)
        cleaned = re.sub(r'"[^"]*"', "", cleaned)

        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]*", cleaned)

        ignored = {
            "abs", "min", "max", "round",
            "avg_all", "AVG_ALL",
            "avg", "AVG"
        }

        return [
            t.lower()
            for t in tokens
            if t.lower() not in {x.lower() for x in ignored}
        ]
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

        safe = safe.replace("abs(", "abs(")

        return safe
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
    try:
        result = resolve(target)

        return {
            "target": target,
            "result": result,
            "logs": logs,
            "values": values
        }

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
def avg_all(base_var: str, values: dict) -> float:
    """
    Calcule la moyenne de toutes les variables du type:
    base_var.y1, base_var.y2, ...
    Exemple: avg_all("ci", values)
    """

    prefix = base_var.lower() + ".y"
    nums = []

    for key, value in values.items():
        if key.lower().startswith(prefix):
            nums.append(float(value))

    if not nums:
        raise ValueError(f"No yearly values found for {base_var}")

    return sum(nums) / len(nums)
@app.post("/inputs/structure_years")
def structure_years(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    from rapidfuzz import fuzz

    rows = data.get("rows", [])
    auto_threshold = float(data.get("auto_threshold", 0.95))
    confirm_threshold = float(data.get("confirm_threshold", 0.80))

    if not rows:
        raise HTTPException(status_code=400, detail="Missing rows")

    codes = load_json_file("codes.json", {})
    synonyms = load_json_file("synonyms.json", {})

    def lookup_label(query: str):
        query = str(query).strip()

        if not query:
            return {
                "status": "missing_label",
                "code": "",
                "label": "",
                "score": 0
            }

        direct_code = query.upper()

        if direct_code in codes:
            return {
                "status": "auto_match",
                "method": "direct_code",
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
            "code": best["code"],
            "label": best["label"],
            "matched_on": best["matched_on"],
            "score": best["score"],
            "needs_confirmation": needs_confirmation,
            "all_matches": matches[:5]
        }

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

        match = lookup_label(label_input)

        if match["status"] not in ["auto_match"]:
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
                    # Valeur courante = dernière année disponible
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
@app.post("/inputs/structure_and_solve")
def structure_and_solve(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    target = str(data.get("target", "")).strip()
    rows = data.get("rows", [])

    if not target:
        raise HTTPException(status_code=400, detail="Missing target")

    if not rows:
        raise HTTPException(status_code=400, detail="Missing rows")

    # 1. Réutiliser la logique de structure_years
    structured = structure_years(
        {
            "rows": rows,
            "auto_threshold": data.get("auto_threshold", 0.95),
            "confirm_threshold": data.get("confirm_threshold", 0.80)
        },
        x_api_key=x_api_key
    )

    if structured.get("needs_confirmation"):
        return {
            "status": "needs_confirmation",
            "message": "Some labels need confirmation before solving",
            "structure": structured
        }

    values = structured.get("values", {})

    # 2. Normaliser aussi la target avec AbsMatch si besoin
    codes = load_json_file("codes.json", {})
    target_code = target.upper()

    formula_targets = set()

    for formula in load_formula_library():
        if "=" in formula:
            left, _ = formula.split("=", 1)
            formula_targets.add(left.strip().upper())

# Si la target est un code officiel OU une variable calculable, on l'accepte directement
    if target_code not in codes and target_code not in formula_targets:
        lookup_result = absmatch_lookup(
            {"query": target},
            x_api_key=x_api_key
        )

        if lookup_result.get("status") != "auto_match":
            return {
                "status": "target_needs_confirmation",
                "message": "Target needs confirmation before solving",
                "target_match": lookup_result,
                "structure": structured
            }

        target_code = lookup_result.get("code", target).upper()

    # 3. Lancer solve avec les values structurées
    solve_result = solve(
        {
            "target": target_code.lower(),
            "values": values
        },
        x_api_key=x_api_key
    )

    return {
        "status": "solved",
        "target": target_code,
        "structure": structured,
        "solve": solve_result
    }
@app.post("/inputs/auto_structure")
def auto_structure(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    from rapidfuzz import fuzz

    cells = data.get("cells", [])

    if not cells:
        raise HTTPException(status_code=400, detail="Missing cells")

    codes = load_json_file("codes.json", {})
    synonyms = load_json_file("synonyms.json", {})

    def is_year(value):
        try:
            y = int(float(value))
            return 1900 <= y <= 2100
        except Exception:
            return False

    def to_year(value):
        return int(float(value))

    def lookup_label_light(query: str):
        query = str(query).strip()

        if not query:
            return None

        direct_code = query.upper()

        if direct_code in codes:
            return {
                "code": direct_code,
                "label": codes[direct_code],
                "score": 1.0,
                "method": "direct_code"
            }

        nq = normalize_text(query)
        best = None

        for code, label in codes.items():
            candidates = [code, label]
            candidates.extend(synonyms.get(code, []))

            for candidate in candidates:
                nc = normalize_text(candidate)

                if nq == nc:
                    return {
                        "code": code,
                        "label": label,
                        "score": 1.0,
                        "method": "exact_or_synonym"
                    }

                score = fuzz.ratio(nq, nc) / 100

                if best is None or score > best["score"]:
                    best = {
                        "code": code,
                        "label": label,
                        "score": round(score, 4),
                        "method": "fuzzy",
                        "matched_on": candidate
                    }

        if best and best["score"] >= 0.80:
            return best

        return None

    # 1. Index cells
    grid = {}
    rows_set = set()
    cols_set = set()

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
        rows_set.add(r)
        cols_set.add(c)

    if not grid:
        raise HTTPException(status_code=400, detail="No usable cells")

    rows = sorted(rows_set)
    cols = sorted(cols_set)

    # 2. Detect year cells
    year_cells = []

    for (r, c), v in grid.items():
        if is_year(v):
            year_cells.append({
                "row": r,
                "col": c,
                "year": to_year(v)
            })

    if not year_cells:
        raise HTTPException(status_code=400, detail="No year detected")

    # 3. Detect label cells with AbsMatch
    label_cells = []

    for (r, c), v in grid.items():
        if is_year(v):
            continue

        try:
            float(v)
            continue
        except Exception:
            pass

        match = lookup_label_light(str(v))

        if match:
            label_cells.append({
                "row": r,
                "col": c,
                "raw": str(v),
                "match": match
            })

    if not label_cells:
        raise HTTPException(status_code=400, detail="No label detected")

    # 4. Orientation scoring
    # Horizontal table:
    # years are mostly in one row, labels are mostly in one column.
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
        orientation = "labels_as_rows_years_as_columns"
        header_year_row = best_year_row
        label_col = best_label_col
    else:
        orientation = "labels_as_columns_years_as_rows"
        year_col = best_year_col
        header_label_row = best_label_row

    extracted_rows = []

    # 5A. Classic orientation
    if orientation == "labels_as_rows_years_as_columns":

        years_by_col = {}

        for y in year_cells:
            if y["row"] == header_year_row:
                years_by_col[y["col"]] = y["year"]

        labels_by_row = {}

        for l in label_cells:
            if l["col"] == label_col:
                labels_by_row[l["row"]] = l

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
                    "value": numeric_value
                })

    # 5B. Transposed orientation
    else:

        years_by_row = {}

        for y in year_cells:
            if y["col"] == year_col:
                years_by_row[y["row"]] = y["year"]

        labels_by_col = {}

        for l in label_cells:
            if l["row"] == header_label_row:
                labels_by_col[l["col"]] = l

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
                    "value": numeric_value
                })

    return {
        "status": "structured",
        "orientation": orientation,
        "year_cells": year_cells,
        "label_cells": label_cells,
        "extracted_rows": extracted_rows,
        "summary": {
            "cells_received": len(cells),
            "years_detected": len(year_cells),
            "labels_detected": len(label_cells),
            "rows_extracted": len(extracted_rows),
            "horizontal_score": horizontal_score,
            "vertical_score": vertical_score
        }
    }
@app.post("/inputs/auto_structure_and_solve")
def auto_structure_and_solve(data: dict, x_api_key: Optional[str] = Header(default=None)):
    check_api_key(x_api_key)

    target = str(data.get("target", "")).strip()
    cells = data.get("cells", [])

    if not target:
        raise HTTPException(status_code=400, detail="Missing target")

    if not cells:
        raise HTTPException(status_code=400, detail="Missing cells")

    # 1. Comprendre automatiquement le tableau brut
    auto_structured = auto_structure(
        {"cells": cells},
        x_api_key=x_api_key
    )

    extracted_rows = auto_structured.get("extracted_rows", [])

    if not extracted_rows:
        return {
            "status": "no_rows_extracted",
            "message": "No usable rows could be extracted from the grid",
            "auto_structure": auto_structured
        }

    # 2. Transformer les lignes extraites en format structure_and_solve
    rows_for_solver = []

    for row in extracted_rows:
        rows_for_solver.append({
            "label": row.get("detected_code") or row.get("label"),
            "year": row.get("year"),
            "value": row.get("value")
        })

    # 3. Structure + solve
    solved = structure_and_solve(
        {
            "target": target,
            "rows": rows_for_solver,
            "auto_threshold": data.get("auto_threshold", 0.95),
            "confirm_threshold": data.get("confirm_threshold", 0.80)
        },
        x_api_key=x_api_key
    )

    return {
        "status": solved.get("status"),
        "target": target,
        "auto_structure": auto_structured,
        "structure_and_solve": solved
    }