from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
# Import your existing functions from your script (or paste your helper math/data functions here)

app = FastAPI(title="Athenaeum Intelligence API")

# Allows your frontend web page to talk to this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"status": "Athenaeum API is live"}

@app.get("/api/analyze")
def analyze(ticker: str):
    try:
        # Calls the data fetch function we built
        resolved = resolve_name_to_ticker(ticker)
        data = fetch_stock_data(resolved, ticker)
        
        return {
            "status": "success",
            "data": data
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
