#!/usr/bin/env python3
# Drives WinPE (the Windows Setup boot environment) over the QEMU monitor to
# inject virtio-blk/virtio-net drivers into an offline Windows image with
# DISM, then shuts the VM down. Windows Setup's answer-file engine rejects a
# windowsPE-pass RunSynchronous block with no ImageInstall/UserData (fails
# with error 0x80070002 - 0x40030 right after the language screen), so this
# drives the same Shift+F10 rescue-console commands a person would type by
# hand instead - proven working via manual testing.
#
# Retries the whole sequence a few times spaced well apart, since we can't
# see the screen to know exactly when Setup is ready for input; DISM's
# /add-driver is idempotent (a second pass on an already-installed driver is
# a harmless no-op), and once wpeutil shutdown succeeds the monitor socket
# stops accepting connections, which ends the retry loop.
import socket
import sys
import time

KEYMAP = {
    " ": "spc", "=": "equal", ":": "shift-semicolon", "\\": "backslash",
    ".": "dot", "-": "minus", "_": "shift-minus", "*": "shift-8",
    "/": "slash", ",": "comma", "!": "shift-1", "(": "shift-9", ")": "shift-0",
    '"': "shift-apostrophe", "'": "apostrophe", "+": "shift-equal",
    "|": "shift-backslash", ";": "semicolon", "<": "shift-comma", ">": "shift-dot",
    "#": "shift-3", "&": "shift-7", "%": "shift-5", "@": "shift-2", "$": "shift-4",
}

DRIVER_COMMANDS = [
    r"cmd /c for %D in (D E F G H) do @if exist %D:\viostor\nul dism /image:C:\ /add-driver /driver:%D:\viostor\w11\amd64\viostor.inf",
    r"cmd /c for %D in (D E F G H) do @if exist %D:\NetKVM\nul dism /image:C:\ /add-driver /driver:%D:\NetKVM\w11\amd64\netkvm.inf",
]


def send_sequence(sock_path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(sock_path)
    time.sleep(0.2)
    s.recv(65536)

    def cmd(c):
        s.sendall((c + "\n").encode())
        time.sleep(0.04)
        try:
            s.recv(65536)
        except Exception:
            pass

    def typeline(line):
        for c in line:
            if c in KEYMAP:
                k = KEYMAP[c]
            elif c.isupper():
                k = f"shift-{c.lower()}"
            else:
                k = c
            cmd(f"sendkey {k}")
        cmd("sendkey ret")

    cmd("sendkey shift-f10")
    time.sleep(2)
    for line in DRIVER_COMMANDS:
        typeline(line)
        time.sleep(10)
    typeline("wpeutil shutdown")
    s.close()


def main():
    sock_path = sys.argv[1]
    for attempt in range(6):
        time.sleep(20 if attempt == 0 else 45)
        try:
            send_sequence(sock_path)
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            break


if __name__ == "__main__":
    main()
