# Bot Telegram Sinyal Saham IDX (Grup) — stock_signal_bot

Bot Python asinkron yang menghasilkan laporan **pre-market (08:30 WIB)** dan **pre-close
(15:00 WIB)** untuk saham BEI dan mengirimkannya **hanya ke grup/supergroup/channel Telegram
dalam allowlist**. Engine deterministik menentukan seluruh angka trading; LLM (opsional)
hanya merangkum konteks. Tidak ada eksekusi order, akses dana, atau transaksi broker.

> **Status proyek: Tahap 6 dari 7 selesai (backtest & gate produksi).** Bot dapat dijalankan dan mengirim laporan ke grup dalam mode live
> **development** setelah Anda mengisi token + ID grup dan menyalakan saklar live secara
> eksplisit. Mode **produksi** tetap diblokir sampai aturan bursa/kalender diverifikasi dan
> gate backtest (Tahap 6) lulus. Semua nilai aturan masih **CONTOH / BELUM TERVERIFIKASI**
> (lihat `docs/verification_required.md`). Arsitektur lengkap ada di `ARCHITECTURE.md`.

## Yang sudah tersedia (Tahap 1–6)

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
- `ai/` — adapter LLM provider-agnostic (`openai`, `anthropic`, `openai_compatible`/lokal,
  `none`) via httpx; narator yang hanya merangkum konteks ≤150 kata dari JSON engine dengan
  validasi ketat (angka harus dapat ditelusuri ke input, simbol tidak boleh bertambah, frasa
  "pasti naik/dijamin/beli sekarang" dan markup ditolak, output kosong/terpotong ditolak) dan
  fallback template deterministik. Tanpa API key bot tetap berjalan penuh.
- `notifications/whatsapp_export.py` — teks siap salin untuk **Saluran WhatsApp** dari snapshot
  yang sama (angka/timestamp/disclaimer identik), format `*tebal*`/`_miring_`, disimpan lokal ke
  `var/exports/whatsapp/`. Tidak ada otomasi WhatsApp (lihat bagian WhatsApp di bawah).
- `backtest/` — runner yang memakai `SignalEngine` + `engine/lifecycle.py` **yang sama** dengan
  produksi, evaluasi per sesi tanpa lookahead, biaya & slippage configurable, gap eksplisit, lot
  penuh dibatasi kas, satu posisi per simbol; metrik dengan definisi kasus tepi; split
  in-sample/out-of-sample berbasis waktu; gate kelayakan produksi yang terikat
  `config_hash` + versi strategi dan disimpan ke DB.
- **Smart money proxy dari data Yahoo** (`engine/money_flow.py`, `engine/strategies/money_flow_proxy.py`):
  Chaikin Money Flow 20, OBV (dalam "hari volume"), hari akumulasi/distribusi (close di area atas/
  bawah range dengan volume di atas rata-rata), rasio volume naik/turun, "quiet accumulation".
  Laporan pagi memuat bagian **💰 Money Flow** (top akumulasi & distribusi watchlist) dan strategi
  `money_flow_proxy` menghasilkan setup swing. **Ini proxy dari harga & volume, bukan data broker/asing.**
- **Smart money / broker akumulasi (data broker nyata)** (`engine/strategies/smart_money.py`): aktif
  hanya bila ada broker summary dari **CSV yang Anda ekspor sendiri** (`data/providers/local_flow.py`);
  Yahoo tidak menyediakan data ini dan belum ada API publik terverifikasi. Tanpa data ⇒ `inactive`.
- **Screener BSJP/BPJS** (`engine/short_term.py`, `main.py screen`): statistik historis gap overnight
  dan pergerakan intraday atas watchlist ∪ `config/universe_candidates.yaml` — peringkat objektif,
  bukan sinyal.
- **Teaser** sebelum laporan ("Are you ready for IHSG SIGNAL?"), configurable, hanya bila ada setup.
- `tests/` — 346 test offline (fixture sintetis berlabel, tanpa token, tanpa jaringan).
- `main.py` — `config`, `health`, `fetch`, `evaluate`, `dryrun`, `run`, `backtest`, `screen`.

Belum tersedia: Docker/Compose, paket ZIP, dokumentasi deployment lengkap (Tahap 7).

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

