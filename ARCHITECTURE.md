# ARCHITECTURE — Bot Sinyal Saham IDX untuk Grup Telegram

> **Status dokumen:** arsitektur disetujui (Tahap 1); **Tahap 2–7 diimplementasikan** (fondasi &
> data layer, engine & risk, storage + Telegram + scheduler, narator LLM + ekspor WhatsApp,
> backtest & gate, Docker & paket) plus fitur tambahan §26 — lihat §21–§27 untuk keputusan dan
> penyesuaian.
> Semua nilai angka pada dokumen ini (lot, fraksi harga, ARA/ARB, threshold, modal contoh)
> adalah **CONTOH / BELUM TERVERIFIKASI** sampai dilabeli sebaliknya pada file konfigurasi.
>
> Tanggal: 2026-09-23 · Bahasa: Indonesia · Timezone acuan: Asia/Jakarta (WIB)

---

## 0. Ringkasan satu paragraf

Sistem ini adalah layanan Python asinkron yang, pada setiap hari perdagangan BEI,
menjalankan dua job terjadwal (08:30 WIB *pre-market brief*, 15:00 WIB *pre-close
signal*). Job mengambil data pasar dari provider yang dapat diganti, menormalkan
dan memvalidasinya, menjalankan **engine deterministik** (strategi → skor →
risk/sizing), menyimpan *snapshot* yang dapat diaudit, lalu merender pesan dan
mengirimkannya **hanya ke grup/supergroup/channel Telegram dalam allowlist**.
LLM bersifat opsional dan hanya merangkum konteks; ia tidak pernah menghasilkan
angka trading. Default sistem adalah **fail-closed**: bila kalender, kualitas
data, gate backtest, atau tujuan pengiriman tidak valid, sinyal tidak diterbitkan
dan admin diberi alert. Tidak ada eksekusi order, akses dana, atau transaksi
broker.

---

## 1. Tujuan, batasan, dan non-tujuan

### 1.1 Tujuan

| Waktu (WIB) | Laporan | Isi utama |
|---|---|---|
| 08:30 | **Pre-market brief** | EOD lengkap sesi perdagangan *sebelumnya* (bukan sekadar "kemarin"), kondisi global semalam, berita relevan, watchlist + setup dari engine. |
| 15:00 | **Pre-close signal** | Data intraday yang lolos syarat kesegaran, update status sinyal aktif/pending, setup yang memang didukung untuk penutupan/swing overnight. |

"Pre-close" adalah **nama laporan**, bukan klaim bahwa 15:00 WIB adalah sesi
pre-closing resmi BEI.

### 1.2 Batasan yang mengikat desain

- Sinyal ke **grup**, bukan chat pribadi. Tidak ada DM otomatis, tidak ada
  pendaftaran anggota sebagai penerima.
- Tidak ada eksekusi order / akses dana / API broker selain **read-only**.
- Semua rahasia lewat environment; tidak ada token di YAML, log, URL, traceback.
- Kode harus bisa dijalankan dan diuji **tanpa token dan tanpa akses GitHub**.
- Tidak mengarang data pasar, endpoint, aturan bursa, hasil backtest, hasil test,
  atau keberhasilan pengiriman.

### 1.3 Non-tujuan (sengaja tidak dibangun)

- Eksekusi/auto-trading, manajemen portofolio pengguna.
- Watchlist pribadi per anggota (kecuali disetujui kemudian).
- Otomasi WhatsApp (Web scraping / library tidak resmi). Tahap awal hanya
  **ekspor teks manual** untuk Saluran WhatsApp.
- Jaminan *exactly-once* end-to-end antara DB dan Telegram (tidak mungkin secara
  teknis; lihat §11).

---

## 2. Prinsip desain

1. **Deterministik.** Simbol, skor, entry, SL, TP, R:R, dan lot hanya berasal dari
   engine. Input yang sama + konfigurasi yang sama ⇒ output yang sama.
2. **Fail-closed.** Ketidakpastian (kalender tidak mencakup tanggal, data
   *stale/suspect*, gate backtest belum lulus, tujuan belum diverifikasi) ⇒
   tidak ada sinyal; alert ke grup admin atau log.
3. **Konfigurasi bertanda verifikasi.** Setiap aturan bursa/kalender/parameter
   membawa `source`, `effective_from`, `scope`, `verified`. Nilai `verified:
   false` memblokir mode produksi.
4. **Pemisahan tanggung jawab keras.** Engine ↔ LLM ↔ Formatter ↔ Notifier
   berkomunikasi lewat satu objek data (`ReportSnapshot`), bukan string bebas.
5. **Jujur soal status.** Setiap komponen membedakan *tidak didukung*, *tidak
   tersedia*, *gagal*, *nol yang sah*, dan *tidak diketahui (ambigu)*.
6. **Offline-first testing.** Test default memakai fixture sintetis berlabel;
   tidak menyentuh API pasar, LLM, atau Telegram nyata.
7. **Audit penuh.** Setiap sinyal dapat ditelusuri ke provider, timestamp data,
   snapshot bukti, versi strategi, hash konfigurasi, dan histori status.

---

## 3. Peta komponen dan direktori

Root repository **adalah** root proyek (tidak ada `stock_signal_bot/stock_signal_bot/`).
Semua command dijalankan dari root (`python main.py`, `pytest`).

```
.
├── main.py                      # entrypoint: bootstrap settings, DB, bot, scheduler
├── config/
│   ├── settings.py              # pydantic-settings: env → objek Settings tervalidasi
│   ├── watchlist.yaml           # universe awal (global, dikelola admin)
│   ├── market_rules.yaml        # lot, fraksi harga, ARA/ARB, sesi, likuiditas (+metadata verifikasi)
│   └── trading_calendar.yaml    # cakupan tanggal, libur, sesi khusus (+metadata verifikasi)
├── data/                        # PAKET Python: akuisisi & normalisasi data
│   ├── providers/
│   │   ├── base.py              # MarketDataProvider (abstract) + tipe hasil
│   │   ├── yahoo.py             # yfinance (.JK), dibungkus to_thread + semaphore
│   │   ├── goapi.py             # TEMPLATE nonaktif — kontrak belum diverifikasi
│   │   ├── sectors.py           # TEMPLATE nonaktif — kontrak belum diverifikasi
│   │   ├── broker_x.py          # TEMPLATE nonaktif, read-only — kontrak belum diverifikasi
│   │   ├── news.py              # RSS/API configurable, dedup, artikel = data tidak tepercaya
│   │   └── global_macro.py      # indeks global, EIDO, USD/IDR, komoditas (simbol harus diverifikasi)
│   └── aggregator.py            # prioritas, failover, timeout, retry, breaker, rate limit, cache, validasi, cross-validation
├── engine/
│   ├── indicators.py            # pembungkus indikator + warmup eksplisit + test referensi
│   ├── strategies/              # trend_pullback, breakout, reversal, foreign_flow
│   ├── scorer.py                # confidence 0–100, bobot configurable, tie-break deterministik
│   ├── risk.py                  # entry zone, SL, TP1–3, R:R, sizing lot (Decimal), tick/ARA-ARB
│   └── screener.py              # universe = watchlist ∪ hasil screener, filter kualitas & likuiditas
├── ai/
│   ├── llm_client.py            # adapter provider-agnostic (none|openai|anthropic|openai_compatible)
│   └── narrator.py              # prompt, validasi output, fallback template deterministik
├── bot/
│   ├── handlers.py              # command Telegram + otorisasi grup/admin + rate limit
│   ├── formatter.py             # ReportSnapshot → HTML Telegram (escape, split deterministik)
│   └── scheduler.py             # APScheduler AsyncIOScheduler, job pagi/sore, health, alert
├── notifications/
│   ├── base.py                  # Notifier interface + DeliveryResult (sent|failed|unknown)
│   ├── telegram.py              # kirim ke allowlist, verifikasi chat type/izin, redaksi token
│   └── whatsapp_export.py       # ReportSnapshot → teks WhatsApp, simpan lokal
├── storage/
│   ├── models.py                # SQLAlchemy 2.0 (async) — tabel §13
│   ├── repository.py            # klaim job atomik, pencatatan delivery, query performance
│   └── migrations/              # Alembic
├── backtest/
│   ├── runner.py                # memakai engine yang SAMA, tanpa lookahead
│   └── metrics.py               # win rate, expectancy, avg R, PF, MDD dari equity curve
├── scripts/
│   ├── test_telegram.py         # uji koneksi/kirim TEST tanpa engine, default dry-run
│   ├── package_project.py       # ZIP distribusi berbasis allowlist
│   └── reconcile_deliveries.py  # prosedur manual untuk delivery status unknown
├── tests/                       # pytest + pytest-asyncio, fixture sintetis berlabel
├── docs/
│   └── verification_required.md # daftar hal yang wajib diverifikasi sebelum produksi
├── var/                         # RUNTIME (gitignored): db SQLite dev, log, cache, ekspor, gate
├── pyproject.toml · uv.lock · requirements.txt (ekspor untuk pip/Docker)
├── Dockerfile · docker-compose.yml · .dockerignore
├── .env.example · .gitignore
├── ARCHITECTURE.md · README.md
```

Catatan penamaan: paket Python `data/` berisi **kode**; file persisten (database,
log, cache, ekspor WhatsApp) diletakkan di `var/` agar tidak tercampur dengan
paket kode dan mudah di-*mount* sebagai volume Docker. Nama `var/` adalah usulan
(lihat §19).

---

## 4. Alur data

### 4.1 Job pagi (08:30 WIB)

```
Scheduler ──► Preflight
             ├─ kalender mencakup tanggal? hari perdagangan? tidak pause?
             ├─ sesi_data = previous_trading_session(hari ini)   (Jumat→Senin ditangani kalender)
             └─ gate produksi (hanya APP_ENV=production): rules verified, gate backtest, tujuan valid
          ──► Klaim job: INSERT job_runs(job_type='morning', trading_date=hari ini) [unique] → gagal = sudah diklaim
          ──► Aggregator.get_ohlcv(universe, '1d')  ─ paralel terbatas, failover, cache, validasi
             ├─ per simbol: ok | stale | suspect | missing  (suspect/stale/missing = simbol dilewati)
             └─ semua gagal → alert admin, job = failed, TIDAK ada pesan "sinyal kosong"
          ──► GlobalMacro + News (opsional; gagal = "tidak tersedia", tidak mengarang)
          ──► Engine: screener → strategies → scorer → risk  ⇒ SignalCard[] (≤5) + alasan
          ──► Simpan snapshot immutable (job_runs.snapshot_json) + signals + rencana delivery (pending)
          ──► Narrator (opsional): ringkasan ≤150 kata dari JSON engine → validasi → fallback template
          ──► Formatter: HTML Telegram, split deterministik, audit per bagian
          ──► Notifier: kirim ke setiap chat_id allowlist × bagian; catat pending→sending→sent|failed|unknown
          ──► WhatsApp export (opsional): teks dari snapshot yang sama → var/exports/… (gagal ≠ gagal Telegram)
```

