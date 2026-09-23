#!/usr/bin/env python3
"""P&ID PID-BFW-100 — Boiler Feedwater System, PLTU 1x300 MW (subcritical drum boiler).

Run:  python3 make_pid.py   ->  PID-BFW-100_RevA.vsdx / .svg / *_InstrumentIndex.csv / *_LineList.csv / *_ValveList.csv
Page = A1 landscape, inches, origin bottom-left.
"""
import os

from pidlib import *  # noqa: F401,F403
from pid_emit import write_vsdx, write_svg, write_csvs

OUT = os.path.dirname(os.path.abspath(__file__))
NAME = "PID-BFW-100_RevA"
PAGE_W, PAGE_H = 33.11, 23.39

# ---------------------------------------------------------------- frame & title
Shape(0.4, 0.4, PAGE_W - 0.8, PAGE_H - 0.8, "Annotation", weight=2.0, name="frame").rect(0, 0, PAGE_W - 0.8, PAGE_H - 0.8, nofill=True)
Shape(0.55, 0.55, PAGE_W - 1.1, PAGE_H - 1.1, "Annotation", weight=0.6, name="frame2").rect(0, 0, PAGE_W - 1.1, PAGE_H - 1.1, nofill=True)
text(PAGE_W / 2, PAGE_H - 0.95, "P&ID  BOILER FEEDWATER SYSTEM  (DEAERATOR  →  ECONOMIZER INLET)   —   PLTU 1×300 MW, SUBCRITICAL DRUM BOILER",
     size=13, bold=True)
for zx, zt in [(4.4, "ZONA A — DEAERATOR & STORAGE"), (12.7, "ZONA B — BOOSTER / BOILER FEED PUMPS (3×50 %)"),
               (21.7, "ZONA C — HP FEEDWATER HEATERS"), (27.9, "ZONA D — FW CONTROL STATION"), (31.4, "ZONA E — BOILER")]:
    text(zx, PAGE_H - 1.3, zt, size=7, bold=True, color="#555555")

# ---------------------------------------------------------------- ZONE A : Deaerator
vessel_v(4.0, 19.05, 1.6, 2.5, "V-101A\nDEAERATOR\n(spray-tray)\n7 barg / 165 °C",
         props={"Tag": "V-101A", "Type": "Spray-tray deaerating head", "Design": "9.5 barg / 200 °C"})
vessel_h(4.4, 16.7, 6.0, 2.2, "V-101B  FEEDWATER STORAGE TANK\n7 barg / 165 °C  —  storage 10 min @ MCR\nDesign 9.5 barg / 200 °C",
         props={"Tag": "V-101B", "Type": "Horizontal storage tank"})

offpage(0.95, 19.6, 0.7, 0.4, "1", "in")
line([(1.3, 19.6), (3.2, 19.6)], "process", label='16"-FW-001  KONDENSAT', lpos=(2.25, 19.95))
valve(1.9, 19.6, "LV-101", act="D", fo='FC 10"')
V("LV-101", "Globe control valve, diaphragm", '10"', "FC", "Deaerator level control (condensate inlet)")
L('16"-FW-001', "Condensate system", "V-101A", '16"', "A106 Gr B / 300#", "Condensate", "40–120 °C, 12 barg")
text(1.3, 20.35, "dari CONDENSATE SYSTEM\nRef. PID-CND-050", size=6, halign=0)

offpage(4.0, 22.55, 0.7, 0.4, "2", "in")
line([(4.0, 22.35), (4.0, 20.3)], "steam", label='8"-LS-010  PEGGING STEAM\n(Aux. / Extr. #4)', lpos=(5.05, 21.5), lalign=0)
valve(4.0, 21.3, "PV-101", act="D", fo='FC 8"', orient="v")
V("PV-101", "Globe control valve, diaphragm", '8"', "FC", "Deaerator pressure (pegging steam)")
L('8"-LS-010', "Aux steam header / Extr #4", "V-101A", '8"', "A106 Gr B / 300#", "LP steam", "8 barg / 250 °C")
line([(4.6, 20.3), (4.6, 20.9), (5.6, 20.9)], "steam", label="VENT ke atm\n(orifice RO-101)", lpos=(6.45, 20.9), lsize=6, lalign=0, weight=1.0)
line([(6.6, 17.8), (6.6, 18.35)], "thin", arrow=False)
valve(6.6, 18.5, "", act="none", w=0.28, h=0.18)
line([(6.74, 18.5), (7.3, 18.5)], "thin", arrow=True)
text(7.35, 18.85, "PSV-101\nset 9.5 barg", size=6, bold=True, halign=0)
V("PSV-101", "Spring-loaded safety relief valve", '6"×8"', "-", "Deaerator overpressure protection", "Set 9.5 barg, ASME VIII")

