# thorlabs_up_led

Driver written with AI assistance (Claude).

Pure-Python, cross-platform driver for the Thorlabs upLED LED controller. Talks to the
device's USB virtual COM port directly; no Thorlabs DLL or driver needed. Only
dependency: pyserial.

## Install

```
pip install .
```

## Usage

```python
from thorlabs_up_led import UpLed

print(UpLed.find())             # [(port, serial_number), ...]

with UpLed() as led:            # auto-finds the single attached upLED
    led.set_current_limit(0.5)  # A
    led.set_current(0.1)        # A
    led.on()
    print(led.measure_current(), led.measure_voltage(), led.measure_temperature())
    led.off()

led = UpLed("/dev/ttyACM0")     # or UpLed(serial_number="M00...")
led.disconnect()
```

Device errors raise `UpLedError` (with `.code`); missing replies raise `UpLedTimeout`.
`set_current` overrides the front knob whatever `get_current_source()` says;
`get_current()` reads 0 while the output is off. No device found raises `LookupError`.
`led_info().wavelength` is negative for white LEDs (value = colour temperature in K).
Tested on upLED firmware 1.3.5 (macOS).
`query(cmd)` / `write(cmd)` send raw commands. See `PROTOCOL.md`.

Tests (no hardware): `python3 -m unittest -v test_thorlabs_up_led`

## Platform notes

- **Linux:** port is `/dev/ttyACM*`; add your user to the `dialout` group (`uucp` on Arch), then log in again.
- **macOS:** port is `/dev/cu.usbmodem*`.
- **Windows:** port is `COMx`; the built-in USB serial driver is enough, no Thorlabs software needed.

## License

MIT, see `LICENSE`. Not affiliated with or endorsed by Thorlabs; "Thorlabs" and
"upLED" are their trademarks. The protocol was worked out from the vendor's
Windows driver and checked against a real device (see `PROTOCOL.md`).