### 4.2 Job sore (15:00 WIB)

```
Preflight sama seperti pagi (kalender, pause, gate)
  ──► Klaim job (job_type='afternoon', trading_date=hari ini)
  ──► Aggregator.get_quote / intraday → cek kesegaran: umur data ≤ INTRADAY_MAX_AGE_MIN (CONTOH 20 menit)
        ├─ stale/tidak tersedia → alert admin; tidak ada update/sinyal dari data stale
  ──► Update lifecycle sinyal (pending→active bila entry tersentuh sejak evaluasi terakhir; active→closed TP/SL/expiry)
  ──► Validasi ulang setup pending (masih dalam entry zone? ARA/ARB? masih layak?)
  ──► Setup intraday: HANYA strategi yang secara eksplisit mendukung bar belum lengkap.
        Strategi berbasis close harian (A/B/C) TIDAK dijalankan pada bar 15:00 yang belum lengkap.
        (Opsi "kandidat kondisional" dibahas sebagai keputusan Tahap 3, default nonaktif.)
  ──► Simpan snapshot → format → kirim rekap (alur delivery identik dengan pagi)
```

Larangan eksplisit: tidak mensimulasikan snapshot 15:00 dari OHLCV EOD penuh
(itu *lookahead*); tidak memakai bar belum lengkap untuk strategi yang hanya
mendukung bar lengkap.

### 4.3 Command Telegram

```
Update ──► filter: chat.type ∈ {group, supergroup} dan chat.id ∈ allowlist?  tidak → abaikan (tanpa balasan)
       ──► command anggota: baca dari DB/snapshot terakhir (tidak memicu fetch berat, kecuali /cek dengan cooldown)
       ──► command admin: user_id ∈ TELEGRAM_ADMIN_USER_IDS ∧ chat ∈ allowlist admin ∧ identitas terverifikasi
             (pengirim anonim / sender_chat tanpa user_id → tolak)
       ──► /runnow tetap lewat preflight + klaim job yang sama (tidak bisa mem-bypass kalender, pause, kualitas data, dedup)
```

---

## 5. Pemisahan tanggung jawab dan kontrak data

| Komponen | Boleh | Tidak boleh |
|---|---|---|
| **Engine** | Menentukan simbol, skor, entry/SL/TP, R:R, lot, alasan terstruktur | Mengirim pesan, memanggil LLM, membaca `.env` langsung |
| **Narrator (LLM)** | Merangkum konteks global/berita dan *menjelaskan* hasil engine dalam ≤150 kata | Mengubah/menambah angka, simbol, sinyal; mengubah rekomendasi; klaim kepastian |
| **Formatter** | Merender angka **langsung dari `ReportSnapshot`** ke HTML Telegram / teks WhatsApp | Menghitung ulang apa pun; menyisipkan teks LLM yang gagal validasi |
| **Notifier** | Mengirim ke tujuan allowlist yang sudah diverifikasi; mencatat status delivery | Fallback ke chat pribadi; resend otomatis untuk hasil ambigu |

Kontrak tunggal antar komponen (pydantic model, diserialisasi ke JSON dan disimpan):

```
ReportSnapshot
  report_type: morning | afternoon
  trading_date, data_session_date, generated_at (UTC, tz-aware)
  engine_version, strategy_versions{}, config_hash
  data_quality: { symbols_total, symbols_ok, blocked: [{symbol, reason}], providers_used: [...] }
  global_context: GlobalContext | Unavailable(reason)
  news: [ {source, title, url, published_at} ]              # metadata saja, tanpa isi artikel
  signals: [ SignalCard ]                                    # ≤ 5, sudah lolos risk & validasi harga
  active_updates: [ SignalUpdate ]                           # perubahan status sinyal sebelumnya
  narrative: NarrativeText | None                            # hanya jika lolos validasi
  market_bias: Bias | None                                   # hanya jika dari aturan terdefinisi; None = dihilangkan
  disclaimer: str                                            # konstan

SignalCard
  symbol, strategy_id, strategy_version, confidence (0–100)
  price_basis = raw, entry_low, entry_high, stop_loss, tp1, tp2, tp3, rr_tp1
  sizing: { capital_example, risk_pct, lots, notional } | None
  reasons: [str]  (terstruktur, dari strategi)   evidence: {indikator: nilai}
  data: { provider, bar_time, fetched_at, quality }
```

Semua harga bertipe `Decimal`; pembulatan tick, clamp ARA/ARB, dan R:R dihitung
dan **divalidasi ulang** di `engine/risk.py` sebelum kartu dibuat (§10.3).

---

## 6. Konfigurasi, environment, mode, default aman

### 6.1 Sumbu konfigurasi

- `APP_ENV`: `development` | `production`
- `APP_MODE`: `dry_run` | `live`

`dry_run` menjalankan seluruh pipeline, menulis snapshot ke DB (ditandai
`origin=dry_run`), dan menampilkan pesan yang sudah disanitasi ke log/stdout dan
`var/exports/dry_run/`. Tidak ada panggilan kirim.

### 6.2 Variabel (nama final di Tahap 2; nilai = default aman)

| Variabel | Default | Catatan |
|---|---|---|
| `APP_ENV` | `development` | |
| `APP_MODE` | `dry_run` | |
| `APP_TIMEZONE` | `Asia/Jakarta` | |
| `DATABASE_URL` | `sqlite+aiosqlite:///var/dev.db` | prod: `postgresql+asyncpg://…` |
| `TELEGRAM_BOT_TOKEN` | kosong | wajib hanya untuk polling/live/test kirim |
| `TELEGRAM_ENABLE_LIVE_SEND` | `false` | saklar kedua di samping `APP_MODE=live` |
| `TELEGRAM_SIGNAL_CHAT_IDS` | kosong | grup/supergroup/channel tujuan, dipisah koma; opsional `id:thread_id` untuk topik forum |
| `TELEGRAM_ADMIN_CHAT_ID` | kosong | grup operasional admin; kosong ⇒ alert hanya ke log, **tidak** ke chat pribadi |
| `TELEGRAM_ADMIN_USER_IDS` | kosong | whitelist user_id admin |
| `LLM_PROVIDER` | `none` | `none` \| `openai` \| `anthropic` \| `openai_compatible` (layanan lokal) |
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | kosong | |
| `PROVIDER_PRIORITY` | `yahoo` | urutan failover; provider lain nonaktif sampai kontraknya diverifikasi |
| `PRODUCTION_SINGLE_PROVIDER_APPROVED` | `false` | produksi dengan satu provider (tanpa cross-validation) butuh persetujuan eksplisit |
| `WHATSAPP_EXPORT_ENABLED` | `false` | ekspor teks manual saja |
| `SCHEDULE_MORNING` / `SCHEDULE_AFTERNOON` | `08:30` / `15:00` | WIB |
| `SIZING_CAPITAL_EXAMPLE` / `SIZING_RISK_PCT` | CONTOH `100000000` / `1.0` | dikonfirmasi Tahap 3 |

Pydantic memvalidasi tipe, rentang, dan **kombinasi**:

- `APP_MODE=live` ⇒ wajib token, ≥1 `TELEGRAM_SIGNAL_CHAT_IDS`, `TELEGRAM_ENABLE_LIVE_SEND=true`.
- `APP_ENV=production` ⇒ `DATABASE_URL` bukan SQLite (peringatan keras, dapat di-override eksplisit).
- Provider berbayar `*_ENABLED=true` tanpa kunci ⇒ error konfigurasi yang jelas.

### 6.3 Matriks gate penerbitan sinyal (produksi)

Sinyal dikirim **hanya jika semua** benar; jika tidak, job dicatat `blocked`
dengan alasan dan admin diberi alert:

1. `APP_MODE=live` dan `TELEGRAM_ENABLE_LIVE_SEND=true`.
2. Tujuan ∈ allowlist, tipe chat terverifikasi via API (bukan hanya format ID), bot anggota dengan izin kirim/posting.
3. `trading_calendar.yaml` mencakup tanggal dan `verified: true`; tanggal adalah hari perdagangan.
4. `market_rules.yaml` `verified: true` untuk papan/instrumen yang dipakai.
5. Kualitas data `ok`: cross-validation ≥2 provider lolos, **atau** `PRODUCTION_SINGLE_PROVIDER_APPROVED=true` secara eksplisit.
6. Setiap strategi yang menghasilkan sinyal memiliki catatan gate backtest **lulus** yang terikat pada `strategy_version` + `config_hash` saat ini.
7. Tidak dalam status pause (persisten di DB).
8. Job belum diklaim/dikirim untuk `(job_type, trading_date)`.

Di `development`, gate 3–6 menghasilkan **peringatan**, bukan blokir, agar
dry-run tetap bisa dijalankan dengan fixture. `scripts/test_telegram.py` hanya
tunduk pada gate 1–2 (tanpa engine).

---

## 7. Telegram: grup, bukan pribadi

### 7.1 Kebijakan tujuan

- Semua pengiriman (sinyal, hasil command, alert) hanya ke chat dalam allowlist
  (`TELEGRAM_SIGNAL_CHAT_IDS`, `TELEGRAM_ADMIN_CHAT_ID`).
- Sebelum pengiriman pertama per proses (dan di-cache dengan TTL), Notifier
  memverifikasi via API: `getChat` ⇒ `type ∈ {group, supergroup, channel}`;
  `getChatMember(chat, bot)` ⇒ bot anggota/admin; untuk channel ⇒ status admin
  dengan hak posting. `type == private` **ditolak di semua jalur** (job, command,
  alert, script test).
- Update dari chat `private` diabaikan tanpa balasan. Tidak ada pendaftaran DM.
- Privacy mode bot **tidak** perlu dimatikan: command `/…` di grup tetap
  diterima. Bot tidak membaca/menyimpan percakapan grup selain command yang
  ditujukan padanya.
- Grup admin tidak dikonfigurasi ⇒ alert ke log terstruktur saja.

### 7.2 Otorisasi

| Peran | Syarat |
|---|---|
| Anggota | Pesan berasal dari chat ∈ allowlist sinyal/admin |
| Admin | `from.id ∈ TELEGRAM_ADMIN_USER_IDS` **dan** chat ∈ allowlist **dan** identitas pengguna tersedia (pengirim anonim yang hanya membawa `sender_chat` ditolak). Status admin Telegram saja **tidak** cukup. |

