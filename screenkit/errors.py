"""Error types for screenkit."""


class ScreenKitError(Exception):
    """Raised for any recoverable failure in a screenkit operation.

    All public functions raise this (and only this) on failure so callers --
    including the CLI and the GUI -- have a single exception to catch.  A clear,
    user-facing message is expected (e.g. "no display", "ffmpeg not found"),
    never a raw traceback.
    """
