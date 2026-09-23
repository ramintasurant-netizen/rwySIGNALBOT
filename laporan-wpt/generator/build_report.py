"""Build 'Laporan Praktikum WPT – Ketidakpastian Pengukuran' as .docx."""
import math, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from docx.shared import Pt, Cm, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT

from docx_helpers import *
from docx_helpers import _table_no_borders
from compute import DATA, stats, cyl_volume, box_volume

HERE = os.path.dirname(os.path.abspath(__file__))
IMG = RENDER = os.path.join(HERE, "assets")

STUDENT = "Putu Bagus Romi Pratama Putra"
NRP = "4225600068"
KELAS = "2 D4 SPE C"
DOSEN = "Hendrik Elvian GP, S.T., M.T."
NIP = "199112172019031014"
TAHUN_AJARAN = "2026/2027"
TAHUN = "2026"

# ----------------------------------------------------------------- number formatting
def f(x, d=2):
    """Indonesian decimal comma."""
    return f"{x:.{d}f}".replace(".", ",")

def c(x, d):
    """Comma decimal inside mathtext (braces avoid the thin space TeX adds after a comma)."""
    return f(x, d).replace(",", "{,}")

def sig_round(value, unc):
    """Aturan baku: unc to 1 significant figure (2 if it starts with 1); value to same decimals."""
    exp = math.floor(math.log10(abs(unc)))
    first = int(abs(unc) / 10**exp)
    sf = 2 if first == 1 else 1
    dec = max(0, -(exp - (sf - 1)))
    return f(round(value, dec), dec), f(round(unc, dec), dec)

def sci(value, unc, unit):
    """(a,bc ± 0,0d) × 10^n notation."""
    n = math.floor(math.log10(abs(value)))
    v, u = sig_round(value / 10**n, unc / 10**n)
    return f"({v} ± {u}) {unit}" if n == 0 else f"({v} ± {u}) × 10^{{{n}}} {unit}"

# ----------------------------------------------------------------- results
R = {k: {q: stats(v) for q, v in d.items()} for k, d in DATA.items()}
S = R["silinder"]; B = R["balok"]; P = R["plat"]
Vs, dVs, dVdD, dVdT = cyl_volume(S["Diameter"]["mean"], S["Tinggi"]["mean"], S["Diameter"]["S"], S["Tinggi"]["S"])
Vb, dVb = box_volume(B["Panjang"]["mean"], B["Lebar"]["mean"], B["Tinggi"]["mean"], B["Panjang"]["S"], B["Lebar"]["S"], B["Tinggi"]["S"])
Vp, dVp = box_volume(P["Panjang"]["mean"], P["Lebar"]["mean"], P["Tinggi"]["mean"], P["Panjang"]["S"], P["Lebar"]["S"], P["Tinggi"]["S"])
cs = {"D": dVdD * S["Diameter"]["S"], "T": dVdT * S["Tinggi"]["S"]}
cb = {"P": B["Lebar"]["mean"] * B["Tinggi"]["mean"] * B["Panjang"]["S"],
      "L": B["Panjang"]["mean"] * B["Tinggi"]["mean"] * B["Lebar"]["S"],
      "T": B["Panjang"]["mean"] * B["Lebar"]["mean"] * B["Tinggi"]["S"]}
cp = {"P": P["Lebar"]["mean"] * P["Tinggi"]["mean"] * P["Panjang"]["S"],
      "L": P["Panjang"]["mean"] * P["Tinggi"]["mean"] * P["Lebar"]["S"],
      "T": P["Panjang"]["mean"] * P["Lebar"]["mean"] * P["Tinggi"]["S"]}
lin_b = sum(cb.values()); lin_p = sum(cp.values())

Dv, Du = sig_round(S["Diameter"]["mean"], S["Diameter"]["S"]); Tv, Tu = sig_round(S["Tinggi"]["mean"], S["Tinggi"]["S"])
Vsv, Vsu = sig_round(Vs, dVs)
Pv, Pu = sig_round(B["Panjang"]["mean"], B["Panjang"]["S"]); Lv, Lu = sig_round(B["Lebar"]["mean"], B["Lebar"]["S"]); Tbv, Tbu = sig_round(B["Tinggi"]["mean"], B["Tinggi"]["S"])
Vbv, Vbu = sig_round(Vb, dVb)
Ppv, Ppu = sig_round(P["Panjang"]["mean"], P["Panjang"]["S"]); Lpv, Lpu = sig_round(P["Lebar"]["mean"], P["Lebar"]["S"]); Tpv, Tpu = sig_round(P["Tinggi"]["mean"], P["Tinggi"]["S"])
Vpv, Vpu = sig_round(Vp, dVp)

# ----------------------------------------------------------------- document
doc = new_document()
add_page_number_footer(doc.sections[0])

# ================================================================= COVER
p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(6)
set_run_font(p.add_run("LAPORAN PRAKTIKUM"), 11, name="Calibri")
for txt, sz in (("WORKSHOP PENGUKURAN TEKNIK", 14), (f"TAHUN AJARAN {TAHUN_AJARAN}", 14), ("MODUL 2 : KETIDAKPASTIAN PENGUKURAN", 12)):
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_after = Pt(0)
    set_run_font(p.add_run(txt), sz, bold=True)
for _ in range(3): spacer(doc, 6)
picture(doc, os.path.join(RENDER, "cover_logo.jpeg"), 7.6, space_after=0)
for _ in range(3): spacer(doc, 6)
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_after = Pt(2)
set_run_font(p.add_run("Dosen Pengajar :"), 14, bold=True)

def two_col(doc, left, right):
    t = doc.add_table(rows=1, cols=2); t.alignment = WD_TABLE_ALIGNMENT.CENTER; t.autofit = False
    for i, (cell, w) in enumerate(zip(t.rows[0].cells, (Cm(8.0), Cm(7.9)))): cell.width = w; t.columns[i].width = w
    a, b = t.rows[0].cells
    pa = a.paragraphs[0]; pa.alignment = WD_ALIGN_PARAGRAPH.LEFT; pa.paragraph_format.space_after = Pt(0); pa.paragraph_format.left_indent = Cm(1.0)
    set_run_font(pa.add_run(left), 12, bold=True)
    pb = b.paragraphs[0]; pb.alignment = WD_ALIGN_PARAGRAPH.RIGHT; pb.paragraph_format.space_after = Pt(0); pb.paragraph_format.right_indent = Cm(1.0)
    set_run_font(pb.add_run(right), 12, bold=True)
    _table_no_borders(t)

