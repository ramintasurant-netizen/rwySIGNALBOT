# PID-BFW-100 — P&ID Boiler Feedwater System (PLTU 1×300 MW)

Diagram P&ID **Boiler Feedwater System** (batas: Deaerator → inlet Economizer) untuk PLTU 1×300 MW dengan
boiler drum subkritis. Tag instrumen mengikuti **ISA-5.1**, nomor loop seri 100.

## File

| File | Isi |
|---|---|
| `PID-BFW-100_RevA.vsdx` | **Gambar Visio native** (A1 landscape, 1 halaman). Buka langsung di Microsoft Visio 2013+ / Visio Plan 2. Semua simbol adalah shape yang bisa diedit; layer: Equipment, Process Piping, Recirc-Bypass, Valves, Instruments, Signal Lines, Annotation. Peralatan & instrumen membawa *Shape Data* (Tag, Type, Duty). |
| `PID-BFW-100_RevA.svg` / `.png` | Pratinjau (SVG juga bisa di-*import* ke Visio: Insert → Pictures). |
| `PID-BFW-100_RevA_InstrumentIndex.csv` | Indeks instrumen (51 tag): tipe, service, lokasi, range/setpoint, I/O, alarm & interlock. |
| `PID-BFW-100_RevA_LineList.csv` | Line list (23 jalur): from/to, ukuran, material/kelas, fluida, kondisi operasi. |
| `PID-BFW-100_RevA_ValveList.csv` | Daftar katup (24 tag): tipe, ukuran, fail position, service. |
| `make_pid.py`, `pidlib.py`, `pid_emit.py` | Generator (Python 3, tanpa dependensi). `python3 make_pid.py` membuat ulang semua file di atas. |

## Struktur diagram (zona kiri → kanan)

**A – Deaerator & storage**: V-101A deaerating head (spray-tray) di atas V-101B storage tank (7 barg/165 °C, storage 10 min).
Kondensat masuk lewat LV-101 (FC), pegging steam lewat PV-101 (FC), vent orifice RO-101, PSV-101 set 9,5 barg.
Instrumen: LT-101 A/B/C (2oo3) → LIC-101 & LSLL-101 (SIS, I-1); PT-101 → PIC-101; TT-101.

**B – Booster / BFP 3×50 %** (2 jalan + 1 standby): per train MOV-101 (suction), strainer SR-103 dengan PDT-103,
P-100 booster + P-101 BFP barrel multistage (motor + variable-speed coupling SC-110), FE/FT-109 orifice, tap min-flow ke
FV-109 (FO, angle multistage) lewat FIC-109, check valve non-slam CV-108, MOV-108 discharge.
Proteksi mesin PT-102, TT-104, VT-105, PT-106 (I-2). Header 16"-FW-110 dengan PT-110 → PDIC-110 → SC-110 A/B/C
(ΔP header–drum ≈ 10 bar). Tap-off 4"/3" ke spray SH/RH. Recirc 6" per pompa → header 8"-FW-109 kembali ke V-101B.

**C – HP heater**: MOV-131 → E-101 (HP heater #6) → E-102 (HP heater #7) → MOV-132; bypass grup 16"-FW-130 dengan
MOV-130 (NC, fast-acting). Ekstraksi via XV-106/107; drain cascade LV-115A → E-101 dan LV-114A → V-101B.
TT-111/112/113, LT-114/115 (2oo3, LSHH → I-3).

**D – FW control station**: FE/FT-116 (flow nozzle, kompensasi PT-116) → FIC-116 three-element (LIC-117 + FT-118 feedforward)
→ FCV-116 14" (FO) dan jalur start-up 6"-FW-119 dengan FCV-119 (< 30 % beban). PT-120, TT-120, AT-121 di batas boiler.

**E – Boiler (referensi)**: Economizer inlet header, steam drum (LT-117 A/B/C, PT-117, FT-118; I-5 → MFT).

## Logika kontrol & interlock (ringkas — lengkap di catatan pada gambar)

| Kode | Fungsi |
|---|---|
| C1 | Drum level 3-element: LIC-117 + FT-118 (FF) + FT-116 → FIC-116 → FCV-116; < 30 % beban otomatis 1-element via FCV-119 (bumpless). |
| C2 | PDIC-110 menjaga ΔP header–drum ≈ 10 bar → SC-110 (kecepatan BFP); standby speed-follow. |
| C3 | FIC-109x membuka FV-109x < 25 % BEP, menutup > 35 % (histeresis). |
| C4 | PIC-101 → PV-101 (pegging 7 barg); LIC-101 → LV-101, split-range ke overflow saat LAH. |
| C5 | LIC-114/115 → LV-114A/115A drain normal; LAH → LV-B emergency drain ke kondensor. |
| I-1 | LSLL-101 DA < 15 % → trip semua BFP; PSLL-102x < 4 barg (3 s) → trip BFP x. |
| I-2 | Proteksi mesin: PSLL-106 lube oil, TSHH-104 > 90 °C, VSHH-105 > 11 mm/s, FSLL-109 < 20 % & FV-109 tidak terbuka dalam 10 s, TSHH-107 winding → trip motor. |
| I-3 | LSHH-114/115 (tube leak) → tutup XV-106/107, buka MOV-130, tutup MOV-131/132 (< 10 s), buka LV-B. |
| I-4 | Trip pompa running / PAL PT-110 → auto-start standby dengan permissive (MOV-101 ZSO, MOV-108 ZSC, FV-109 ZSO, PSH-106, ΔT casing < 40 °C, LT-101 > LAL). |
| I-5 | LSLL/LSHH-117 drum (2oo3) → Master Fuel Trip; FCV hold; turbine trip → FIC-116 SP minimum, PDIC-110 manual-hold. |

Rev. A — *Issued for Review*; ukuran pipa & setpoint adalah estimasi desain awal, bukan untuk konstruksi.

## Versi sederhana — `PID-STM-001_Simple.vsdx`

P&ID satu halaman A3, gaya klasik hitam‑putih, siklus uap PLTU lengkap: **Economizer → Steam Drum → Evaporator → Superheater**
→ Main Steam (PSV → Silencer) → MSV/GV (Turbine Trip / Governor) → Turbin → Generator → Kondensor (Cooling Tower + CW Pump)
→ Condensate Pump → Deaerator → BFP → Economizer. Kontrol: LIC‑01 → FCV‑01 (level drum), TIC‑01 → TCV‑01 (spray attemperator).
Dibangun ulang dengan `python3 make_pid_simple.py` (juga menghasilkan `.svg`).
