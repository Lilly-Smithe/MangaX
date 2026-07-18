# migrate_folders.py
# Eski UUID adlı downloads klasörlerini manga başlığına göre yeniden adlandırır
# main.py startup'ta otomatik çağrılır

import os
import re
import json
import shutil


from config import BASE_DIR, DATA_DIR, DOWNLOADS_DIR
LIBRARY_FILE = os.path.join(DATA_DIR, "library.json")


def migrate_internal_files_to_root():
    """
    Eğer kullanıcı yanlışlıkla _internal altında data veya downloads oluşturduysa,
    bunları exe yanındaki kök dizine taşır ve library.json dosyalarını birleştirir.
    """
    import sys
    current_file_path = os.path.abspath(__file__)
    is_frozen = getattr(sys, "frozen", False) or "_internal" in current_file_path
    if not is_frozen:
        return
        
    exe_dir = os.path.dirname(sys._MEIPASS)
    # _MEIPASS geçici klasörüdür. Ancak onedir modunda _internal klasörü doğrudan exe yanındadır.
    internal_dir = os.path.join(exe_dir, "_internal")
    
    if not os.path.exists(internal_dir):
        return

    # _internal altındaki kaynaklar
    internal_data = os.path.join(internal_dir, "data")
    internal_downloads = os.path.join(internal_dir, "downloads")
    
    # Exe yanındaki hedefler
    root_data = os.path.join(exe_dir, "data")
    root_downloads = os.path.join(exe_dir, "downloads")
    
    # 1. library.json taşınması / birleştirilmesi
    internal_lib_file = os.path.join(internal_data, "library.json")
    root_lib_file = os.path.join(root_data, "library.json")
    if os.path.exists(internal_lib_file):
        try:
            os.makedirs(root_data, exist_ok=True)
            if os.path.exists(root_lib_file):
                # Her iki dosyayı da oku ve birleştir
                try:
                    with open(internal_lib_file, "r", encoding="utf-8") as f:
                        int_data = json.load(f)
                    with open(root_lib_file, "r", encoding="utf-8") as f:
                        rt_data = json.load(f)
                    
                    int_mangas = int_data.get("mangas", {})
                    rt_mangas = rt_data.setdefault("mangas", {})
                    
                    for m_id, m_val in int_mangas.items():
                        if m_id not in rt_mangas:
                            rt_mangas[m_id] = m_val
                        else:
                            # Bölümleri birleştir
                            int_ch = m_val.get("downloaded_chapters", {})
                            rt_ch = rt_mangas[m_id].setdefault("downloaded_chapters", {})
                            for ch_id, ch_val in int_ch.items():
                                rt_ch[ch_id] = ch_val
                    
                    with open(root_lib_file, "w", encoding="utf-8") as f:
                        json.dump(rt_data, f, indent=4, ensure_ascii=False)
                    print(f"[Migrate] library.json dosyalari birlestirildi.")
                except Exception as read_err:
                    print(f"[Migrate] library.json okuma/birlesim hatasi: {read_err}")
                    # Hata durumunda üzerine kopyalamayı dene (eskiyi kurtarmak adına)
                    shutil.copy2(internal_lib_file, root_lib_file)
            else:
                shutil.copy2(internal_lib_file, root_lib_file)
                print(f"[Migrate] library.json _internal'dan kök dizine kopyalandi.")
            
            # Eski library.json'ı kaldır
            try:
                os.remove(internal_lib_file)
            except Exception:
                pass
        except Exception as e:
            print(f"[Migrate] library.json genel tasima hatasi: {e}")

    # 2. downloads klasörünün taşınması
    if os.path.exists(internal_downloads):
        try:
            os.makedirs(root_downloads, exist_ok=True)
            for item in os.listdir(internal_downloads):
                src_item = os.path.join(internal_downloads, item)
                dst_item = os.path.join(root_downloads, item)
                if os.path.isdir(src_item):
                    if not os.path.exists(dst_item):
                        shutil.move(src_item, dst_item)
                        print(f"[Migrate] Klasor tasindi: {item}")
                    else:
                        for sub in os.listdir(src_item):
                            sub_src = os.path.join(src_item, sub)
                            sub_dst = os.path.join(dst_item, sub)
                            if not os.path.exists(sub_dst):
                                shutil.move(sub_src, sub_dst)
                        try:
                            shutil.rmtree(src_item)
                        except Exception:
                            pass
        except Exception as e:
            print(f"[Migrate] downloads tasima hatasi: {e}")


def _safe_folder_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', '', name)
    name = name.strip().strip('.')
    if len(name) > 80:
        name = name[:80].strip()
    return name or "Unknown"


def _safe_chapter_folder(chapter_num: str, chapter_title: str) -> str:
    padded = chapter_num.zfill(3) if chapter_num.isdigit() else chapter_num
    name = f"Chapter {padded}"
    if chapter_title and chapter_title.strip() and chapter_title.strip() != f"Chapter {chapter_num}":
        name = f"Chapter {padded} - {_safe_folder_name(chapter_title)}"
    return name