two_col(doc, DOSEN, f"NIP. {NIP}")
spacer(doc, 6); spacer(doc, 6)
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_after = Pt(2)
set_run_font(p.add_run(f"Nama Mahasiswa ({KELAS}):"), 12, bold=True)
two_col(doc, STUDENT, f"NRP. {NRP}")
spacer(doc, 6)
for line in ("PROGRAM STUDI SISTEM PEMBANGKIT ENERGI", "DEPARTEMEN MEKATRONIKA DAN ENERGI",
             "POLITEKNIK ELEKTRONIKA NEGERI SURABAYA", "SURABAYA", TAHUN):
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_after = Pt(0)
    set_run_font(p.add_run(line), 12, bold=True)

# ================================================================= ABSTRAK
heading1(doc, "ABSTRAK")
para(doc, "Setiap pengukuran besaran fisis selalu disertai ketidakpastian, sehingga hasil pengukuran harus dilaporkan "
     "bersama nilai ketidakpastiannya. Praktikum ini bertujuan menentukan dimensi tiga benda uji, yaitu silinder, balok, "
     "dan plat, beserta ketidakpastiannya, kemudian menghitung volume setiap benda melalui perambatan ketidakpastian. "
     "Pengukuran dilakukan secara berulang sebanyak lima kali pada posisi yang berbeda menggunakan jangka sorong dan "
     "mistar. Nilai terbaik diambil dari rata-rata sampel, ketidakpastian pengukuran langsung dihitung dengan deviasi "
     "standar, dan ketidakpastian volume dihitung dengan metode turunan parsial (akar kuadrat jumlah kuadrat). "
     f"Hasil pengukuran silinder adalah D = ({Dv} ± {Du}) cm, T = ({Tv} ± {Tu}) cm, dan V = ({Vsv} ± {Vsu}) cm^{{3}}; "
     f"balok P = ({Pv} ± {Pu}) cm, L = ({Lv} ± {Lu}) cm, T = ({Tbv} ± {Tbu}) cm, dan V = ({Vbv} ± {Vbu}) cm^{{3}}; "
     f"plat P = ({Ppv} ± {Ppu}) cm, L = ({Lpv} ± {Lpu}) cm, T = ({Tpv} ± {Tpu}) cm, dan V = ({Vpv} ± {Vpu}) cm^{{3}}. "
     f"Ketidakpastian relatif volume terkecil diperoleh pada silinder ({f(dVs/Vs*100,1)} %) yang diukur dengan jangka sorong, "
     f"sedangkan yang terbesar pada plat ({f(dVp/Vp*100,1)} %) karena didominasi ketidakpastian tebal plat yang nilainya "
     "sangat kecil terhadap skala alat. Hasil ini menunjukkan bahwa ketelitian alat ukur (NST), keteraturan bentuk benda, "
     "dan kesalahan paralaks sangat menentukan besar ketidakpastian pengukuran.")
p = para(doc, "", align="left", indent_first=False)
add_rich(p, "**Kata kunci:** ketidakpastian pengukuran, pengukuran berulang, deviasi standar, perambatan ketidakpastian, volume.")

# ================================================================= BAB I
heading1(doc, "BAB I\nPENDAHULUAN")
heading2(doc, "1.1 Latar Belakang")
para(doc, "Pengukuran merupakan kegiatan dasar dalam ilmu pengetahuan dan rekayasa. Hubungan antar besaran fisis, "
     "kinerja suatu peralatan, hingga spesifikasi komponen pembangkit energi hanya dapat diketahui melalui pengukuran "
     "yang tepat dan cermat. Namun, sebaik apa pun pengukuran dilakukan, hasilnya tidak pernah bebas dari ketidakpastian. "
     "Ketidakpastian dapat bersumber dari kesalahan kalibrasi alat ukur, fluktuasi parameter yang diukur, kesalahan "
     "paralaks, kesalahan titik nol, keterbatasan nilai skala terkecil (NST) alat, kondisi lingkungan, serta tingkat "
     "keterampilan pengamat yang berbeda-beda.")
para(doc, "Oleh karena itu, seorang praktikan dituntut tidak hanya mampu membaca alat ukur dengan benar, tetapi juga mampu "
     "mengolah data pengukuran secara statistik dan melaporkan hasilnya dalam bentuk nilai terbaik beserta "
     "ketidakpastiannya, yaitu x = x̄ ± Δx. Pada besaran yang diperoleh secara tak langsung, misalnya volume yang "
     "dihitung dari panjang, lebar, dan tinggi, ketidakpastian setiap besaran masukan akan merambat ke hasil akhir, "
     "sehingga diperlukan metode perambatan ketidakpastian.")
para(doc, "Praktikum Workshop Pengukuran Teknik Modul 2 ini melatih kemampuan tersebut melalui pengukuran berulang dimensi "
     "silinder, balok, dan plat menggunakan jangka sorong dan mistar, dilanjutkan dengan perhitungan rata-rata, deviasi "
     "standar, dan volume beserta ketidakpastiannya. Dengan demikian, mahasiswa memahami bagaimana kualitas alat ukur "
     "dan cara pengukuran memengaruhi ketelitian hasil.")

heading2(doc, "1.2 Permasalahan")
para(doc, "Berdasarkan latar belakang di atas, permasalahan yang dibahas dalam praktikum ini adalah:")
numbered(doc, [
    "Bagaimana menentukan nilai terbaik dan ketidakpastian dari hasil pengukuran berulang dimensi silinder, balok, dan plat?",
    "Bagaimana menghitung volume silinder, balok, dan plat beserta ketidakpastiannya dari besaran yang diukur secara tak langsung berulang?",
    "Bagaimana menuliskan hasil pengukuran dalam aturan baku (x = x̄ ± Δx) dan menafsirkan tingkat ketelitiannya?",
    "Faktor apa saja yang memengaruhi besar ketidakpastian pada setiap benda uji dan alat ukur yang digunakan?",
])

