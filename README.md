# MangaX Reader

MangaX Reader, yerel manga arşivlerini düzenlemek ve internet bağlantısı olmadan okumak için hazırlanmış bir Windows masaüstü uygulamasıdır.

## Özellikler

- Klasör, ZIP ve CBZ arşivlerinden manga içe aktarma
- JPG, PNG ve WebP sayfa desteği
- Tamamen çevrimdışı okuma
- Okuma ilerlemesini ve son kalınan sayfayı hatırlama
- Koleksiyonlar, okuma durumları, kişisel puanlar ve notlar
- Webtoon, tek sayfa ve çift sayfa okuyucu görünümleri
- Yakınlaştırma, sayfaya veya genişliğe sığdırma, parlaklık ve arka plan ayarları
- Klavye kısayolları ve otomatik sonraki bölüme geçiş
- Yerel yedekleme ile kütüphane ve ayarları dışa/içe aktarma

## İndirme

Hazır Windows kurulum dosyasını [Releases](https://github.com/Lilly-Smithe/MangaX/releases) sayfasından indirebilirsiniz.

## Kaynaktan çalıştırma

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python app_gui.py
```

## Kaynaktan build alma

```powershell
.\build_reader.bat
```

Betik, Reader klasor paketinden kurulum konumu ve istege bagli masaustu
kisayolu sunan standart Windows Setup dosyasini da olusturur. Bunun icin
Inno Setup 6 veya 7 derleyicisinin kurulu olmasi gerekir.