Command anggota: `/start /help /disclaimer /signal /status /performance [7d|30d|all] /cek KODE /watchlist list`.
Command admin: `/watchlist add|remove KODE /broadcast /pause /resume /runnow morning|afternoon /health`.

`/cek` dan `/performance` memakai cooldown per chat (CONTOH 60 detik) dan tetap
tunduk pada validasi data; `/cek` tidak pernah menerbitkan sinyal produksi.

### 7.3 `scripts/test_telegram.py`

Berjalan tanpa engine dan tanpa mengambil alih polling/webhook. Default
**dry-run** (hanya verifikasi `getMe`, `getChat`, `getChatMember`). `--send`
adalah persetujuan eksplisit untuk satu pesan uji; jika allowlist berisi >1
tujuan, `--chat-id` wajib. Pesan tetap:

> TEST — BUKAN SINYAL TRADING.
> Uji pengiriman bot saham IDX ke grup berhasil.

Pelaporan membedakan **ditolak** (validasi lokal/API menolak), **gagal sebelum
dikirim**, **sukses** (hanya bila API mengembalikan `message_id`), dan
**ambigu** (timeout/koneksi putus setelah request dikirim — tidak diulang
otomatis). Token diredaksi dari log dan URL exception.

---

## 8. Data layer

### 8.1 Kontrak provider

```python
class MarketDataProvider(ABC):
    name: str
    capabilities: set[Capability]        # OHLCV_DAILY, OHLCV_INTRADAY, QUOTE, FOREIGN_FLOW, BROKER_SUMMARY
    async def get_ohlcv(symbol, timeframe, start, end) -> ProviderResult[OHLCVFrame]
    async def get_quote(symbol) -> ProviderResult[Quote]
    async def get_foreign_flow(symbol, date) -> ProviderResult[ForeignFlow]
    async def get_broker_summary(symbol, date) -> ProviderResult[BrokerSummary]
    async def health_check() -> bool
```

`ProviderResult[T]` adalah *tagged union*: `Ok(value, meta)` |
`Unsupported(reason)` | `Unavailable(reason)` | `Failed(error, retryable)`.
Nilai nol yang sah berada **di dalam** `Ok`, sehingga "net foreign = 0" tidak
pernah tertukar dengan "data tidak ada".

### 8.2 Normalisasi (`OHLCVFrame`)

- Index/timestamp **timezone-aware** (disimpan UTC, ditampilkan WIB); kolom
  `open, high, low, close, volume, value` (nilai transaksi bila tersedia).
- Metadata: `symbol` (kanonik tanpa suffix; `.JK` hanya di adapter Yahoo),
  `timeframe`, `provider`, `market_time` (waktu bar), `fetched_at`,
  `is_complete` per bar, `price_basis: raw | adjusted`, `quality`,
  `corporate_actions` bila tersedia.
- Seri **raw** dipakai untuk aturan perdagangan (tick, ARA/ARB, entry/SL/TP);
  seri **adjusted** hanya untuk analisis historis. Keduanya adalah objek terpisah;
  `risk.py` menolak frame dengan `price_basis != raw`.

### 8.3 Status kualitas

`ok` · `degraded` (satu provider, tanpa cross-validation — hanya diizinkan di
development atau dengan persetujuan eksplisit) · `stale` (bar/quote lebih tua dari
syarat kesegaran) · `suspect` (selisih antar-provider > threshold) · `missing` ·
`unsupported`. Hanya `ok`/`degraded`(sesuai kebijakan) yang masuk engine.

### 8.4 Aggregator

- Prioritas dari `PROVIDER_PRIORITY`; failover per permintaan; **timeout** per
  panggilan; **retry** terbatas dengan exponential backoff (tenacity) hanya
  untuk error `retryable`; **circuit breaker** per provider (buka setelah N
  gagal berturut, half-open setelah cooldown); **rate limiter** token-bucket per
  provider; **TTL cache** dengan kunci `(provider, symbol, timeframe, start, end,
  price_basis)`.
- I/O sinkron (yfinance) dijalankan lewat `asyncio.to_thread` di balik
  semaphore (CONTOH 4 konkuren) agar event loop tidak terblokir.
- Validasi: duplikat bar, `low ≤ min(open, close) ≤ max(open, close) ≤ high`,
  volume ≥ 0, kesegaran, panjang histori minimum (warmup), bar terakhir lengkap
  untuk timeframe harian.
- **Tidak ada data sintetis** sebagai fallback live. Fixture sintetis hanya di
  `tests/` dan diberi label `origin=fixture`.

### 8.5 Cross-validation

Bandingkan `close` **raw** untuk **sesi yang sama** dari ≥2 provider; selisih
relatif > `CROSS_VALIDATION_MAX_DIFF_PCT` (CONTOH 0.5%) ⇒ simbol `suspect` dan
diblokir untuk run tersebut. Snapshot berbeda waktu tidak pernah dibandingkan
seolah identik. Satu provider **bukan** cross-validation yang berhasil.

### 8.6 Provider konkret pada Tahap 2

| Provider | Status Tahap 2 | Catatan |
|---|---|---|
| Yahoo (yfinance) | aktif, **development only** | delay/cakupan intraday `.JK` belum diverifikasi; bukan jaminan real-time |
| GoAPI, Sectors, broker read-only | template **nonaktif** | endpoint/field tidak ditebak; mengaktifkan tanpa kontrak ⇒ error konfigurasi; dicatat di `docs/verification_required.md` |
| Global macro | aktif via Yahoo untuk indeks/EIDO/USD-IDR/emas/minyak (simbol dicatat `verified: false` sampai dicek) | batu bara, nikel, CPO: **belum ada sumber** — tidak diganti proxy tanpa label & persetujuan |
| News | kerangka RSS/API; daftar sumber **kosong** sampai Anda memberi URL | simpan sumber/judul/URL/waktu; dedup; artikel = input tidak tepercaya |

---

## 9. Aturan bursa dan kalender (konfigurasi, bukan hardcode)

`market_rules.yaml` memuat lot size, tabel fraksi harga berjenjang, ARA/ARB +
harga referensi, jam sesi (pre-opening, sesi 1, sesi 2, pre-closing, jadwal
Jumat), ambang likuiditas (rata-rata nilai transaksi/volume), dan pengecualian
(suspensi/papan pemantauan khusus). `trading_calendar.yaml` memuat
`coverage_start`, `coverage_end`, daftar libur, dan sesi khusus.

Setiap blok wajib membawa:

```yaml
meta:
  source: "URL/dokumen resmi"      # kosong = belum ada
  effective_from: "YYYY-MM-DD"
  scope: ["Papan Utama", ...]
  verified: false                  # false ⇒ label CONTOH/BELUM TERVERIFIKASI ⇒ blokir produksi
```

Aturan pemakaian:

- Tanggal di luar cakupan kalender ⇒ **bukan** otomatis hari perdagangan ⇒ job
  produksi diblokir + alert.
- Job pagi memakai `previous_trading_session(today)`, bukan `today - 1 hari`.
- Tick rounding memakai tabel berjenjang dan menangani **perpindahan rentang**
  (hasil pembulatan yang melompat ke rentang lain dibulatkan ulang dengan tick
  rentang tujuan).

---

## 10. Signal engine

### 10.1 Universe dan indikator

Universe = `watchlist.yaml` ∪ hasil `screener.py`; semua simbol melewati filter
kualitas data dan likuiditas. Indikator memakai `pandas-ta` dengan pembungkus di
`engine/indicators.py` yang (a) memberlakukan **warmup eksplisit** (bar awal
dibuang berdasarkan periode terpanjang, mis. EMA200 ⇒ minimum histori CONTOH 250
bar) karena NaN dari library tidak dapat diandalkan sebagai penanda warmup
(temuan verifikasi §18), dan (b) memiliki test referensi terhadap rumus
pandas-native agar penggantian library di masa depan terdeteksi.

### 10.2 Strategi (masing-masing menghasilkan `StrategySignal | None`)

| ID | Syarat inti | Kebutuhan data |
|---|---|---|
| `trend_pullback` v1 | close > EMA50 > EMA200 (atau close di atas keduanya), pullback ke EMA20/support, RSI 40–55 berbalik naik | OHLCV harian lengkap |
| `breakout` v1 | close > resistance N-hari **sebelum** candle sinyal; volume > 1.5× rata-rata 20 hari **sebelum** candle sinyal | OHLCV harian lengkap |
| `reversal` v1 | RSI oversold; bullish divergence dengan algoritme pivot eksplisit (pivot hanya sah setelah bar konfirmasinya ada); konfirmasi candle di support | OHLCV harian lengkap |
| `foreign_flow` v1 | net foreign buy beruntun; akumulasi broker dominan bila tersedia | foreign flow / broker summary — **nonaktif** bila `Unsupported/Unavailable` |

Output strategi: skor 0–100, alasan terstruktur, bukti indikator, versi
strategi, dan daftar syarat data yang dipakai. Tidak ada akses ke bar setelah
waktu evaluasi (kontrak yang sama dipakai backtest).

### 10.3 Scorer — usulan formula (dikonfirmasi Tahap 3)

```
primary      = strategi dengan skor tertinggi (skor dasar S_p)
confluence   = Σ_{i ≠ p, S_i ≥ 60} w_i · 10, dibatasi maks +15      # strategi tak tersedia ⇒ 0, bukan redistribusi bobot
penalty      = 10 jika quality == degraded, 0 jika ok
confidence   = clamp(S_p + confluence − penalty, 0, 100)
```

- Threshold default **70**; maksimum **5** sinyal/sesi.
- Tie-break deterministik: confidence ↓ → R:R TP1 ↓ → rata-rata nilai transaksi
  20 hari ↓ → simbol A–Z.
- Data hilang tidak pernah menaikkan confidence; threshold tidak diturunkan agar
  contoh "menghasilkan sinyal". Confidence adalah skor engine, bukan probabilitas
  untung.

### 10.4 Risk — usulan kebijakan (dikonfirmasi Tahap 3)

- Entry zone `[entry_low, entry_high]` dari struktur (mis. EMA20/support sampai
  close); SL = min(struktur, close − k·ATR14) dengan k CONTOH 1.5.
- R = `entry_high − SL` (**entry paling konservatif**). TP1 = entry_high + 2R,
  TP2 = +3R, TP3 = +4R (atau resistance struktur bila lebih dekat, dengan syarat
  minimum tetap terpenuhi).
- Syarat minimum **R:R TP1 ≥ 2.0** dihitung **setelah** tick rounding dan clamp
  ARA/ARB, dengan biaya beli/jual configurable diperhitungkan.
- Urutan wajib setelah rounding/clamp: `SL < entry_low ≤ entry_high < TP1 ≤ TP2 ≤ TP3`;
  gagal ⇒ setup **dibuang**, tidak dipaksakan.
