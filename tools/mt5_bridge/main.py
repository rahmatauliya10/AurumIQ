"""FastAPI application for the local read-only MetaTrader 5 data bridge."""
from contextlib import asynccontextmanager
from datetime import datetime
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, status

from .adapter import Mt5ReadOnlyAdapter
from .config import Mt5BridgeConfig
from .schemas import (
    BarsResponse,
    HealthResponse,
    HistoryCapabilityResponse,
    ProviderResponse,
    QuoteResponse,
    SymbolResponse,
    TicksResponse,
)

logger = logging.getLogger(__name__)

# Global singleton adapter
adapter = Mt5ReadOnlyAdapter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager: initialize MT5 connection on startup, shutdown on exit."""
    logger.info("Starting MT5 Read-Only Bridge microservice...")
    adapter.initialize()
    yield
    logger.info("Stopping MT5 Read-Only Bridge microservice...")
    adapter.shutdown()


app = FastAPI(
    title="AurumIQ MT5 Read-Only Market Data Bridge",
    description="Local read-only HTTP adapter providing spot gold data from MetaTrader 5 without trading capabilities.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Check terminal IPC connection and bridge diagnostic status.",
)
def get_health() -> HealthResponse:
    return adapter.get_health()


@app.get(
    "/provider",
    response_model=ProviderResponse,
    summary="Provider metadata",
    description="Retrieve safe provider attribution and environment information without account numbers or balances.",
)
def get_provider() -> ProviderResponse:
    return adapter.get_provider_metadata()


@app.get(
    "/symbol",
    response_model=SymbolResponse,
    summary="Symbol specifications",
    description="Get contract specifications and digits precision for gold symbol on the broker.",
)
def get_symbol(symbol: Optional[str] = Query(None, description="Optional explicit broker symbol")) -> SymbolResponse:
    try:
        return adapter.get_symbol_info(symbol=symbol)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.get(
    "/quote",
    response_model=QuoteResponse,
    summary="Live quote ticker",
    description="Fetch instantaneous bid, ask, mid, and relative spread basis points.",
)
def get_quote(symbol: Optional[str] = Query(None, description="Optional explicit broker symbol")) -> QuoteResponse:
    try:
        return adapter.get_live_quote(symbol=symbol)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@app.get(
    "/bars",
    response_model=BarsResponse,
    summary="Historical candlesticks",
    description="Retrieve closed historical bars for a given timeframe.",
)
def get_bars(
    timeframe: str = Query("15m", description="Timeframe: 1m, 5m, 15m, 1h, 4h, 1d"),
    start: Optional[datetime] = Query(None, description="Start UTC timestamp (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="End UTC timestamp (ISO 8601)"),
    limit: int = Query(500, ge=1, le=50000, description="Max bars when range not specified"),
    symbol: Optional[str] = Query(None, description="Optional explicit broker symbol"),
) -> BarsResponse:
    try:
        return adapter.get_bars(timeframe=timeframe, start=start, end=end, limit=limit, symbol=symbol)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get(
    "/ticks",
    response_model=TicksResponse,
    summary="Historical ticks",
    description="Retrieve granular bid/ask tick data for spread and execution distribution analysis.",
)
def get_ticks(
    start: datetime = Query(..., description="Start UTC timestamp (ISO 8601)"),
    end: datetime = Query(..., description="End UTC timestamp (ISO 8601)"),
    limit: int = Query(1000, ge=1, le=100000, description="Max ticks to return"),
    symbol: Optional[str] = Query(None, description="Optional explicit broker symbol"),
) -> TicksResponse:
    try:
        return adapter.get_ticks(start=start, end=end, limit=limit, symbol=symbol)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get(
    "/history-capability",
    response_model=HistoryCapabilityResponse,
    summary="Historical data capabilities",
    description="Probe server history availability across all analytical timeframes.",
)
def get_history_capability(
    symbol: Optional[str] = Query(None, description="Optional explicit broker symbol"),
) -> HistoryCapabilityResponse:
    try:
        return adapter.get_history_capability(symbol=symbol)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


def run():
    """Entrypoint to launch the bridge microservice on localhost."""
    import uvicorn
    cfg = Mt5BridgeConfig.from_env()
    uvicorn.run("tools.mt5_bridge.main:app", host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    run()
