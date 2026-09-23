# Verifikasi yang Wajib Dilakukan Sebelum Produksi

Dokumen ini mencatat semua hal yang **belum diverifikasi** dan memblokir penerbitan sinyal
produksi (fail-closed). Perbarui kolom *Status* dan isi *Sumber* saat sudah diverifikasi,
lalu ubah `meta.verified: true` pada berkas konfigurasi terkait.

Legenda status: ❌ belum · ⚠️ sebagian · ✅ selesai

## 1. Aturan bursa (config/market_rules.yaml)

| Item | Status | Sumber yang dibutuhkan | Catatan |
|---|---|---|---|
| Ukuran lot (100 lembar) | ❌ | Peraturan Perdagangan BEI yang berlaku | nilai contoh |
| Tabel fraksi harga berjenjang | ❌ | Peraturan/Keputusan Direksi BEI terbaru | rentang & tick contoh |
| Auto rejection atas/bawah (ARA/ARB) per rentang harga | ❌ | Keputusan Direksi BEI terbaru | persentase contoh; papan akselerasi & full call auction belum dimodelkan |
| Harga referensi ARA/ARB (previous close vs harga teoretis pasca aksi korporasi) | ❌ | idem | saat ini `previous_close` |
| Jam sesi (pre-opening, sesi 1, sesi 2, pre-closing, post-trading; jadwal Jumat) | ❌ | Pengumuman jam perdagangan BEI | contoh |
| Ambang likuiditas (nilai/volume rata-rata) | ❌ | Keputusan internal pengguna | parameter kebijakan, bukan aturan bursa |
| Daftar suspensi dan papan pemantauan khusus | ❌ | Pengumuman harian BEI | perlu proses pembaruan rutin |

## 2. Kalender (config/trading_calendar.yaml)

| Item | Status | Sumber | Catatan |
|---|---|---|---|
| Libur bursa tahun berjalan (termasuk cuti bersama) | ❌ | Kalender libur bursa BEI resmi | hanya 3 libur bertanggal tetap sebagai contoh |
| Cakupan tahun berikutnya | ❌ | idem | tanggal di luar cakupan memblokir job |

## 3. Data pasar

| Item | Status | Sumber | Catatan |
|---|---|---|---|
| Yahoo Finance: cakupan & delay OHLCV harian `.JK` | ⚠️ | Uji langsung + ketentuan Yahoo | 2026-09-23: fetch BBCA 479 bar berhasil dari sandbox; delay/keterlambatan bar harian belum diukur |
| Yahoo Finance: ketersediaan & delay intraday `.JK` (1m/5m/15m) untuk laporan 15:00 | ❌ | Uji langsung | quote diturunkan dari bar 1m; delay belum diverifikasi |
| Yahoo Finance: ketentuan penggunaan/redistribusi data ke grup Telegram | ❌ | Yahoo Terms of Service | wajib sebelum produksi |
| Yahoo: semantik harga (split-adjusted vs dividend-adjusted) dan aksi korporasi | ⚠️ | Dokumentasi yfinance | adapter memakai `auto_adjust=False`; hanya bar setelah aksi korporasi terakhir identik dengan harga transaksi |
| GoAPI: endpoint, autentikasi, skema field, basis harga, batas rate, biaya, lisensi | ❌ | Dokumentasi resmi GoAPI | adapter template nonaktif |
| Sectors: endpoint, definisi foreign flow, delay publikasi, biaya, lisensi | ❌ | Dokumentasi resmi Sectors | adapter template nonaktif |
| Broker read-only: ketersediaan API, scope read-only, delay, lisensi | ❌ | Dokumentasi broker | adapter template nonaktif; tidak akan ada endpoint order |
| Kebijakan produksi satu provider (tanpa cross-validation) | ❌ | Persetujuan pemilik | `PRODUCTION_SINGLE_PROVIDER_APPROVED` |

## 4. Makro global (config/global_macro.yaml)

| Instrumen | Simbol saat ini | Status | Catatan |
|---|---|---|---|
| S&P 500 / Nasdaq / Dow | `^GSPC` `^IXIC` `^DJI` | ❌ | verifikasi simbol & satuan |
| EIDO | `EIDO` | ❌ | ETF, USD |
| USD/IDR | `IDR=X` | ❌ | arah kurs (IDR per USD) |
| Emas, minyak WTI | `GC=F` `CL=F` | ❌ | kontrak futures front-month; satuan |
| Batu bara, nikel, CPO | — | ❌ | **belum ada sumber**; tidak memakai proxy tanpa label & persetujuan |