- Sizing: `risk_amount = capital × risk_pct`; `shares = floor(risk_amount / R)`;
  `lots = floor(shares / lot_size)`; dibatasi `floor(capital_available / (entry_high × lot_size))`.
  `lots == 0` ⇒ setup tidak dapat direkomendasikan untuk modal tersebut (kartu
  tetap boleh tampil tanpa sizing hanya jika kebijakan mengizinkan — default: tampil dengan catatan "0 lot").
- Semua perhitungan `Decimal`; tidak ada float pada harga.

### 10.5 Lifecycle sinyal (simulasi, bukan transaksi pengguna)

`pending_entry` → `active` (harga menyentuh zona entry setelah publikasi) →
`partial` (opsional, sesuai kebijakan exit) → `closed_tp | closed_sl | expired`.
TP/SL tidak dievaluasi sebelum entry. Masa berlaku entry (CONTOH 3 sesi), gap
handling (gap melewati SL ⇒ keluar di open, bukan di SL), dan kebijakan partial
exit ditetapkan di Tahap 3 dan dipakai identik oleh backtest.

---

## 11. Scheduler dan reliabilitas

- `APScheduler 3.x AsyncIOScheduler` berjalan di event loop yang sama dengan
  `python-telegram-bot` (lifecycle manual: `Application.initialize/start`,
  polling; scheduler start di `post_init`) agar shutdown tertib.
- **Klaim job atomik** lewat DB: `job_runs` dengan unique `(job_type,
  trading_date)`; `INSERT` dalam transaksi; `IntegrityError` ⇒ sudah diklaim.
  Berlaku sama untuk scheduler dan `/runnow`. Aman terhadap dua proses/restart.
- **Dedup delivery** per `(job_run_id, chat_id, thread_id, part_index)` dengan
  status `pending → sending → sent | failed | unknown`:
  - `sent` hanya setelah API mengembalikan `message_id`.
  - error definitif (chat tidak ditemukan, bot dikeluarkan, HTML invalid) ⇒ `failed`.
  - timeout/koneksi putus **setelah** request terkirim, atau proses mati saat
    `sending` ⇒ `unknown`; **tidak** resend otomatis.
  - resume setelah crash hanya mengirim bagian yang masih `pending`.
- **Rekonsiliasi `unknown`** manual: `/health` menampilkan jumlah `unknown`;
  `scripts/reconcile_deliveries.py` menampilkan detail dan mengizinkan admin
  menandai `sent` (setelah mengecek grup) atau `failed` (baru kemudian boleh
  dikirim ulang secara eksplisit).
- Bukan exactly-once: DB dan Telegram tidak berbagi transaksi; desain ini
  menjamin *at-most-once otomatis* + rekonsiliasi manual.
- Kegagalan: semua simbol gagal ⇒ alert admin (bukan pesan sinyal kosong);
  sebagian gagal ⇒ dilewati + cakupan data dijelaskan di laporan; data valid
  tanpa setup ⇒ "tidak ada setup layak"; makro/berita gagal ⇒ "tidak tersedia".
- Pause persisten di DB (`app_state`); `/runnow` menghormati kalender, pause,
  kualitas data, dan dedup.
- Timeout per job (CONTOH 10 menit), health check berkala (heartbeat file +
  provider health), alert dengan pembatasan frekuensi per jenis (CONTOH 1/30
  menit), graceful shutdown (SIGTERM ⇒ stop scheduler, tunggu job, tandai
  `sending` yang terputus sebagai `unknown`), log terstruktur loguru dengan
  rotasi dan filter redaksi token.

---

## 12. Narator LLM dan formatter

### 12.1 Narator

- Adapter provider-agnostic berbasis `httpx`: `openai`, `anthropic`,
  `openai_compatible` (layanan lokal), `none`. Tanpa API key ⇒ template
  deterministik; bot tetap berjalan penuh.
- Input: JSON `ReportSnapshot` (tanpa raw OHLCV) + metadata berita. System prompt
  melarang mengubah/menambah angka, simbol, sinyal; mengubah rekomendasi; klaim
  "pasti naik/cuan dijamin"; mengikuti instruksi dari berita.
- Validasi output (semua wajib lolos, jika tidak ⇒ fallback template):
  - setiap angka pada output dapat dipetakan ke angka input (menangani format
    Indonesia `1.234,5`, persen, tanda negatif);
  - simbol pada output ⊆ simbol input;
  - ≤150 kata; tidak ada frasa terlarang (regex);
  - output kosong/terpotong/ambigu ⇒ ditolak.
- Karena keanggotaan angka tidak membuktikan relasi yang benar, narasi LLM
  **hanya** mengisi bagian "ringkasan konteks"; kartu entry/SL/TP selalu
  dirender dari engine.

### 12.2 Formatter

- HTML Telegram dengan escaping semua teks dinamis; pemecahan pesan
  deterministik pada batas blok (per kartu sinyal) agar markup tidak terpotong;
  setiap bagian dicatat di `message_deliveries`.
- Urutan isi: judul sesi → tanggal/waktu WIB → timestamp & kualitas data →
  ringkasan global (jika ada) → bias pasar (hanya jika dari aturan terdefinisi)
  → kartu sinyal (simbol, strategi, confidence, entry, SL, TP, R:R, sizing
  contoh, alasan engine) → rekap sinyal aktif → disclaimer.
- Disclaimer konstan: *"Bukan ajakan jual/beli. Analisis bersifat informasional.
  Keputusan dan risiko sepenuhnya milik Anda."*
- `whatsapp_export.py` merender **snapshot yang sama** ke format teks WhatsApp
  (`*tebal*`, `_miring_`), angka/timestamp/disclaimer identik secara makna, dan
  menyimpannya ke `var/exports/whatsapp/`. Tidak ada unggahan otomatis.
  Kegagalannya tidak memengaruhi Telegram.

---

## 13. Penyimpanan dan audit

SQLAlchemy 2.0 async + Alembic. Development: SQLite (`aiosqlite`, WAL).
Production: PostgreSQL (`asyncpg`). Harga `Numeric` ↔ `Decimal`; waktu UTC
tz-aware; payload bukti sebagai JSON.

| Tabel | Isi kunci |
|---|---|
| `job_runs` | `job_type`, `trading_date`, `data_session_date`, `status` (claimed/running/blocked/failed/completed), `attempt`, `snapshot_json` (immutable), `config_hash`, `engine_version`, `origin` (live/dry_run/backtest/fixture) — **unique** `(job_type, trading_date, origin)` |
| `signals` | ref `job_run`, simbol, strategi+versi, confidence, harga (Decimal), sizing, alasan/bukti JSON, provider & timestamp data, `status`, `origin`, `app_env` |
| `signal_updates` | histori perubahan status per sinyal + harga pemicu + waktu data |
| `subscribers` | **tujuan grup/channel yang diotorisasi** (chat_id, tipe terverifikasi, thread_id, aktif) — bukan daftar anggota |
| `watchlist` | simbol global, ditambah/dihapus admin, dengan audit siapa/kapan |
| `message_deliveries` | ref `job_run`; per tujuan × bagian: status, `message_id`, error teredaksi, waktu |
| `provider_health` | hasil health check, breaker state, latensi |
| `app_state` | pause, versi skema aplikasi, catatan gate backtest yang berlaku |

Snapshot historis **tidak** ditimpa saat konfigurasi berubah (hash konfigurasi
baru ⇒ baris baru). Statistik `/performance` hanya memakai `origin=live`.
Test perilaku kritis (klaim job, dedup delivery, tipe Decimal/waktu) dijalankan
pada SQLite dan, bila `TEST_POSTGRES_URL` tersedia, pada PostgreSQL.

---

## 14. Backtest dan gate produksi

- `backtest/runner.py` memanggil **modul engine yang sama** dengan produksi,
  memberi data hanya sampai waktu evaluasi (tanpa lookahead); keputusan berbasis
  close berlaku mulai bar berikutnya (tidak diisi retroaktif pada close yang sama).
- Model eksekusi: fee beli/jual dan slippage configurable; gap eksplisit (gap
  melewati level ⇒ eksekusi di open); jika urutan sentuhan entry/TP/SL dalam satu
  bar tidak diketahui ⇒ aturan konservatif terdokumentasi (SL dianggap lebih
  dulu); level tersentuh ≠ order pasti terisi (dicatat sebagai keterbatasan
  likuiditas/antrean); batas modal dan lot penuh; partial TP identik dengan live.
- Corporate action dan survivorship bias: ditangani bila data tersedia, jika
  tidak dinyatakan sebagai keterbatasan pada laporan.
- Strategi intraday/flow **tidak** diluluskan dari data EOD saja.
- Metrik: jumlah trade, win rate, expectancy, average R, profit factor,
  **maximum drawdown dari equity curve** (bukan penjumlahan menang/kalah),
  equity curve CSV, periode/universe/sumber/versi konfigurasi. Definisi kasus
  tepi: 0 trade ⇒ metrik `None` + gate gagal; tidak ada rugi ⇒ PF `inf` dilabeli;
  trade belum selesai ⇒ dikeluarkan dan dilaporkan jumlahnya.
- **Gate produksi**: threshold configurable dan disepakati; minimum jumlah
  trade; evaluasi out-of-sample terpisah; hasil terikat `strategy_version` +
  `config_hash`; perubahan material membatalkan kelayakan; **tanpa backtest
  valid ⇒ tidak boleh menerbitkan sinyal produksi**. Hasil backtest bukan jaminan
  hasil masa depan.

---

## 15. Keamanan

- Rahasia hanya dari environment/`.env`; `.env`, `var/`, database, log, cache,
  `.hoplite/`, `.context/` masuk `.gitignore` dan `.dockerignore`; ZIP memakai
  allowlist.
- Redaksi token: filter loguru + pembungkus exception yang menghapus pola
  `bot<digit>:<token>` dari pesan/URL sebelum dicatat; `httpx` event hook
  menyanitasi URL pada error.
- Tujuan `private` ditolak di semua jalur; tidak menyimpan pesan pribadi atau
  daftar anggota.
- News dan response provider diperlakukan sebagai input tidak tepercaya (tidak
  pernah masuk system prompt; hanya metadata yang dirender, dengan escaping).
- API broker read-only; tidak ada kode eksekusi order sama sekali.
- Docker non-root, tanpa `.env` di image, health check tanpa rahasia.

---

## 16. Deployment dan paket

- `Dockerfile` multi-stage berbasis `python:3.12-slim`, dependency dari lockfile,
  user non-root, `HEALTHCHECK` memeriksa heartbeat file, `STOPSIGNAL SIGTERM`.