heading2(doc, "1.3 Tujuan")
para(doc, "Tujuan yang ingin dicapai dari praktikum ini adalah:")
numbered(doc, [
    "Mahasiswa mampu menggunakan jangka sorong dan mistar untuk melakukan pengukuran berulang dengan benar.",
    "Mahasiswa mampu menghitung rata-rata dan ketidakpastian (deviasi standar) dari data pengukuran berulang.",
    "Mahasiswa mampu menghitung volume silinder, balok, dan plat beserta ketidakpastiannya menggunakan metode perambatan ketidakpastian.",
    "Mahasiswa mampu melaporkan hasil pengukuran dalam aturan baku serta menganalisis sumber-sumber ketidakpastian pengukuran.",
])

# ================================================================= BAB II
heading1(doc, "BAB II\nDASAR TEORI")
heading2(doc, "2.1 Pengukuran, Alat Ukur, dan Nilai Skala Terkecil")
para(doc, "Pengukuran adalah kegiatan membandingkan suatu besaran fisis dengan besaran sejenis yang ditetapkan sebagai "
     "satuan. Alat ukur adalah perangkat yang digunakan untuk menentukan nilai suatu besaran fisis. Berdasarkan cara "
     "penyajian hasilnya, alat ukur dibedakan menjadi alat ukur analog yang menghasilkan nilai kontinu (misalnya jarum "
     "penunjuk pada amperemeter) dan alat ukur digital yang menampilkan nilai diskrit dalam sejumlah digit tertentu.")
para(doc, "Setiap alat ukur memiliki keterbatasan kemampuan ukur yang disebut nilai skala terkecil (NST) atau *least count*, "
     "yaitu nilai skala terkecil yang tidak dapat dibagi-bagi lagi. Ketelitian alat ukur bergantung pada NST-nya. "
     "Mistar pada Gambar 2.1, misalnya, memiliki NST 1 mm (0,1 cm). Untuk meningkatkan ketelitian, alat ukur seperti "
     "jangka sorong dilengkapi skala nonius: sejumlah skala utama dibagi dengan sejumlah skala nonius sehingga garis "
     "nonius yang berimpit dengan skala utama menunjukkan pecahan skala utama. Jangka sorong yang digunakan pada "
     "praktikum ini memiliki NST 0,01 cm (0,1 mm), sedangkan mikrometer sekrup dapat mencapai NST 0,001 cm.")
picture(doc, os.path.join(RENDER, "modul_mistar.png"), 12.5)
caption(doc, "Gambar 2.1 Skala pada mistar dengan NST 1 mm (Sumber: Modul Praktikum 2 WPT)")

heading2(doc, "2.2 Ketidakpastian Pengukuran")
para(doc, "Ketidakpastian menyatakan bahwa hasil suatu pengukuran tidak dapat ditentukan secara tepat; yang dapat "
     "dilaporkan adalah nilai terbaik beserta rentang penyimpangannya. Ketidakpastian dibedakan menjadi ketidakpastian "
     "mutlak (Δx, bersatuan sama dengan besaran yang diukur) dan ketidakpastian relatif (Δx/x̄, biasanya dinyatakan dalam "
     "persen). Keduanya dapat diterapkan pada pengukuran tunggal maupun pengukuran berulang.")
heading3(doc, "a. Ketidakpastian pada pengukuran tunggal")
para(doc, "Pengukuran tunggal adalah pengukuran yang hanya dilakukan sekali. Ketidakpastiannya ditetapkan sebesar "
     "setengah nilai skala terkecil alat, sehingga hasil pengukuran dituliskan sebagai:")
equation(doc, r"\Delta x = \frac{1}{2}\,\mathrm{NST}, \qquad x = x_0 \pm \Delta x", 1)
para(doc, "Sebagai contoh, mistar dengan NST 1 mm memberikan Δx = 0,5 mm; hasil pembacaan 2,6 cm dilaporkan sebagai "
     "(2,60 ± 0,05) cm, artinya nilai sebenarnya berada di antara 2,55 cm dan 2,65 cm.")
heading3(doc, "b. Ketidakpastian pada pengukuran berulang")
para(doc, "Agar hasil percobaan lebih akurat, pengukuran diulang beberapa kali. Menurut statistika dasar, nilai terbaik "
     "dari n kali pengukuran x_{1}, x_{2}, …, x_{n} adalah rata-rata sampel:")
equation(doc, r"\bar{x} = \frac{x_1 + x_2 + x_3 + \cdots + x_n}{n} = \frac{1}{n}\sum_{i=1}^{n} x_i", 2)
para(doc, "Ketidakpastiannya dinyatakan oleh deviasi standar. Modul praktikum memberikan dua bentuk yang dapat digunakan, "
     "yaitu deviasi standar rata-rata:")
equation(doc, r"S_{\bar{x}} = \frac{1}{n}\sqrt{\frac{n\sum x_i^{2} - \left(\sum x_i\right)^{2}}{n-1}}", 3)
para(doc, "atau deviasi standar sampel:", indent_first=False)
equation(doc, r"S = \sqrt{\frac{\sum_{i=1}^{n}\left(x_i - \bar{x}\right)^{2}}{n-1}}", 4)
para(doc, "Deviasi standar inilah yang digunakan sebagai Δx pada pengukuran berulang. Dalam laporan ini digunakan "
     "Persamaan (4), sesuai dengan pengolahan data pada lembar praktikum. Hasil pengukuran kemudian ditulis dalam "
     "aturan baku, yaitu ketidakpastian dibulatkan ke satu angka penting (dua angka penting bila angka pertamanya 1) "
     "dan nilai terbaik dibulatkan pada posisi desimal yang sama, misalnya x = (10,45 ± 0,06) cm = (1,045 ± 0,006) × 10^{1} cm. "
     "Ketidakpastian relatif (KR) dihitung dengan:")
equation(doc, r"\mathrm{KR} = \frac{\Delta x}{\bar{x}} \times 100\,\%", 5)

heading2(doc, "2.3 Pengukuran Tak Langsung dan Perambatan Ketidakpastian")
para(doc, "Pengukuran langsung menghasilkan besaran yang diinginkan secara langsung dari alat ukur, misalnya panjang "
     "dengan jangka sorong. Pengukuran tak langsung menghasilkan besaran melalui persamaan matematis dari besaran lain "
     "yang diukur, misalnya volume balok dari panjang, lebar, dan tinggi. Dengan demikian pengukuran dapat dikelompokkan "
     "menjadi pengukuran langsung-tunggal, langsung-berulang, tak langsung-tunggal, dan tak langsung-berulang.")
