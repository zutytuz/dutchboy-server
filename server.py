import os
from typing import Optional

from fastapi import FastAPI, Header, HTTPException

app = FastAPI()

# =========================================================
# Configuration
# =========================================================
# La clé API est stockée dans Render :
# Environment Variable -> DUTCHBOY_API_KEY
API_KEY = os.getenv("DUTCHBOY_API_KEY")


# =========================================================
# Fonctions utilitaires
# =========================================================
def check_api_key(provided_key: Optional[str]) -> None:
    """
    Vérifie que la clé API fournie correspond à celle stockée
    dans la variable d'environnement DUTCHBOY_API_KEY.
    """
    if API_KEY is None:
        raise HTTPException(
            status_code=500,
            detail="Server API key not configured."
        )

    if provided_key != API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key."
        )


# =========================================================
# Routes publiques
# =========================================================
@app.get("/")
def home():
    return {
        "status": "ok",
        "message": "DutchBoy public server is alive"
    }


# =========================================================
# Routes protégées
# =========================================================
@app.get("/ping")
def ping(x_api_key: Optional[str] = Header(default=None)):
    """
    Test protégé par header x-api-key
    """
    check_api_key(x_api_key)

    return {
        "status": "ok",
        "message": "DutchBoy server is alive"
    }


@app.get("/calculate_get")
def calculate_get(
    a: float = 0,
    b: float = 0,
    x_api_key: Optional[str] = Header(default=None)
):
    """
    Calcul simple protégé par header x-api-key.
    """
    check_api_key(x_api_key)

    return {
        "result": a + b,
        "formula": "a + b"
    }


@app.get("/calculate_get_urlkey")
def calculate_get_urlkey(
    a: float = 0,
    b: float = 0,
    api_key: str = ""
):
    """
    Version pratique pour Excel Mac :
    la clé API est passée directement dans l'URL.
    Exemple :
    /calculate_get_urlkey?a=5&b=7&api_key=ma_cle
    """
    check_api_key(api_key)

    return {
        "result": a + b,
        "formula": "a + b"
    }


@app.post("/calculate")
def calculate(
    data: dict,
    x_api_key: Optional[str] = Header(default=None)
):
    """
    Endpoint POST générique.
    Attend :
    {
        "values": {
            "a": 5,
            "b": 7
        }
    }
    """
    check_api_key(x_api_key)

    values = data.get("values", {})

    a = float(values.get("a", 0))
    b = float(values.get("b", 0))

    return {
        "result": a + b,
        "formula": "a + b"
