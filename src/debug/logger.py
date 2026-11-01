import io
import sys
import time

from build_info import DEBUG

LOG_FILE = "debug.log"
MAX_LOG_BYTES = 64 * 1024
LOG_FLUSH_INTERVAL_MS = 250
LIVE_LEVEL = 1
MAX_LIVE_DETAILS = 160
LIVE_CATEGORIES = {"boot", "command", "hardware", "program", "upload"}

_file = None
_live_comm = None
_size = 0
_disabled = False
_last_flush = 0


def _now():
    ticks_ms = getattr(time, "ticks_ms", None)
    return ticks_ms() if ticks_ms is not None else int(time.time() * 1000)


def configure(live_comm=None):
    global _file, _live_comm, _size, _disabled, _last_flush
    if not DEBUG:
        return
    if live_comm is not None:
        _live_comm = live_comm
    if _file is not None or _disabled:
        return
    try:
        _file = open(LOG_FILE, "w")
        _size = 0
        _last_flush = _now()
        _disabled = False
    except Exception:
        _file = None
        _disabled = True


def _format(category, event, details):
    values = ["[%d]" % _now(), category, event]
    values.extend("%s=%s" % (key, value) for key, value in details.items())
    return " ".join(values)


def log(category, event, level=0, force_flush=False, **details):
    global _size, _disabled, _live_comm, _last_flush
    if not DEBUG or _disabled:
        return

    line = _format(category, event, details) + "\n"
    encoded = line.encode()
    if _file is not None and _size < MAX_LOG_BYTES:
        remaining = MAX_LOG_BYTES - _size
        if len(encoded) <= remaining:
            try:
                _file.write(line)
                _size += len(encoded)
                now = _now()
                if force_flush or now - _last_flush >= LOG_FLUSH_INTERVAL_MS:
                    _file.flush()
                    _last_flush = now
            except Exception:
                _disabled = True
        elif _size < MAX_LOG_BYTES:
            try:
                _file.write("[%d] logger limit_reached\n" % _now())
                _file.flush()
            except Exception:
                pass
            _size = MAX_LOG_BYTES

    is_debug_message = (
        category == "communication"
        and event == "tx"
        and details.get("message") == "debug"
    )
    if (
        level >= LIVE_LEVEL
        and category in LIVE_CATEGORIES
        and _live_comm is not None
        and not is_debug_message
    ):
        live_details = _format(category, event, details)
        if len(live_details) > MAX_LIVE_DETAILS:
            live_details = live_details[:MAX_LIVE_DETAILS] + "..."
        try:
            _live_comm.write_message("debug", live_details)
        except Exception:
            _live_comm = None


def _exception_line(error):
    """Return the last traceback line containing a source location."""
    stream = io.StringIO()
    try:
        sys.print_exception(error, stream)
        traceback = stream.getvalue()
    except Exception:
        return ""

    location = ""
    for line in traceback.splitlines():
        if "line " in line:
            location = line.strip()
    return location


def log_error(category, error, event="error", **details):
    details["error"] = type(error).__name__
    details["message"] = str(error)
    line = _exception_line(error)
    if line:
        details["line"] = line
    log(category, event, level=1, **details)