para(doc, "Jika besaran C dihitung dari besaran A dan B yang masing-masing diukur berulang, dengan A = Ā ± ΔA dan "
     "B = B̄ ± ΔB, maka nilai terbaik C adalah C̄ = C(Ā, B̄), dan ketidakpastiannya diperoleh dari turunan parsial fungsi "
     "C terhadap tiap variabel. Karena ΔA dan ΔB berasal dari sebaran acak yang saling bebas, ketidakpastian C dijumlahkan "
     "secara kuadrat (akar kuadrat dari jumlah kuadrat):")
equation(doc, r"\Delta C = \sqrt{\left(\frac{\partial C}{\partial A}\right)^{2}\left(\Delta A\right)^{2} + \left(\frac{\partial C}{\partial B}\right)^{2}\left(\Delta B\right)^{2}}", 6)
para(doc, "Bentuk penjumlahan linier ΔC = |∂C/∂A|ΔA + |∂C/∂B|ΔB seperti yang tertulis pada modul merupakan batas atas "
     "(kasus terburuk) dari Persamaan (6). Untuk balok dan plat dengan volume V = P·L·T, Persamaan (6) memberikan:")
equation(doc, r"\bar{V} = \bar{P}\,\bar{L}\,\bar{T}, \qquad \Delta V = \sqrt{\left(\bar{L}\,\bar{T}\,\Delta P\right)^{2} + \left(\bar{P}\,\bar{T}\,\Delta L\right)^{2} + \left(\bar{P}\,\bar{L}\,\Delta T\right)^{2}}", 7)
para(doc, "Untuk silinder dengan diameter D dan tinggi T, volumenya V = ¼πD²T, dengan ∂V/∂D = ½πDT dan ∂V/∂T = ¼πD², "
     "sehingga:", indent_first=False)
equation(doc, r"\bar{V} = \frac{1}{4}\pi\,\bar{D}^{2}\,\bar{T}, \qquad \Delta V = \sqrt{\left(\frac{1}{2}\pi\,\bar{D}\,\bar{T}\,\Delta D\right)^{2} + \left(\frac{1}{4}\pi\,\bar{D}^{2}\,\Delta T\right)^{2}}", 8)
para(doc, "Hasil akhir pengukuran tak langsung kemudian dituliskan sebagai V = V̄ ± ΔV. Apabila massa benda m diketahui, "
     "massa jenis ρ = m/V beserta ketidakpastiannya dapat dihitung dengan cara yang sama dan dicocokkan dengan tabel "
     "massa jenis bahan untuk menentukan jenis bahan benda.")

# ================================================================= BAB III
heading1(doc, "BAB III\nMETODOLOGI PERCOBAAN")
heading2(doc, "3.1 Alat dan Bahan")
para(doc, "Praktikum dilakukan di Laboratorium Program Studi Sistem Pembangkit Energi PENS. Tiga benda uji dengan bentuk "
     "geometri berbeda diukur dimensinya secara berulang. Alat dan bahan yang digunakan beserta fungsinya dirangkum pada "
     "Tabel 3.1. Pemilihan alat disesuaikan dengan ukuran benda dan ketelitian yang dibutuhkan: besaran yang kecil atau "
     "memerlukan ketelitian tinggi (diameter dan tinggi silinder, tebal plat) diukur dengan jangka sorong, sedangkan "
     "besaran yang relatif besar (dimensi balok serta panjang dan lebar plat) diukur dengan mistar.", keep_with_next=True)
data_table(doc, ["No", "Alat / Bahan", "Spesifikasi", "Jumlah", "Fungsi"], [
    ["1", "Jangka sorong", "NST 0,01 cm", "1 buah", "Mengukur diameter dan tinggi silinder serta tebal plat"],
    ["2", "Mistar (penggaris)", "NST 0,1 cm", "1 buah", "Mengukur panjang, lebar, dan tinggi balok serta panjang dan lebar plat"],
    ["3", "Silinder logam (stainless steel)", "Benda uji 1", "1 buah", "Objek pengukuran diameter dan tinggi"],
    ["4", "Balok (kotak nama)", "Benda uji 2", "1 buah", "Objek pengukuran panjang, lebar, dan tinggi"],
    ["5", "Plat (PCB)", "Benda uji 3", "1 buah", "Objek pengukuran panjang, lebar, dan tebal"],
    ["6", "Alat tulis dan lembar data", "–", "1 set", "Mencatat hasil pengukuran"],
], [1.0, 3.9, 2.4, 1.6, 7.0], size=10.5)
caption(doc, "Tabel 3.1 Alat dan bahan praktikum")

heading2(doc, "3.2 Flowchart Percobaan")
picture(doc, os.path.join(IMG, "flowchart.png"), 11.6)
caption(doc, "Gambar 3.1 Flowchart percobaan ketidakpastian pengukuran")

heading2(doc, "3.3 Penjelasan Flowchart")
para(doc, "Langkah-langkah percobaan yang digambarkan pada Gambar 3.1 dijelaskan sebagai berikut:")
numbered(doc, [
    "**Mulai.** Praktikum dimulai setelah praktikan memahami modul dan tujuan percobaan.",
    "**Menyiapkan alat dan bahan.** Jangka sorong, mistar, dan tiga benda uji (silinder, balok, plat) disiapkan di meja kerja beserta lembar data.",
    "**Memeriksa titik nol dan menentukan NST.** Rahang jangka sorong dirapatkan untuk memastikan skala nonius menunjuk nol (tidak ada kesalahan titik nol), lalu NST setiap alat ditentukan: jangka sorong 0,01 cm dan mistar 0,1 cm.",
    "**Mengukur dimensi benda uji.** Diameter dan tinggi silinder diukur dengan jangka sorong; panjang, lebar, dan tinggi balok diukur dengan mistar; panjang dan lebar plat diukur dengan mistar sedangkan tebal plat diukur dengan jangka sorong. Setiap pengulangan dilakukan pada posisi yang berbeda agar ketidakseragaman bentuk benda ikut terwakili. Pembacaan dilakukan dengan mata tegak lurus terhadap skala untuk menghindari paralaks.",
    "**Mencatat hasil pengukuran** pada tabel data dengan satuan cm dan jumlah desimal sesuai NST alat.",
    "**Pengukuran sudah 5 kali?** Jika belum, kembali ke langkah pengukuran pada posisi lain; jika sudah, lanjut ke pengecekan benda berikutnya.",
    "**Semua benda uji selesai diukur?** Jika belum, benda uji diganti dan langkah pengukuran diulang; jika sudah, dilanjutkan ke pengolahan data.",
    "**Menghitung rata-rata dan deviasi standar** setiap besaran (D, T untuk silinder; P, L, T untuk balok dan plat) menggunakan Persamaan (2) dan (4).",
    "**Menghitung volume dan ketidakpastiannya** menggunakan Persamaan (7) untuk balok dan plat serta Persamaan (8) untuk silinder.",
    "**Menuliskan hasil** setiap besaran dan volume dalam bentuk x = x̄ ± Δx menurut aturan baku, dilengkapi ketidakpastian relatifnya.",
    "**Menganalisis hasil dan menyusun kesimpulan** dengan membandingkan ketidakpastian antarbenda dan antaralat serta mengidentifikasi sumber kesalahan.",
    "**Selesai.**",
])