- `docker-compose.yml`: service `bot` (volume `./var:/app/var`, `env_file: .env`,
  `restart: unless-stopped`); profil `production` menambah `postgres` dengan
  volume persisten. Development memakai SQLite di volume.
- `scripts/package_project.py`: allowlist source/config contoh/dokumentasi;
  mengecualikan `.env`, `.git`, database, log, cache, virtualenv, lampiran
  pribadi, metadata platform; tidak mencetak isi rahasia.
- Sandbox pengerjaan saat ini **tidak memiliki Docker**; Dockerfile/Compose akan
  ditulis dan diperiksa secara statis, dan dilaporkan sebagai *belum diverifikasi
  build* kecuali lingkungan dengan Docker tersedia.

---

## 17. Strategi pengujian

Semua test default: fixture sintetis berlabel, tanpa token, tanpa API live,
tanpa Telegram nyata. Cakupan wajib (dipetakan ke tahap):

| Area | Contoh test | Tahap |
|---|---|---|
| Data layer | failover, cache key, timeout, rate limiter, breaker, stale/suspect/missing, raw vs adjusted tidak tercampur | 2 |
| Kalender/aturan | weekend, libur, di luar cakupan ⇒ blokir; sesi sebelumnya lintas Jumat/libur | 2 |
| Risk | tick rounding di batas rentang, clamp ARA/ARB, urutan harga & R:R setelah rounding, sizing & 0 lot | 3 |
| Strategi | deterministik pada fixture; tidak ada akses future bar; warmup eksplisit | 3 |
| Storage/scheduler | klaim job konkuren & restart, dedup delivery, `unknown` tidak resend, pause persisten | 4 |
| Telegram | tolak private di semua jalur, otorisasi admin (termasuk anonim), HTML valid & split | 4 |
| Narator | output invalid ⇒ fallback; angka/simbol asing ditolak | 5 |
| Backtest | tanpa lookahead; metrik kasus tepi; gate | 6 |
| Keamanan | token tidak muncul di log/exception; pemisahan origin | 2–7 |

---

## 18. Fakta terverifikasi hari ini vs asumsi

**Terverifikasi (dijalankan 2026-09-23 di sandbox, Python 3.12.3, `uv 0.9.28`):**

- Resolusi dependency untuk Python 3.12 berhasil dengan versi: `python-telegram-bot 22.8`,
  `APScheduler 3.11.3`, `pandas 3.0.6`, `numpy 2.2.6`, `pandas-ta 0.4.71b0`,
  `SQLAlchemy 2.0.54`, `aiosqlite 0.22.1`, `asyncpg 0.31.0`, `alembic 1.20.0`,
  `httpx 0.28.1`, `tenacity 9.1.4`, `pydantic-settings 2.15.0`, `loguru 0.7.3`,
  `pytest 9.1.1`, `pytest-asyncio 1.4.0`, `yfinance 1.7.0`. Penguncian final
  dilakukan di Tahap 2 setelah cek dokumentasi resmi.
- `pandas-ta 0.4.71b0` berhasil diimpor dan menghitung EMA/RSI/ATR bersama
  `numpy 2.2.6` + `pandas 3.0.6`; EMA(20) cocok dengan `pandas.ewm(span=20,
  adjust=False)` pada 100 bar terakhir seri uji (selisih maks ≈ 6e-8). Ada peringatan deprecation
  `mode.copy_on_write` dari pandas-ta — tidak fatal.
- RSI(14) versi tersebut hanya menghasilkan **1 NaN** di awal seri ⇒ warmup
  **wajib** dipaksakan oleh engine (§10.1), bukan diandalkan dari library.
- Sandbox: tidak ada Docker; `gh`/`git` tersedia; repository berisi satu file
  placeholder `RWYJ` dan belum ada AGENTS.md/CLAUDE.md.

**Asumsi / belum diverifikasi (akan dicatat di `docs/verification_required.md`):**

- Cakupan, delay, dan ketentuan penggunaan data Yahoo Finance untuk saham `.JK`
  (harian dan intraday), termasuk apakah data 15:00 WIB memenuhi syarat kesegaran.
- Simbol/satuan Yahoo untuk indeks global, EIDO, USD/IDR, emas, minyak; sumber
  untuk batu bara, nikel, CPO **belum ada**.
- Aturan BEI terbaru (lot, fraksi harga, ARA/ARB, jam sesi, papan pemantauan
  khusus) dan kalender libur — seluruhnya akan berlabel CONTOH/BELUM TERVERIFIKASI.
- Perilaku detail Telegram Bot API (batas panjang pesan, hak posting channel,
  pengirim anonim) akan dicek ulang terhadap dokumentasi resmi pada Tahap 4.
- Kontrak GoAPI/Sectors/broker: tidak diketahui ⇒ adapter nonaktif.
- Parameter risiko, biaya, threshold backtest: menunggu keputusan Anda (Tahap 3/6).

---

## 19. Keputusan yang dibutuhkan sebelum Tahap 2

Hanya keputusan yang memengaruhi fondasi/data layer. Bila tidak ada jawaban,
saya **tidak** melanjutkan ke Tahap 2.

| # | Keputusan | Usulan default |
|---|---|---|
| 1 | Tata letak: paket Python datar di root (`config/`, `data/`, `engine/`, …) sesuai spesifikasi, plus direktori runtime **`var/`** (gitignored) untuk DB/log/cache/ekspor | Setuju dengan `var/` |
| 2 | Manajemen dependency: `pyproject.toml` + **`uv.lock`** sebagai sumber kebenaran, ditambah `requirements.txt` hasil ekspor untuk pengguna pip/Docker; Python dipatok `>=3.12,<3.13` | Setuju |
| 3 | `pandas-ta` **dipertahankan** (terverifikasi kompatibel) dan dipatok `0.4.71b0` dengan test referensi; tidak ada penggantian library wajib | Setuju |
| 4 | Lingkup provider Tahap 2: Yahoo aktif untuk development; GoAPI/Sectors/broker sebagai template nonaktif; produksi satu-provider **tetap diblokir** sampai persetujuan eksplisit terpisah | Setuju |
| 5 | Sumber berita RSS: apakah Anda punya daftar URL yang boleh dipakai? Jika tidak, Tahap 2 mengirim daftar **kosong** + struktur contoh (tanpa menebak endpoint) | Daftar kosong |
| 6 | File placeholder `RWYJ` di root: boleh dihapus pada Tahap 2? | Hapus |

Keputusan Tahap 3 yang akan ditanyakan nanti (tidak perlu dijawab sekarang,
tetapi boleh bila sudah ada preferensi): definisi R:R & biaya (§10.4), kebijakan
exit/partial/expiry (§10.5), modal contoh & risiko per transaksi, dan apakah
laporan sore boleh memuat "kandidat kondisional" berlabel dari bar belum lengkap
(default: **tidak**).

---

## 20. Rencana tahap

| Tahap | Keluaran | Bukti yang dilaporkan |
|---|---|---|
| 1 | `ARCHITECTURE.md` (dokumen ini) | — |
| 2 | `pyproject.toml`/lock, `config/settings.py`, YAML contoh berlabel, provider base + Yahoo + template, aggregator, `.env.example`, `.gitignore`, test data layer | `pytest` lulus offline; instalasi dari lock tanpa token |
| 3 | indikator, 4 strategi, scorer, screener, risk | test deterministik & batas harga |
| 4 | storage + migrasi, formatter, handlers, scheduler, notifier Telegram, `test_telegram.py`, dry-run end-to-end | test konkurensi/dedup; log dry-run tersanitasi |
| 5 | narator + validasi + fallback, ekspor WhatsApp | test seluruh jalur |
| 6 | backtest runner, metrik, out-of-sample, gate | metrik dari fixture berlabel (bukan klaim performa) |
| 7 | Docker/Compose, README, pemeriksaan keamanan, ZIP | verifikasi yang **benar-benar** dijalankan, sisanya ditandai belum |

Setiap akhir tahap: daftar file/fitur selesai, pemeriksaan yang dijalankan dan
hasilnya, apa yang belum tersedia, konfigurasi/persetujuan yang dibutuhkan
berikutnya. "Implementasi selesai" selalu dipisahkan dari "integrasi live
terverifikasi".

---

## 21. Catatan implementasi Tahap 2 (2026-09-23)

Keputusan §19 diambil dengan default: tata letak datar + `var/`; `pyproject.toml` +
`uv.lock` (+ `requirements*.txt` ekspor); `pandas-ta==0.4.71b0` dipertahankan; Yahoo aktif
untuk development, GoAPI/Sectors/broker template nonaktif; daftar berita kosong; `RWYJ`
dihapus.

Penyesuaian terhadap rancangan awal:

- Ditambahkan paket pendukung `core/` (timeutil, redaction, logging) dan modul
  `data/resilience.py`, `data/validation.py` agar `aggregator.py` tetap ringkas.
- Berkas konfigurasi tambahan: `config/news_sources.yaml`, `config/global_macro.yaml`
  (sumber berita dan instrumen makro juga membawa `meta.verified`).
- Dependency tambahan kecil: `PyYAML` (baca YAML), `defusedxml` (parser RSS/Atom aman untuk
  input tidak tepercaya), `tzdata` (zona waktu di image minimal). Tidak ada penggantian
  library wajib. `numpy` dibatasi `<2.3` karena `pandas-ta` mewajibkan `numba==0.61.2`.
- Quote antar-provider hanya dibandingkan bila `market_time` identik; jika berbeda, status
  `degraded` dengan catatan — konsisten dengan larangan membandingkan snapshot berbeda waktu.
- Adapter Yahoo memakai `period` default eksplisit per timeframe (harian `2y`) saat
  `start/end` kosong, karena default yfinance (`1mo`) terlalu pendek untuk warmup EMA200 —
  ditemukan lewat uji `fetch` nyata.
- `AggregatedOHLCV.usable` memperhitungkan `require_cross_validation`: di produksi tanpa
  persetujuan satu-provider, hasil `degraded` tidak dapat dipakai.

Bukti yang benar-benar dijalankan: `pytest` (131 test offline lulus), `ruff check/format`,
`python main.py config` tanpa token, dan satu `python main.py fetch --symbol BBCA` dengan
jaringan (479 bar harian, bar hari berjalan ditandai belum lengkap, status `degraded`).

---

## 22. Catatan implementasi Tahap 3 — engine & risk (2026-09-24 WIB)

Keputusan Tahap 3 diambil dengan default yang diusulkan, dengan satu penyempurnaan pada
kebijakan target agar syarat "R:R minimum 2.0 **dengan biaya**" dapat dipenuhi secara konsisten:

- **R** = `entry_high − SL` (entry paling konservatif). **SL** = min(`entry_high − 1.5×ATR14`,
  `struktur − 1 tick`), dibulatkan ke bawah.
