"""Cross-platform driver for the Thorlabs upLED (USB CDC-ACM, VID 0x1313 PID 0x8103).

Speaks the device's ASCII protocol directly over pyserial; no Thorlabs DLL needed.
See PROTOCOL.md for the wire format.
"""

from __future__ import annotations

import enum
import math
import re
import threading
from dataclasses import dataclass
from types import TracebackType

import serial
from serial.tools import list_ports

VID: int = 0x1313
PID: int = 0x8103

ERRORS: dict[int, str] = {
    1: "not supported by this upSERIES type",
    2: "error parsing a parameter",
    **{2 + n: f"parameter {n} out of range" for n in range(1, 9)},
    11: "unexpected end of command",
    12: "only allowed in service mode",
    13: "not allowed in service mode",
    16: "HAL error",
    17: "HAL busy",
    18: "HAL timeout",
    19: "string conversion out of range",
    20: "USB library error",
    21: "not authenticated",
    22: "authentication attempt failed",
    25: "invalid attribute",
    26: "invalid response from device",
    27: "invalid parameter response from device",
    40: "NVMEM boot error",
    41: "NVMEM empty",
    42: "NVMEM checksum error",
    50: "DAC out of range",
    51: "ADC out of range",
    60: "buffer overflow",
    61: "buffer underflow",
    62: "command buffer overflow",
    65: "only allowed in idle mode",
    80: "range error",
    90: "adjust value error",
    100: "setup value error",
    111: "1-wire timeout",
    112: "no LED detected",
    113: "switch ON cancelled due to over-temperature",
    114: "no 12 V supply detected, needed for LED ON",
    **{n: "flash / data-set error" for n in range(70, 79)},
}

# STATE? bit index -> name (TLUP.h TLUP_OPERATION_MODE_*).
STATE_BITS: dict[int, str] = {
    0: "RUN",
    1: "ENA",
    2: "OWB_DET",
    3: "OWB_OK",
    4: "VCC_ERR",
    5: "E_VCC_ERR",
    6: "OTP",
    7: "E_OTP",
    8: "LED_ERR",
    9: "OPEN",
    10: "NON_THOR_LED",
    11: "RECON",
    12: "PON",
    13: "START_ERR",
    14: "LED_PRESENT",
    15: "E_POTI",
    16: "BUCK",
}
# Bits 17+ are multi-bit fields (ISRC, OP_MODE, AUTH); hardware shows OP_MODE in
# bits 20-23 but the rest is unverified, so they are not decoded. Use get_op_mode().


class UpLedError(Exception):
    """Device returned a nonzero error code (or an unparsable reply)."""

    def __init__(self, code: int, message: str | None = None) -> None:
        self.code: int = code
        self.message: str = message or ERRORS.get(code, "unknown error")
        super().__init__(f"upLED error {code}: {self.message}")


class UpLedTimeout(UpLedError):
    """No reply line arrived within the timeout."""

    def __init__(self, cmd: str) -> None:
        super().__init__(-1, f"timeout waiting for reply to {cmd!r}")


class CurrentSource(enum.IntEnum):
    POTENTIOMETER = 0
    NVMEM = 1
    REMOTE = 2


_SRC_LETTER: str = "PNU"  # index = CurrentSource value


@dataclass(frozen=True)
class LedInfo:
    name: str
    serial: str
    current_limit: float  # A
    forward_voltage: float  # V
    wavelength: float  # nm; negative = white LED, abs value is colour temp in K


@dataclass(frozen=True)
class DeviceInfo:
    manufacturer: str
    device: str
    serial: str
    firmware: str


def _unquote(s: str) -> str:
    """Text between the first two '"' (as the DLL does), else the stripped value."""
    parts: list[str] = s.split('"')
    return parts[1] if len(parts) >= 3 else s.strip()


def _to_int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        raise UpLedError(27, f"invalid value {s!r}") from None


def _to_float(s: str) -> float:
    try:
        x: float = float(s)
    except ValueError:
        raise UpLedError(27, f"invalid value {s!r}") from None
    # Same sentinel mapping as the vendor DLL.
    if x >= 9.909e37:
        return math.nan
    if x >= 9.899e37:
        return math.inf
    if x <= -9.899e37:
        return -math.inf
    return x


