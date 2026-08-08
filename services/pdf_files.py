import subprocess
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None


PDF_VIEWER_CANDIDATES = [
    Path(r"C:\Program Files\Adobe\Acrobat DC\Acrobat\Acrobat.exe"),
    Path(r"C:\Program Files (x86)\Adobe\Acrobat DC\Acrobat\Acrobat.exe"),
    Path(r"C:\Program Files\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe"),
    Path(r"C:\Program Files (x86)\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe"),
]


def archive_metadata(path):
    name = path.name
    document_type = "PDF"
    invoice_number = ""
    for prefix, label in (
        ("Devis_Quantitatif_", "DQ"),
        ("Devis_Estimatif_", "DE"),
        ("Facture_", "FACT"),
    ):
        if name.startswith(prefix):
            document_type = label
            rest = name[len(prefix):].removesuffix(".pdf")
            invoice_number = rest.rsplit("_", 1)[0] if "_" in rest else rest
            break
    return document_type, invoice_number


def cleanup_old_exports(export_dir, stem, suffix, keep=3):
    files = sorted(
        export_dir.glob(f"{stem}_*{suffix}"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in files[keep:]:
        try:
            path.unlink()
        except OSError:
            pass


def open_pdf_with_system_viewer(pdf_path):
    adobe = next((path for path in PDF_VIEWER_CANDIDATES if path.exists()), None)
    if not adobe and winreg is not None:
        for key_path in (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Acrobat.exe",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\AcroRd32.exe",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\Acrobat.exe",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\AcroRd32.exe",
        ):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                    candidate = Path(winreg.QueryValue(key, ""))
                    if candidate.exists():
                        adobe = candidate
                        break
            except OSError:
                pass
    if adobe:
        subprocess.Popen(
            [str(adobe), str(pdf_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        )
        return
    subprocess.Popen(
        ["cmd", "/c", "start", "", str(pdf_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
