"""Private Xlib selection worker (no PTK imports or terminal escape sequences).

Uses the system libX11, like terminal Vim. See ICCCM selections and INCR:
https://www.x.org/releases/current/doc/xorg-docs/icccm/icccm.html
This module is launched by x11_clipboard.py, never in the UI process.
"""

import ctypes as C
import locale
import os
import select
import signal
import sys
import time


MAX_BYTES = 64 * 1024 * 1024
CHUNK = 64 * 1024
PROPERTY_CHANGE = 1 << 22
SELECTION_CLEAR, SELECTION_REQUEST, SELECTION_NOTIFY, PROPERTY_NOTIFY = 29, 30, 31, 28
U = C.c_ulong
P = C.c_void_p
I = C.c_int
COMMON = [("type", I), ("serial", U), ("send_event", I), ("display", P)]


class SelectionRequest(C.Structure):
    _fields_ = COMMON + [(name, U) for name in ("owner", "requestor", "selection", "target", "property", "time")]


class SelectionNotify(C.Structure):
    _fields_ = COMMON + [(name, U) for name in ("requestor", "selection", "target", "property", "time")]


class PropertyNotify(C.Structure):
    _fields_ = COMMON + [(name, U) for name in ("window", "atom", "time")] + [("state", I)]


class Event(C.Union):
    _fields_ = [("type", I), ("request", SelectionRequest), ("selection", SelectionNotify),
                ("property", PropertyNotify), ("pad", C.c_long * 24)]


class TextProperty(C.Structure):
    _fields_ = [("value", P), ("encoding", U), ("format", I), ("nitems", U)]


