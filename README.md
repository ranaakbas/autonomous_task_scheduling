# Autonomous Task Scheduling Agent

Ogrenciler icin algoritmik, adaptif gorev planlama uygulamasi.

## Ozellikler

- FastAPI backend + SQLite veritabani
- Strict gorev modeli: `title`, `deadline`, `total_duration`, `remaining_duration`, `difficulty`, `completed`
- Takvim tabanli schedule modeli: gunluk bloklar ve durum takibi (`pending`, `completed`, `missed`)
- Deadline-first + dengeli dagitim ile otomatik planlama
- Her blok icin "kac saat yaptin?" girisi; 0 saat = `missed`, 0'dan buyuk = `completed`
- **Undo (5sn)**: Completed/Missed aksiyonlari 5 saniye icinde geri alinabilir (event/action log tabanli)
- **User availability constraints**:
  - Gunluk kapasite, `AvailabilitySlot` girdilerine gore dinamik hesaplanir (blocked/available)
  - Varsayilan: gun 24 saat musait kabul edilir, blocked slotlar dusulur
- Modalli modern frontend:
  - Aylik takvim
  - Task olusturma/duzenleme/silme
  - Her blok icin Completed/Missed + Undo aksiyonlari
  - Availability modalindan slot ekleme/silme

## Gereksinimler

- Python 3.10+ (onerilir)

## Kurulum

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Ortam degiskenleri (.env)

Bu repo **.env dosyasini commit etmez**. Cohere kullanmak istersen:

```bash
cp .env.example .env
# sonra .env icinde COHERE_API_KEY degerini doldur
```

## Calistirma

```bash
uvicorn app.main:app --reload
```

Tarayicida:

`http://127.0.0.1:8000`

## Veritabani

- Uygulama varsayilan olarak yerelde SQLite kullanir.
- Yerel `.db` dosyalari (ornegin `autonomous_agent.db`) `.gitignore` ile **repoya eklenmez**.

## Mimari

- `app/main.py`: API endpointleri ve uygulama kurulumu
- `app/services.py`: Cohere entegrasyonu, scheduling ve adaptation engine
- `app/models.py`: SQLAlchemy modelleri
- `frontend/`: Basit web arayuzu

## API Ozet

- `POST /tasks`
- `GET /tasks`
- `PATCH /tasks/{task_id}`
- `DELETE /tasks/{task_id}`
- `GET /plan`
- `POST /reschedule`
- `PUT /profile`
- `GET /profile`
