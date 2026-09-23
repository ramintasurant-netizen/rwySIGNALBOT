# Bot Telegram Sinyal Saham IDX (Grup) — stock_signal_bot

Bot Python asinkron yang menghasilkan laporan **pre-market (08:30 WIB)** dan **pre-close
(15:00 WIB)** untuk saham BEI dan mengirimkannya **hanya ke grup/supergroup/channel Telegram
dalam allowlist**. Engine deterministik menentukan seluruh angka trading; LLM (opsional)
hanya merangkum konteks. Tidak ada eksekusi order, akses dana, atau transaksi broker.

> **Status proyek: semua 7 tahap selesai (arsitektur → data → engine → bot Telegram → narator/WhatsApp
> → backtest/gate → Docker & paket).** Bot dapat dijalankan dan mengirim laporan ke grup dalam mode live
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
- **Filter rezim pasar** (`engine/regime.py`): dari indeks IHSG (`^JKSE`, Yahoo) — bearish
  (close < EMA50 < EMA200) ⇒ bot **tidak menerbitkan setup long baru** (evaluasi tetap dilaporkan,
  sinyal terbuka tetap dikelola). Data indeks tidak tersedia ⇒ `unknown` ⇒ default menahan
  (fail-closed). Dipakai identik oleh live dan backtest; tampil sebagai "📈 Rezim pasar" di laporan.
- `tests/` — 346 test offline (fixture sintetis berlabel, tanpa token, tanpa jaringan).
- `main.py` — `config`, `health`, `fetch`, `evaluate`, `dryrun`, `run`, `backtest`, `screen`.

- `Dockerfile` (multi-stage, non-root, health check heartbeat), `docker-compose.yml` (SQLite dev;
  profil `production` dengan PostgreSQL + migrasi), `.dockerignore`, `scripts/package_project.py`
  (ZIP distribusi berbasis allowlist + pemindaian rahasia), CI GitHub Actions (lint, test, package
  check, build image + smoke test).

**Yang masih membutuhkan tindakan pemilik** (bukan kode): isi token bot & ID grup lalu uji `TEST`;
verifikasi aturan BEI/kalender resmi (ganti nilai CONTOH, set `verified: true`); kalibrasi strategi
sampai gate backtest lulus; sepakati threshold gate dan biaya aktual. Lihat `docs/verification_required.md`.

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

> Hasil nyata pada 2026-09-23 (12 saham watchlist, Yahoo, parameter CONTOH, **dengan filter rezim
> IHSG**): in-sample 50 trade, expectancy +0,04R, PF 1,05 (rezim: 111 sesi bullish, 64 netral,
> 77 bearish); out-of-sample 8 trade, expectancy −0,37R, PF 0,54, MDD 5,5 % (rezim: 82 dari 125 sesi
> **bearish** → sebagian besar setup ditahan) → **gate TIDAK lulus** (trade < 30, expectancy < 0).
> Tanpa filter rezim, OOS sebelumnya −7,7 % / PF 0,42 / MDD 8,8 %: filter mengurangi kerugian,
> tetapi setup yang lolos di sesi netral masih negatif. Dilaporkan apa adanya; kalibrasi strategi
> tetap diperlukan sebelum produksi.

### 16. Database
- Development: SQLite `var/dev.db` (skema dibuat otomatis).
- Production: PostgreSQL, jalankan migrasi: `DATABASE_URL=postgresql+asyncpg://... uv run alembic upgrade head`.
- Snapshot laporan tersimpan immutable di tabel `job_runs`.

### 17. Docker Compose
Prasyarat: Docker Engine + Compose v2, berkas `.env` sudah diisi (lihat §2–§5).
```bash
# Development (SQLite di ./var, mode sesuai .env — default dry_run):
docker compose build
docker compose up -d bot
docker compose logs -f bot
docker compose exec bot python main.py config              # konfigurasi tersanitasi
docker compose exec bot python scripts/test_telegram.py    # verifikasi tujuan (tanpa kirim)
docker compose exec bot python main.py dryrun morning
docker compose down                                        # SIGTERM → shutdown tertib (grace 30 s)

# Production (PostgreSQL):
#   di .env: DATABASE_URL=postgresql+asyncpg://bot:${POSTGRES_PASSWORD}@postgres:5432/stock_signal_bot
#            POSTGRES_PASSWORD=<kata sandi kuat>   APP_ENV=production
docker compose --profile production up -d postgres
docker compose --profile production run --rm migrate       # alembic upgrade head
docker compose --profile production up -d bot
```
Catatan keamanan image: berjalan sebagai user `bot` (uid 10001), filesystem read-only kecuali
`/app/var` dan `/tmp`, `no-new-privileges`, tanpa `.env` di dalam image (hanya `env_file` saat
runtime), health check membaca heartbeat tanpa menyentuh rahasia. `./config` di-mount read-only
sehingga aturan/kalender/watchlist dapat diperbarui tanpa rebuild (restart container).

