import _thread
import time

MAX_QUEUED_MESSAGES = 64
QUEUE_WAIT_MS = 2000
QUEUE_POLL_MS = 2
DROP_NOTICE = "[%d line(s) of output dropped: the link could not keep up]"

outgoing_messages = []
queue_lock = _thread.allocate_lock()
dropped_message_count = 0
unreported_drops = {}
draining_thread = _thread.get_ident()


def _queue_is_full():
    queue_lock.acquire()
    try:
        return len(outgoing_messages) >= MAX_QUEUED_MESSAGES
    finally:
        queue_lock.release()


def queue_outgoing_message(comm, message_type, content):
    global dropped_message_count

    if _thread.get_ident() != draining_thread:
        deadline = time.ticks_add(time.ticks_ms(), QUEUE_WAIT_MS)
        while _queue_is_full():
            if time.ticks_diff(time.ticks_ms(), deadline) >= 0:
                break
            time.sleep(QUEUE_POLL_MS / 1000)

    queue_lock.acquire()
    try:
        if len(outgoing_messages) >= MAX_QUEUED_MESSAGES:
            victim = outgoing_messages.pop(0)
            dropped_message_count += 1
            unreported_drops[victim[0]] = unreported_drops.get(victim[0], 0) + 1
        outgoing_messages.append((comm, message_type, content))
    finally:
        queue_lock.release()


def flush_outgoing_messages():
    pending = None

    queue_lock.acquire()
    try:
        blocked = []
        for index in range(len(outgoing_messages)):
            comm = outgoing_messages[index][0]
            if comm in blocked:
                continue
            if hasattr(comm, "can_send_now") and not comm.can_send_now():
                blocked.append(comm)
                continue
            missing = unreported_drops.get(comm)
            if missing:
                unreported_drops[comm] = 0
                pending = (comm, "console", DROP_NOTICE % missing)
            else:
                pending = outgoing_messages.pop(index)
            break
    finally:
        queue_lock.release()

    if pending is None:
        return False
    comm, message_type, content = pending
    comm._write_message_now(message_type, content)
    return True


def queued_message_count():
    queue_lock.acquire()
    try:
        return len(outgoing_messages)
    finally:
        queue_lock.release()
