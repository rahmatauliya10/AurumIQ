"""Domain exceptions for the pure trading signal engine."""


class EngineError(Exception):
    """Base exception for all engine-level errors."""
    pass


class IncompleteCandleError(EngineError, ValueError):
    """Raised when an engine analysis operation is attempted on an unclosed candle."""
    pass


class LookaheadViolationError(EngineError, RuntimeError):
    """Raised when point-in-time causality is violated."""
    pass


class InsufficientDataError(EngineError, ValueError):
    """Raised when minimum required lookback data is unavailable."""
    pass
