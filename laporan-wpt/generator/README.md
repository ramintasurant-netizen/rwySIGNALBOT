# Generator laporan praktikum WPT Modul 2

Skrip Python yang membangun `../Laporan Praktikum WPT Modul 2 - Ketidakpastian Pengukuran - *.docx`.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python flowchart.py        # (opsional) gambar ulang flowchart -> assets/flowchart.png
python build_report.py     # tulis .docx ke folder induk
```

- `compute.py` — data mentah WPT.01 dan perhitungan statistik (rata-rata, deviasi standar, volume, perambatan ketidakpastian).
- `eq.py` — render rumus (matplotlib mathtext) menjadi gambar untuk disisipkan ke Word.
- `flowchart.py` — flowchart percobaan.
- `docx_helpers.py` — helper python-docx (heading, tabel, persamaan bernomor, nomor halaman).
- `build_report.py` — isi laporan (cover, abstrak, bab I–V, daftar pustaka, lampiran).

Ganti nama/NRP/kelas/tahun ajaran di bagian atas `build_report.py` bila diperlukan.
