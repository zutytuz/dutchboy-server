from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {
        "status": "ok",
        "message": "DutchBoy public server is alive"
    }

@app.get("/ping")
def ping():
    return {
        "status": "ok",
        "message": "DutchBoy server is alive"
    }

@app.post("/calculate")
def calculate(data: dict):
    values = data.get("values", {})
    a = float(values.get("a", 0))
    b = float(values.get("b", 0))

    return {
        "result": a + b,
        "formula": "a + b"
    }