> Build image belum dijalankan di lingkungan pengembangan ini (tanpa Docker); Dockerfile dan
> Compose diperiksa statis dan dibangun + smoke-test oleh CI GitHub Actions (`ci/github-workflow-ci.yml` (pindahkan ke `.github/workflows/` untuk mengaktifkan; lihat `ci/README.md`)).

### 18. Backup dan restore
- **SQLite**: hentikan bot (`docker compose stop bot`), salin `var/dev.db` (beserta `-wal`/`-shm`
  bila ada) ke lokasi aman, lalu jalankan lagi. Restore = kembalikan berkas saat bot berhenti.
- **PostgreSQL**: `docker compose --profile production exec postgres pg_dump -U bot stock_signal_bot > backup.sql`;
  restore dengan `psql -U bot stock_signal_bot < backup.sql` pada database kosong yang sudah dimigrasi.
- Sertakan `var/exports/` (laporan HTML/WhatsApp) dan `var/backtests/` bila ingin menyimpan jejak audit.
- `.env` **tidak** ikut backup otomatis; simpan di pengelola secret.

### 19. Membuat arsip ZIP distribusi
```bash
uv run python scripts/package_project.py --check   # daftar berkas + pemindaian rahasia
uv run python scripts/package_project.py           # -> dist/stock_signal_bot_<tgl>_<commit>.zip
```
Arsip berisi kode, konfigurasi contoh, dokumentasi, `uv.lock`/`requirements*.txt`, Dockerfile/Compose,
dan `.env.example` — tanpa `.env`, database, log, cache, `.git`, virtualenv, atau metadata internal.
Bila ada pola token/kunci di berkas non-test, pembuatan arsip dibatalkan. Memulai di mesin lain:
ekstrak → `uv sync --all-groups` (atau `pip install -r requirements-dev.txt`) → `cp .env.example .env`
→ `uv run pytest` → ikuti bagian Telegram di atas.

### 20. Troubleshooting umum
| Gejala | Penyebab umum | Tindakan |
|---|---|---|
| `Konfigurasi tidak valid: APP_MODE=live membutuhkan ...` | saklar live belum lengkap | isi `TELEGRAM_BOT_TOKEN`, `TELEGRAM_SIGNAL_CHAT_IDS`, `TELEGRAM_ENABLE_LIVE_SEND=true` |
| `tujuan ... ditolak: tipe chat 'private'` | ID yang ditulis adalah chat pribadi | pakai ID grup/supergroup/channel (negatif); `scripts/test_telegram.py --discover` |
| `bot tidak menjadi anggota` / `channel: bot harus administrator` | bot belum ditambahkan / tanpa hak posting | tambahkan bot ke grup; di channel jadikan admin dengan *post messages* |
| Job pagi `skipped: ... bukan hari perdagangan` | akhir pekan/libur atau kalender belum mencakup tanggal | perbarui `config/trading_calendar.yaml` |
| Semua simbol `missing: histori ... < minimum 250` | data provider pendek | cek jaringan/Yahoo; turunkan `DAILY_MIN_HISTORY_BARS` hanya untuk development |
| Status pengiriman `unknown` | respons Telegram hilang setelah request | cek grup manual, `scripts/reconcile_deliveries.py` (§9) |
| Command admin `Ditolak: hanya diterima di grup admin` | dikirim dari grup sinyal | kirim di `TELEGRAM_ADMIN_CHAT_ID` sebagai user dalam whitelist (bukan anonim) |
| Produksi `blocked: market_rules.yaml belum terverifikasi` | nilai CONTOH | verifikasi aturan resmi, set `meta.verified: true` dengan `source` & `effective_from` |
| Produksi `blocked: gate backtest belum tersedia/tidak lulus` | belum ada backtest OOS yang lulus | `main.py backtest ... --save-gate` setelah kalibrasi; jangan turunkan threshold |
| Container `unhealthy` | heartbeat basi (> 40 menit) | cek log scheduler; pastikan waktu host benar (TZ) |

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
scripts/     test_telegram.py, reconcile_deliveries.py, package_project.py, healthcheck.py
Dockerfile · docker-compose.yml · .dockerignore · ci/github-workflow-ci.yml
tests/       test offline
docs/        verification_required.md
var/         runtime (db, log, cache, ekspor) — gitignored
```

## Disclaimer

Bukan ajakan jual/beli. Analisis bersifat informasional. Keputusan dan risiko sepenuhnya
milik Anda.