# ================================================================= BAB IV
heading1(doc, "BAB IV\nHASIL DAN ANALISA")
heading2(doc, "4.1 Data Hasil Pengukuran")
para(doc, "Pengukuran dilakukan sebanyak n = 5 kali untuk setiap besaran. Tabel 4.1 sampai Tabel 4.3 menyajikan data "
     "mentah beserta nilai jumlah (Σx), rata-rata (x̄), jumlah kuadrat simpangan Σ(x_{i} − x̄)², dan ketidakpastian Δx "
     "yang dihitung dengan Persamaan (4). Seluruh satuan dalam cm.")

DEC = {("silinder", "Diameter"): 2, ("silinder", "Tinggi"): 2, ("plat", "Tinggi"): 2}   # others 1 decimal

def build_table(obj, caption_text, quantities, note=None):
    hdr = ["No"]
    for q in quantities:
        hdr += [f"{q} (cm)", "(x_{i} − x̄)² (cm^{2})"]
    n = 5
    rows = []
    for i in range(n):
        row = [str(i + 1)]
        for q in quantities:
            xs = DATA[obj][q]; st = R[obj][q]; dec = DEC.get((obj, q), 1)
            row += [f(xs[i], dec), f(st["dev2"][i], 6 if dec == 2 else 4)]
        rows.append(row)
    def summary(label, key, col, dec):
        row = [label]
        for q in quantities:
            st = R[obj][q]
            row += [f(st[key], dec), ""] if col == 0 else ["", f(st[key], dec)]
        return row
    rows.append(summary("Σx", "sum", 0, 2))
    rows.append(summary("x̄", "mean", 0, 3))
    rows.append(summary("Σ(x_{i} − x̄)²", "sumdev2", 1, 6))
    rows.append(summary("Δx = S", "S", 0, 6))
    ncol = len(hdr)
    widths = [2.2] + [(13.7 / (ncol - 1))] * (ncol - 1)
    data_table(doc, hdr, rows, widths, size=10.5 if ncol <= 5 else 9.5, bold_rows=range(n, n + 4))
    caption(doc, caption_text)
    if note:
        para(doc, note, size=10, indent_first=False, line_spacing=1.0, space_after=8)

build_table("silinder", "Tabel 4.1 Data pengukuran silinder (jangka sorong, NST 0,01 cm)", ["Diameter", "Tinggi"])
build_table("balok", "Tabel 4.2 Data pengukuran balok (mistar, NST 0,1 cm)", ["Panjang", "Tinggi", "Lebar"],
    note="*Catatan: pada lembar data mentah, pengukuran panjang balok ke-5 tertulis 9,8 cm, tetapi jumlah (47,9 cm), rata-rata "
         "(9,58 cm), dan perhitungan Δx pada lembar analisis praktikum konsisten dengan nilai 9,5 cm; nilai 9,5 cm inilah yang digunakan.*")
build_table("plat", "Tabel 4.3 Data pengukuran plat (panjang dan lebar dengan mistar; tebal dengan jangka sorong)", ["Panjang", "Tinggi", "Lebar"])

heading2(doc, "4.2 Pengolahan Data")
para(doc, "Berikut perhitungan rata-rata (Persamaan 2) dan ketidakpastian (Persamaan 4) untuk setiap besaran, "
     "dilanjutkan perhitungan volume beserta ketidakpastiannya (Persamaan 7 dan 8).")

def calc_block(title, obj, q, sym, dec, label=None):
    xs = DATA[obj][q]; st = R[obj][q]
    heading3(doc, title)
    terms = " + ".join(c(x, dec) for x in xs)
    equation(doc, rf"\bar{{{sym}}} = \frac{{{terms}}}{{5}} = \frac{{{c(st['sum'], dec)}}}{{5}} = {c(st['mean'], 3)}\ \mathrm{{cm}}")
    dev_terms = " + ".join(rf"({c(x, dec)} - {c(st['mean'], 3)})^2" for x in xs)
    equation(doc, rf"\Delta {sym} = \sqrt{{\frac{{{dev_terms}}}{{5 - 1}}}}", max_width_in=6.2)
    equation(doc, rf"\Delta {sym} = \sqrt{{\frac{{{c(st['sumdev2'], 6)}}}{{4}}}} = \sqrt{{{c(st['sumdev2']/4, 7)}}} = {c(st['S'], 6)}\ \mathrm{{cm}}")
    v, u = sig_round(st["mean"], st["S"])
    para(doc, f"Sehingga {label or q.lower()} {obj} dituliskan {sym} = ({f(st['mean'],3)} ± {f(st['S'],6)}) cm ≈ ({v} ± {u}) cm, "
         f"dengan ketidakpastian relatif {f(st['rel'],2)} %.", indent_first=False, left_indent=0.5)

def volume_note(nm, V, dV, vv, vu):
    para(doc, f"Sehingga volume {nm} V = ({f(V,4)} ± {f(dV,4)}) cm^{{3}} ≈ ({vv} ± {vu}) cm^{{3}} = {sci(V, dV, 'cm^{3}')}, "
         f"dengan ketidakpastian relatif {f(dV/V*100,2)} %.", indent_first=False, left_indent=0.5)

