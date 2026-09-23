# Bot Telegram Sinyal Saham IDX (Grup) — stock_signal_bot

Bot Python asinkron yang menghasilkan laporan **pre-market (08:30 WIB)** dan **pre-close
(15:00 WIB)** untuk saham BEI dan mengirimkannya **hanya ke grup/supergroup/channel Telegram
dalam allowlist**. Engine deterministik menentukan seluruh angka trading; LLM (opsional)
hanya merangkum konteks. Tidak ada eksekusi order, akses dana, atau transaksi broker.

> **Status proyek: Tahap 4 dari 7 selesai (storage, bot Telegram grup-only, scheduler,
> dry-run end-to-end).** Bot dapat dijalankan dan mengirim laporan ke grup dalam mode live
> **development** setelah Anda mengisi token + ID grup dan menyalakan saklar live secara
> eksplisit. Mode **produksi** tetap diblokir sampai aturan bursa/kalender diverifikasi dan
> gate backtest (Tahap 6) lulus. Semua nilai aturan masih **CONTOH / BELUM TERVERIFIKASI**
> (lihat `docs/verification_required.md`). Arsitektur lengkap ada di `ARCHITECTURE.md`.

## Yang sudah tersedia (Tahap 1–4)

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
- `engine/lifecycle.py` — lifecycle sinyal simulasi (pending → active → closed/expired) dengan
  aturan konservatif terdokumentasi; dipakai identik oleh live dan backtest.
- `storage/` — SQLAlchemy 2.0 async (SQLite dev / PostgreSQL prod), Alembic; klaim job atomik
  lewat UNIQUE constraint, pencatatan pengiriman per tujuan × bagian dengan status
  `pending/sending/sent/failed/unknown`, watchlist admin, pause persisten, audit sinyal.
- `notifications/` — notifier Telegram grup-only: verifikasi tipe chat & izin bot via API,
  penolakan chat pribadi di semua jalur, pemetaan hasil kirim yang jujur (timeout = `unknown`,
  tidak dikirim ulang otomatis).
- `bot/` — formatter HTML (escape, split deterministik, validasi tag), router command dengan
  otorisasi grup/admin (pengirim anonim ditolak), gate produksi, ReportService (job pagi/sore),
  scheduler APScheduler WIB, alert admin dengan rate limit, adapter python-telegram-bot.
- `scripts/test_telegram.py` (uji koneksi/kirim TEST, default dry-run) dan
  `scripts/reconcile_deliveries.py` (rekonsiliasi status `unknown`).
- `tests/` — 283 test offline (fixture sintetis berlabel, tanpa token, tanpa jaringan).
- `main.py` — `config`, `health`, `fetch`, `evaluate`, `dryrun morning|afternoon`, `run`.

Belum tersedia: narator LLM & ekspor teks WhatsApp (Tahap 5), backtest & gate produksi (Tahap 6),
Docker/Compose, paket ZIP, dokumentasi deployment lengkap (Tahap 7).

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

## Menyiapkan bot Telegram (grup, bukan chat pribadi)

### 1. Membuat bot di BotFather
1. Buka Telegram, cari **@BotFather**, kirim `/newbot`, ikuti instruksi (nama & username).
2. BotFather memberi **token** berbentuk `123456789:AA...`. Token = password bot: jangan
   dibagikan, jangan di-commit, jangan ditempel di chat mana pun.
3. Privacy mode **tidak perlu dimatikan**: bot hanya membaca command `/...` di grup.

### 2. Mengisi `.env` dengan aman
```bash
cp .env.example .env
chmod 600 .env
```
Isi `TELEGRAM_BOT_TOKEN=` di `.env` (atau lewat pengelola secret/variabel environment).
`.env` sudah ada di `.gitignore` dan `.dockerignore`.

### 3. Menambahkan bot ke grup
Tambahkan bot sebagai anggota grup sinyal (dan grup admin terpisah, disarankan). Untuk
**channel**, bot harus dijadikan administrator dengan hak *post messages*. Untuk grup dengan
**topik (forum)**, Anda dapat menunjuk topik dengan format `chat_id:thread_id`.