bubble(8.15, 17.0, "LT\n101"); leader(7.4, 17.0, 7.91, 17.0); text(8.15, 16.6, "A/B/C 2oo3", size=5.5)
bubble(8.15, 18.15, "LIC\n101", "dcs")
bubble(8.15, 19.3, "LSLL\n101", "sis"); flag(8.85, 19.3, "I-1")
line([(8.15, 17.24), (8.15, 17.91)], "signal", arrow=False)
line([(8.15, 18.39), (8.15, 19.06)], "signal", arrow=False)
line([(8.39, 19.3), (8.65, 19.3)], "signal", arrow=False)
line([(8.15, 19.54), (8.15, 21.75), (1.9, 21.75), (1.9, 20.05)], "signal")  # LIC-101 -> LV-101
I("LT-101A/B/C", "Level transmitter (dP), 2oo3", "Deaerator storage level", "V-101B side nozzles", "0–2500 mmWC", "AI ×3", "LAH/LAL/LALL alarm; LSLL trip via SIS")
I("LIC-101", "Level indicating controller (DCS)", "DA level", "DCS", "0–100 %", "AO → LV-101", "Split-range to overflow valve LV-101B at LAH")
I("LSLL-101", "Level switch low-low (SIS voting 2oo3)", "BFP protection", "SIS", "trip @ 15 %", "DO", "Interlock I-1")
bubble(2.35, 20.55, "PT\n101"); leader(3.2, 20.2, 2.59, 20.45)
bubble(2.35, 21.65, "PIC\n101", "dcs")
line([(2.35, 20.79), (2.35, 21.41)], "signal", arrow=False)
line([(2.59, 21.65), (3.35, 21.65), (3.35, 21.3), (3.55, 21.3)], "signal")
I("PT-101", "Pressure transmitter", "Deaerator pressure", "V-101A top", "0–16 barg", "AI", "PAH 8.5 / PAL 5.5 barg")
I("PIC-101", "Pressure indicating controller (DCS)", "DA pressure", "DCS", "0–16 barg", "AO → PV-101", "")
bubble(6.6, 19.35, "TT\n101"); leader(6.0, 17.8, 6.4, 19.13)
I("TT-101", "Temperature transmitter, RTD Pt100 + thermowell", "FW temperature at DA outlet", 'V-101B / 24"-FW-101', "0–250 °C", "AI", "TAL 155 °C (deaeration check)")

line([(4.4, 15.6), (4.4, 14.4), (9.0, 14.4)], "process", arrow=False,
     label='24"-FW-101  SUCTION HEADER  (FW 165 °C, ~10 barg incl. static head ≥ 20 m NPSHa)', lpos=(6.7, 14.65))
line([(9.0, 14.4), (9.0, 7.6)], "process", arrow=False)
L('24"-FW-101', "V-101B bottom", "Suction header", '24"', "A106 Gr B / 300#", "Feedwater", "165 °C, ~10 barg", "Velocity ≤ 1.5 m/s for NPSH")

