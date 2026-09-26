---
name: gimp-cli
description: Edit images headlessly with GIMP from the command line — convert formats (xcf/psd/png/jpg/webp/tiff), crop, resize, rotate, flip, grayscale, brightness/contrast, batch folders, or run raw Script-Fu. Use when the user asks to edit, convert, resize or batch-process images "with GIMP", wants GIMP as a CLI, or needs to open .xcf files without the GUI.
---

# gimp-cli

`scripts/gimp_cli.py` drives `gimp-console` in batch mode with generated Script-Fu. It detects GIMP 2.10 vs 3.x and emits the right API dialect. Python stdlib only.

## Prerequisites

- GIMP 2.10 or 3.x installed. Auto-detected on PATH, `C:\Program Files\GIMP 3\bin`, `C:\Program Files\GIMP 2\bin`, and `/Applications/GIMP.app`.
- Anything else: `export GIMP_CONSOLE=/path/to/gimp-console-3.0` or pass `--gimp`.
- Always run `doctor` first when a session starts.

## Commands

```bash
S=scripts/gimp_cli.py   # path relative to this skill folder

python3 $S doctor                                   # locate GIMP, print version/dialect
python3 $S info photo.jpg art.xcf --json            # size, mode (RGB/GRAY/INDEXED), layers
python3 $S convert art.xcf -o art.png               # format comes from the extension
python3 $S edit in.jpg -o out.jpg --resize 1080x    # keep aspect
python3 $S edit in.png -o sq.png --crop 1080x1080+0+420
python3 $S edit "shots/*.png" --out-dir web --format webp --resize 50% --force
python3 $S edit in.jpg -o bw.jpg --grayscale --contrast 0.2 --brightness 0.05
python3 $S script '(gimp-message "hello")'          # raw Script-Fu; '-' reads stdin
python3 $S edit in.jpg -o out.png --rotate 90 --dry-run   # print the Script-Fu only
```

`edit` always applies operations in this order: crop → resize → rotate → flip → grayscale → brightness/contrast.

| Flag           | Format                                       |
| -------------- | -------------------------------------------- |
| `--resize`     | `WxH`, `Wx` (keep aspect), `xH`, `N%`        |
| `--crop`       | `WxH+X+Y` (must fit inside the image)        |
| `--rotate`     | `90`, `180`, `270`                           |
| `--flip`       | `h` or `v`                                   |
| `--brightness` | `-1.0`..`1.0`                                |
| `--contrast`   | `-1.0`..`1.0`                                |
| `--out-dir`    | batch mode; `--format` changes the extension |

## Rules

1. Inputs are never overwritten in place; existing outputs need `--force`.
2. Each output is checked after GIMP exits. `ok`/`FAIL` is printed per file and the exit code is 1 if any file failed. GIMP's own error is printed as `gimp error: ...`.
3. GIMP 3 stops a batch at the first failing file. Re-run the rest after fixing it.
4. All files in one call share one GIMP start-up (~3–10 s). Batch with globs rather than looping per file.
5. Exporting to JPG/BMP flattens transparency onto the background. Use PNG or WebP to keep alpha.
6. For effects not covered here, use `script` with Script-Fu for the detected dialect (check `doctor`). Procedure names differ between 2.10 and 3.x, e.g. `gimp-image-width` vs `gimp-image-get-width`.

## Exit codes

`0` ok · `1` one or more files failed · `2` bad arguments / GIMP not found.