## 5. Berita (config/news_sources.yaml)

| Item | Status | Catatan |
|---|---|---|
| Daftar URL RSS/API yang boleh dipakai | ❌ | daftar kosong sampai disediakan |
| Lisensi/ketentuan tiap sumber | ❌ | judul+URL saja yang ditampilkan |

## 6. Telegram (Tahap 4)

| Item | Status | Catatan |
|---|---|---|
| Batas panjang pesan (4096) & tag HTML yang didukung | ⚠️ | dipakai dari konstanta python-telegram-bot 22.8; formatter memvalidasi tag & panjang. Perilaku nyata di grup belum diuji dari sandbox (tanpa token) |
| Hak posting bot pada channel dan supergroup forum (`message_thread_id`) | ⚠️ | diverifikasi saat runtime via `getChat`/`getChatMember` (`can_post_messages`, `is_forum`); belum diuji dengan akun nyata |
| Penanganan pengirim anonim/`sender_chat` untuk otorisasi admin | ⚠️ | ditolak by design (sender_chat, GroupAnonymousBot 1087968824, is_bot); belum diuji dengan grup nyata |
| Uji kirim TEST nyata ke grup | ❌ | membutuhkan token & ID grup pemilik; jalankan `scripts/test_telegram.py --send` |

## 7. Parameter risiko & kebijakan (Tahap 3)

| Item | Status | Catatan |
|---|---|---|
| Definisi R:R | ✅ | R = entry_high − SL; gate 2,0 pada TP1 setelah pembulatan, dengan biaya (ARCHITECTURE §22) |
| Biaya beli/jual aktual (broker + levy + PPh final) | ❌ | default CONTOH 0,15 % / 0,25 %; sesuaikan dengan broker pengguna |
| Slippage | ❌ | akan dipakai backtest (Tahap 6) |
| Kebijakan exit (tanpa partial TP), masa berlaku entry 3 sesi, gap ⇒ keluar di open | ⚠️ | disepakati; implementasi lifecycle di Tahap 4/6 |
| Modal contoh & risiko per transaksi | ⚠️ | default CONTOH 100 juta / 1 %; ubah lewat `.env` |
| Bobot strategi & threshold confidence | ⚠️ | default bobot 1,0 / threshold 70; belum dikalibrasi backtest |

## 8. Backtest & gate (Tahap 6)

| Item | Status | Catatan |
|---|---|---|
| Threshold gate (trade minimum, expectancy, PF, MDD), periode out-of-sample | ❌ | harus disepakati sebelum sinyal produksi |
| Sumber data historis & survivorship bias | ❌ | idem |

## 9. WhatsApp (jika kelak diaktifkan)

| Item | Status | Catatan |
|---|---|---|
| Dukungan API resmi (WhatsApp Business Platform) untuk **Saluran**, batas fitur/peserta, biaya | ❌ | tahap awal hanya ekspor teks manual (`notifications/whatsapp_export.py`); tidak ada endpoint palsu |
| Kebijakan platform terkait konten finansial/sinyal | ❌ | wajib sebelum otomasi apa pun |
| Larangan otomasi tidak resmi (WhatsApp Web, scraping sesi, library tak resmi) | ✅ | tidak dipakai, by design |

## 10. Narator LLM (jika diaktifkan)

| Item | Status | Catatan |
|---|---|---|
| Kontrak API OpenAI Chat Completions (`POST /chat/completions`, Bearer) | ⚠️ | diverifikasi dari dokumentasi resmi 2026-09; belum diuji dengan kunci nyata |
| Kontrak API Anthropic Messages (`POST /v1/messages`, `x-api-key`, `anthropic-version: 2023-06-01`) | ⚠️ | idem |
| Biaya token dan batas rate provider | ❌ | menunggu keputusan pemilik |
| Kebijakan data: snapshot engine dikirim ke pihak ketiga | ❌ | hanya JSON ringkas (tanpa OHLCV mentah); pastikan sesuai kebijakan Anda |