# ---------------------------------------------------------------- ZONE B : pump trains
TRAINS = [("A", 14.4), ("B", 11.0), ("C", 7.6)]
HDR_X, TAP_X, RC_X = 16.7, 13.6, 8.3   # discharge header / min-flow tap / common recirc riser
for tr, y in TRAINS:
    line([(9.0, y), (HDR_X, y)], "process", arrow=False)
    text(9.4, y + 0.62, f'16"-FW-101{tr}', size=6, color=C_PROC)
    text(14.15, y + 0.3, f'10"-FW-108{tr}', size=6, color=C_PROC)
    valve(9.7, y, f"MOV-101{tr}", act="M", fo='FL 16"')
    V(f"MOV-101{tr}", "Gate valve, motor operated", '16"', "FL", f"Suction isolation train {tr}", "ZSO/ZSC limit switches")
    st = Shape(10.3, y - 0.17, 0.3, 0.34, "Equipment", weight=1.0, fill=F_WHITE, name=f"SR-103{tr}")
    st.rect(0, 0, 0.3, 0.34); st.poly([(0, 0.34), (0.3, 0)], nofill=True)
    text(10.45, y + 0.42, f"SR-103{tr}", size=5.5, bold=True)
    pump(11.2, y, 0.3, f"P-100{tr}\nBOOSTER", props={"Tag": f"P-100{tr}", "Type": "Single-stage centrifugal, low NPSH, 3×50 %", "Duty": "550 m³/h @ 12 bar"})
    pump(12.1, y, 0.4, f"P-101{tr}  BFP\nbarrel multistage\nMD + VSC SC-110{tr}",
         props={"Tag": f"P-101{tr}", "Type": "Barrel multistage centrifugal, 3×50 %", "Duty": "550 m³/h @ 195 barg, 165 °C"})
    fe = Shape(13.22, y - 0.16, 0.16, 0.32, "Instruments", weight=1.0, fill=F_WHITE, name=f"FE-109{tr}")
    fe.rect(0, 0, 0.16, 0.32)
    bubble(13.3, y + 0.85, f"FT\n109{tr}"); leader(13.3, y + 0.16, 13.3, y + 0.61)
    bubble(10.2, y + 0.85, f"PT\n102{tr}"); leader(10.2, y, 10.2, y + 0.61)
    bubble(10.35, y - 0.85, f"PDT\n103{tr}"); leader(10.45, y - 0.17, 10.35, y - 0.61)
    bubble(10.9, y - 0.85, f"TT\n104{tr}"); leader(11.2, y - 0.3, 10.9, y - 0.61)
    bubble(11.45, y - 0.85, f"VT\n105{tr}"); leader(12.1, y - 0.4, 11.45, y - 0.61)
    flag(11.95, y - 0.85, "I-2"); line([(11.69, y - 0.85), (11.75, y - 0.85)], "signal", arrow=False)
    bubble(12.55, y - 0.85, f"PT\n108{tr}"); leader(12.55, y - 0.02, 12.55, y - 0.61)
    valve(14.7, y, f"CV-108{tr}", act="C", fo='10"')
    valve(15.5, y, f"MOV-108{tr}", act="M", fo='FL 10"')
    V(f"CV-108{tr}", "Swing check, non-slam (dashpot)", '10"', "-", f"BFP {tr} discharge", "")
    V(f"MOV-108{tr}", "Gate valve, motor operated", '10"', "FL", f"BFP {tr} discharge isolation", "Opens after pump speed ≥ min")
    # min-flow: tap upstream of check valve, run back under the train to the common riser
    tee(TAP_X, y)
    line([(TAP_X, y), (TAP_X, y - 2.05), (RC_X, y - 2.05)], "recirc", arrow=False)
    valve(9.3, y - 2.05, f"FV-109{tr}", act="D", fo='FO 6"')
    V(f"FV-109{tr}", "Angle multistage pressure-reducing control valve", '6"', "FO", f"Min-flow recirculation BFP {tr}", "Anti-cavitation trim, ΔP ≈ 185 bar")
    bubble(10.75, y - 1.6, f"FIC\n109{tr}", "dcs")
    line([(13.06, y + 0.85), (13.0, y + 0.85), (13.0, y - 1.32), (10.75, y - 1.32), (10.75, y - 1.36)], "signal")
    line([(10.51, y - 1.6), (9.3, y - 1.6), (9.3, y - 1.63)], "signal")
    I(f"PT-102{tr}", "Pressure transmitter", f"Booster suction {tr}", f'16"-FW-101{tr}', "0–16 barg", "AI", "PSLL 4 barg → trip (I-1), 3 s delay")
    I(f"PDT-103{tr}", "Differential pressure transmitter", f"Suction strainer SR-103{tr} ΔP", "across strainer", "0–1 bar", "AI", "PDAH 0.3 bar")
    I(f"TT-104{tr}", "RTD bearing temperature (×4)", f"P-100/P-101{tr} bearings", "bearing housings", "0–150 °C", "AI", "TAH 80 / TSHH 90 °C trip (I-2)")
    I(f"VT-105{tr}", "Vibration transmitter (×2, radial)", f"P-101{tr} bearings", "bearing housings", "0–25 mm/s", "AI", "VAH 7.1 / VSHH 11 mm/s trip (I-2)")
    I(f"PT-106{tr}", "Lube oil pressure transmitter", f"P-101{tr} lube oil", "lube oil skid", "0–6 barg", "AI", "PSLL 1.0 barg trip (I-2); PSH permissive")
    I(f"PT-108{tr}", "Pressure transmitter", f"BFP {tr} discharge", f'10"-FW-108{tr}', "0–250 barg", "AI", "PAL 170 barg (pump health)")
    I(f"FE/FT-109{tr}", "Orifice plate + dP transmitter", f"BFP {tr} discharge flow", f'10"-FW-108{tr}', "0–700 m³/h", "AI", "Min-flow control; FSLL 20 % trip if FV-109 not open (I-2)")
    I(f"FIC-109{tr}", "Flow indicating controller (DCS)", "Min-flow recirculation", "DCS", "0–700 m³/h", f"AO → FV-109{tr}", "Open <25 % BEP, close >35 % (hysteresis)")
    I(f"SC-110{tr}", "Speed controller (VSC hydraulic coupling)", f"BFP {tr} speed", "local / DCS", "0–100 %", "AO", "Setpoint from PDIC-110")
    L(f'16"-FW-101{tr}', "Suction header", f"P-100{tr}", '16"', "A106 Gr B / 300#", "Feedwater", "165 °C, ~10 barg")
    L(f'10"-FW-108{tr}', f"P-101{tr}", "Discharge header", '10"', "A335 P11 / 1500#", "Feedwater", "180 °C, ~195 barg")
    L(f'6"-FW-109{tr}', f"P-101{tr} discharge", "Recirc header", '6"', "A335 P11 / 1500#", "Feedwater", "min-flow 25–30 %")