def _set_folder_icon(folder_path: str, cover_webp_path: str):
    """Klasör simgesini kapak resmiyle ayarla."""
    try:
        from PIL import Image
        import subprocess

        ico_path = os.path.join(folder_path, "folder.ico")
        img = Image.open(cover_webp_path).convert("RGBA")
        img = img.resize((256, 256), Image.LANCZOS)
        img.save(ico_path, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32)])

        ini_path = os.path.join(folder_path, "desktop.ini")
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write("[.ShellClassInfo]\n")
            f.write("IconResource=folder.ico,0\n")
            f.write("IconFile=folder.ico\n")
            f.write("IconIndex=0\n")

        subprocess.run(["attrib", "+s", "+h", ini_path], check=False, creationflags=0x08000000)
        subprocess.run(["attrib", "+s", "+h", ico_path], check=False, creationflags=0x08000000)
        subprocess.run(["attrib", "+r", folder_path], check=False, creationflags=0x08000000)
    except Exception as e:
        print(f"[Migrate] Folder icon error: {e}")


def migrate_downloads():
    """
    downloads/ altındaki UUID adlı manga klasörlerini manga başlığına yeniden adlandırır.
    Bölüm alt klasörlerini de Chapter 001 formatına çevirir.
    library.json'u günceller.
    """
    # Önce _internal altındaki eski dosyaları taşı
    migrate_internal_files_to_root()

    if not os.path.exists(LIBRARY_FILE):
        return

    try:
        with open(LIBRARY_FILE, "r", encoding="utf-8") as f:
            library = json.load(f)
    except Exception as e:
        print(f"[Migrate] library.json read error: {e}")
        return

    mangas = library.get("mangas", {})
    changed = False

    for manga_id, manga_data in mangas.items():
        title = manga_data.get("title", "")
        if not title:
            continue

        safe_title = _safe_folder_name(title)
        old_manga_dir = os.path.join(DOWNLOADS_DIR, manga_id)
        new_manga_dir = os.path.join(DOWNLOADS_DIR, safe_title)

        # Eski UUID klasörü varsa yeniden adlandır
        if os.path.exists(old_manga_dir) and not os.path.exists(new_manga_dir):
            try:
                os.rename(old_manga_dir, new_manga_dir)
                print(f"[Migrate] Renamed: {manga_id[:12]}... -> '{safe_title}'")
                changed = True
            except Exception as e:
                print(f"[Migrate] Rename error: {e}")
                continue

        if not os.path.exists(new_manga_dir):
            continue

        # folder_name güncelle
        manga_data["folder_name"] = safe_title

        # cover_path'i güncelle
        cover_path = manga_data.get("cover_path", "")
        if cover_path and manga_id in cover_path:
            new_cover_rel = cover_path.replace(manga_id, safe_title)
            manga_data["cover_path"] = new_cover_rel
            changed = True

        # Klasör simgesini ayarla (daha önce ayarlanmamışsa)
        cover_rel = manga_data.get("cover_path", "")
        # cover.jpg veya cover.webp olabilir; her ikisini de dene
        cover_abs = ""
        if cover_rel:
            cover_abs = os.path.join(
                BASE_DIR,
                cover_rel.replace("/", os.sep)
            )
        # cover bulunamadıysa klasörde ara
        if not cover_abs or not os.path.exists(cover_abs):
            for ext in ("cover.webp", "cover.jpg", "cover.jpeg", "cover.png"):
                candidate = os.path.join(new_manga_dir, ext)
                if os.path.exists(candidate):
                    cover_abs = candidate
                    break
        ico_path = os.path.join(new_manga_dir, "folder.ico")
        if cover_abs and os.path.exists(cover_abs) and not os.path.exists(ico_path):
            _set_folder_icon(new_manga_dir, cover_abs)

        # Bölüm klasörlerini de yeniden adlandır
        chapters = manga_data.get("downloaded_chapters", {})
        for ch_id, ch_data in chapters.items():
            ch_num = ch_data.get("chapter", "0")
            ch_title = ch_data.get("title", "")
            safe_ch = _safe_chapter_folder(ch_num, ch_title)

            old_ch_dir = os.path.join(new_manga_dir, ch_id)
            new_ch_dir = os.path.join(new_manga_dir, safe_ch)

            if os.path.exists(old_ch_dir) and not os.path.exists(new_ch_dir):
                try:
                    os.rename(old_ch_dir, new_ch_dir)
                    print(f"[Migrate] Chapter folder: '{ch_id[:20]}...' -> '{safe_ch}'")
                    changed = True
                except Exception as e:
                    print(f"[Migrate] Chapter rename error: {e}")

            # path'i güncelle
            if os.path.exists(new_ch_dir):
                rel = os.path.relpath(new_ch_dir, BASE_DIR)
                ch_data["path"] = rel.replace("\\", "/")
                changed = True

    if changed:
        try:
            with open(LIBRARY_FILE, "w", encoding="utf-8") as f:
                json.dump(library, f, indent=4, ensure_ascii=False)
            print("[Migrate] library.json updated.")
        except Exception as e:
            print(f"[Migrate] library.json save error: {e}")


if __name__ == "__main__":
    migrate_downloads()
    print("[Migrate] Done.")
