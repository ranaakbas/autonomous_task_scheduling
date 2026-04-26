# Autonomous Task Scheduling Agent

Ogrenciler icin algoritmik, adaptif gorev planlama uygulamasi.

## Ozellikler

- FastAPI backend + SQLite veritabani
- Strict gorev modeli: `title`, `deadline`, `total_duration`, `remaining_duration`, `difficulty`, `completed`
- Takvim tabanli schedule modeli: gunluk bloklar ve durum takibi (`pending`, `partial`, `completed`, `missed`)
- Deadline-first + dengeli dagitim ile otomatik planlama
- **Partial completion**: her blok icin "kac saat yaptin?" girisi (kismi tamamlanma destekli)
- **Undo (5sn)**: Done/Missed/Partial aksiyonlari 5 saniye icinde geri alinabilir (event/action log tabanli)
- **User availability constraints**:
  - Gunluk kapasite, `AvailabilitySlot` girdilerine gore dinamik hesaplanir (blocked/available)
  - Varsayilan: gun 24 saat musait kabul edilir, blocked slotlar dusulur
- Modalli modern frontend:
  - Aylik takvim
  - Task olusturma/duzenleme/silme
  - Her blok icin Partial/Completed/Missed + Undo aksiyonlari
  - Availability modalindan slot ekleme/silme

## Kurulum

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Opsiyonel Cohere:

```bash
echo "COHERE_API_KEY=your_key_here" > .env
```

## Calistirma

```bash
uvicorn app.main:app --reload
```

Tarayicida:

`http://127.0.0.1:8000`

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