ltext(14.0, 6.0, 'Warm-up 2"-FW-140 A/B/C\n(dari header via RO-140, ke suction pompa standby)\n– ditunjukkan skematik', size=5.5, color="#555555")
line([(HDR_X, 7.6), (HDR_X, 6.4), (15.6, 6.4)], "hidden", arrow=True, weight=0.7)
L('2"-FW-140 A/B/C', "Discharge header", "BFP suction (standby pump)", '2"', "A335 P11 / 1500#", "Feedwater", "warm-up flow via RO-140", "Keeps casing ΔT < 40 °C")

line([(RC_X, 14.4 - 2.05), (RC_X, 7.6 - 2.05), (1.0, 7.6 - 2.05), (1.0, 16.7), (1.4, 16.7)], "recirc", arrow=True,
     label='8"-FW-109  MIN-FLOW RECIRC RETURN  →  V-101B  (ke storage tank, spray nozzle bawah)', lpos=(4.6, 5.3))
L('8"-FW-109', "FV-109 A/B/C outlet", "V-101B", '8"', "A106 Gr C / 600#", "Feedwater (flashing)", "downstream of breakdown", "Restriction orifice RO-109 at tank nozzle")

line([(HDR_X, 7.6), (HDR_X, 18.0)], "process", arrow=False)
ltext(HDR_X + 0.1, 13.1, '16"-FW-110\nDISCHARGE\nHEADER\n~195 barg\n180 °C', size=6, color=C_PROC)
L('16"-FW-110', "Discharge header", "E-101 inlet", '16"', "A335 P11 / 1500#", "Feedwater", "180 °C, ~195 barg")
line([(HDR_X, 18.0), (19.4, 18.0)], "process", arrow=False, weight=1.4,
     label='4"-FW-122 → SH SPRAY (TCV-122)   /   3"-FW-123 → RH SPRAY (TCV-123)', lpos=(22.0, 18.28), lalign=0)
offpage(19.75, 18.0, 0.7, 0.4, "3", "out")
L('4"-FW-122 / 3"-FW-123', "Discharge header (intermediate stage tap)", "SH / RH attemperator", '4" / 3"', "A335 P11 / 1500#", "Feedwater", "180 °C", "Ref. PID-BLR-210")
bubble(17.55, 16.2, "PT\n110"); leader(HDR_X, 16.2, 17.31, 16.2)
bubble(17.55, 17.1, "PDIC\n110", "dcs")
line([(17.55, 16.44), (17.55, 16.86)], "signal", arrow=False)
bubble(12.1, 15.75, "SC\n110"); leader(12.1, 15.33, 12.1, 15.51); ltext(12.4, 15.75, "A/B/C", size=5.5)
line([(17.31, 17.1), (12.1, 17.1), (12.1, 15.99)], "signal")
I("PT-110", "Pressure transmitter", "Discharge header pressure", '16"-FW-110', "0–250 barg", "AI", "PAL 175 barg → auto-start standby (I-4)")
I("PDIC-110", "ΔP indicating controller header–drum (DCS)", "BFP speed control", "DCS", "0–30 bar", "AO → SC-110 A/B/C", "SP ≈ 10 bar so FCV-116 works 40–80 % open; input PT-117 (drum)")

# ---------------------------------------------------------------- ZONE C : HP heaters
Y = 11.6
line([(HDR_X, Y), (18.8, Y)], "process", arrow=False)
valve(18.0, Y, "MOV-131", act="M", fo='FL 16"')
exchanger(20.05, Y, 2.5, 1.4, "E-101  HP HEATER #6\nShell & tube (U-tube, horizontal) + drain cooler\nTube: FW  |  Shell: ekstraksi #6",
          props={"Tag": "E-101", "Type": "HP feedwater heater, U-tube horizontal"}, label_dy=2.75)
line([(21.3, Y), (22.1, Y)], "process", arrow=False)
exchanger(23.35, Y, 2.5, 1.4, "E-102  HP HEATER #7\nShell & tube (U-tube, horizontal) + drain cooler\nTube: FW  |  Shell: ekstraksi #7",
          props={"Tag": "E-102", "Type": "HP feedwater heater, U-tube horizontal"}, label_dy=2.75)