### 10. Narator LLM (opsional)
Default `LLM_PROVIDER=none`: ringkasan memakai template deterministik. Untuk mengaktifkan:
```
LLM_PROVIDER=openai            # atau anthropic | openai_compatible (server lokal, mis. Ollama/vLLM)
LLM_API_KEY=...                # tidak diperlukan untuk openai_compatible tanpa autentikasi
LLM_BASE_URL=                  # wajib untuk openai_compatible, mis. http://localhost:11434/v1
LLM_MODEL=nama-model
```
LLM hanya menulis paragraf "Ringkasan"; kartu entry/SL/TP selalu dari engine. Output yang
memuat angka tak tertelusur, simbol baru, klaim kepastian, ajakan beli/jual, atau markup ditolak
dan diganti template (alasan dicatat di log). Berita masuk ke LLM hanya sebagai judul/sumber
yang ditandai tidak tepercaya.

### 11. Ekspor teks Saluran WhatsApp (manual)
```
WHATSAPP_EXPORT_ENABLED=true
```
Setiap laporan menghasilkan `var/exports/whatsapp/<origin>/<tanggal>_<jenis>.whatsapp.txt` dengan
format WhatsApp, dari snapshot yang sama dengan Telegram. Salin isinya ke Saluran WhatsApp secara
manual. **Tidak ada** integrasi otomatis: API WhatsApp Business belum diverifikasi mendukung
Saluran, dan otomasi WhatsApp Web/library tidak resmi tidak dipakai. Kegagalan ekspor tidak
memengaruhi pengiriman Telegram.

### 12. Smart money / broker akumulasi (data dari CSV Anda)
Yahoo tidak menyediakan broker summary/foreign flow dan belum ada API publik terverifikasi. Jalur
yang jujur: ekspor data dari aplikasi sekuritas/terminal Anda ke CSV, lalu:
```
FLOW_CSV_DIR=var/data/flow
PROVIDER_PRIORITY=yahoo,local_flow
FLOW_HISTORY_SESSIONS=5
```
Struktur berkas (tanggal WIB `YYYY-MM-DD`, nilai rupiah):
```
var/data/flow/foreign_flow/BBCA.csv        date,buy_value,sell_value      (atau date,net_value)
var/data/flow/broker_summary/BBCA.csv      date,broker,buy_value,sell_value   (satu baris per broker)
```
Baris tanggal yang tidak ada ⇒ "tidak tersedia" (bukan nol); nol yang tertulis ⇒ nol yang sah.
Strategi `smart_money` membutuhkan ≥3 sesi berturut yang berakhir pada sesi laporan; broker
*akumulator* = net buy positif di setiap sesi jendela. Semua bukti (kode broker, intensitas,
konsentrasi) tampil pada kartu sinyal. Lisensi/ketentuan data adalah tanggung jawab pengguna.

### 13. Screener BSJP / BPJS (statistik, bukan sinyal)
```bash
uv run python main.py screen --style bsjp --top 5          # beli sore, jual pagi: gap overnight
uv run python main.py screen --style bpjs --top 5          # beli pagi, jual sore: pergerakan intraday
uv run python main.py screen --style bsjp --lookback 90 --symbols BBCA TLKM
```
Universe = watchlist ∪ `config/universe_candidates.yaml` (CONTOH; kode tanpa data dilewati). Kolom:
rata-rata nilai transaksi 20 sesi (filter likuiditas), ATR %, rata-rata & win rate gap overnight
(BSJP) atau intraday (BPJS), `t` = ukuran konsistensi untuk mengurutkan, gap terburuk (risiko).
**Ini statistik historis, bukan prediksi.** BSJP menanggung risiko gap turun semalam tanpa stop loss;
karena itu BSJP belum dijadikan strategi sinyal otomatis (butuh kebijakan exit di open, bukan TP/SL).

### 14. Teaser sebelum laporan
```
TEASER_ENABLED=true
TEASER_TEXT=🔔 Are you ready for IHSG SIGNAL? 🔔
TEASER_ONLY_WITH_SIGNALS=true      # tidak ada setup ⇒ tanpa teaser
```
Teaser dikirim sebagai pesan pertama (tercatat dan dide-dup seperti bagian lain), diikuti laporan.

