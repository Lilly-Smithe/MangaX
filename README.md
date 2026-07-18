# MangaX Reader

+MangaX Reader, klasör, ZIP, CBZ, JPG, PNG ve WebP biçimindeki yerel mangaları çevrimdışı okumak için hazırlanmış kaynak paketidir. Keşfet, scraper, çevrimiçi manga kaynağı, eklenti mağazası ve çevrimiçi bölüm indirme kodu içermez.
+
+## Kaynaktan çalıştırma
+
+```powershell
+python -m venv .venv
+.\.venv\Scripts\python -m pip install -r requirements.txt
+.\.venv\Scripts\python app_gui.py
+```
+
+Güvenlik denetimi:
+
+```powershell
+python tools\audit_public_reader.py .
+python -m unittest discover -s tests
+```
+
+Bu depo kullanıcı verisi, kimlik bilgisi, token, scraper veya MangaX Full kaynak kodu içermez.
+