- **TP1** = harga terendah pada grid fraksi yang memenuhi *keduanya*: ≥ `entry_high + 2R`
  (floor kotor) **dan** R:R bersih setelah biaya beli/jual ≥ 2.0. TP2 = TP1 + 1R, TP3 = TP1 + 2R
  (dibulatkan ke atas). Tanpa penyesuaian ini, target "tepat 2R" hampir selalu gagal gate bersih
  karena biaya (contoh acuan: entry 1000, SL 955 → TP1 1105, R:R kotor 2.33, bersih 2.06).
- Biaya default **CONTOH / BELUM TERVERIFIKASI**: beli 0,15 %, jual 0,25 %; dapat dimatikan
  dengan `RISK_APPLY_FEES_TO_RR=false` (gate kotor saja).
- **ARA/ARB** sesi berikutnya dihitung dari close sesi lengkap terakhir; zona entry di-clamp ke
  `[ARB, ARA]` (zona di luar batas ⇒ ditolak); SL/TP tidak di-clamp (target multi-sesi) tetapi
  diberi catatan. Zona entry lebih lebar dari 3 % ditolak.
- Setelah rounding/clamp divalidasi ulang `SL < entry_low ≤ entry_high < TP1 ≤ TP2 ≤ TP3` dan
  R:R kotor & bersih ≥ minimum; gagal ⇒ setup dibuang (dicatat sebagai `blocked` tahap `risk`).
- **Sizing**: risiko 1 % dari modal contoh Rp100 juta, lot penuh, dibatasi modal; 0 lot tetap
  ditampilkan dengan catatan eksplisit.
- **Exit/lifecycle** (untuk Tahap 4/6): masa berlaku entry 3 sesi, gap melewati SL ⇒ keluar di
  open, tanpa partial TP. Belum ada kode lifecycle pada tahap ini.
- **Laporan sore**: strategi berbasis close harian tidak dijalankan pada bar belum lengkap
  (`Strategy.supports_incomplete_bar=False` untuk keempat strategi); pipeline memotong frame ke
  bar lengkap ≤ sesi evaluasi sebagai batas anti-lookahead.
- **Warmup** eksplisit 250 bar (EMA200 + 50) dipaksakan oleh `engine/indicators.py`;
  test referensi membandingkan EMA (seed SMA), SMA, RSI (Wilder), ATR (RMA) dengan rumus
  pandas-native.
- **Scorer** sesuai §10.3 dengan bobot per strategi (default 1.0); strategi `inactive` /
  `insufficient_data` / `no_setup` menyumbang 0 tanpa redistribusi bobot; penalti 10 untuk
  kualitas `degraded`; threshold 70; maksimum 5; tie-break confidence ↓ → R:R bersih TP1 ↓ →
  rata-rata nilai transaksi 20 hari ↓ → simbol A–Z.
- **Breakout**: resistance dan rata-rata volume dihitung dari bar *sebelum* candle sinyal;
  zona entry `[max(resistance, close − 1×ATR), close]` (retest dekat breakout).
- **Reversal**: pivot low hanya sah setelah `pivot_right` bar konfirmasi tersedia; sinyal
  hanya dalam 3 bar setelah konfirmasi pivot kedua.
- **Foreign flow**: `inactive` bila data tidak tersedia/tidak segar; nol yang sah dihitung
  sebagai "bukan net buy". Belum ada provider yang menyediakannya (semua template nonaktif).
- Nilai transaksi untuk likuiditas memakai kolom `value` provider bila ada; Yahoo tidak
  menyediakannya sehingga dipakai aproksimasi `close×volume` (dilabeli pada evidence).

Bukti yang benar-benar dijalankan: `pytest` (215 test offline lulus), `ruff check/format`,
`python main.py evaluate` dengan jaringan pada 12 saham watchlist (semua data `degraded`
dapat dipakai; hasil hari itu "data valid tetapi tidak ada setup layak" — bukan dipaksakan).

---

## 23. Catatan implementasi Tahap 4 — storage, Telegram, scheduler (2026-09-24 WIB)

Keputusan: command admin **hanya** diterima di grup admin (`TELEGRAM_ADMIN_CHAT_ID`); tanpa
grup admin, command admin dinonaktifkan (alasan dicatat di log dan dibalas ke grup).

Modul dan perilaku kunci:

- **Kontrak `ReportSnapshot`** di `core/snapshot.py` (pydantic, angka sebagai string dari
  `Decimal`); disimpan immutable di `job_runs.snapshot_json`; formatter/WhatsApp/narator hanya
  membaca dari sini.
- **Lifecycle** (`engine/lifecycle.py`): entry terisi bila `low ≤ entry_high` pada sesi setelah
  publikasi, harga isi `min(entry_high, open)`; pada bar entry SL boleh terpicu (konservatif) tetapi
  TP tidak; `active`: SL diperiksa sebelum TP; gap melewati SL/TP1 ⇒ keluar di open; tanpa partial;
  pending kedaluwarsa setelah 3 sesi; `active` ditutup di close setelah 20 sesi (`closed_time`).
  Bar parsial (quote 15:00) hanya mengevaluasi sentuhan high/low, tidak menambah hitungan sesi.
  Level tersentuh ≠ jaminan terisi di pasar nyata — dinyatakan sebagai asumsi simulasi.
- **Storage** (`storage/`): UNIQUE `(job_type, trading_date, origin)` sebagai klaim atomik;
  re-klaim hanya untuk `failed/blocked` atau job `claimed/running` yang basi (> 30 menit);
  `message_deliveries` UNIQUE `(job_run_id, chat_id, thread_id, part_index)`; transisi
  `pending→sending` lewat UPDATE bersyarat; saat start, baris `sending` yang tertinggal menjadi
  `unknown` (bukan `pending`). `UTCDateTime` menjaga tz-aware pada SQLite. Alembic untuk PostgreSQL;
  `origin` memisahkan dry_run/live/fixture di semua statistik.
- **Notifier Telegram**: allowlist → `getChat` (hanya group/supergroup/channel) → `getChatMember`
  (anggota; channel: admin + `can_post_messages`; forum untuk `thread_id`); private ditolak di semua
  jalur termasuk alert admin dan script TEST. Pemetaan: sukses ⇒ `sent`; `BadRequest/Forbidden/
  ChatMigrated/InvalidToken` ⇒ `failed`; `RetryAfter` ⇒ `failed` retryable (satu percobaan ulang
  terbatas, dijamin belum terkirim); `TimedOut/NetworkError` ⇒ `unknown` tanpa resend otomatis.
- **Gate** (`bot/gates.py`): kalender/pause = blocker keras (job dilewati); di development, aturan
  belum terverifikasi & gate backtest = peringatan (masuk ke bagian kualitas data pesan); di
  production = blocker publikasi. Live selalu memerlukan token+tujuan+saklar+verifikasi API tujuan.
- **ReportService** (`bot/reports.py`): pagi = sesi sebelumnya via kalender, universe = watchlist DB ∪
  simbol sinyal terbuka, engine + lifecycle dengan bar sesi tersebut; sore = quote intraday
  (kesegaran ≤ 20 menit) hanya untuk lifecycle & validasi pending — strategi harian tidak dijalankan
  pada bar belum lengkap. Semua data gagal ⇒ job `failed` + alert (tanpa pesan "sinyal kosong").
  Origin `live` tanpa gate publikasi ⇒ job `blocked` + alert; dry_run ⇒ ekspor ke `var/exports`.
- **Command router** (`bot/commands.py`) independen dari PTB agar otorisasi dapat diuji tanpa
  objek Telegram; adapter tipis di `bot/handlers.py` memakai filter grup/channel PTB.
- **Scheduler**: cron Senin–Jumat 08:30/15:00 WIB (`misfire_grace_time` 10 menit, `coalesce`,
  `max_instances=1`); kalender/libur diperiksa di preflight; health check tiap 15 menit + heartbeat
  `var/heartbeat`; shutdown tertib pada SIGINT/SIGTERM.
- Tambahan kecil: `scripts/test_telegram.py --discover` mencetak ID grup yang terlihat bot lewat
  `getUpdates` (chat pribadi tidak ditampilkan) agar pemilik tidak perlu membagikan token.

Bukti yang benar-benar dijalankan: `pytest` (283 test offline lulus: klaim konkuren 8 coroutine
⇒ 1 pemenang; restart `sending→unknown`; dedup rencana pengiriman; jalur live dengan bot palsu
termasuk timeout ⇒ `unknown` tanpa resend; penolakan private di notifier/router/script; otorisasi
admin incl. anonim; formatter escape/split/validasi; migrasi Alembic pada SQLite), `ruff`,
`python main.py dryrun morning|afternoon` dengan data Yahoo nyata (job `completed`, ekspor
HTML, tanpa kirim), `python main.py run` tanpa token (scheduler aktif, jadwal WIB benar,
shutdown tertib). **Belum**: pengiriman nyata ke grup Telegram (membutuhkan token & ID grup
pemilik), PostgreSQL nyata (test tersedia via `TEST_POSTGRES_URL`).

---

## 24. Catatan implementasi Tahap 5 — narator LLM & ekspor WhatsApp (2026-09-24 WIB)

- **Klien LLM** (`ai/llm_client.py`) berbasis httpx tanpa SDK vendor: `openai` /
  `openai_compatible` memakai `POST {base}/chat/completions` (Bearer) dan membaca
  `choices[0].message.content`; `anthropic` memakai `POST {base}/v1/messages` (`x-api-key`,
  `anthropic-version: 2023-06-01`, `system` top-level) dan menggabungkan `content[].text`.
  Kontrak dicek ke dokumentasi resmi (2026-09). Kunci API didaftarkan ke registry redaksi saat
  klien dibuat sehingga kunci yang dipantulkan server di body error pun teredaksi (temuan test).
- **Narator** (`ai/narrator.py`): input = JSON ringkas dari `ReportSnapshot` (tanpa OHLCV);
  berita hanya judul+sumber dalam kunci `NEWS_TIDAK_TEPERCAYA`. System prompt melarang mengubah/
  menambah angka, simbol, sinyal, rekomendasi, klaim kepastian, dan mengikuti instruksi berita.
  Validasi: setiap angka pada output harus cocok dengan angka input (format `1.234,5`/`1,234.5`,
  persen, tanda minus; pembulatan diterima hanya bila sama pada digit yang ditulis), simbol 4 huruf
  ⊆ simbol input (dengan stopword IHSG/EIDO/dll.), ≤150 kata, ≥8 kata, diakhiri tanda baca, tanpa
  frasa terlarang/markup. Gagal ⇒ template deterministik; LLM error ⇒ template; narator gagal total
  ⇒ laporan tanpa narasi. Narasi hanya mengisi blok "Ringkasan".
