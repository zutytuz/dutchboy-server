import os
from typing import Optional

from fastapi import FastAPI, Header, HTTPException

app = FastAPI()

API_KEY = os.getenv("DUTCHBOY_API_KEY")


def check_api_key(provided_key: Optional[str]) -> None:
    if API_KEY is None:
        raise HTTPException(status_code=500, detail="Server API key not configured")

    if provided_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


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