line([(24.6, Y), (26.1, Y)], "process", arrow=False)
valve(25.5, Y, "MOV-132", act="M", fo='FL 16"')
V("MOV-131", "Gate valve, motor operated", '16"', "FL", "HP heater group inlet isolation", "Fast-acting (< 10 s) on tube leak")
V("MOV-132", "Gate valve, motor operated", '16"', "FL", "HP heater group outlet isolation", "Fast-acting (< 10 s) on tube leak")
text(21.7, Y - 0.3, '16"-FW-111', size=6, color=C_PROC)
text(25.12, Y + 0.3, '16"-FW-113', size=5.5, color=C_PROC)
L('16"-FW-111', "E-101", "E-102", '16"', "A335 P11 / 1500#", "Feedwater", "215 °C")
L('16"-FW-113', "E-102", "FW control station", '16"', "A335 P11 / 1500#", "Feedwater", "250 °C, ~190 barg")
for cx, n, opc in [(20.05, "6", "4"), (23.35, "7", "5")]:
    line([(cx, 13.55), (cx, Y + 0.7)], "steam", label=f"EXTRACTION #{n}\n(XV-10{n} FC, NRV)", lpos=(cx + 0.95, 13.3), lsize=6, lalign=0)
    valve(cx, 12.85, f"XV-10{n}", act="D", orient="v", w=0.3, h=0.16)
    offpage(cx, 13.8, 0.7, 0.4, opc, "in")
    V(f"XV-10{n}", "Extraction steam quick-closing valve + NRV", '14"' if n == "6" else '12"', "FC", f"Extraction #{n} to E-10{int(n) - 5}", "Closes on heater LSHH / turbine trip")
line([(23.35, Y - 0.7), (23.35, 10.0), (20.6, 10.0), (20.6, Y - 0.7)], "process", arrow=True, weight=1.1,
     label='4"-HD-115 drain (cascade ke E-101)', lpos=(21.5, 10.22), lsize=5.5)
valve(22.6, 10.0, "LV-115A", act="D", fo='FC 4"', w=0.3, h=0.16)
line([(19.5, Y - 0.7), (19.5, 10.0), (17.85, 10.0)], "process", arrow=True, weight=1.1)
text(19.1, 9.78, '6"-HD-114 → V-101B', size=5.5, color=C_PROC)
valve(18.4, 10.0, "LV-114A", act="D", fo='FC 6"', w=0.3, h=0.16)
offpage(17.55, 10.0, 0.5, 0.36, "6", "out")
V("LV-114A", "Globe control valve, diaphragm", '6"', "FC", "E-101 normal drain to V-101B (cascade)", 'LV-114B emergency drain 6" FO → condenser (not shown)')
V("LV-115A", "Globe control valve, diaphragm", '4"', "FC", "E-102 normal drain to E-101 shell (cascade)", 'LV-115B emergency drain 4" FO → condenser (not shown)')
L('6"-HD-114', "E-101 shell", "V-101B", '6"', "A106 Gr B / 600#", "Heater drain (saturated)", "215 °C")
L('4"-HD-115', "E-102 shell", "E-101 shell", '4"', "A106 Gr B / 900#", "Heater drain (saturated)", "250 °C")
bubble(18.45, 12.5, "TT\n111"); leader(18.45, Y, 18.45, 12.26)
bubble(21.7, 12.5, "TT\n112"); leader(21.7, Y, 21.7, 12.26)
bubble(24.8, 12.5, "TT\n113"); leader(24.8, Y, 24.8, 12.26)
bubble(21.75, 10.7, "LT\n114"); leader(21.3, 10.95, 21.55, 10.85)
bubble(24.95, 10.7, "LT\n115"); leader(24.6, 10.95, 24.75, 10.85)
flag(22.25, 10.7, "I-3"); line([(21.99, 10.7), (22.05, 10.7)], "signal", arrow=False)
flag(25.45, 10.7, "I-3"); line([(25.19, 10.7), (25.25, 10.7)], "signal", arrow=False)
for tnum, svc, loc in [("111", "FW inlet E-101", '16"-FW-110'), ("112", "FW between heaters", '16"-FW-111'), ("113", "FW outlet E-102 (final FW temp)", '16"-FW-113')]:
    I(f"TT-{tnum}", "RTD Pt100 + thermowell", svc, loc, "0–300 °C", "AI", "TTD monitoring; TAL on TT-113 235 °C")
I("LT-114 A/B/C", "Level transmitter dP, 2oo3 (A/B/C)", "E-101 shell level", "E-101 shell", "0–800 mm", "AI ×3", "LIC-114 → LV-114A; LAH → LV-114B; LSHH → I-3")
I("LT-115 A/B/C", "Level transmitter dP, 2oo3 (A/B/C)", "E-102 shell level", "E-102 shell", "0–800 mm", "AI ×3", "LIC-115 → LV-115A; LAH → LV-115B; LSHH → I-3")

tee(17.1, Y); tee(26.1, Y)
line([(17.1, Y), (17.1, 9.4), (26.1, 9.4), (26.1, Y)], "bypass", arrow=False,
     label='16"-FW-130  HP HEATER GROUP BYPASS  (MOV-130 normally closed, opens on I-3)', lpos=(21.7, 8.72))
