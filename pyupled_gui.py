"""Optional Tk GUI for the upLED driver.

Run: python -m pyupled_gui   (pick "Demo" as port to try it without hardware)

Drag the knob (or scroll on it) to set the current, click the LED head or the big
button to switch the output. The drawing is a generic stylized LED driver.
"""

from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any, TypeVar

import serial

from pyupled import UpLed, UpLedError

BG: str = "#1d1f24"
PANEL: str = "#272a31"
FG: str = "#e8e8e8"
MUTED: str = "#8b909a"
ACCENT: str = "#4fa3ff"
DEMO: str = "Demo (no hardware)"
POLL_MS: int = 300
W: int = 600
H: int = 300
KNOB: tuple[int, int, int] = (150, 150, 52)  # centre x, centre y, radius
LENS: tuple[int, int] = (468, 150)

Rgb = tuple[int, int, int]
T = TypeVar("T")


def wavelength_rgb(nm: float) -> Rgb:
    """Approximate visible colour of a wavelength (UV/IR clamp to violet/red)."""
    nm = min(max(nm, 380.0), 780.0)
    r: float
    g: float
    b: float
    if nm < 440:
        r, g, b = (440 - nm) / 60, 0.0, 1.0
    elif nm < 490:
        r, g, b = 0.0, (nm - 440) / 50, 1.0
    elif nm < 510:
        r, g, b = 0.0, 1.0, (510 - nm) / 20
    elif nm < 580:
        r, g, b = (nm - 510) / 70, 1.0, 0.0
    elif nm < 645:
        r, g, b = 1.0, (645 - nm) / 65, 0.0
    else:
        r, g, b = 1.0, 0.0, 0.0
    return int(r * 255), int(g * 255), int(b * 255)


def colour_temp_rgb(kelvin: float) -> Rgb:
    """Approximate RGB of a black body at `kelvin` (T. Helland's fit)."""
    t: float = max(kelvin, 1000.0) / 100

    def clamp(x: float) -> int:
        return int(min(max(x, 0.0), 255.0))

    r: float = 255 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g: float = (
        99.4708025861 * math.log(t) - 161.1195681661
        if t <= 66
        else 288.1221695283 * (t - 60) ** -0.0755148492
    )
    b: float = 255.0 if t >= 66 else 0.0
    if 19 < t < 66:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307
    return clamp(r), clamp(g), clamp(b)


def led_rgb(wavelength: float) -> Rgb:
    """upLED convention: >0 nm, <0 white LED colour temp in K, 0 unknown (white)."""
    if wavelength < 0:
        return colour_temp_rgb(-wavelength)
    if wavelength > 0:
        return wavelength_rgb(wavelength)
    return 255, 255, 255


def mix(a: str | Rgb, b: Rgb, t: float) -> str:
    """Blend colour a towards b by t in [0, 1]; returns '#rrggbb'."""
    if isinstance(a, str):
        a = (int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16))
    t = min(max(t, 0.0), 1.0)
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(a, b))


class DemoLed:
    """Stand-in with the subset of the UpLed API the GUI uses."""

    port: str = DEMO

    def __init__(self) -> None:
        self._on: bool = False
        self._i: float = 0.1
        self._lim: float = 1.0

    def led_info(self) -> tuple[str, float]:
        return "demo LED", 530.0

    def set_output(self, enabled: bool) -> None:
        self._on = enabled

    def get_output(self) -> bool:
        return self._on

    def set_current(self, amps: float) -> None:
        self._i = amps

    def set_current_limit(self, amps: float) -> None:
        self._lim = amps

    def get_current_limit(self) -> float:
        return self._lim

    def get_current_min(self) -> float:
        return 0.01

    def measure_current(self) -> float:
        return self._i * 0.98 if self._on else 0.0

    def measure_voltage(self) -> float:
        return 2.4 + 0.8 * self._i if self._on else 0.0

    def measure_temperature(self) -> float:
        return 24.0 + 6.0 * self._i * self._on

    def measure_supply_voltage(self) -> float:
        return 12.1

    def get_op_mode(self) -> tuple[int, str]:
        return (9, "LED ON Interface") if self._on else (7, "LED OFF VccOK")

    def disconnect(self) -> None:
        pass


