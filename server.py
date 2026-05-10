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

    query = str(data.get("query", "")).strip()

    if not query:
        raise HTTPException(status_code=400, detail="Missing query")

    codes = load_json_file("codes.json", {})
    synonyms = load_json_file("synonyms.json", {})

    matches = []

    for code, label in codes.items():
        candidates = [code, label]
        candidates.extend(synonyms.get(code, []))

        best_candidate = None
        best_score = 0

        for candidate in candidates:
            score = absmatch_score(query, candidate)

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

    return {
        "query": query,
        "best_match": matches[0],
        "all_matches": matches
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
        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]*", expr)
        return [t.lower() for t in tokens]

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

                result = eval(
                    safe_expr(right),
                    {"__builtins__": {}, "abs": abs, "min": min, "max": max, "round": round},
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