valve(21.7, 9.4, "MOV-130", act="M", fo='FL 16"  NC')
V("MOV-130", "Gate valve, motor operated, fast-acting", '16"', "FL (NC)", "HP heater group bypass", "Opens < 10 s on I-3")
L('16"-FW-130', "Upstream MOV-131", "Downstream MOV-132", '16"', "A335 P11 / 1500#", "Feedwater", "180 °C", "Bypass, normally no flow")

# ---------------------------------------------------------------- ZONE D : FW control station
fe = Shape(26.42, Y - 0.2, 0.16, 0.4, "Instruments", weight=1.0, fill=F_WHITE, name="FE-116"); fe.rect(0, 0, 0.16, 0.4)
text(26.5, Y + 0.4, "FE-116\nflow nozzle", size=5.5)
bubble(26.5, 10.7, "FT\n116"); leader(26.5, Y - 0.2, 26.5, 10.94)
bubble(25.95, 10.7, "PT\n116"); leader(25.95, Y, 25.95, 10.94)
line([(26.1, Y), (29.9, Y)], "process", arrow=True)
tee(27.0, Y); tee(29.3, Y)
valve(27.9, Y, "FCV-116", act="D", fo='FO 14"  (100 %)', w=0.4, h=0.22)
V("FCV-116", "Globe control valve, cage-guided, equal-%, pneumatic diaphragm + positioner", '14"', "FO", "Main feedwater control (3-element)", "Cv sized 110 % MCR; hold-last on MFT")
line([(27.0, Y), (27.0, 14.0), (29.3, 14.0), (29.3, Y)], "bypass", arrow=False)
rtext(26.85, 14.15, '6"-FW-119\nSTART-UP LINE\n(< 30 % beban)', size=6, color=C_BYPASS)
valve(27.9, 14.0, "FCV-119", act="D", fo='FO 6"  (30 %)', w=0.36, h=0.2)
V("FCV-119", "Globe control valve, anti-cavitation multistage, pneumatic", '6"', "FO", "Start-up / low-load feedwater control (1-element)", "Bumpless transfer ↔ FCV-116 at 30 % steam flow")
L('6"-FW-119', "Upstream FCV-116", "Downstream FCV-116", '6"', "A335 P11 / 1500#", "Feedwater", "250 °C", "Start-up bypass")
L('16"-FW-116', "FW control station", "Economizer inlet", '16"', "A335 P11 / 1500#", "Feedwater", "250 °C, ~185 barg")
text(29.6, Y + 0.32, '16"-FW-116  250 °C', size=6, color=C_PROC)
bubble(28.7, 12.9, "FIC\n116", "dcs")
line([(26.74, 10.7), (27.3, 10.7), (27.3, 12.75), (28.46, 12.75)], "signal")     # FT-116 -> FIC-116
line([(28.7, 12.66), (28.7, 12.3), (27.9, 12.3), (27.9, 12.05)], "signal")        # FIC-116 -> FCV-116
line([(28.94, 12.9), (29.7, 12.9), (29.7, 14.6), (27.9, 14.6), (27.9, 14.45)], "signal")  # FIC-116 -> FCV-119
I("FE/FT-116", "Flow nozzle + dP transmitter, P/T compensated (PT-116, TT-113)", "Total feedwater flow", '16"-FW-113', "0–1200 t/h", "AI", "3rd element of drum level control")
I("PT-116", "Pressure transmitter", "FW pressure at control station (compensation)", '16"-FW-113', "0–250 barg", "AI", "")
I("FIC-116", "Flow indicating controller / 3-element drum level (DCS)", "Main FW control", "DCS", "0–1200 t/h", "AO → FCV-116 / FCV-119", "Cascade from LIC-117 + FF FT-118; auto 1-element < 30 %")
I("ZT-116 / ZT-119", "Valve position transmitters", "FCV-116 / FCV-119 stem position", "valve actuators", "0–100 %", "AI", "Deviation alarm 5 %")
bubble(28.6, 10.7, "PT\n120"); leader(28.6, Y, 28.6, 10.94)
bubble(29.2, 10.7, "TT\n120"); leader(29.2, Y, 29.2, 10.94)
bubble(29.8, 10.7, "AT\n121"); leader(29.8, Y, 29.8, 10.94)
I("PT-120", "Pressure transmitter", "Economizer inlet pressure", '16"-FW-116', "0–250 barg", "AI", "PAL 175 barg")
I("TT-120", "RTD Pt100 + thermowell", "Economizer inlet temperature", '16"-FW-116', "0–300 °C", "AI", "")
I("AT-121", "Analyser panel: pH, DO, cation conductivity, Na", "FW chemistry at ECO inlet", '16"-FW-116 sample', "pH 9.2–9.6; DO < 7 ppb", "AI ×4", "AAH DO 10 ppb")

