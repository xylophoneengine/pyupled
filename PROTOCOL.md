# upLED wire protocol

Reverse-engineered from the vendor DLL (TLUP_64.dll) and TLUP.h. Verified on an upLED (fw 1.3.5, LED head MCWHL8) on 2026-10-07; see "Hardware-verified".

## Transport

- USB CDC-ACM virtual COM port, VID `0x1313`, PID `0x8103` (composite device, COM port on interface 1). Serial number = USB iSerialNumber.
- 8N1, no flow control. Baud is irrelevant for CDC-ACM; 115200 used. DLL does not touch DTR/RTS.
- Timeout 1.5 s (DLL value for upLED).
- No handshake on open, no reset command, no `*IDN?`, no device error queue.

## Framing

- Command: ASCII, terminated by a single `\n` (no `\r`).
- Reply: one line terminated by `\n`, max 256 bytes.
- **Set command** -> ack line `<code>`. `0` (or <= 0) = OK, otherwise error code.
- **Query** (`...?`) -> `<code>,<value>`. If only `<code>` comes back, it is an error.
- Floats are sent as C `%f` (6 decimals). Replies `>= 9.909e37` = NaN, `>= 9.899e37` = +Inf, `<= -9.899e37` = -Inf.

## Commands (LED)

| Command | Reply value | Meaning |
|---|---|---|
| `LED0` / `LED1` | ack | output off / on (can fail with 112, 113, 114) |
| `LED?` | int 0/1 | output state |
| `ILED<f>` / `ILED?` | ack / float | current setpoint (A); `ILED?` reads 0 while output off |
| `ILEDI?` `ILEDA?` `ILEDD?` | float | setpoint min / max / default |
| `ILEDLIM<f>` / `ILEDLIM?` (+`I`,`A`,`D`) | ack / float | user current limit |
| `ILEDSTARTUP<f>` / `ILEDSTARTUP?` (+`I`,`A`,`D`) | ack / float | startup current |
| `CONFISETP` / `N` / `U`, `CONFISET?` | ack / letter | setpoint source: potentiometer / NVMEM / remote (USB) |
| `CONFSTARTONY` / `N`, `CONFSTARTON?` | ack / Y,N | switch on at power-up |
| `CONFDISCOFFY` / `N`, `CONFDISCOFF?` | ack / Y,N | switch off on USB disconnect |
| `CONFNONTHORLEDY` / `N`, `CONFNONTHORLED?` | ack / Y,N | allow non-Thorlabs LED |
| `NVSAVA` / `NVSAVO` / `NVSAVI` | ack | save all / LED op mode / LED currents to NVMEM |
| `LEDINFON?` `LEDINFOS?` | string | LED head name, serial |
| `LEDINFOL?` `LEDINFOU?` `LEDINFOW?` | float | LED current limit (A), forward voltage (V), wavelength (nm; negative = white LED colour temp in K) |
| `INFOM?` `INFOD?` `INFOS?` `INFOR?` | `"text"` | manufacturer, device, serial, firmware (quoted) |
| `MC?` `MV?` `MP?` `MS?` | float | measured LED current, LED voltage, potentiometer, supply voltage |
| `MI?` | float | device temperature, deg C (DLL K-conversion does not apply) |
| `STATE?` | uint32 | status bitmask (bits below) |
| `OM?` | `<n> <text>` | operating mode |
| `LOG?` | string | log message |

STATE bits (TLUP.h `TLUP_OPERATION_MODE_*`): 0 RUN, 1 ENA, 2 OWB_DET, 3 OWB_OK, 4 VCC_ERR, 5 E_VCC_ERR, 6 OTP, 7 E_OTP, 8 LED_ERR, 9 OPEN, 10 NON_THOR_LED, 11 RECON, 12 PON, 13 START_ERR, 14 LED_PRESENT, 15 E_POTI, 16 BUCK. Bits 17+ hold multi-bit fields (ISRC, OP_MODE, AUTH). Observed: `0x76402C` with LED off (op mode 7), `0x99400F` with LED on (op mode 9), so OP_MODE = bits 20-23; ISRC/AUTH positions unverified. Use `OM?` instead.

## Error codes

| Code | Meaning |
|---|---|
| 1 | not supported by this upSERIES type |
| 2 | error parsing a parameter |
| 3-10 | parameter 1-8 out of range |
| 11 | unexpected end of command |
| 12 / 13 | only / not allowed in service mode |
| 16-18 | HAL error / busy / timeout |
| 19 | string conversion out of range |
| 20 | USB library error |
| 21 / 22 | not authenticated / authentication failed |
| 25 | invalid attribute |
| 26 / 27 | invalid response / invalid parameter response |
| 30-38 | temperature errors (upTEMP) |
| 40-42 | NVMEM boot / empty / checksum |
| 50 / 51 | DAC / ADC out of range |
| 60-62 | buffer overflow / underflow / command buffer overflow |
| 65 | only allowed in idle mode |
| 70-78 | flash / data-set errors |
| 80 / 90 / 100 | range / adjust / setup value error |
| 111 | 1-wire timeout |
| 112 | no LED detected |
| 113 | switch ON cancelled, over-temperature |
| 114 | no 12 V supply, needed for LED ON |

## Hardware-verified (fw 1.3.5)

- pyserial `list_ports`: vid `0x1313`, pid `0x8103`, serial_number = device serial (`M01105010`), product `upLED`; macOS port `/dev/cu.usbmodem<serial>2`.
- Replies: `INFOM?` -> `0,"THORLABS"`, `INFOR?` -> `0,"1.3.5"`, `LEDINFON?` -> `0,"MCWHL8"` (quoted), `CONFISET?` -> `0,P` (bare letter), `LED?` -> `0,0`, `ILEDA?` -> `0,1.200000e+00`, `OM?` -> `0,7 LED OFF    VccOK`.
- Ack `0`; unknown command -> bare `1`; `ILED2.0` -> `3` (param 1 out of range); `ILED0.005` (below `ILEDI?` = 0.01) is acked.
- Units: A and V. `MI?` = 22.5 at room temp, i.e. deg C.
- `ILED<f>` drives the LED even with `CONFISET?` = `P`; op mode becomes `9 LED ON Interface`. `CONFISETU` is acked but `CONFISET?` still reads `P` (probably needs `NVSAV`).
- `MC?` reads ~10 % low at 10-20 mA (8.87 mA at a 10 mA setpoint).

## Open questions
- Whether commands other than `LED1` work without 12 V supply.
- `MP?` unit (A or fraction).