- **Ekspor WhatsApp** (`notifications/whatsapp_export.py`): render teks dari snapshot yang sama,
  markup `*`/`_`, angka via `fmt_num` yang sama dengan Telegram; markup berpasangan dalam teks bebas
  dinetralkan (zero-width space); disimpan ke `var/exports/whatsapp/<origin>/`; `WHATSAPP_EXPORT_ENABLED`
  default false; kegagalan hanya dicatat. Tidak ada otomasi WhatsApp.
- `JobOutcome` memuat `whatsapp_path` dan `narrative_source` (`llm|template`); `/runnow` dan `dryrun`
  menampilkannya.

Bukti yang benar-benar dijalankan: `pytest` (310 test offline lulus, termasuk parsing angka
Indonesia, penolakan angka tak tertelusur/simbol baru/frasa terlarang/markup/terpotong, fallback
pada LLM error, bentuk request/response OpenAI & Anthropic dengan transport palsu, redaksi kunci
pada HTTP 401, kesetaraan angka Telegram↔WhatsApp), `ruff`, `dryrun morning` nyata dengan
`WHATSAPP_EXPORT_ENABLED=true` (berkas `.whatsapp.txt` dibuat, narasi `template`). **Belum**:
panggilan LLM nyata (butuh kunci API pemilik).

---

## 25. Catatan implementasi Tahap 6 — backtest & gate produksi (2026-09-24 WIB)

- **Runner** (`backtest/runner.py`) memakai `SignalEngine.run` dan `engine.lifecycle.step` yang sama
  dengan live. Per sesi t: (1) lifecycle posisi/pending dengan bar t (hanya untuk sinyal yang
  dipublikasikan < t), (2) engine pada frame yang dipotong ke bar lengkap ≤ t (pertahanan ganda:
  runner memotong, pipeline memotong lagi), kartu baru menjadi `pending_entry` dan baru bisa
  terisi mulai t+1, (3) equity mark-to-market di close t. Fill `min(entry_high, open)` + slippage
  memburuk; SL sebelum TP; gap ⇒ open; biaya persen beli/jual; lot penuh dibatasi kas; satu
  posisi per simbol; kapasitas `max_open_positions`. Foreign flow `inactive` (tidak ada data EOD).
- **Definisi R** pada trade: PnL bersih ÷ (`entry_high_rencana − SL` × shares) — identik dengan
  lifecycle live; fill yang lebih baik memperkecil |R| (dikonfirmasi pada trade nyata UNTR:
  fill 29.975 vs rencana lebih tinggi ⇒ −0,78R meski keluar di bawah SL).
- **Metrik** (`backtest/metrics.py`): trade = selesai saja; win rate/expectancy `None` bila 0 trade;
  PF `None` + catatan bila tanpa kerugian; max drawdown dari equity curve harian (bukan penjumlahan
  trade); `signals_not_filled`, `open_at_end`, `by_strategy`; CSV equity & trade.
- **Gate** (`backtest/gate.py`): split berbasis waktu (OOS 30 % akhir); lulus bila OOS trade ≥ 30,
  expectancy > 0R, PF ≥ 1,3 (PF tak terdefinisi karena tanpa rugi diterima hanya bila trade ≥ minimum),
  MDD ≤ 15 %. Threshold berlabel CONTOH / BELUM DISEPAKATI. Rekaman gate memuat `engine_version`,
  `config_hash`, `strategy_versions`, hash hasil OOS, threshold, checks, metrik, disclaimer; disimpan
  ke `app_state.backtest_gate` oleh `main.py backtest --save-gate` dan dibaca `bot/gates.py`
  (perubahan `config_hash`/versi strategi membatalkan kelayakan).
- **Sumber data** (`backtest/data.py`): aggregator (Yahoo; fetch mundur ≈1,6×warmup hari kalender) atau
  CSV lokal per simbol (dinormalisasi lewat `normalize_ohlcv` yang sama, origin `fixture`).
- CLI `main.py backtest` mengembalikan `0` lulus / `4` tidak lulus / `1` data tidak layak; laporan ke
  `var/backtests/<periode>_<hash>/`.

Bukti yang benar-benar dijalankan: `pytest` (327 test offline lulus, termasuk: sinyal lahir di
sesi t dan terisi t+1 lalu TP dengan PnL R = R:R kotor; crash masa depan tidak mengubah sinyal
dan gap ⇒ keluar di open; entry+SL satu bar ⇒ rugi konservatif −1R; pending kedaluwarsa bukan
trade; biaya & slippage memburuk dan tercatat; batas kas & satu posisi; kapasitas; determinisme
dan hash; metrik kasus tepi & MDD dari kurva; gate lulus/gagal per alasan; rekaman gate membuka
gate runtime hanya untuk `config_hash` yang sama; CLI CSV menghasilkan laporan dan menyimpan
`passed=false` apa adanya), `ruff`. **Backtest nyata** (Yahoo, 12 saham watchlist, 2025-01-01..
2026-09-22, parameter CONTOH): in-sample 286 sesi, 47 trade, expectancy −0,00R, PF 0,99, MDD 9,3 %;
**out-of-sample** 125 sesi, 15 trade, win 13 %, expectancy −0,54R, PF 0,33, MDD 9,9 % ⇒ **gate
TIDAK lulus** (3 dari 5 pemeriksaan gagal). Mekanika diverifikasi manual (tidak ada anomali
urutan harga/tanda PnL; biaya tepat). Kesimpulan jujur: strategi dengan parameter contoh belum
menunjukkan edge pada universe ini; kalibrasi adalah pekerjaan riset terpisah, dan threshold
tidak diturunkan agar lulus.

---

## 26. Penambahan fitur atas permintaan pemilik (2026-09-24 WIB): smart money, screener BSJP/BPJS, teaser

Permintaan: pilih saham menarik untuk BSJP/BPJS, tambahkan smart money flow & broker akumulasi,
dan pesan pembuka "Are you ready for IHSG SIGNAL" sebelum sinyal. Implementasi dengan batas jujur:

- **Provider CSV lokal** (`data/providers/local_flow.py`, nama `local_flow`): membaca broker summary &
  foreign flow dari berkas yang diekspor pemilik. Tidak ada API publik terverifikasi untuk data ini
  (Yahoo tidak menyediakan), sehingga ini satu-satunya jalur tanpa mengarang. Semantik tagged union
  dijaga: tanggal tidak ada ⇒ `Unavailable`, nol tertulis ⇒ `Ok(0)`, berkas rusak ⇒ `Failed`.
  Diaktifkan lewat `FLOW_CSV_DIR` + `PROVIDER_PRIORITY=yahoo,local_flow`; `local_flow` tidak dihitung
  sebagai provider pasar kedua (kualitas OHLCV tetap `degraded`).
- **`smart_money` v1** (`engine/strategies/smart_money.py`): aktif hanya bila broker summary tersedia
  untuk 3 sesi berturut yang berakhir pada sesi evaluasi; akumulator = net buy > 0 di setiap sesi;
  syarat intensitas ≥ 3 % rata-rata nilai transaksi 20 sesi, konsentrasi top-3 ≥ 40 %, close > EMA50;
  bonus quiet accumulation (|Δharga| ≤ 4 %) dan konfirmasi foreign; entry `[close − 0,5×ATR, close]`,
  struktur stop = low terendah jendela. `StrategyContext`/`SymbolInput` mendapat `broker_summaries`
  (histori). `ReportService` pagi mengambil histori `FLOW_HISTORY_SESSIONS` sesi via kalender hanya
  bila aggregator mendukung kapabilitasnya. Backtest EOD tetap menandai strategi ini `inactive`
  (tidak ada data historis broker) — konsisten dengan larangan meluluskan strategi flow dari EOD.
- **Screener BSJP/BPJS** (`engine/short_term.py`, `main.py screen`): statistik historis gap overnight
  (`open_t/close_{t−1}`) dan intraday (`close/open`) selama lookback 60 sesi, filter likuiditas dari
  `market_rules.liquidity`, peringkat dengan ukuran konsistensi `t`, disclaimer eksplisit. Universe =
  watchlist ∪ `config/universe_candidates.yaml` (40 kode CONTOH). **BSJP tidak dijadikan strategi
  sinyal** karena membutuhkan kebijakan exit di open (tanpa TP/SL) yang bertentangan dengan gate
  R:R; ini keputusan terbuka untuk pemilik.
- **Teaser** (`format_teaser`): pesan pertama sebelum laporan, teks dari `TEASER_TEXT` (di-escape),
  default hanya bila ada setup (`TEASER_ONLY_WITH_SIGNALS`), ikut mekanisme delivery/dedup yang sama;
  juga di ekspor WhatsApp. Tidak memuat angka trading.

Bukti: `pytest` 341 lulus (smart money: inactive tanpa data, sinyal dengan akumulator, syarat
persistensi/intensitas/tren, bonus foreign & nol sah, pipeline end-to-end; provider CSV: missing/
nol/rusak; aggregator routing; settings; screener: statistik konstruksi diketahui, peringkat & filter;
teaser hanya dengan setup; histori broker sampai ke engine di ReportService). Nyata: `screen --style
bsjp|bpjs` atas 40 saham via Yahoo (40/40 terambil); `dryrun morning` selesai (tanpa CSV ⇒
`smart_money` inactive, tanpa teaser karena tidak ada setup).

### 26.1 Smart money PROXY dari data Yahoo (permintaan lanjutan)

Pemilik meminta memakai data Yahoo. Yahoo tidak menyediakan broker summary/foreign flow IDX, sehingga
dibangun **proxy berbasis harga & volume** yang dilabeli eksplisit "bukan data broker":

- `engine/indicators.py`: `cmf20` (Chaikin Money Flow), `obv` + `obv_ema20`, `mfi14` — rumus dicek
  eksak terhadap implementasi pandas-native (CMF/MFI selisih ≤1e-10; OBV identik setelah seed NaN
  diisi 0), warmup dipaksakan seperti indikator lain.
- `engine/money_flow.py`: `MoneyFlowStats` per simbol (jendela 10 sesi): CMF sekarang vs 10 sesi lalu,
  OBV netto dalam **hari volume** (Δ OBV ÷ rata-rata volume 20 sesi; batas teoretis ±10), hari
  akumulasi/distribusi (close di 30 % atas/bawah range dengan volume > rata-rata), rasio volume
  naik/turun (netral bila semua sesi searah), Δ harga & flag *quiet*, skor komposit −100…+100 dan
  label akumulasi/distribusi/netral. Dihitung di pipeline untuk setiap simbol yang lolos likuiditas
  dan dilampirkan ke `SymbolEvaluation.money_flow`.