def box_volume_eqs(Q, cq, V, dV, tdec):
    Pm, Lm, Tm = Q['Panjang']['mean'], Q['Lebar']['mean'], Q['Tinggi']['mean']
    equation(doc, rf"\bar{{V}} = \bar{{P}}\,\bar{{L}}\,\bar{{T}} = ({c(Pm,2)})({c(Lm,2)})({c(Tm,tdec)}) = {c(V,4)}\ \mathrm{{cm^3}}")
    equation(doc, rf"\Delta V = \sqrt{{\left(\bar{{L}}\,\bar{{T}}\,\Delta P\right)^{{2}} + \left(\bar{{P}}\,\bar{{T}}\,\Delta L\right)^{{2}} + \left(\bar{{P}}\,\bar{{L}}\,\Delta T\right)^{{2}}}}")
    equation(doc, rf"\Delta V = \sqrt{{\left[({c(Lm,2)})({c(Tm,tdec)})({c(Q['Panjang']['S'],6)})\right]^{{2}} + \left[({c(Pm,2)})({c(Tm,tdec)})({c(Q['Lebar']['S'],6)})\right]^{{2}} + \left[({c(Pm,2)})({c(Lm,2)})({c(Q['Tinggi']['S'],6)})\right]^{{2}}}}", max_width_in=6.3)
    equation(doc, rf"\Delta V = \sqrt{{({c(cq['P'],4)})^{{2}} + ({c(cq['L'],4)})^{{2}} + ({c(cq['T'],4)})^{{2}}}} = \sqrt{{{c(sum(x**2 for x in cq.values()),4)}}} = {c(dV,4)}\ \mathrm{{cm^3}}")

heading3(doc, "A. Silinder")
calc_block("1) Diameter silinder", "silinder", "Diameter", "D", 2)
calc_block("2) Tinggi silinder", "silinder", "Tinggi", "T", 2)
heading3(doc, "3) Volume silinder")
Dm, Tm = S['Diameter']['mean'], S['Tinggi']['mean']
equation(doc, rf"\bar{{V}} = \frac{{1}}{{4}}\pi\,\bar{{D}}^{{2}}\,\bar{{T}} = \frac{{1}}{{4}}\,\pi\,({c(Dm,2)})^{{2}}\,({c(Tm,2)}) = {c(Vs,4)}\ \mathrm{{cm^3}}")
equation(doc, rf"\Delta V = \sqrt{{\left(\frac{{1}}{{2}}\pi\,\bar{{D}}\,\bar{{T}}\,\Delta D\right)^{{2}} + \left(\frac{{1}}{{4}}\pi\,\bar{{D}}^{{2}}\,\Delta T\right)^{{2}}}}")
equation(doc, rf"\Delta V = \sqrt{{\left(\frac{{1}}{{2}}\pi\,({c(Dm,2)})({c(Tm,2)})({c(S['Diameter']['S'],6)})\right)^{{2}} + \left(\frac{{1}}{{4}}\pi\,({c(Dm,2)})^{{2}}({c(S['Tinggi']['S'],6)})\right)^{{2}}}}", max_width_in=6.3)
equation(doc, rf"\Delta V = \sqrt{{({c(cs['D'],4)})^{{2}} + ({c(cs['T'],4)})^{{2}}}} = \sqrt{{{c(cs['D']**2,5)} + {c(cs['T']**2,5)}}} = {c(dVs,4)}\ \mathrm{{cm^3}}")
volume_note("silinder", Vs, dVs, Vsv, Vsu)

heading3(doc, "B. Balok")
calc_block("1) Panjang balok", "balok", "Panjang", "P", 1)
calc_block("2) Tinggi balok", "balok", "Tinggi", "T", 1)
calc_block("3) Lebar balok", "balok", "Lebar", "L", 1)
heading3(doc, "4) Volume balok")
box_volume_eqs(B, cb, Vb, dVb, 2)
volume_note("balok", Vb, dVb, Vbv, Vbu)

heading3(doc, "C. Plat")
calc_block("1) Panjang plat", "plat", "Panjang", "P", 1)
calc_block("2) Tebal (tinggi) plat", "plat", "Tinggi", "T", 2, label="tebal")
calc_block("3) Lebar plat", "plat", "Lebar", "L", 1)
heading3(doc, "4) Volume plat")
box_volume_eqs(P, cp, Vp, dVp, 3)
volume_note("plat", Vp, dVp, Vpv, Vpu)

heading2(doc, "4.3 Rekapitulasi Hasil Pengukuran")
para(doc, "Seluruh hasil pengukuran langsung dan tak langsung dirangkum pada Tabel 4.4, baik dalam bentuk nilai lengkap "
     "maupun dalam aturan baku, beserta ketidakpastian relatifnya (KR).")
def rrow(q, st):
    v, u = sig_round(st["mean"], st["S"])
    return ["", q, f"{f(st['mean'],3)} ± {f(st['S'],6)}", f"({v} ± {u}) cm", f(st["rel"], 2)]
def vrow(V, dV):
    v, u = sig_round(V, dV)
    return ["", "Volume, V", f"{f(V,4)} ± {f(dV,4)}", f"({v} ± {u}) cm^{{3}}", f(dV / V * 100, 2)]
rows = [rrow("Diameter, D", S["Diameter"]), rrow("Tinggi, T", S["Tinggi"]), vrow(Vs, dVs),
        rrow("Panjang, P", B["Panjang"]), rrow("Lebar, L", B["Lebar"]), rrow("Tinggi, T", B["Tinggi"]), vrow(Vb, dVb),
        rrow("Panjang, P", P["Panjang"]), rrow("Lebar, L", P["Lebar"]), rrow("Tebal, T", P["Tinggi"]), vrow(Vp, dVp)]
t = data_table(doc, ["Benda", "Besaran", "x̄ ± Δx (nilai lengkap)", "Aturan baku", "KR (%)"], rows, [2.0, 2.8, 4.6, 4.6, 1.9], size=10.5)
for a, b_, nm in ((1, 3, "Silinder"), (4, 7, "Balok"), (8, 11, "Plat")):
    merge_first_col(t, a, b_)
    cell = t.cell(a, 0); cell.text = ""; pp = cell.paragraphs[0]; pp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pp.paragraph_format.space_after = Pt(0); cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    add_rich(pp, nm, size=10.5, bold=True)
caption(doc, "Tabel 4.4 Rekapitulasi hasil pengukuran beserta ketidakpastiannya")