# ---------------------------------------------------------------- ZONE E : boiler boundary
box(31.4, Y, 2.4, 1.1, "ECONOMIZER INLET\nHEADER\n~185 barg / 250 °C\nRef. PID-BLR-200", fill=F_WHITE, pattern=2, size=6.5)
offpage(30.5, Y + 0.85, 0.6, 0.36, "7", "out")
box(31.4, 15.7, 2.4, 1.4, "STEAM DRUM  (referensi)\nLT-117 A/B/C 2oo3, PT-117\nFT-118 main steam flow\nRef. PID-BLR-200", fill=F_WHITE, pattern=2, size=6.5)
bubble(29.55, 15.7, "LT\n117"); leader(30.2, 15.7, 29.79, 15.7); text(29.55, 15.28, "A/B/C 2oo3", size=5.5)
bubble(28.7, 15.7, "LIC\n117", "dcs")
line([(29.31, 15.7), (28.94, 15.7)], "signal", arrow=False)
line([(28.7, 15.46), (28.7, 13.14)], "signal")                                    # LIC-117 -> FIC-116
bubble(30.9, 17.15, "FT\n118"); leader(30.9, 16.4, 30.9, 16.91)
line([(30.9, 17.39), (30.9, 17.9), (28.27, 17.9), (28.27, 13.05), (28.46, 13.05)], "signal")  # FT-118 feedforward
bubble(31.7, 17.15, "PT\n117"); leader(31.7, 16.4, 31.7, 16.91)
line([(31.7, 17.39), (31.7, 18.5), (17.55, 18.5), (17.55, 17.34)], "signal")     # PT-117 -> PDIC-110
flag(29.55, 14.75, "I-5"); line([(29.55, 15.46), (29.55, 14.95)], "signal", arrow=False)
I("LT-117 A/B/C", "Drum level dP transmitters 2oo3, P-compensated", "Steam drum level", "Steam drum", "±300 mm", "AI ×3", "LSLL/LSHH → MFT (I-5)")
I("LIC-117", "Drum level controller (DCS), master of 3-element", "Drum level", "DCS", "±300 mm", "SP → FIC-116", "")
I("FT-118", "Main steam flow (nozzle), P/T compensated", "Feedforward", "Main steam line", "0–1100 t/h", "AI", "")
I("PT-117", "Drum pressure transmitter", "PDIC-110 reference", "Steam drum", "0–250 barg", "AI", "")

# ---------------------------------------------------------------- legend, notes, title block
box(4.9, 2.75, 8.6, 3.9, "", fill=F_WHITE, weight=1.0)
text(4.9, 4.5, "LEGENDA SIMBOL (ISA-5.1)", size=8, bold=True)
bubble(1.2, 3.85, "PT\n101"); ltext(1.6, 3.85, "Field-mounted instrument", size=6.5)
bubble(1.2, 3.15, "FIC\n116", "dcs"); ltext(1.6, 3.15, "Shared display/control (DCS),\nprimary location", size=6.5)
bubble(1.2, 2.45, "LSLL\n101", "sis"); ltext(1.6, 2.45, "Safety/interlock logic (SIS / BMS)", size=6.5)
flag(1.2, 1.75, "I-n"); ltext(1.6, 1.75, "Interlock flag → lihat tabel interlock", size=6.5)
ltext(0.95, 1.15, "Huruf ISA-5.1: pertama = variabel (P,T,L,F,A,V,Z,S)  •  berikutnya = fungsi (T transmitter, I indicator, C controller, V valve, S switch, LL/HH trip)",
      size=5.5, w=7.9)
valve(5.2, 3.85, "", act="none"); ltext(5.55, 3.85, "Gate / globe valve (manual)", size=6.5)
valve(5.2, 3.15, "", act="M"); ltext(5.55, 3.15, "Motor-operated valve (MOV)", size=6.5)
valve(5.2, 2.45, "", act="D"); ltext(5.55, 2.45, "Control valve, diaphragm actuator\n(FO/FC = fail position)", size=6.5)
valve(5.2, 1.75, "", act="C"); ltext(5.55, 1.75, "Check valve (arah aliran)", size=6.5)
for yy, kind, lbl in [(4.05, "process", "Process (FW)"), (3.7, "steam", "Steam / drain"), (3.35, "recirc", "Recirc"),
                      (3.0, "bypass", "Bypass"), (2.65, "signal", "Sinyal elektrik"), (2.3, "software", "Software link")]:
    line([(7.7, yy), (8.5, yy)], kind, arrow=True); ltext(8.55, yy, lbl, size=5.5)