def _amps(amps: float) -> str:
    if not math.isfinite(amps):
        raise ValueError(f"current must be finite, got {amps}")
    return "%f" % amps


class UpLed:
    """Thorlabs upLED LED driver. Units: A, V, nm, deg C."""

    def __init__(
        self,
        port: str | None = None,
        serial_number: str | None = None,
        timeout: float = 1.5,
        current_limit: float | None = None,
        current: float | None = None,
        non_thorlabs_led: bool | None = None,
    ) -> None:
        if port is None:
            found: list[tuple[str, str]] = self.find()
            if serial_number is not None:
                found = [f for f in found if f[1] == serial_number]
            if len(found) != 1:
                raise LookupError(
                    f"expected exactly one upLED (serial={serial_number!r}), "
                    f"found {found}",
                )
            port = found[0][0]
        self.port: str = port
        self._lock: threading.Lock = threading.Lock()
        self._ser: serial.Serial | None = serial.Serial(
            port, baudrate=115200, bytesize=8, parity="N", stopbits=1,
            timeout=timeout, write_timeout=timeout, exclusive=True,
        )
        try:
            if non_thorlabs_led is not None:
                self.set_non_thorlabs_led(non_thorlabs_led)
            if current_limit is not None:
                self.set_current_limit(current_limit)
            if current is not None:
                self.set_current(current)
        except BaseException:
            self.disconnect()
            raise

    @staticmethod
    def find() -> list[tuple[str, str]]:
        """Return (port, serial_number) for every attached upLED."""
        return [
            (p.device, p.serial_number or "")
            for p in list_ports.comports()
            if p.vid == VID and p.pid == PID
        ]

    # --- transport -------------------------------------------------------

    def _exchange(self, cmd: str) -> str:
        with self._lock:
            if self._ser is None:
                raise RuntimeError("upLED not connected")
            self._ser.reset_input_buffer()
            self._ser.write(cmd.encode("ascii") + b"\n")
            line: bytes = self._ser.readline()
            if not line.endswith(b"\n"):
                # Drop a late reply so it cannot pair with the next command.
                self._ser.reset_input_buffer()
                raise UpLedTimeout(cmd)
        return line.decode("ascii", errors="replace").strip()

    def write(self, cmd: str) -> None:
        """Send a set command and check its ack (leading int, like scanf "%d")."""
        reply: str = self._exchange(cmd)
        match: re.Match[str] | None = re.match(r"\s*([+-]?\d+)", reply)
        if match is None:
            raise UpLedError(26, f"invalid ack to {cmd!r}: {reply!r}")
        code: int = int(match.group(1))
        if code > 0:
            raise UpLedError(code)

    def query(self, cmd: str) -> str:
        """Send a query; return the value part of '<code>,<value>'."""
        reply: str = self._exchange(cmd)
        head, sep, value = reply.partition(",")
        try:
            code: int = int(head)
        except ValueError:
            raise UpLedError(26, f"invalid reply to {cmd!r}: {reply!r}") from None
        if not sep:
            raise UpLedError(code if code > 0 else 26)
        if code > 0:
            raise UpLedError(code)
        return value.strip()

    def _qf(self, cmd: str) -> float:
        return _to_float(self.query(cmd))

    def _qyn(self, cmd: str) -> bool:
        value: str = _unquote(self.query(cmd)).upper()
        if value not in ("Y", "N"):
            raise UpLedError(27, f"expected Y/N, got {value!r}")
        return value == "Y"

    def disconnect(self) -> None:
        with self._lock:
            if self._ser is not None:
                self._ser.close()
                self._ser = None

    def __enter__(self) -> UpLed:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.disconnect()

    # --- output ----------------------------------------------------------

    def set_output(self, enabled: bool) -> None:
        self.write(f"LED{int(bool(enabled))}")

    def get_output(self) -> bool:
        return _to_int(self.query("LED?")) != 0

    def on(self) -> None:
        self.set_output(True)

    def off(self) -> None:
        self.set_output(False)

    # --- currents (A) ----------------------------------------------------

    def set_current(self, amps: float) -> None:
        """Set the LED current. Works whatever the current source; the device then
        reports op mode 9 ("LED ON Interface")."""
        self.write("ILED" + _amps(amps))

    def get_current(self) -> float:
        """Active setpoint; the device reports 0 while the output is off."""
        return self._qf("ILED?")

    def get_current_min(self) -> float:
        return self._qf("ILEDI?")

    def get_current_max(self) -> float:
        return self._qf("ILEDA?")

    def set_current_limit(self, amps: float) -> None:
        self.write("ILEDLIM" + _amps(amps))

    def get_current_limit(self) -> float:
        return self._qf("ILEDLIM?")

    def get_current_limit_max(self) -> float:
        return self._qf("ILEDLIMA?")

    def set_startup_current(self, amps: float) -> None:
        self.write("ILEDSTARTUP" + _amps(amps))

    def get_startup_current(self) -> float:
        return self._qf("ILEDSTARTUP?")

    # --- configuration ---------------------------------------------------

    def set_current_source(self, source: CurrentSource | int) -> None:
        self.write("CONFISET" + _SRC_LETTER[CurrentSource(source)])

    def get_current_source(self) -> CurrentSource:
        letter: str = _unquote(self.query("CONFISET?"))[:1].upper()
        if not letter or letter not in _SRC_LETTER:
            raise UpLedError(27, f"unknown current source {letter!r}")
        return CurrentSource(_SRC_LETTER.index(letter))

    def set_switch_on_at_startup(self, enabled: bool) -> None:
        self.write("CONFSTARTON" + ("Y" if enabled else "N"))

    def get_switch_on_at_startup(self) -> bool:
        return self._qyn("CONFSTARTON?")

    def set_switch_off_at_disconnect(self, enabled: bool) -> None:
        self.write("CONFDISCOFF" + ("Y" if enabled else "N"))

    def get_switch_off_at_disconnect(self) -> bool:
        return self._qyn("CONFDISCOFF?")

    def set_non_thorlabs_led(self, enabled: bool) -> None:
        self.write("CONFNONTHORLED" + ("Y" if enabled else "N"))

    def get_non_thorlabs_led(self) -> bool:
        return self._qyn("CONFNONTHORLED?")

    def save_to_nvmem(self, group: int = 0) -> None:
        """0 = all, 1 = LED operating mode, 2 = LED currents."""
        cmds: dict[int, str] = {0: "NVSAVA", 1: "NVSAVO", 2: "NVSAVI"}
        if group not in cmds:
            raise ValueError(f"group must be 0, 1 or 2, got {group}")
        self.write(cmds[group])

    # --- info ------------------------------------------------------------

    def led_info(self) -> LedInfo:
        return LedInfo(
            name=_unquote(self.query("LEDINFON?")),
            serial=_unquote(self.query("LEDINFOS?")),
            current_limit=self._qf("LEDINFOL?"),
            forward_voltage=self._qf("LEDINFOU?"),
            wavelength=self._qf("LEDINFOW?"),
        )

    def identify(self) -> DeviceInfo:
        q: list[str] = [
            _unquote(self.query(c)) for c in ("INFOM?", "INFOD?", "INFOS?", "INFOR?")
        ]
        return DeviceInfo(*q)

    def get_state(self) -> frozenset[str]:
        """Decode STATE? bits 0-16 into flag names (TLUP.h numbering)."""
        mask: int = _to_int(self.query("STATE?"))
        return frozenset(name for b, name in STATE_BITS.items() if mask >> b & 1)

    def get_op_mode(self) -> tuple[int, str]:
        """Operating mode, e.g. (7, "LED OFF VccOK") or (9, "LED ON Interface")."""
        num, _, text = self.query("OM?").partition(" ")
        return _to_int(num), " ".join(text.split())

    # --- measurements ----------------------------------------------------

    def measure_current(self) -> float:
        return self._qf("MC?")

    def measure_voltage(self) -> float:
        return self._qf("MV?")

    def measure_potentiometer(self) -> float:
        return self._qf("MP?")

    def measure_supply_voltage(self) -> float:
        return self._qf("MS?")

    def measure_temperature(self) -> float:
        """Device temperature in deg C (verified on hardware: no K offset)."""
        return self._qf("MI?")