class Tip:
    """Hover tooltip."""

    def __init__(self, widget: tk.Misc, text: str) -> None:
        self.widget: tk.Misc = widget
        self.text: str = text
        self.win: tk.Toplevel | None = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _: tk.Event[tk.Misc]) -> None:
        self.hide(None)
        self.win = tk.Toplevel(self.widget)
        self.win.wm_overrideredirect(True)
        x: int = self.widget.winfo_rootx() + 18
        y: int = self.widget.winfo_rooty() + 22
        self.win.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.win, text=self.text, bg="#3a3e47", fg=FG, justify="left",
            wraplength=280, padx=10, pady=7,
        ).pack()

    def hide(self, _: tk.Event[tk.Misc] | None) -> None:
        if self.win is not None:
            self.win.destroy()
            self.win = None


def labelled(parent: tk.Misc, text: str, tip: str, style: str = "TLabel") -> ttk.Frame:
    """Label followed by a hoverable '?' that explains it."""
    frame = ttk.Frame(parent, style="Card.TFrame" if style != "TLabel" else "TFrame")
    ttk.Label(frame, text=text, style=style).pack(side="left")
    mark = ttk.Label(frame, text=" ?", style="Help.TLabel", cursor="question_arrow")
    if style != "TLabel":
        mark.configure(background=PANEL)
    mark.pack(side="left")
    Tip(mark, tip)
    return frame


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root: tk.Tk = root
        self.led: UpLed | DemoLed | None = None
        self.led_name: str = ""
        self.rgb: Rgb = (255, 255, 255)
        self.on: bool = False
        self.limit: float = 1.0  # A
        self.setpoint: float = 0.1  # A
        self.measured: float = 0.0  # A
        self._apply_job: str | None = None
        self._poll_job: str | None = None

        root.title("upLED Control")
        root.configure(bg=BG)
        root.resizable(False, False)
        self._style()

        top = ttk.Frame(root, padding=(12, 10))
        top.pack(fill="x")
        labelled(
            top, "Port", "upLEDs found on USB (matched by their USB ID). "
            "⟳ rescans. Pick Demo to try the app without hardware.",
        ).pack(side="left")
        self.port_var: tk.StringVar = tk.StringVar()
        self.port_box = ttk.Combobox(
            top, textvariable=self.port_var, width=34, state="readonly"
        )
        self.port_box.pack(side="left", padx=6)
        refresh = ttk.Button(top, text="⟳", width=3, command=self.refresh_ports)
        refresh.pack(side="left")
        self.conn_btn = ttk.Button(top, text="Connect", command=self.toggle_connection)
        self.conn_btn.pack(side="left", padx=6)

        self.canvas = tk.Canvas(root, width=W, height=H, bg=BG, highlightthickness=0)
        self.canvas.pack(padx=12)
        self.canvas.tag_bind("knob", "<B1-Motion>", self._knob_drag)
        self.canvas.tag_bind("knob", "<Button-1>", self._knob_drag)
        # Canvas tags can't take wheel events; _knob_wheel checks the position.
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(seq, self._knob_wheel)
        self.canvas.tag_bind("head", "<Button-1>", lambda e: self.toggle_output())
        for tag in ("knob", "head"):
            self.canvas.tag_bind(tag, "<Enter>", lambda e: self._cursor("hand2"))
            self.canvas.tag_bind(tag, "<Leave>", lambda e: self._cursor(""))

        ctl = ttk.Frame(root, padding=(12, 8))
        ctl.pack(fill="x")
        self.out_btn = ttk.Button(
            ctl, text="LED OFF", style="Off.TButton", command=self.toggle_output
        )
        self.out_btn.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 14))
        Tip(self.out_btn, "Switch the LED output. Switching on first sends the "
            "current setpoint. Clicking the LED head in the drawing does the same.")
        labelled(
            ctl, "Current (mA)", "LED current setpoint. Drag or scroll the knob, "
            "click or drag the bar, or type a value and press Enter. Overrides the "
            "knob on the device itself.",
        ).grid(row=0, column=1, sticky="w")
        self.scale = ttk.Scale(
            ctl, from_=0, to=1000, length=260, command=self._scale_moved
        )
        self.scale.grid(row=0, column=2, padx=6)
        self.scale.bind("<Button-1>", self._scale_click)
        self.scale.bind("<B1-Motion>", self._scale_click)
        self.cur_var: tk.StringVar = tk.StringVar()
        cur_entry = ttk.Entry(ctl, textvariable=self.cur_var, width=8)
        cur_entry.grid(row=0, column=3)
        cur_entry.bind("<Return>", self._cur_entered)
        labelled(
            ctl, "Limit (mA)", "User current limit: the highest setpoint allowed. "
            "It can't exceed the limit stored in the LED head. Type a value and "
            "press Enter.",
        ).grid(row=1, column=1, sticky="w")
        self.lim_var: tk.StringVar = tk.StringVar()
        lim_entry = ttk.Entry(ctl, textvariable=self.lim_var, width=8)
        lim_entry.grid(row=1, column=3, pady=4)
        lim_entry.bind("<Return>", self._lim_entered)
        ttk.Label(ctl, text="press Enter to apply", foreground=MUTED).grid(
            row=1, column=2, sticky="e", padx=6
        )

        read = ttk.Frame(root, padding=(12, 4))
        read.pack(fill="x")
        self.readouts: dict[str, tk.StringVar] = {}
        tips: dict[str, str] = {
            "Current": "Measured LED current (reads ~10 % low at a few mA).",
            "Voltage": "Measured LED forward voltage.",
            "Temp": "Temperature inside the driver.",
            "Supply": "12 V supply input. The LED can't switch on without it.",
        }
        for col, key in enumerate(tips):
            box = ttk.Frame(read, style="Card.TFrame", padding=(10, 6))
            box.grid(row=0, column=col, padx=(0, 8), sticky="ew")
            read.columnconfigure(col, weight=1)
            labelled(box, key, tips[key], "CardMuted.TLabel").pack(anchor="w")
            var = tk.StringVar(value="–")
            ttk.Label(box, textvariable=var, style="Value.TLabel").pack(anchor="w")
            self.readouts[key] = var

        self.status: tk.StringVar = tk.StringVar(value="Not connected")
        ttk.Label(
            root, textvariable=self.status, foreground=MUTED, padding=(12, 8)
        ).pack(fill="x")

        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_ports()
        self._sync_inputs()
        self.draw()

    def _cursor(self, name: str) -> None:
        self.canvas.config(cursor=name)

    # --- look -------------------------------------------------------------

    def _style(self) -> None:
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL)
        s.configure(
            "TButton", background=PANEL, foreground=FG, borderwidth=0, padding=6
        )
        s.map("TButton", background=[("active", "#353943")])
        s.configure("Off.TButton", font=("TkDefaultFont", 13, "bold"), padding=(18, 14))
        s.configure(
            "On.TButton", font=("TkDefaultFont", 13, "bold"), padding=(18, 14),
            background=ACCENT, foreground="#0d1117",
        )
        s.map("On.TButton", background=[("active", "#7bbcff")])
        edge: dict[str, str] = {
            "bordercolor": "#3a3e47", "lightcolor": PANEL, "darkcolor": PANEL
        }
        s.configure("TCombobox", arrowcolor=FG, background=PANEL, **edge)
        s.map(
            "TCombobox",
            fieldbackground=[("readonly", PANEL)],
            foreground=[("readonly", FG)],
            background=[("active", "#353943"), ("readonly", PANEL)],
            selectbackground=[("readonly", PANEL)],
            selectforeground=[("readonly", FG)],
        )
        for opt, val in (("background", PANEL), ("foreground", FG),
                         ("selectBackground", ACCENT), ("selectForeground", BG)):
            self.root.option_add(f"*TCombobox*Listbox.{opt}", val)
        s.configure("TEntry", insertcolor=FG, **edge)
        s.configure(
            "Help.TLabel", foreground=ACCENT, font=("TkDefaultFont", 10, "bold")
        )
        s.configure("Card.TFrame", background=PANEL)
        s.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED)
        s.configure(
            "Value.TLabel", background=PANEL, font=("TkDefaultFont", 15, "bold")
        )
        s.configure(
            "Horizontal.TScale", background=ACCENT, troughcolor=PANEL, **edge
        )

    def draw(self) -> None:
        c: tk.Canvas = self.canvas
        c.delete("all")
        connected: bool = self.led is not None
        frac: float = self.setpoint / self.limit if self.limit > 0 else 0.0
        level: float = self.measured if self.measured > 0 else self.setpoint
        glow: float = 0.0
        if self.on and self.limit > 0:
            glow = math.sqrt(min(level / self.limit, 1.0))
        lx, ly = LENS

        # Light first so the hardware draws on top of it.
        for i in range(16, 0, -1):
            r: float = 14 + i * 11 * (0.4 + 0.6 * glow)
            fill: str = mix(BG, self.rgb, glow * (1 - i / 17) ** 2)
            c.create_oval(lx - r, ly - r, lx + r, ly + r, outline="", fill=fill)
        if glow:
            for k in range(-3, 4):
                a: float = math.radians(k * 11)
                ln: float = 40 + 70 * glow
                c.create_line(
                    lx + 26 * math.cos(a), ly + 26 * math.sin(a),
                    lx + (26 + ln) * math.cos(a), ly + (26 + ln) * math.sin(a),
                    fill=mix(BG, self.rgb, 0.5 * glow), width=2,
                )

        # Driver box.
        self._round_rect(
            40, 50, 270, 250, 22, fill="#3a3e47", outline="#4b505b", width=2
        )
        self._round_rect(52, 62, 258, 238, 16, fill="#32363e", outline="")
        c.create_rectangle(26, 132, 40, 168, fill="#6c717c", outline="")  # USB
        c.create_rectangle(30, 140, 40, 160, fill="#1d1f24", outline="")
        c.create_text(70, 78, text="LED DRIVER", anchor="w", fill=MUTED,
                      font=("TkDefaultFont", 9, "bold"))
        dot: str = "#555a64" if not connected else ACCENT if self.on else "#3ccf6e"
        c.create_oval(236, 72, 248, 84, fill=dot, outline="")

        # Knob: 270 degree sweep, ticks, indicator.
        kx, ky, kr = KNOB
        for n in range(11):
            a = math.radians(225 - 27 * n)
            c.create_line(
                kx + (kr + 8) * math.cos(a), ky - (kr + 8) * math.sin(a),
                kx + (kr + 14) * math.cos(a), ky - (kr + 14) * math.sin(a),
                fill=ACCENT if n / 10 <= frac + 1e-9 else "#5a5f6a", width=2,
            )
        c.create_oval(kx - kr, ky - kr, kx + kr, ky + kr, fill="#23262c",
                      outline="#565b66", width=3, tags="knob")
        c.create_oval(kx - kr + 10, ky - kr + 10, kx + kr - 10, ky + kr - 10,
                      fill="#2d3038", outline="", tags="knob")
        a = math.radians(225 - 270 * frac)
        c.create_line(kx + 12 * math.cos(a), ky - 12 * math.sin(a),
                      kx + (kr - 14) * math.cos(a), ky - (kr - 14) * math.sin(a),
                      fill=FG, width=4, capstyle="round", tags="knob")
        c.create_text(kx, ky + kr + 30, text=f"{self.setpoint * 1e3:.0f} mA",
                      fill=FG, font=("TkDefaultFont", 14, "bold"))

        # Cable and LED head.
        c.create_line(270, 150, 320, 150, 345, 190, 380, 150, 420, 150,
                      smooth=True, width=7, fill="#15171b", capstyle="round")
        self._round_rect(400, 116, 456, 184, 8, fill="#585d68", outline="#6d7380",
                         width=2, tags="head")
        for y in (128, 140, 160, 172):  # heat-sink fins
            c.create_line(406, y, 450, y, fill="#4a4f59", width=2, tags="head")
        c.create_oval(lx - 14, ly - 26, lx + 14, ly + 26, outline="#7a808c", width=2,
                      fill=mix("#2a2d33", self.rgb, 0.25 + 0.75 * glow), tags="head")
        self._pill(428, 208, self.led_name or "LED head", 10, FG)
        self._pill(428, 96, "click to switch", 9, "#b4b9c3")

    def _pill(self, x: float, y: float, text: str, size: int, fill: str) -> None:
        """Text on a dark backing so it stays readable on top of the glow."""
        c: tk.Canvas = self.canvas
        t: int = c.create_text(x, y, text=text, fill=fill, font=("TkDefaultFont", size))
        x1, y1, x2, y2 = c.bbox(t)
        self._round_rect(x1 - 8, y1 - 3, x2 + 8, y2 + 3, 8, fill="#16181c", outline="")
        c.tag_raise(t)

    def _round_rect(
        self, x1: float, y1: float, x2: float, y2: float, r: float, **kw: Any
    ) -> None:
        pts: list[float] = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r,
                            x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
                            x1, y1 + r, x1, y1]
        self.canvas.create_polygon(pts, smooth=True, **kw)

    # --- connection -------------------------------------------------------

    def refresh_ports(self) -> None:
        ports: list[str] = [f"{p}  ({sn})" for p, sn in UpLed.find()] + [DEMO]
        self.port_box["values"] = ports
        if self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def toggle_connection(self) -> None:
        if self.led is not None:
            self.disconnect()
            return
        choice: str = self.port_var.get()
        try:
            if choice == DEMO:
                self.led = DemoLed()
                self.led_name, wl = self.led.led_info()
            else:
                led = UpLed(choice.split("  (")[0])
                self.led = led
                info = led.led_info()
                self.led_name, wl = info.name, info.wavelength
            self.rgb = led_rgb(wl)
            self.limit = self.led.get_current_limit()
            low: float = self.led.get_current_min()
            self.setpoint = min(max(self.setpoint, low), self.limit)
            colour: str = f"{-wl:.0f} K" if wl < 0 else f"{wl:.0f} nm" if wl else ""
            self.status.set(f"Connected to {choice}   {self.led_name} {colour}")
        except (UpLedError, serial.SerialException, OSError) as e:
            self.disconnect()
            self.status.set(f"Connect failed: {e}")
            return
        self.conn_btn.config(text="Disconnect")
        self._sync_inputs()
        self.poll()

    def disconnect(self) -> None:
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        if self.led is not None:
            try:
                self.led.disconnect()
            except (UpLedError, serial.SerialException, OSError):
                pass
        self.led = None
        self.on = False
        self.measured = 0.0
        self.conn_btn.config(text="Connect")
        self.out_btn.config(text="LED OFF", style="Off.TButton")
        for var in self.readouts.values():
            var.set("–")
        self.status.set("Not connected")
        self.root.title("upLED Control")
        self.draw()

    def _call(self, what: str, fn: Callable[..., T], *args: object) -> T | None:
        """Run a device call; show errors in the status bar instead of raising."""
        try:
            return fn(*args)
        except UpLedError as e:
            self.status.set(f"{what}: {e}")
        except (serial.SerialException, OSError) as e:
            self.disconnect()
            self.status.set(f"{what}: connection lost ({e})")
        return None

    # --- actions ----------------------------------------------------------

    def toggle_output(self) -> None:
        if self.led is None:
            self.status.set("Connect first")
            return
        if not self.on:
            self._call("Set current", self.led.set_current, self.setpoint)
        if self.led is not None:
            self._call("Switch", self.led.set_output, not self.on)
            self.poll(reschedule=False)

    def _set_setpoint(self, amps: float, apply_now: bool = False) -> None:
        self.setpoint = min(max(amps, 0.0), self.limit)
        self._sync_inputs()
        self.draw()
        if self._apply_job:
            self.root.after_cancel(self._apply_job)
        self._apply_job = self.root.after(0 if apply_now else 150, self._apply)

    def _apply(self) -> None:
        self._apply_job = None
        if self.led is not None and self.on:
            self._call("Set current", self.led.set_current, self.setpoint)

    def _sync_inputs(self) -> None:
        self.scale.config(to=self.limit * 1e3)
        self.scale.set(self.setpoint * 1e3)
        self.cur_var.set(f"{self.setpoint * 1e3:.1f}")
        self.lim_var.set(f"{self.limit * 1e3:.0f}")

    def _scale_click(self, e: tk.Event[ttk.Scale]) -> str:
        """Jump to the clicked spot (ttk default only steps) and drag from there."""
        pad: int = 10  # ~half the slider handle
        width: int = max(self.scale.winfo_width() - 2 * pad, 1)
        self._set_setpoint(min(max((e.x - pad) / width, 0.0), 1.0) * self.limit)
        return "break"

    def _scale_moved(self, value: str) -> None:
        if abs(float(value) / 1e3 - self.setpoint) > 1e-6:
            self._set_setpoint(float(value) / 1e3)

    def _cur_entered(self, _: tk.Event[tk.Entry]) -> None:
        try:
            self._set_setpoint(float(self.cur_var.get()) / 1e3, apply_now=True)
        except ValueError:
            self._sync_inputs()

    def _lim_entered(self, _: tk.Event[tk.Entry]) -> None:
        try:
            amps: float = float(self.lim_var.get()) / 1e3
        except ValueError:
            self._sync_inputs()
            return
        if self.led is not None:
            self._call("Set limit", self.led.set_current_limit, amps)
            got: float | None = self._call("Read limit", self.led.get_current_limit)
            if got is not None:
                self.limit = got
        else:
            self.limit = amps
        self._set_setpoint(self.setpoint)

    def _knob_drag(self, e: tk.Event[tk.Canvas]) -> None:
        kx, ky, _ = KNOB
        a: float = math.degrees(math.atan2(ky - e.y, e.x - kx))
        if a < -90:
            a += 360
        self._set_setpoint((225 - a) / 270 * self.limit)

    def _knob_wheel(self, e: tk.Event[tk.Canvas]) -> None:
        kx, ky, kr = KNOB
        if math.hypot(e.x - kx, e.y - ky) > kr + 16:
            return
        up: bool = e.num == 4 or (e.num != 5 and e.delta > 0)  # X11 uses num 4/5
        self._set_setpoint(self.setpoint + (0.005 if up else -0.005))

    # --- polling ----------------------------------------------------------

    def poll(self, reschedule: bool = True) -> None:
        led = self.led
        if led is None:
            return
        out: bool | None = self._call("Output", led.get_output)
        floats: dict[str, float | None] = {}
        for name, fn in (
            ("Current", led.measure_current),
            ("Voltage", led.measure_voltage),
            ("Temp", led.measure_temperature),
            ("Supply", led.measure_supply_voltage),
        ):
            if self.led is None:  # connection lost mid-poll
                return
            floats[name] = self._call(name, fn)
        mode: tuple[int, str] | None = self._call("Mode", led.get_op_mode)
        if self.led is None:
            return
        cur, volt, temp, sup = floats.values()
        if out is not None:
            self.on = out
            self.out_btn.config(
                text="LED ON" if out else "LED OFF",
                style="On.TButton" if out else "Off.TButton",
            )
        if cur is not None:
            self.measured = cur
            self.readouts["Current"].set(f"{cur * 1e3:.1f} mA")
        if volt is not None:
            self.readouts["Voltage"].set(f"{volt:.2f} V")
        if temp is not None:
            self.readouts["Temp"].set(f"{temp:.1f} °C")
        if sup is not None:
            self.readouts["Supply"].set(f"{sup:.1f} V")
        if mode is not None:
            self.root.title(f"upLED Control  ·  {mode[1]}")
        self.draw()
        if reschedule:
            self._poll_job = self.root.after(POLL_MS, self.poll)

    def close(self) -> None:
        self.disconnect()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
