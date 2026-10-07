"""Hardware-free tests: python3 -m unittest -v test_thorlabs_up_led"""

from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from typing import cast
from unittest import mock

import thorlabs_up_led as m


class FakeSerial:
    """Records writes; readline() pops scripted replies (b'' = timeout)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.writes: list[bytes] = []
        self.replies: list[bytes] = []
        self.closed: bool = False
        self.resets: int = 0

    def reset_input_buffer(self) -> None:
        self.resets += 1

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def readline(self) -> bytes:
        return self.replies.pop(0) if self.replies else b""

    def close(self) -> None:
        self.closed = True


def make(*replies: bytes) -> tuple[m.UpLed, FakeSerial]:
    with mock.patch.object(m.serial, "Serial", FakeSerial):
        led: m.UpLed = m.UpLed("FAKE")
    fake: FakeSerial = cast(FakeSerial, led._ser)
    fake.replies.extend(replies)
    return led, fake


class TestUpLed(unittest.TestCase):
    def test_write_framing_and_ack(self) -> None:
        led, fake = make(b"0\n", b"0\n", b"0\n")
        led.set_current(0.1)
        led.on()
        led.set_current_source(m.CurrentSource.REMOTE)
        self.assertEqual(fake.writes, [b"ILED0.100000\n", b"LED1\n", b"CONFISETU\n"])
        self.assertEqual(fake.resets, 3)

    def test_ack_error(self) -> None:
        led, _ = make(b"112\n")
        with self.assertRaises(m.UpLedError) as cm:
            led.on()
        self.assertEqual(cm.exception.code, 112)
        self.assertIn("no LED detected", str(cm.exception))

    def test_query_float_and_errors(self) -> None:
        led, fake = make(b"0,0.250000\n", b"3\n", b"0,9.91e37\n")
        self.assertAlmostEqual(led.get_current_limit(), 0.25)
        self.assertEqual(fake.writes[-1], b"ILEDLIM?\n")
        with self.assertRaises(m.UpLedError) as cm:
            led.get_current()
        self.assertEqual(cm.exception.code, 3)
        self.assertTrue(math.isnan(led.measure_current()))

    def test_query_bool_conf_source(self) -> None:
        led, _ = make(b"0,1\n", b"0,Y\n", b"0,N\n", b"0,P\n", b"0,N\n")
        self.assertTrue(led.get_output())
        self.assertTrue(led.get_switch_on_at_startup())
        self.assertFalse(led.get_non_thorlabs_led())
        self.assertIs(led.get_current_source(), m.CurrentSource.POTENTIOMETER)
        self.assertIs(led.get_current_source(), m.CurrentSource.NVMEM)

    def test_identify_strips_quotes(self) -> None:
        led, _ = make(b'0,"Thorlabs"\n', b'0,"upLED"\n', b'0,"M001"\n', b'0,"1.0.0"\n')
        self.assertEqual(
            led.identify(), m.DeviceInfo("Thorlabs", "upLED", "M001", "1.0.0")
        )

    def test_temperature_is_celsius(self) -> None:
        led, fake = make(b"0,2.254559e+01\n")  # real device reply at room temp
        self.assertAlmostEqual(led.measure_temperature(), 22.54559)
        self.assertEqual(fake.writes, [b"MI?\n"])

    def test_state_decode(self) -> None:
        led, _ = make(b"0,10043407\n")  # real device reply, LED on
        self.assertEqual(
            led.get_state(), {"RUN", "ENA", "OWB_DET", "OWB_OK", "LED_PRESENT", "BUCK"}
        )

    def test_op_mode(self) -> None:
        led, _ = make(b"0,7 LED OFF    VccOK\n")  # real device reply
        self.assertEqual(led.get_op_mode(), (7, "LED OFF VccOK"))

    def test_timeout(self) -> None:
        led, _ = make()
        with self.assertRaises(m.UpLedTimeout):
            led.get_output()

    def test_find_filters(self) -> None:
        ns = SimpleNamespace
        ports: list[SimpleNamespace] = [
            ns(device="/dev/a", vid=0x1313, pid=0x8103, serial_number="S1"),
            ns(device="/dev/b", vid=0x1313, pid=0x8101, serial_number="S2"),
            ns(device="/dev/c", vid=None, pid=None, serial_number=None),
        ]
        with mock.patch.object(m.list_ports, "comports", return_value=ports):
            self.assertEqual(m.UpLed.find(), [("/dev/a", "S1")])

    def test_ack_leading_int(self) -> None:
        led, _ = make(b"0,ok\n", b"112,x\n")
        led.on()
        with self.assertRaises(m.UpLedError) as cm:
            led.off()
        self.assertEqual(cm.exception.code, 112)

    def test_yn_strict_and_bad_value(self) -> None:
        led, _ = make(b"0,1\n", b"0,abc\n")
        with self.assertRaises(m.UpLedError):
            led.get_switch_on_at_startup()
        with self.assertRaises(m.UpLedError):
            led.measure_current()

    def test_identify_first_quoted_field(self) -> None:
        self.assertEqual(m._unquote('"Thorlabs" extra "x"'), "Thorlabs")
        self.assertEqual(m._unquote(" 1.0 "), "1.0")

    def test_nonfinite_current_rejected(self) -> None:
        led, fake = make()
        with self.assertRaises(ValueError):
            led.set_current(math.nan)
        self.assertEqual(fake.writes, [])

    def test_init_failure_closes_port(self) -> None:
        holder: list[FakeSerial] = []

        class Scripted(FakeSerial):
            def __init__(self, *a: object, **k: object) -> None:
                super().__init__()
                self.replies = [b"112\n"]
                holder.append(self)

        with mock.patch.object(m.serial, "Serial", Scripted):
            with self.assertRaises(m.UpLedError):
                m.UpLed("FAKE", current=0.1)
        self.assertTrue(holder[0].closed)

    def test_autodiscovery(self) -> None:
        ns = SimpleNamespace
        two: list[SimpleNamespace] = [
            ns(device="/dev/a", vid=0x1313, pid=0x8103, serial_number="S1"),
            ns(device="/dev/b", vid=0x1313, pid=0x8103, serial_number="S2"),
        ]
        with mock.patch.object(m.serial, "Serial", FakeSerial):
            with mock.patch.object(m.list_ports, "comports", return_value=two):
                self.assertEqual(m.UpLed(serial_number="S2").port, "/dev/b")
                with self.assertRaises(LookupError):
                    m.UpLed()
            with mock.patch.object(m.list_ports, "comports", return_value=[]):
                with self.assertRaises(LookupError):
                    m.UpLed()

    def test_context_manager_closes(self) -> None:
        with mock.patch.object(m.serial, "Serial", FakeSerial):
            with m.UpLed("FAKE") as led:
                fake: FakeSerial = cast(FakeSerial, led._ser)
        self.assertTrue(fake.closed)
        led.disconnect()  # idempotent

    def test_init_kwargs_applied(self) -> None:
        class Scripted(FakeSerial):
            def __init__(self, *a: object, **k: object) -> None:
                super().__init__()
                self.replies = [b"0\n", b"0\n", b"0\n"]

        with mock.patch.object(m.serial, "Serial", Scripted):
            led: m.UpLed = m.UpLed(
                "FAKE", current_limit=0.5, current=0.1, non_thorlabs_led=True
            )
        fake: FakeSerial = cast(FakeSerial, led._ser)
        self.assertEqual(
            fake.writes,
            [b"CONFNONTHORLEDY\n", b"ILEDLIM0.500000\n", b"ILED0.100000\n"],
        )


if __name__ == "__main__":
    unittest.main()
