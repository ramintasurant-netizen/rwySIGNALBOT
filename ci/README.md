# CI GitHub Actions

Berkas `github-workflow-ci.yml` adalah workflow CI (lint, test offline, package check, build image
Docker + smoke test). Ia sengaja **tidak** ditempatkan di `.github/workflows/` karena kredensial
otomatis yang dipakai untuk membuat PR ini tidak memiliki izin `workflows` untuk menulis ke sana.

Untuk mengaktifkan (sekali, oleh pemilik repo):

```bash
mkdir -p .github/workflows
git mv ci/github-workflow-ci.yml .github/workflows/ci.yml
git commit -m "Aktifkan CI"
git push
```

Setelah aktif, setiap push/PR akan menjalankan test dan membangun image tanpa push ke registry.
