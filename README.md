# Bot Telegram Sinyal Saham IDX (Grup) — stock_signal_bot

Bot Python asinkron yang menghasilkan laporan **pre-market (08:30 WIB)** dan **pre-close
(15:00 WIB)** untuk saham BEI dan mengirimkannya **hanya ke grup/supergroup/channel Telegram
dalam allowlist**. Engine deterministik menentukan seluruh angka trading; LLM (opsional)
hanya merangkum konteks. Tidak ada eksekusi order, akses dana, atau transaksi broker.

> **Status proyek: Tahap 3 dari 7 (engine & risk selesai) — belum bisa mengirim sinyal.**
> Semua aturan bursa, kalender, dan simbol makro masih berlabel **CONTOH / BELUM
> TERVERIFIKASI** dan memblokir mode produksi (lihat `docs/verification_required.md`).
> Arsitektur lengkap ada di `ARCHITECTURE.md`.

## Yang sudah tersedia (Tahap 1–3)

- `ARCHITECTURE.md` — desain, alur data, kebijakan grup-only, gate produksi.
- `config/settings.py` — konfigurasi env (pydantic-settings) dengan default aman dan
  validasi kombinasi (mode live, produksi, provider belum terverifikasi, LLM).
- `config/*.yaml` — aturan bursa, kalender, watchlist, sumber berita, instrumen makro;
  setiap blok membawa `meta.verified` dan label.
- `data/` — kontrak provider (tagged union `Ok|Unsupported|Unavailable|Failed`), adapter
  Yahoo Finance (development), template GoAPI/Sectors/broker (nonaktif), berita RSS/Atom
  aman, makro global, aggregator (failover, timeout, retry, breaker, rate limit, cache,
  validasi, cross-validation).
- `core/` — util waktu WIB/UTC, redaksi token, logging loguru dengan redaksi.
- `engine/` — indikator dengan warmup eksplisit (test referensi vs pandas-native), empat
  strategi (trend pullback, breakout, reversal/divergence, foreign flow), scorer dengan formula
  terdokumentasi, screener likuiditas/pengecualian, risk (tick rounding lintas rentang, ARA/ARB,
  SL ATR/struktur, TP1–3, R:R kotor & bersih, sizing lot), pipeline dengan batas anti-lookahead.
- `tests/` — 215 test offline (fixture sintetis berlabel, tanpa token, tanpa jaringan).
- `main.py` — CLI diagnostik + `evaluate` (engine pada watchlist, tanpa kirim).

Belum tersedia: database, Telegram, scheduler, lifecycle sinyal (Tahap 4), narator LLM & ekspor
WhatsApp (Tahap 5), backtest & gate (Tahap 6), Docker & paket ZIP (Tahap 7).

## Instalasi lokal

Prasyarat: Python 3.12 dan [uv](https://docs.astral.sh/uv/) (disarankan) atau pip.

```bash
# dengan uv (memakai uv.lock)
uv sync --all-groups

# atau dengan pip (requirements hasil ekspor dari lock)
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
```

Semua perintah dijalankan dari root repository.

## Konfigurasi

```bash
cp .env.example .env   # isi seperlunya; JANGAN commit .env
```

Default aman: `APP_ENV=development`, `APP_MODE=dry_run`, live send nonaktif, LLM nonaktif,
provider berbayar nonaktif. Token bot dan API key hanya lewat `.env`/pengelola secret.

Periksa konfigurasi tersanitasi dan daftar pemblokir produksi:

```bash
uv run python main.py config
```

## Menjalankan test dan lint

```bash
uv run pytest            # offline; tidak butuh token/jaringan
uv run ruff check .
uv run ruff format --check .
```

## Uji data layer dengan jaringan (opsional, development)

```bash
uv run python main.py health                 # health check provider aktif
uv run python main.py fetch --symbol BBCA    # OHLCV harian via aggregator + validasi
uv run python main.py evaluate               # engine pada watchlist: kartu sinyal (tanpa kirim)
uv run python main.py evaluate --symbols BBCA BBRI
```

Hasil `fetch` menampilkan status kualitas (`ok|degraded|stale|suspect|missing`), provider
yang dipakai, alasan, dan 3 bar terakhir. Dengan satu provider statusnya maksimal `degraded`.
`evaluate` mencetak sesi evaluasi, status tiap strategi per simbol, simbol yang diblokir dan
alasannya, serta kartu (entry/SL/TP/R:R/sizing) bila ada setup layak — semua angka dari engine,
dengan label aturan/kalender yang masih CONTOH.

## Kebijakan engine (ringkas; detail di ARCHITECTURE.md §10 dan §22)

- Universe = watchlist ∪ kandidat; semua lewat pengecualian + likuiditas (rata-rata 20 hari).
- Indikator hanya dari bar **lengkap** sampai sesi evaluasi; warmup 250 bar dipaksakan.
- Confidence = skor strategi utama + konfluens (maks +15) − penalti data `degraded` (10);
  threshold 70; maksimum 5 sinyal; tie-break deterministik.
- Risk: R = entry_high − SL; SL = min(entry − 1,5×ATR, struktur − 1 tick); TP1 = target
  terendah yang memenuhi ≥ 2R kotor **dan** R:R bersih ≥ 2,0 setelah biaya (CONTOH 0,15 %/0,25 %);
  TP2/TP3 = TP1 + 1R/2R. Zona entry di-clamp ke ARA/ARB sesi berikutnya. Urutan harga & R:R
  divalidasi ulang setelah pembulatan; gagal ⇒ setup dibuang.
- Sizing contoh: modal Rp100 juta, risiko 1 %, lot penuh; 0 lot dilaporkan apa adanya.

## Struktur singkat

```
config/      settings + YAML aturan/kalender/watchlist/berita/makro
core/        timeutil, redaction, logging
data/        providers/ (base, yahoo, templates, news, global_macro), resilience, validation, aggregator
engine/      indicators, strategies/, scorer, screener, risk, pipeline
tests/       test offline
docs/        verification_required.md
var/         runtime (db, log, cache, ekspor) — gitignored
```

## Disclaimer

Bukan ajakan jual/beli. Analisis bersifat informasional. Keputusan dan risiko sepenuhnya
milik Anda.