### 15. Backtest dan gate produksi
```bash
# data Yahoo (development) untuk watchlist, periode 2025-01-01..2026-09-22
uv run python main.py backtest --start 2025-01-01 --end 2026-09-22
# data CSV lokal per simbol (kolom date,open,high,low,close,volume)
uv run python main.py backtest --start 2025-01-01 --end 2026-06-30 --csv-dir data_csv/
# simpan hasil gate ke DB (membuka gate produksi HANYA bila lulus)
uv run python main.py backtest --start ... --end ... --save-gate
```
Keluaran di `var/backtests/<periode>_<hash>/`: `report.json`, `oos_trades.csv`, `oos_equity.csv`
(dan in-sample). Kode keluar `0` = gate lulus, `4` = tidak lulus, `1` = data tidak layak.

**Membaca metrik** (`report.json → out_of_sample.metrics`):
- `trades` = trade selesai (TP/SL/masa tahan); sinyal yang tidak terisi dihitung di `signals_not_filled`.
- `win_rate_pct`, `expectancy_r` (rata-rata PnL bersih dalam R rencana), `profit_factor`
  (gross profit / gross loss; `null` + catatan bila tidak ada kerugian atau tidak ada trade).
- `max_drawdown_pct` dari **equity curve** mark-to-market harian, bukan dari penjumlahan trade.
- `by_strategy` memecah per strategi. `limitations` mencantumkan keterbatasan (survivorship bias,
  aksi korporasi, satu provider, tanpa antrean/partial fill, SL diprioritaskan sebelum TP).

**Gate** (threshold CONTOH, ubah lewat `--min-trades --min-pf --max-dd --oos-fraction`): pada
periode **out-of-sample** (30 % akhir) trade ≥ 30, expectancy > 0R, profit factor ≥ 1,3,
max drawdown ≤ 15 %. Hasil terikat `engine_version` + `config_hash` + versi strategi: mengubah
parameter risk/scorer/aturan membatalkan kelayakan lama. **Tanpa gate yang lulus, mode produksi
tidak akan menerbitkan sinyal.** Hasil backtest tidak menjamin keuntungan masa depan.

> Hasil nyata pada 2026-09-23 (12 saham watchlist, Yahoo, parameter CONTOH, termasuk strategi
> `money_flow_proxy`): in-sample 57 trade, expectancy +0,10R, PF 1,20 (proxy money flow: 11 trade,
> win 64 %, +0,44R); out-of-sample 17 trade, expectancy −0,44R, PF 0,42 → **gate TIDAK lulus**.
> Periode OOS (Mar–Sep 2026) merugikan semua strategi long. Dilaporkan apa adanya; kalibrasi dan
> filter rezim pasar adalah pekerjaan riset sebelum produksi.

### 16. Database
- Development: SQLite `var/dev.db` (skema dibuat otomatis).
- Production: PostgreSQL, jalankan migrasi: `DATABASE_URL=postgresql+asyncpg://... uv run alembic upgrade head`.
- Backup: salin berkas SQLite saat bot berhenti, atau `pg_dump` untuk PostgreSQL. Snapshot laporan
  tersimpan immutable di tabel `job_runs`.

## Struktur singkat

```
config/      settings + YAML aturan/kalender/watchlist/berita/makro
core/        timeutil, redaction, logging
data/        providers/ (base, yahoo, local_flow CSV, templates, news, global_macro), resilience, validation, aggregator
engine/      indicators, strategies/ (termasuk smart_money), scorer, screener, short_term, risk, pipeline, lifecycle
storage/     models, repository, migrations/ (Alembic)
notifications/ base, telegram
bot/         formatter, commands, handlers, gates, reports, scheduler, alerts, runtime
ai/          llm_client (openai/anthropic/openai_compatible), narrator (validasi + template)
notifications/whatsapp_export.py  teks Saluran WhatsApp (manual)
backtest/    runner (engine+lifecycle produksi), metrics, gate, data (Yahoo/CSV)
scripts/     test_telegram.py, reconcile_deliveries.py
tests/       test offline
docs/        verification_required.md
var/         runtime (db, log, cache, ekspor) — gitignored
```

## Disclaimer

Bukan ajakan jual/beli. Analisis bersifat informasional. Keputusan dan risiko sepenuhnya
milik Anda.