### 4. Memperoleh ID grup tanpa membagikan token
Setelah bot ada di grup, ketik `/start` di grup itu, lalu (saat bot **tidak** sedang berjalan):
```bash
uv run python scripts/test_telegram.py --discover
```
Skrip mencetak ID grup/channel (angka negatif, mis. `-1001234567890`) yang terlihat oleh bot
Anda sendiri; chat pribadi tidak ditampilkan. Token tetap di mesin Anda. Salin ID ke:
```
TELEGRAM_SIGNAL_CHAT_IDS=-1001234567890          # bisa lebih dari satu, pisahkan koma
TELEGRAM_ADMIN_CHAT_ID=-1009876543210            # grup operasional admin
TELEGRAM_ADMIN_USER_IDS=123456789                # user_id admin (bukan username)
```
ID pengguna admin dapat dilihat lewat pengaturan Telegram Desktop/aplikasi pihak ketiga
tepercaya; bot ini tidak memerlukan pesan pribadi untuk itu.

### 5. Izin bot dan allowlist admin
- Sinyal hanya dikirim ke chat pada `TELEGRAM_SIGNAL_CHAT_IDS`; alert hanya ke
  `TELEGRAM_ADMIN_CHAT_ID`. Tanpa grup admin, alert dicatat ke log — tidak pernah ke chat pribadi.
- Sebelum mengirim, bot memverifikasi via API: tipe chat harus group/supergroup/channel, bot
  harus anggota (channel: admin dengan hak posting). Chat pribadi ditolak meski ID-nya ditulis.
- Command admin (`/watchlist add|remove`, `/pause`, `/resume`, `/runnow`, `/broadcast`, `/health`)
  hanya diterima **di grup admin** dari `user_id` dalam whitelist; pengirim anonim ditolak.

### 6. Dry-run (tanpa mengirim apa pun)
```bash
uv run python main.py dryrun morning      # satu job pagi end-to-end; cetak pesan tersanitasi
uv run python main.py dryrun afternoon
```
Hasil disimpan ke `var/exports/dry_run/` dan `var/dev.db` (origin `dry_run`, terpisah dari live).

### 7. Uji kirim TEST dengan persetujuan eksplisit
```bash
uv run python scripts/test_telegram.py            # verifikasi bot & tujuan saja
uv run python scripts/test_telegram.py --send     # kirim SATU pesan TEST (bukan sinyal)
uv run python scripts/test_telegram.py --send --chat-id -1001234567890   # bila tujuan > 1
```
Kode keluar: `0` sukses (API mengonfirmasi `message_id`), `2` ditolak, `3` gagal sebelum
terkirim, `4` ambigu (timeout — periksa grup manual; tidak dikirim ulang otomatis).

### 8. Menjalankan bot
```bash
# development, dry-run: scheduler berjalan, laporan hanya diekspor ke var/exports
uv run python main.py run

# development, live ke grup: nyalakan DUA saklar secara sadar
APP_MODE=live TELEGRAM_ENABLE_LIVE_SEND=true uv run python main.py run
```
Jadwal default 08:30 dan 15:00 WIB pada hari perdagangan (kalender + libur diperiksa saat
job berjalan). Hentikan dengan Ctrl+C/SIGTERM (shutdown tertib).

### 9. Penanganan pengiriman `unknown`
Jika respons Telegram hilang setelah request dikirim, pengiriman ditandai `unknown` dan
**tidak** diulang otomatis (menghindari pesan ganda). `/health` menampilkan jumlahnya. Periksa
grup secara manual, lalu:
```bash
uv run python scripts/reconcile_deliveries.py list
uv run python scripts/reconcile_deliveries.py mark-sent 12 --by "admin:123456789"
uv run python scripts/reconcile_deliveries.py mark-failed 12 --by "admin:123456789"
uv run python scripts/reconcile_deliveries.py requeue 12      # failed → pending
```

### 10. Database
- Development: SQLite `var/dev.db` (skema dibuat otomatis).
- Production: PostgreSQL, jalankan migrasi: `DATABASE_URL=postgresql+asyncpg://... uv run alembic upgrade head`.
- Backup: salin berkas SQLite saat bot berhenti, atau `pg_dump` untuk PostgreSQL. Snapshot laporan
  tersimpan immutable di tabel `job_runs`.

## Struktur singkat

```
config/      settings + YAML aturan/kalender/watchlist/berita/makro
core/        timeutil, redaction, logging
data/        providers/ (base, yahoo, templates, news, global_macro), resilience, validation, aggregator
engine/      indicators, strategies/, scorer, screener, risk, pipeline, lifecycle
storage/     models, repository, migrations/ (Alembic)
notifications/ base, telegram
bot/         formatter, commands, handlers, gates, reports, scheduler, alerts, runtime
scripts/     test_telegram.py, reconcile_deliveries.py
tests/       test offline
docs/        verification_required.md
var/         runtime (db, log, cache, ekspor) — gitignored
```

## Disclaimer

Bukan ajakan jual/beli. Analisis bersifat informasional. Keputusan dan risiko sepenuhnya
milik Anda.