class Selection:
    def __init__(self) -> None:
        try:
            self.x = C.CDLL("libX11.so.6")
        except OSError as exc:
            raise ImportError("libX11 unavailable") from exc
        signatures = {
            "XOpenDisplay": (P, [C.c_char_p]), "XCloseDisplay": (I, [P]),
            "XDefaultRootWindow": (U, [P]), "XConnectionNumber": (I, [P]),
            "XCreateSimpleWindow": (U, [P, U, I, I, C.c_uint, C.c_uint, C.c_uint, U, U]),
            "XInternAtom": (U, [P, C.c_char_p, I]), "XSelectInput": (I, [P, U, C.c_long]),
            "XSetSelectionOwner": (I, [P, U, U, U]), "XGetSelectionOwner": (U, [P, U]),
            "XConvertSelection": (I, [P, U, U, U, U, U]),
            "XChangeProperty": (I, [P, U, U, U, I, I, P, I]),
            "XDeleteProperty": (I, [P, U, U]),
            "XGetWindowProperty": (I, [P, U, U, C.c_long, C.c_long, I, U,
                                      C.POINTER(U), C.POINTER(I), C.POINTER(U), C.POINTER(U), C.POINTER(P)]),
            "XPending": (I, [P]), "XNextEvent": (I, [P, C.POINTER(Event)]),
            "XSendEvent": (I, [P, U, I, C.c_long, C.POINTER(Event)]),
            "XFree": (I, [P]),
            "Xutf8TextListToTextProperty": (I, [P, C.POINTER(C.c_char_p), I, I, C.POINTER(TextProperty)]),
            "Xutf8TextPropertyToTextList": (I, [P, C.POINTER(TextProperty), C.POINTER(C.POINTER(C.c_char_p)), C.POINTER(I)]),
            "XFreeStringList": (None, [C.POINTER(C.c_char_p)]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self.x, name)
            function.restype, function.argtypes = result, arguments
        # A disappearing paste requestor must not kill the selection owner.
        # Xlib's fatal I/O handler remains confined to this private process.
        handler_type = C.CFUNCTYPE(I, P, P)
        self.error_handler = handler_type(lambda display, error: 0)
        self.x.XSetErrorHandler.argtypes = [handler_type]
        self.x.XSetErrorHandler.restype = P
        self.x.XSetErrorHandler(self.error_handler)
        self.display = self.x.XOpenDisplay(None)
        if not self.display:
            raise ConnectionError("X11 display unavailable")
        self.window = self.x.XCreateSimpleWindow(self.display, self.x.XDefaultRootWindow(self.display),
                                                0, 0, 1, 1, 0, 0, 0)
        self.x.XSelectInput(self.display, self.window, PROPERTY_CHANGE)
        self.fd = self.x.XConnectionNumber(self.display)
        names = ("CLIPBOARD", "UTF8_STRING", "COMPOUND_TEXT", "STRING", "TEXT", "TARGETS",
                 "TIMESTAMP", "MULTIPLE", "ATOM_PAIR", "ATOM", "INTEGER", "INCR", "_HUB_CLIPBOARD")
        self.atoms = {name: self.x.XInternAtom(self.display, name.encode(), 0) for name in names}
        self.transfers = {}
        self.content = b""
        self.timestamp = 0

    def close(self) -> None:
        self.x.XCloseDisplay(self.display)

    def property(self, window: int, atom: int, *, delete: bool = False) -> tuple[int, int, bytes | tuple[int, ...]]:
        kind, size, count, after, value = U(), I(), U(), U(), P()
        status = self.x.XGetWindowProperty(self.display, window, atom, 0, MAX_BYTES // 4,
            delete, 0, C.byref(kind), C.byref(size), C.byref(count), C.byref(after), C.byref(value))
        try:
            if status or after.value:
                raise RuntimeError("X11 property unavailable or too large")
            if size.value == 32:
                data = tuple(C.cast(value, C.POINTER(U))[index] for index in range(count.value))
            elif size.value == 8:
                data = C.string_at(value, count.value)
            else:
                data = b""
            return kind.value, size.value, data
        finally:
            if value:
                self.x.XFree(value)

    def put(self, window: int, atom: int, kind: int, data, size: int = 8) -> None:
        buffer = (U * len(data))(*data) if size == 32 else C.create_string_buffer(data)
        self.x.XChangeProperty(self.display, window, atom, kind, size, 0, buffer, len(data))

    def event(self, deadline: float, *, watch_parent: bool = False) -> Event:
        while time.monotonic() < deadline:
            if self.x.XPending(self.display):
                event = Event()
                self.x.XNextEvent(self.display, C.byref(event))
                return event
            # XPending already flushes requests and checks Xlib's event queue.
            # Another XFlush can read a newly arriving event into that queue;
            # polling only the socket afterward would then miss the event.
            ready, _, _ = select.select([self.fd, *([0] if watch_parent else [])], [], [],
                                         max(0, deadline - time.monotonic()))
            if watch_parent and 0 in ready and not os.read(0, 1):
                raise EOFError
        raise TimeoutError

    def read(self) -> bytes:
        if not self.x.XGetSelectionOwner(self.display, self.atoms["CLIPBOARD"]):
            return b""
        deadline = time.monotonic() + 2.5
        prop = self.atoms["_HUB_CLIPBOARD"]
        for name in ("UTF8_STRING", "COMPOUND_TEXT", "STRING"):
            target = self.atoms[name]
            self.x.XConvertSelection(self.display, self.atoms["CLIPBOARD"], target, prop, self.window, 0)
            while True:
                event = self.event(deadline)
                if event.type == SELECTION_NOTIFY and event.selection.target == target:
                    break
            if not event.selection.property:
                continue
            kind, size, data = self.property(self.window, prop, delete=True)
            if kind == self.atoms["INCR"]:
                chunks, length = [], 0
                while True:
                    event = self.event(deadline)
                    if (event.type != PROPERTY_NOTIFY or event.property.atom != prop
                            or event.property.state != 0):
                        continue
                    kind, size, data = self.property(self.window, prop, delete=True)
                    if not kind:  # An already consumed initial property notification.
                        continue
                    if size != 8:
                        raise RuntimeError("Invalid incremental clipboard text")
                    if not data:
                        break
                    length += len(data)
                    if length > MAX_BYTES:
                        raise RuntimeError("Clipboard text too large")
                    chunks.append(data)
                data = b"".join(chunks)
            if size != 8:
                raise RuntimeError("Invalid clipboard text")
            if kind == self.atoms["UTF8_STRING"]:
                return data.decode("utf-8").encode("utf-8")
            if kind == self.atoms["STRING"]:
                return data.decode("latin-1").encode("utf-8")
            buffer = C.create_string_buffer(data)
            text = TextProperty(C.cast(buffer, P), kind, size, len(data))
            strings, count = C.POINTER(C.c_char_p)(), I()
            status = self.x.Xutf8TextPropertyToTextList(self.display, C.byref(text), C.byref(strings), C.byref(count))
            try:
                if status < 0:
                    raise RuntimeError("Clipboard text conversion failed")
                return b"\n".join(strings[index] for index in range(count.value))
            finally:
                if strings:
                    self.x.XFreeStringList(strings)
        raise RuntimeError("Clipboard has no text target")

    def convert(self, target: int) -> tuple[int, bytes] | None:
        if target == self.atoms["UTF8_STRING"]:
            return target, self.content
        styles = {self.atoms["STRING"]: 0, self.atoms["COMPOUND_TEXT"]: 1, self.atoms["TEXT"]: 3}
        if target not in styles:
            return None
        text = TextProperty()
        strings = (C.c_char_p * 1)(self.content)
        status = self.x.Xutf8TextListToTextProperty(self.display, strings, 1, styles[target], C.byref(text))
        try:
            if status != 0:  # Never report a lossy conversion as successful.
                return None
            return text.encoding, C.string_at(text.value, text.nitems)
        finally:
            if text.value:
                self.x.XFree(text.value)

    def answer(self, window: int, target: int, prop: int) -> bool:
        if not prop:
            return False
        if target == self.atoms["TARGETS"]:
            self.put(window, prop, self.atoms["ATOM"], [self.atoms[name] for name in
                ("TARGETS", "TIMESTAMP", "MULTIPLE", "UTF8_STRING", "COMPOUND_TEXT", "STRING", "TEXT")], 32)
        elif target == self.atoms["TIMESTAMP"]:
            self.put(window, prop, self.atoms["INTEGER"], [self.timestamp], 32)
        elif target == self.atoms["MULTIPLE"]:
            kind, size, data = self.property(window, prop)
            if kind not in (self.atoms["ATOM_PAIR"], self.atoms["ATOM"]) or size != 32 or len(data) % 2:
                return False
            pairs = list(data)
            for index in range(0, len(pairs), 2):
                if (pairs[index] == target or pairs[index + 1] == prop
                        or not self.answer(window, pairs[index], pairs[index + 1])):
                    pairs[index + 1] = 0
            self.put(window, prop, self.atoms["ATOM_PAIR"], pairs, 32)
        else:
            converted = self.convert(target)
            if converted is None:
                return False
            kind, data = converted
            if len(data) > CHUNK:
                self.x.XSelectInput(self.display, window, PROPERTY_CHANGE)
                self.transfers[(window, prop)] = (kind, data, 0, time.monotonic())
                self.put(window, prop, self.atoms["INCR"], [len(data)], 32)
            else:
                self.put(window, prop, kind, data)
        return True

    def copy(self, content: bytes) -> None:
        self.content = content
        prop = self.atoms["_HUB_CLIPBOARD"]
        self.put(self.window, prop, self.atoms["INTEGER"], b"")
        deadline = time.monotonic() + 2.5
        while True:
            event = self.event(deadline)
            if event.type == PROPERTY_NOTIFY and event.property.atom == prop:
                self.timestamp = event.property.time
                break
        self.x.XSetSelectionOwner(self.display, self.atoms["CLIPBOARD"], self.window, self.timestamp)
        if self.x.XGetSelectionOwner(self.display, self.atoms["CLIPBOARD"]) != self.window:
            raise RuntimeError("Could not own clipboard")
        sys.stdout.buffer.write(b"owned\n")
        sys.stdout.buffer.flush()
        while True:
            try:
                event = self.event(time.monotonic() + 1, watch_parent=True)
            except TimeoutError:
                # A requestor can disappear during an INCR transfer.
                self.transfers = {key: value for key, value in self.transfers.items()
                                  if time.monotonic() - value[3] < 10}
                continue
            if event.type == SELECTION_CLEAR:
                return
            if event.type == SELECTION_REQUEST:
                request = event.request
                prop = request.property or request.target
                accepted = request.selection == self.atoms["CLIPBOARD"] and self.answer(request.requestor, request.target, prop)
                reply = Event()
                reply.selection = SelectionNotify(SELECTION_NOTIFY, 0, 1, self.display,
                    request.requestor, request.selection, request.target, prop if accepted else 0, request.time)
                self.x.XSendEvent(self.display, request.requestor, 0, 0, C.byref(reply))
            elif event.type == PROPERTY_NOTIFY and event.property.state == 1:
                key = (event.property.window, event.property.atom)
                transfer = self.transfers.get(key)
                if transfer:
                    kind, data, offset, _ = transfer
                    chunk = data[offset:offset + CHUNK]
                    self.put(*key, kind, chunk)
                    if chunk:
                        self.transfers[key] = (kind, data, offset + len(chunk), time.monotonic())
                    else:
                        del self.transfers[key]


def main() -> None:
    # A killed host cannot leave a selection owner (and its text) orphaned.
    parent = os.getppid()
    libc = C.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(C.get_errno(), "Cannot bind clipboard worker lifetime")
    if parent == 1 or os.getppid() != parent:
        return
    try:
        locale.setlocale(locale.LC_CTYPE, "")
    except locale.Error:
        # SSH can forward a locale that is not installed on the remote host.
        # UTF8_STRING does not depend on that locale being available.
        locale.setlocale(locale.LC_CTYPE, "C")
    selection = Selection()
    try:
        if sys.argv[1] == "read":
            sys.stdout.buffer.write(selection.read())
        else:
            header = sys.stdin.buffer.read(8)
            if len(header) != 8:
                raise EOFError
            length = int.from_bytes(header, "big")
            if length > MAX_BYTES:
                raise ValueError("Clipboard text too large")
            content = sys.stdin.buffer.read(length)
            if len(content) != length:
                raise EOFError
            selection.copy(content)
    finally:
        selection.close()


if __name__ == "__main__":
    try:
        main()
    except EOFError:
        pass
    except ImportError:
        sys.exit(2)
    except ConnectionError:
        sys.exit(3)
    except TimeoutError:
        sys.exit(4)
    except (OSError, RuntimeError, ValueError):
        sys.exit(1)
