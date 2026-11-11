"""`python -m frost.overlay` — the always-on side panel.

A slim translucent strip pinned to the right edge of the screen, like a phone's
assistant: an orb that shows what FROST is doing, the last few exchanges, and a
talk button. Frameless, always on top, draggable, and dismissible to a small
puck so it can sit there all day without being in the way.

Built on tkinter rather than PyQt deliberately. tkinter ships with Python, so
this adds nothing to install, and Windows exposes exactly the three things this
needs through it: `-topmost`, `-alpha` for translucency, and `overrideredirect`
for a frameless window. PyQt would buy nicer widgets at the cost of a ~100MB
dependency, which is the wrong trade for a panel that is mostly one canvas.

Threading: tkinter owns the main thread and asyncio cannot share it, so the
voice session runs on a worker thread and posts events into a queue that the UI
drains on a timer. Nothing touches a widget from the worker.
"""

from __future__ import annotations

import asyncio
import queue
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont

from .config import LISTEN_MODE, MODEL, WAKE_WORD

# Palette. Near-black with one cool accent, deliberately restrained.
BG = "#0a0e14"
FG = "#c8d3e0"
DIM_FG = "#5a6878"
ACCENT = "#4db8ff"
USER_FG = "#7ee0a8"

STATE_COLOUR = {
    "idle": "#3a4757",
    "listening": USER_FG,
    "thinking": "#ffc857",
    "speaking": ACCENT,
}
STATE_LABEL = {
    "idle": "idle",
    "listening": "listening",
    "thinking": "thinking",
    "speaking": "speaking",
}

WIDTH = 340
MARGIN = 16
MAX_LINES = 40


class Orb(tk.Canvas):
    """The state indicator. Breathes when idle, pulses harder when busy."""

    def __init__(self, master, size: int = 84) -> None:
        super().__init__(master, width=size, height=size, bg=BG, highlightthickness=0)
        self.size = size
        self.state = "idle"
        self._phase = 0.0
        self._animate()

    def set_state(self, state: str) -> None:
        self.state = state if state in STATE_COLOUR else "idle"

    def _animate(self) -> None:
        import math

        self.delete("all")
        c = self.size / 2
        colour = STATE_COLOUR[self.state]
        speed = {"idle": 0.04, "listening": 0.16, "thinking": 0.11, "speaking": 0.22}[self.state]
        self._phase += speed

        # Concentric rings, each offset in phase, so it reads as motion rather
        # than a blinking light.
        for i, base in enumerate((0.92, 0.72, 0.52)):
            wobble = math.sin(self._phase - i * 0.7) * (0.05 if self.state == "idle" else 0.12)
            r = c * (base + wobble)
            self.create_oval(c - r, c - r, c + r, c + r, outline=colour, width=1 + (i == 0))

        core = c * (0.2 + 0.05 * math.sin(self._phase * 1.4))
        self.create_oval(c - core, c - core, c + core, c + core, fill=colour, outline="")
        self.after(50, self._animate)


