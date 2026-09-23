# Bot Telegram Sinyal Saham IDX (Grup) — stock_signal_bot

Bot Python asinkron yang menghasilkan laporan **pre-market (08:30 WIB)** dan **pre-close
(15:00 WIB)** untuk saham BEI dan mengirimkannya **hanya ke grup/supergroup/channel Telegram
dalam allowlist**. Engine deterministik menentukan seluruh angka trading; LLM (opsional)
hanya merangkum konteks. Tidak ada eksekusi order, akses dana, atau transaksi broker.

> **Status proyek: Tahap 2 dari 7 (fondasi & data layer) — belum bisa mengirim sinyal.**
> Semua aturan bursa, kalender, dan simbol makro masih berlabel **CONTOH / BELUM
> TERVERIFIKASI** dan memblokir mode produksi (lihat `docs/verification_required.md`).
> Arsitektur lengkap ada di `ARCHITECTURE.md`.

## Yang sudah tersedia (Tahap 1–2)

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
- `tests/` — 130+ test offline (fixture sintetis, tanpa token, tanpa jaringan).
- `main.py` — CLI diagnostik.

Belum tersedia: engine/strategi/risk (Tahap 3), database, Telegram, scheduler (Tahap 4),
narator LLM & ekspor WhatsApp (Tahap 5), backtest & gate (Tahap 6), Docker & paket ZIP (Tahap 7).

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
```

Hasil `fetch` menampilkan status kualitas (`ok|degraded|stale|suspect|missing`), provider
yang dipakai, alasan, dan 3 bar terakhir. Dengan satu provider statusnya maksimal `degraded`.

## Struktur singkat

```
config/      settings + YAML aturan/kalender/watchlist/berita/makro
core/        timeutil, redaction, logging
data/        providers/ (base, yahoo, templates, news, global_macro), resilience, validation, aggregator
tests/       test offline
docs/        verification_required.md
var/         runtime (db, log, cache, ekspor) — gitignored
```

## Disclaimer

Bukan ajakan jual/beli. Analisis bersifat informasional. Keputusan dan risiko sepenuhnya
milik Anda.