heading2(doc, "4.4 Analisa")
para(doc, f"**Ketelitian alat ukur.** Besaran yang diukur dengan jangka sorong (NST 0,01 cm) menghasilkan ketidakpastian "
     f"mutlak yang jauh lebih kecil daripada yang diukur dengan mistar (NST 0,1 cm). Diameter dan tinggi silinder "
     f"memiliki Δx sebesar {f(S['Diameter']['S'],4)} cm dan {f(S['Tinggi']['S'],4)} cm (KR {f(S['Diameter']['rel'],2)} % dan "
     f"{f(S['Tinggi']['rel'],2)} %), sedangkan panjang, tinggi, dan lebar balok yang diukur dengan mistar memiliki Δx "
     f"antara {f(B['Tinggi']['S'],3)} cm sampai {f(B['Panjang']['S'],3)} cm (KR {f(B['Lebar']['rel'],2)} % sampai "
     f"{f(B['Tinggi']['rel'],2)} %). Hal ini sesuai dengan teori bahwa ketelitian pengukuran dibatasi oleh NST alat: "
     f"semakin kecil NST, semakin kecil sebaran hasil pembacaan.")
para(doc, f"**Perbandingan Δx dengan ½ NST.** Untuk silinder, Δx diameter ({f(S['Diameter']['S'],4)} cm) lebih besar daripada "
     f"½ NST jangka sorong (0,005 cm). Artinya, sebaran data tidak hanya disebabkan oleh keterbatasan skala, tetapi juga "
     f"oleh variasi diameter silinder yang sesungguhnya pada posisi pengukuran yang berbeda serta variasi tekanan rahang "
     f"jangka sorong terhadap benda. Sebaliknya, Δx tinggi silinder ({f(S['Tinggi']['S'],4)} cm) hampir sama dengan ½ NST, "
     f"menunjukkan permukaan alas dan tutup silinder yang cukup rata dan sejajar. Pada balok, Δx ({f(B['Tinggi']['S'],3)}–"
     f"{f(B['Panjang']['S'],3)} cm) berkisar satu sampai tiga kali ½ NST mistar (0,05 cm); penyimpangan ini dipengaruhi "
     f"kesalahan paralaks saat membaca skala mistar, posisi ujung nol mistar yang tidak tepat pada tepi benda, serta "
     f"sudut dan tepi balok yang tidak sempurna.")
para(doc, f"**Ketidakpastian relatif tebal plat.** Ketidakpastian relatif terbesar dari seluruh besaran terjadi pada tebal "
     f"plat, yaitu {f(P['Tinggi']['rel'],2)} %, meskipun ketidakpastian mutlaknya kecil ({f(P['Tinggi']['S'],4)} cm). "
     f"Hal ini terjadi karena nilai tebal plat ({f(P['Tinggi']['mean'],3)} cm) hanya sekitar 14 kali NST jangka sorong, "
     f"sehingga penyimpangan satu skala saja sudah berarti hampir 7 %. Untuk benda yang sangat tipis, alat dengan NST "
     f"lebih kecil seperti mikrometer sekrup (NST 0,001 cm) akan memberikan ketidakpastian relatif yang jauh lebih baik.")
para(doc, f"**Perambatan ketidakpastian ke volume.** Ketidakpastian relatif volume selalu lebih besar daripada ketidakpastian "
     f"relatif masing-masing besaran penyusunnya karena ketidakpastian tersebut merambat dan terakumulasi. Pada silinder, "
     f"kontribusi diameter (½πD̄T̄ΔD = {f(cs['D'],4)} cm^{{3}}) jauh lebih besar daripada kontribusi tinggi "
     f"(¼πD̄²ΔT = {f(cs['T'],4)} cm^{{3}}) karena diameter muncul berpangkat dua pada rumus volume, sehingga KR volume "
     f"({f(dVs/Vs*100,2)} %) sekitar dua kali KR diameter. Pada balok, ketiga kontribusi relatif seimbang "
     f"({f(cb['P'],2)}; {f(cb['L'],2)}; {f(cb['T'],2)} cm^{{3}}) sehingga KR volume menjadi {f(dVb/Vb*100,2)} %. Pada plat, "
     f"kontribusi tebal (P̄L̄ΔT = {f(cp['T'],3)} cm^{{3}}) mendominasi dibandingkan kontribusi panjang ({f(cp['P'],3)} cm^{{3}}) "
     f"dan lebar ({f(cp['L'],3)} cm^{{3}}), sehingga KR volume plat ({f(dVp/Vp*100,2)} %) hampir seluruhnya ditentukan oleh "
     f"ketelitian pengukuran tebal. Ini menegaskan bahwa perbaikan ketelitian harus difokuskan pada besaran yang "
     f"kontribusinya paling besar.")
para(doc, f"**Metode penjumlahan ketidakpastian.** Apabila ketidakpastian volume dihitung dengan penjumlahan linier seperti "
     f"rumus pada modul (ΔV = L̄T̄ΔP + P̄T̄ΔL + P̄L̄ΔT), diperoleh ΔV balok = {f(lin_b,2)} cm^{{3}} dan ΔV plat = {f(lin_p,2)} cm^{{3}}, "
     f"lebih besar daripada hasil penjumlahan kuadrat ({f(dVb,2)} cm^{{3}} dan {f(dVp,2)} cm^{{3}}). Penjumlahan linier "
     f"mengasumsikan semua penyimpangan terjadi searah sekaligus (kasus terburuk), sedangkan penjumlahan kuadrat lebih "
     f"tepat untuk ketidakpastian acak yang saling bebas seperti pada pengukuran berulang ini. Nilai yang dilaporkan pada "
     f"Tabel 4.4 menggunakan penjumlahan kuadrat, konsisten dengan lembar pengolahan data praktikum.")
para(doc, "**Sumber kesalahan.** Sumber ketidakpastian yang teridentifikasi selama praktikum meliputi: (1) kesalahan "
     "paralaks karena posisi mata tidak tegak lurus terhadap skala; (2) kesalahan titik nol dan penempatan ujung mistar "
     "pada tepi benda; (3) variasi tekanan rahang jangka sorong yang dapat menekan atau tidak merapat sempurna pada benda; "
     "(4) benda uji yang tidak ideal (tepi balok tidak siku sempurna, permukaan plat tidak rata); dan (5) pembulatan saat "
     "pembacaan skala. Pengulangan pada posisi berbeda membantu meredam kesalahan acak, tetapi tidak menghilangkan "
     "kesalahan sistematis seperti kalibrasi alat.")