NOTES = (
    "LOGIKA KONTROL\n"
    "C1  Drum level 3-element: LIC-117 (level) + FT-118 (steam, feedforward) + FT-116 (FW, feedback) → FIC-116 → FCV-116. Beban < 30 %: otomatis 1-element via FCV-119 (bumpless, berdasarkan FT-118).\n"
    "C2  Kecepatan BFP: PDIC-110 menjaga ΔP header–drum (PT-110 − PT-117) ≈ 10 bar → SC-110 A/B/C; pompa standby speed-follow.\n"
    "C3  Min-flow: FIC-109x membuka FV-109x penuh bila FT-109x < 25 % BEP, menutup bila > 35 % (histeresis anti-chatter). FV fail-open.\n"
    "C4  Deaerator: PIC-101 → PV-101 (pegging, SP 7 barg); LIC-101 → LV-101 (kondensat), split-range ke LV-101B overflow saat LAH.\n"
    "C5  HP heater: LIC-114/115 → LV-114A/115A drain normal (cascade); LAH → LV-114B/115B emergency drain ke kondensor.\n"
    "\nINTERLOCK / TRIP (SIS, 2oo3 untuk trip kritikal)\n"
    "I-1  LSLL-101 deaerator (< 15 %) → trip semua BFP; PSLL-102x suction < 4 barg (3 s) → trip BFP x.\n"
    "I-2  Proteksi mesin BFP x: PSLL-106 lube oil, TSHH-104 bearing > 90 °C, VSHH-105 > 11 mm/s, FSLL-109 < 20 % & FV-109 tidak ZSO dalam 10 s, TSHH-107 winding → trip motor.\n"
    "I-3  LSHH-114/115 shell HP heater (tube leak) → tutup XV-106/107 ekstraksi, buka MOV-130, tutup MOV-131 & MOV-132 (< 10 s), buka LV-B emergency drain.\n"
    "I-4  Trip pompa running atau PAL PT-110 < 175 barg → auto-start BFP standby (permissive: MOV-101 ZSO, MOV-108 ZSC, FV-109 ZSO, PSH-106 lube OK, ΔT casing < 40 °C, LT-101 > LAL); MOV-108 dibuka setelah speed ≥ min.\n"
    "I-5  LSLL-117 / LSHH-117 drum (2oo3) → Master Fuel Trip; FCV-116/119 hold posisi. Turbine trip → FIC-116 ke SP minimum, PDIC-110 manual-hold.\n"
    "\nCATATAN UMUM: 1) Tag per ISA-5.1, nomor loop seri 100 = BFW.  2) Ukuran pipa estimasi (v ≤ 1.5 m/s suction, ≤ 4.5 m/s discharge); material A335 P11 kelas 1500# untuk > 100 barg.  "
    "3) Semua MOV dilengkapi ZSO/ZSC.  4) Vent, drain, sampling & injeksi kimia (N2H4/NH3 di suction header) tidak digambar.  5) Rev.A – untuk review, bukan untuk konstruksi."
)
Shape(9.6, 0.8, 15.0, 3.9, "Annotation", text=NOTES, size=6.3, halign=0, valign=0, weight=1.0, fill=F_NOTE, name="notes").rect(0, 0, 15.0, 3.9)

box(28.85, 1.95, 7.7, 2.3, "", fill=F_WHITE, weight=1.4)
line([(25.0, 2.55), (32.7, 2.55)], "thin", arrow=False); line([(25.0, 1.75), (32.7, 1.75)], "thin", arrow=False)
line([(28.2, 0.8), (28.2, 2.55)], "thin", arrow=False); line([(30.6, 0.8), (30.6, 1.75)], "thin", arrow=False)
text(28.85, 2.85, "PLTU 1×300 MW  —  BOILER FEEDWATER SYSTEM (BFW)\nPIPING & INSTRUMENTATION DIAGRAM", size=8, bold=True)
text(26.6, 2.15, "Dwg. No.\nPID-BFW-100", size=7, bold=True)
text(29.4, 2.15, "Rev. A — Issued for Review", size=7)
text(31.65, 2.15, "Sheet 1 / 1", size=7)
text(26.6, 1.27, "Skala: NTS\nUkuran: A1", size=6.5)
text(29.4, 1.27, "Standar: ISA-5.1, ISO 10628\nDisiapkan: Senior P&I Engineer", size=6.5)
text(31.65, 1.27, "Tanggal\n2026-09-23", size=6.5)
ltext(25.0, 4.35, "OFF-PAGE:  1 Condensate (PID-CND-050)   2 Aux steam (PID-STM-030)   3 Attemperator (PID-BLR-210)\n"
                  "4/5 Extraction #6/#7 (PID-TRB-400)   6 Heater drain → V-101B   7 Economizer (PID-BLR-200)", size=5.5, w=7.6)

# ---------------------------------------------------------------- emit
if __name__ == "__main__":
    base = os.path.join(OUT, NAME)
    write_vsdx(base + ".vsdx", "PID-BFW-100", PAGE_W, PAGE_H, "PID-BFW-100 Boiler Feedwater System")
    write_svg(base + ".svg", PAGE_W, PAGE_H)
    write_csvs(base)
    print(f"shapes={len(shapes)} instruments={len(instr_index)} lines={len(line_list)} valves={len(valve_list)}")