class Overlay:
    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()
        self.session = None
        self._collapsed = False
        self._drag = (0, 0)

        self.root = tk.Tk()
        self.root.title("FROST")
        self.root.overrideredirect(True)          # frameless
        self.root.attributes("-topmost", True)    # always on top
        self.root.attributes("-alpha", 0.90)      # translucent
        self.root.configure(bg=BG)

        self._height = min(620, self.root.winfo_screenheight() - 2 * MARGIN)
        self._place()
        self._build()
        # Windows applies geometry to an override-redirect window unreliably
        # before it is mapped, so the panel can end up hanging off the right
        # edge. Re-assert the position once the window actually exists.
        self.root.after(120, self._place)
        self.root.after(600, self._place)
        self.root.bind("<space>", lambda _e: self.talk())
        self.root.bind("<Escape>", lambda _e: self.quit())
        self.root.after(60, self._drain)

    # --- layout ------------------------------------------------------------

    def _build(self) -> None:
        mono = tkfont.Font(family="Consolas", size=9)
        self.body_font = tkfont.Font(family="Segoe UI", size=10)

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=14, pady=(12, 4))
        header.bind("<Button-1>", self._grab)
        header.bind("<B1-Motion>", self._move)

        title = tk.Label(header, text="F R O S T", bg=BG, fg=ACCENT,
                         font=tkfont.Font(family="Consolas", size=11, weight="bold"))
        title.pack(side="left")
        title.bind("<Button-1>", self._grab)
        title.bind("<B1-Motion>", self._move)

        tk.Button(header, text="✕", command=self.quit, bg=BG, fg=DIM_FG,
                  activebackground=BG, activeforeground=FG, bd=0,
                  font=mono, cursor="hand2").pack(side="right")
        tk.Button(header, text="—", command=self.toggle, bg=BG, fg=DIM_FG,
                  activebackground=BG, activeforeground=FG, bd=0,
                  font=mono, cursor="hand2").pack(side="right", padx=(0, 8))

        self.panel = tk.Frame(self.root, bg=BG)
        self.panel.pack(fill="both", expand=True)

        self.orb = Orb(self.panel)
        self.orb.pack(pady=(6, 0))

        self.state_label = tk.Label(self.panel, text="idle", bg=BG, fg=DIM_FG, font=mono)
        self.state_label.pack(pady=(2, 8))

        self.transcript = tk.Text(
            self.panel, bg=BG, fg=FG, bd=0, highlightthickness=0, wrap="word",
            font=self.body_font, padx=14, pady=4, spacing1=3, spacing3=6,
            cursor="arrow",
        )
        self.transcript.pack(fill="both", expand=True)
        self.transcript.tag_configure("user", foreground=USER_FG)
        self.transcript.tag_configure("frost", foreground=FG)
        self.transcript.tag_configure("status", foreground=DIM_FG, font=mono)
        self.transcript.configure(state="disabled")

        footer = tk.Frame(self.panel, bg=BG)
        footer.pack(fill="x", padx=14, pady=(0, 12))
        self.talk_btn = tk.Button(
            footer, text="press SPACE to talk", command=self.talk,
            bg="#131a24", fg=FG, activebackground="#1b2530", activeforeground=ACCENT,
            bd=0, font=mono, cursor="hand2", pady=7,
        )
        self.talk_btn.pack(fill="x")
        tk.Label(footer, text=f"{MODEL}  ·  drag title to move  ·  Esc quits",
                 bg=BG, fg=DIM_FG, font=tkfont.Font(family="Consolas", size=7)).pack(pady=(6, 0))

        self._say("status", "starting up...")

    # --- window behaviour --------------------------------------------------

    def _place(self) -> None:
        """Pin to the right edge, clamped so it can never sit off-screen."""
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        x = max(0, sw - WIDTH - MARGIN)
        self.root.geometry(f"{WIDTH}x{self._height}+{x}+{MARGIN}")
        self.root.lift()

    def _grab(self, event) -> None:
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _move(self, event) -> None:
        self.root.geometry(f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    def toggle(self) -> None:
        """Collapse to just the orb, so it can live on screen unobtrusively."""
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.transcript.pack_forget()
            self.state_label.pack_forget()
            self._height = 160
        else:
            self.state_label.pack(pady=(2, 8))
            self.transcript.pack(fill="both", expand=True)
            self._height = min(620, self.root.winfo_screenheight() - 2 * MARGIN)
        self._place()

    def talk(self) -> None:
        if self.session is not None:
            self.session.request_talk()

    def quit(self) -> None:
        try:
            if self.session is not None:
                self.session._quit.set()
                self.session.request_talk()  # unblock the waiting loop
        finally:
            self.root.after(150, self.root.destroy)

    # --- events ------------------------------------------------------------

    def post(self, kind: str, text: str) -> None:
        """Thread-safe: called from the voice worker, drained on the UI thread."""
        self.events.put((kind, text))

    def _drain(self) -> None:
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "state":
                    self.orb.set_state(text)
                    self.state_label.configure(
                        text=STATE_LABEL.get(text, text), fg=STATE_COLOUR.get(text, DIM_FG)
                    )
                    if text == "listening":
                        self.talk_btn.configure(text="listening — speak now", fg=USER_FG)
                    elif text == "idle":
                        hint = ("press SPACE to talk" if LISTEN_MODE == "push"
                                else f"say '{WAKE_WORD}'")
                        self.talk_btn.configure(text=hint, fg=FG)
                    else:
                        self.talk_btn.configure(text=STATE_LABEL.get(text, text), fg=DIM_FG)
                else:
                    self._say(kind, text)
        except queue.Empty:
            pass
        self.root.after(60, self._drain)

    def _say(self, kind: str, text: str) -> None:
        prefix = {"user": "you   ", "frost": "frost ", "status": "      "}.get(kind, "")
        self.transcript.configure(state="normal")
        self.transcript.insert("end", f"{prefix}{text}\n", kind)
        # Keep the buffer bounded; an all-day panel would otherwise grow forever.
        lines = int(self.transcript.index("end-1c").split(".")[0])
        if lines > MAX_LINES:
            self.transcript.delete("1.0", f"{lines - MAX_LINES}.0")
        self.transcript.see("end")
        self.transcript.configure(state="disabled")

    # --- run ---------------------------------------------------------------

    def start_voice(self) -> None:
        from .voice import VoiceSession

        def worker() -> None:
            session = VoiceSession(on_event=self.post)
            self.session = session
            try:
                asyncio.run(session.run())
            except Exception as exc:  # noqa: BLE001
                # Degrade loudly: a dead mic must not look like a quiet one.
                self.post("status", f"voice stopped: {exc}")
            finally:
                session.close()
                self.post("state", "idle")
                self.post("status", "voice session ended")

        threading.Thread(target=worker, daemon=True).start()

    def run(self) -> None:
        self.start_voice()
        self.root.mainloop()


def main() -> int:
    Overlay().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