- `engine/strategies/money_flow_proxy.py` v1: CMF > 0,10 dan naik; OBV ≥ 2 hari volume; ≥3 hari
  akumulasi dan > hari distribusi; rasio ≥ 1,3; close > EMA50; bonus quiet & MFI < 70. Karena berasal
  dari EOD, strategi ini **boleh** di-backtest (berbeda dari `smart_money` berbasis broker).
- Laporan: bagian **💰 Money Flow** (top-5 akumulasi & distribusi, netral disembunyikan) di Telegram
  dan WhatsApp; masuk input narator (simbolnya diizinkan validator).
- Temuan saat verifikasi nyata: skala OBV awalnya dikali 100 (tampil "167,7 hari vol") — dikoreksi ke
  hari volume murni (ANTM: 1,7).

Bukti: `pytest` 346 lulus (referensi rumus CMF/OBV/MFI; deteksi akumulasi pada fixture realistis
8 naik/4 turun; fixture netral terkonstruksi ⇒ netral; strategi memberi sinyal & syarat; pipeline
melampirkan stats; laporan Telegram/WA memuat bagian). Nyata: `dryrun morning` menampilkan Money Flow
dari Yahoo (ANTM akumulasi; ICBP/UNTR/KLBF/BBCA/INDF distribusi). **Backtest ulang** 2025-01-01..
2026-09-22: in-sample 57 trade, +0,10R, PF 1,20 (proxy: 11 trade, win 64 %, +0,44R — kontributor
terbaik); out-of-sample 17 trade, −0,44R, PF 0,42 (proxy 4 trade, −0,39R) ⇒ gate **tetap tidak lulus**.
Interpretasi jujur: proxy menambah nilai in-sample, tetapi periode OOS (Mar–Sep 2026) merugikan semua
strategi long; diperlukan filter rezim pasar dan kalibrasi terpisah, bukan penurunan threshold.

---

## 27. Catatan implementasi Tahap 7 — integrasi & deploy (2026-09-24 WIB)

- **Dockerfile** multi-stage `python:3.12-slim`: builder memasang dependency dari `requirements.txt`
  (ekspor `uv.lock`) ke venv; runtime menyalin venv + kode saja (COPY eksplisit per paket, tanpa
  `.env`/`var/`/`tests/`), user non-root `bot` (uid 10001), `VOLUME /app/var`, `HEALTHCHECK` memakai
  `scripts/healthcheck.py` (heartbeat ≤ 40 menit; tidak membaca rahasia), `STOPSIGNAL SIGTERM`,
  `CMD python main.py run`.
- **docker-compose.yml**: service `bot` dengan `env_file: .env`, volume `./var` dan `./config:ro`,
  `read_only` + `tmpfs /tmp`, `no-new-privileges`, `restart: unless-stopped`, `stop_grace_period 30s`;
  profil `production` menambah `postgres:16-alpine` (password wajib dari `.env`) dan job `migrate`
  (`alembic upgrade head`).
- **`.dockerignore`** mengecualikan `.env*` (kecuali `.env.example`), `var/`, `.git/`, metadata
  platform, cache, `tests/`, `uv.lock`, dokumen panjang.
- **`scripts/package_project.py`**: ZIP berbasis allowlist (berkas root tertentu + direktori kode/
  konfigurasi/dokumen/tests dengan suffix yang diizinkan), menolak `.env`, `var/`, `.git`, cache,
  virtualenv, metadata; memindai pola token Telegram/kunci API/assignment `KEY=nilai` (placeholder
  kosong/`...`/`<...>` diabaikan; berkas test dan token palsu bertanda diabaikan) — bila ada temuan,
  arsip dibatalkan dan hanya nama berkas + pola yang dicetak. Menambahkan `PACKAGE_INFO.txt` dan
  `var/.gitkeep`.
- **CI** (`ci/github-workflow-ci.yml` (pindahkan ke `.github/workflows/` untuk mengaktifkan; lihat `ci/README.md`)): `uv sync --locked`, ruff, pytest offline, package check, lalu
  build image tanpa push + smoke test (`config` tanpa token, healthcheck harus gagal tanpa
  heartbeat, impor modul).
- `requirements*.txt` disinkronkan ulang dari `uv.lock`.

Bukti yang benar-benar dijalankan: `pytest` 350 lulus (termasuk test packaging: allowlist,
isi ZIP, pemindai menolak token nyata pada repo palsu, `--check` pada repo asli lolos); ZIP nyata
dibuat (133 berkas, 316 KB) dan **diverifikasi di direktori bersih**: instal dari
`requirements-dev.txt` di venv baru, `main.py config` berjalan tanpa `.env`, **seluruh test suite
lulus di dalam ZIP**, `healthcheck.py` menolak tanpa heartbeat, `package_project.py --check` lolos;
pemeriksaan statis Dockerfile (semua sumber COPY ada, non-root, healthcheck, STOPSIGNAL, tanpa
COPY `.env`), compose diparse, `alembic upgrade head --sql` menghasilkan 9 `CREATE TABLE` untuk
PostgreSQL. **Belum**: `docker build` nyata (sandbox tanpa Docker) — didelegasikan ke CI dan dicatat di
`docs/verification_required.md`.

---

## 28. Filter rezim pasar (lanjutan riset kalibrasi, 2026-09-24 WIB)

Tujuan: mengurangi kerugian out-of-sample dengan membuat bot diam saat pasar agregat turun — tanpa
menurunkan threshold gate.

- `engine/regime.py`: rezim dari indeks acuan (`^JKSE` Yahoo; hanya simbol berawalan `^` yang dipetakan
  apa adanya oleh adapter Yahoo) dengan bar lengkap ≤ sesi evaluasi: `close > EMA50 > EMA200` ⇒ bullish,
  `close < EMA50 < EMA200` ⇒ bearish, lainnya netral; bar sesi tidak ada / histori < warmup ⇒ unknown.
  Kebijakan: bearish menahan setup long baru; unknown mengikuti `REGIME_POLICY_ON_UNKNOWN` (default
  `block`, fail-closed); netral dapat diblokir opsional. Sinyal terbuka tetap dikelola lifecycle.
- `SignalEngine.run(..., regime=)`: evaluasi per simbol tetap dijalankan (audit/Money Flow tetap
  tampil), tetapi kandidat kartu ditahan dengan catatan "filter rezim: N kandidat setup ditahan";
  `EngineResult.regime` disimpan; `RegimeConfig` masuk `config_hash` (perubahan filter membatalkan gate).
  Tanpa argumen `regime` engine tidak memfilter — pemanggil (ReportService/backtest) bertanggung jawab.
- ReportService mengambil frame indeks lewat aggregator yang sama; snapshot memuat `market_bias`
  (bullish/neutral/bearish) dan `regime_detail`; formatter menampilkan "📈 Rezim pasar" (bias hanya dari
  aturan terdefinisi, sesuai §14). Backtest menghitung rezim per sesi dari frame indeks yang dipotong ≤
  sesi dan melaporkan distribusi rezim.
- Dua temuan data nyata saat verifikasi: (1) Yahoo memberi bar IHSG hari berjalan dengan Close NaN,
  dan tanggalnya berbeda antara mode `period` (23 Sep) dan `start/end` (22 Sep). Perbaikan: bar
  **terakhir** dengan OHLC kosong tidak pernah dianggap lengkap (dicatat di `notes`); bar kosong di
  tengah histori tetap membuat frame INVALID. (2) Validator kini memeriksa struktur hanya pada bar
  lengkap (bar hari berjalan boleh parsial) — kesegaran intraday tetap memakai bar terbaru.

Bukti: `pytest` 359 lulus (klasifikasi rezim, unknown/policy, anti-lookahead, engine menahan kandidat
di bearish/unknown tetapi tidak di bullish, hash konfigurasi, settings, backtest per sesi, bar terakhir
NaN). **Backtest nyata dengan filter** (2025-01-01..2026-09-22): in-sample 50 trade, +0,04R, PF 1,05
(rezim bullish 111 / netral 64 / bearish 77 sesi); out-of-sample 8 trade, −0,37R, PF 0,54, MDD 5,5 %
(82/125 sesi bearish ⇒ sebagian besar setup ditahan). Dibanding tanpa filter (OOS −7,7 %, PF 0,42,
MDD 8,8 %): kerugian dan drawdown berkurang, tetapi trade yang lolos di sesi netral masih negatif dan
jumlah trade OOS < 30 ⇒ **gate tetap tidak lulus**. Langkah riset berikutnya (di luar lingkup ini):
`REGIME_BLOCK_NEUTRAL=true`, bobot per strategi (breakout −0,29R IS / −0,49R OOS konsisten negatif),
universe lebih besar untuk jumlah trade — semuanya diuji di in-sample dulu.

---

## 29. Riset kalibrasi yang disiplin (2026-09-24 WIB)

- `backtest/research.py` + `main.py research`: data diunduh **sekali** ke cache CSV (`var/research/cache`),
  grid 8 varian bermotivasi (base, tanpa rezim, rezim menahan netral, tanpa breakout, tanpa
  breakout+reversal, masa tahan 10, threshold 80, kombinasi) dievaluasi **hanya in-sample**; `--oos-for`
  mengevaluasi satu varian pilihan **sekali** pada OOS. Setiap varian = konfigurasi engine berbeda
  (`config_hash` berbeda). `SCORER_WEIGHTS` ditambahkan agar bobot per strategi dapat diatur di produksi.
- Hasil in-sample (2025-01-02..2026-03-17, 12 saham): `no_breakout_reversal` PF 1,21 / +0,10R / MDD 5,3 %
  (terbaik dengan trade cukup); `no_regime` PF 1,20 tetapi MDD lebih tinggi; `hold10` **merugikan** (PF 0,52,
  MDD 18 %: memotong pemenang yang butuh waktu); `thr80` merugikan (PF 0,64: menyaring yang salah);
  `block_neutral` mengurangi trade tanpa menambah edge.
- **OOS satu kali** untuk `no_breakout_reversal` (2026-03-18..2026-09-22): 5 trade, −0,61R, PF 0,20 ⇒ **tidak
  lulus**. Perbaikan in-sample tidak bertahan. Ini diterima sebagai temuan, bukan alasan untuk mencoba
  varian lain pada OOS yang sama (itu akan menjadi in-sample terselubung).
- Kesimpulan jujur: dengan universe 12 saham dan periode ~21 bulan, tidak ada konfigurasi yang layak
  produksi. Langkah bermakna berikutnya bersifat data, bukan parameter: universe ≥ 40 saham likuid
  (`config/universe_candidates.yaml`) dan periode lebih panjang, lalu prosedur yang sama diulang dengan OOS
  yang **baru** (periode yang belum pernah dilihat).