para(doc, "**Keterbatasan data.** Pengukuran dilakukan lima kali, lebih sedikit dari sepuluh kali yang disarankan modul, "
     "sehingga estimasi deviasi standar masih relatif kasar. Massa benda tidak ditimbang pada praktikum ini sehingga "
     "massa jenis dan jenis bahan benda belum dapat ditentukan.")

# ================================================================= BAB V
heading1(doc, "BAB V\nKESIMPULAN DAN SARAN")
heading2(doc, "5.1 Kesimpulan")
numbered(doc, [
    "Pengukuran berulang (n = 5) dengan jangka sorong dan mistar dapat diolah menjadi nilai terbaik dan ketidakpastiannya "
    "menggunakan rata-rata dan deviasi standar sampel. Hasil pengukuran silinder adalah "
    f"D = ({Dv} ± {Du}) cm dan T = ({Tv} ± {Tu}) cm; balok P = ({Pv} ± {Pu}) cm, L = ({Lv} ± {Lu}) cm, T = ({Tbv} ± {Tbu}) cm; "
    f"plat P = ({Ppv} ± {Ppu}) cm, L = ({Lpv} ± {Lpu}) cm, T = ({Tpv} ± {Tpu}) cm.",
    "Volume beserta ketidakpastiannya yang diperoleh melalui perambatan ketidakpastian adalah "
    f"V_{{silinder}} = ({Vsv} ± {Vsu}) cm^{{3}}, V_{{balok}} = ({Vbv} ± {Vbu}) cm^{{3}}, dan V_{{plat}} = ({Vpv} ± {Vpu}) cm^{{3}}, "
    f"dengan ketidakpastian relatif berturut-turut {f(dVs/Vs*100,2)} %, {f(dVb/Vb*100,2)} %, dan {f(dVp/Vp*100,2)} %.",
    f"Ketelitian pengukuran ditentukan oleh NST alat ukur: besaran yang diukur dengan jangka sorong (NST 0,01 cm) memiliki "
    f"ketidakpastian relatif di bawah 0,4 %, sedangkan yang diukur dengan mistar (NST 0,1 cm) berkisar 1–2 %. Ketidakpastian "
    f"relatif terbesar terjadi pada tebal plat ({f(P['Tinggi']['rel'],1)} %) karena nilainya sangat kecil terhadap NST alat, dan "
    f"komponen inilah yang mendominasi ketidakpastian volume plat.",
    "Ketidakpastian besaran masukan merambat ke besaran turunan; besaran yang berpangkat lebih tinggi dalam rumus (diameter "
    "pada volume silinder) dan besaran dengan ketidakpastian relatif terbesar (tebal plat) memberi kontribusi paling besar "
    "terhadap ketidakpastian hasil akhir.",
    "Perbedaan hasil antarpengulangan disebabkan oleh kesalahan paralaks, kesalahan titik nol, variasi tekanan rahang "
    "jangka sorong, ketidaksempurnaan bentuk benda uji, dan pembulatan pembacaan skala.",
])
heading2(doc, "5.2 Saran")
numbered(doc, [
    "Jumlah pengulangan sebaiknya ditambah menjadi sepuluh kali sesuai modul agar estimasi deviasi standar lebih andal.",
    "Tebal plat dan benda tipis lainnya sebaiknya diukur dengan mikrometer sekrup (NST 0,001 cm) untuk menekan ketidakpastian relatif.",
    "Seluruh dimensi balok sebaiknya diukur dengan jangka sorong, bukan mistar, karena ukurannya masih berada dalam jangkauan rahang jangka sorong.",
    "Sebelum mengukur, praktikan harus memeriksa titik nol alat, membaca skala dengan mata tegak lurus, dan menjaga tekanan rahang jangka sorong tetap konsisten.",
    "Massa setiap benda sebaiknya ditimbang dengan neraca teknis agar massa jenis beserta ketidakpastiannya dapat dihitung dan jenis bahan benda dapat ditentukan.",
    "Pencatatan data mentah perlu diperiksa ulang sebelum diolah agar tidak terjadi ketidaksesuaian antara data dan hasil perhitungan.",
])

# ================================================================= DAFTAR PUSTAKA
heading1(doc, "DAFTAR PUSTAKA")
refs = [
    "Hendrik Elvian GP. *Modul Praktikum 2 Workshop Sistem Pengukuran Teknik: Ketidakpastian Pengukuran*. Program Studi Sistem Pembangkit Energi, Departemen Mekatronika dan Energi, Politeknik Elektronika Negeri Surabaya.",
    "Taylor, J. R. (1997). *An Introduction to Error Analysis: The Study of Uncertainties in Physical Measurements* (2nd ed.). Sausalito, CA: University Science Books.",
    "Bevington, P. R., & Robinson, D. K. (2003). *Data Reduction and Error Analysis for the Physical Sciences* (3rd ed.). New York: McGraw-Hill.",
    "JCGM. (2008). *Evaluation of Measurement Data — Guide to the Expression of Uncertainty in Measurement* (JCGM 100:2008). Joint Committee for Guides in Metrology.",
    "Morris, A. S., & Langari, R. (2020). *Measurement and Instrumentation: Theory and Application* (3rd ed.). London: Academic Press.",
]
for i, r in enumerate(refs, 1):
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    pf = p.paragraph_format; pf.left_indent = Cm(1.0); pf.first_line_indent = Cm(-1.0); pf.space_after = Pt(6); pf.line_spacing = 1.5
    add_rich(p, f"[{i}]\t{r}")

# ================================================================= LAMPIRAN
heading1(doc, "LAMPIRAN")
para(doc, "Lampiran 1. Lembar data hasil pengukuran praktikum (kelompok) yang menjadi dasar pengolahan data pada laporan ini.",
     indent_first=False)
picture(doc, os.path.join(RENDER, "wpt_p1_clean.png"), 13.8)
caption(doc, "Gambar L.1 Lembar data pengukuran praktikum")

OUTFILE = os.path.join(os.path.dirname(HERE), "Laporan Praktikum WPT Modul 2 - Ketidakpastian Pengukuran - Putu Bagus Romi Pratama Putra.docx")
doc.save(OUTFILE)
print("saved:", OUTFILE)
