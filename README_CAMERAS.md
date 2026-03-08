# Kamera-Auswahl

## Konfiguration

Kopiere `cameras.json.example` nach `cameras.json` und passe die Werte an:

```json
{
  "cam1": 2,
  "cam2": 4
}
```

- **Index**: `0`, `1`, `2`, … für `/dev/video0`, `/dev/video1`, …
- **Pfad**: `"/dev/video4"` für direkte Geräteangabe

## Nikon D3100 (USB Mini-B)

Die Nikon D3100 nutzt einen **USB Mini-B Stecker** (trapezförmig, „hutähnlich“).

**Standard:** DSLRs erscheinen nicht als `/dev/video*`. Nutze:

1. **gphoto2** + **v4l2loopback** installieren:
   ```bash
   sudo apt install gphoto2 v4l2loopback-dkms ffmpeg
   ```

2. Virtuelles Gerät erstellen und Kamerapreview starten:
   ```bash
   sudo modprobe v4l2loopback
   gphoto2 --stdout --capture-movie | ffmpeg -i - -vcodec rawvideo -pix_fmt yuv420p -threads 0 -f v4l2 /dev/video4
   ```

3. In `cameras.json` eintragen:
   ```json
   {
     "cam1": 2,
     "cam2": "/dev/video4"
   }
   ```

## Verfügbare Kameras prüfen

```bash
v4l2-ctl --list-devices
```
