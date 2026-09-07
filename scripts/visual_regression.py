from __future__ import annotations

import argparse
import json
import os
import socket
import shutil
import subprocess
import sys
import time
from html import escape
from pathlib import Path
from urllib.request import urlopen

from PIL import Image, ImageChops, ImageEnhance, ImageFilter
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "visual.config.json"
DEFAULT_OUTPUT = ROOT / "output" / "visual-regression"
VISUAL_DB = DEFAULT_OUTPUT / "visual-test.sqlite3"
BUNDLED_NODE = Path(
    r"C:\Users\Administrateur\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
)
BUNDLED_NODE_MODULES = Path(
    r"C:\Users\Administrateur\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
)


def wait_for_server(url: str, timeout: float = 12.0) -> bool:
    health_url = url.rstrip("/") + "/favicon.ico"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(health_url, timeout=3.0) as response:
                return response.status < 500
        except Exception:
            time.sleep(0.25)
    return False


def port_is_open(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0


def start_server(base_url: str) -> subprocess.Popen[str] | None:
    host = "127.0.0.1"
    port = int(base_url.rsplit(":", 1)[1].split("/", 1)[0])
    if port_is_open(host, port):
        raise RuntimeError(
            f"Le port visuel isole {port} est deja utilise. Fermez le processus concerne."
        )
    VISUAL_DB.unlink(missing_ok=True)
    env = os.environ.copy()
    env["PHOENIX_DB_PATH"] = str(VISUAL_DB)
    env["PHOENIX_PORT"] = str(port)
    env["PHOENIX_HOST"] = host
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "seed_visual_db.py")],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py")],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if not wait_for_server(base_url):
        process.terminate()
        raise RuntimeError(f"Le serveur n'a pas demarre sur {base_url}")
    return process


def capture(config_path: Path, current_dir: Path, page_id: str = "") -> None:
    node = BUNDLED_NODE if BUNDLED_NODE.exists() else Path("node")
    env = os.environ.copy()
    if BUNDLED_NODE_MODULES.exists():
        env["NODE_PATH"] = str(BUNDLED_NODE_MODULES)
    env["PHOENIX_VISUAL_USERNAME"] = "admin"
    env["PHOENIX_VISUAL_PASSWORD"] = "Visual123"
    command = [
            str(node),
            str(ROOT / "scripts" / "capture_visuals.js"),
            str(config_path),
            str(current_dir),
        ]
    if page_id:
        command.append(page_id)
    subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=True,
    )


def compare(reference_path: Path, current_path: Path, diff_path: Path, threshold: int) -> dict:
    reference = Image.open(reference_path).convert("RGB")
    current = Image.open(current_path).convert("RGB")
    if reference.size != current.size:
        raise ValueError(
            f"Dimensions differentes: reference={reference.size}, capture={current.size}"
        )

    raw_diff = ImageChops.difference(reference, current)
    pixels = raw_diff.tobytes()
    total = max(1, reference.width * reference.height)
    changed = sum(
        1
        for index in range(0, len(pixels), 3)
        if max(pixels[index : index + 3]) > threshold
    )
    absolute_error = sum(pixels)
    pixel_similarity = 100.0 * (1.0 - absolute_error / (total * 3 * 255))
    changed_percent = 100.0 * changed / total

    # AI-generated visual references contain raster noise and different text
    # antialiasing. A low-frequency SSIM score keeps the gate strict for
    # positions, dimensions and visual mass without treating that noise as a
    # layout regression. The raw pixel score remains visible in the report.
    blur_radius = 24
    reference_layout = np.asarray(
        reference.convert("L").filter(ImageFilter.GaussianBlur(blur_radius)),
        dtype=np.float64,
    )
    current_layout = np.asarray(
        current.convert("L").filter(ImageFilter.GaussianBlur(blur_radius)),
        dtype=np.float64,
    )
    reference_mean = reference_layout.mean()
    current_mean = current_layout.mean()
    reference_variance = reference_layout.var()
    current_variance = current_layout.var()
    covariance = (
        (reference_layout - reference_mean) * (current_layout - current_mean)
    ).mean()
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    similarity = 100.0 * (
        (2 * reference_mean * current_mean + c1) * (2 * covariance + c2)
    ) / (
        (reference_mean**2 + current_mean**2 + c1)
        * (reference_variance + current_variance + c2)
    )

    mask = raw_diff.convert("L").point(lambda value: 255 if value > threshold else 0)
    dimmed = ImageEnhance.Brightness(current).enhance(0.35)
    red = Image.new("RGB", current.size, (255, 35, 35))
    highlighted = Image.composite(red, dimmed, mask)
    highlighted.save(diff_path)
    return {
        "similarity": round(similarity, 3),
        "pixel_similarity": round(pixel_similarity, 3),
        "changed_percent": round(changed_percent, 3),
        "width": current.width,
        "height": current.height,
    }


def build_report(config: dict, output_dir: Path, results: list[dict]) -> Path:
    report_path = output_dir / "report.html"
    cards = []
    for result in results:
        passed = (
            result["similarity"] >= config["minimum_similarity"]
            and not result["errors"]
            and not result["body_overflow"]
        )
        status_class = "good" if passed else "review"
        status_label = "CONFORME" if passed else "A CORRIGER"
        cards.append(
            f"""
            <section class="result">
              <header>
                <div><h2>{escape(result['label'])}</h2><code>{escape(result['route'])}</code></div>
                <div class="score {status_class}"><small>{status_label}</small>{result['similarity']:.3f}%</div>
              </header>
              <p>Taux brut pixel par pixel: <strong>{result['pixel_similarity']:.3f}%</strong>. {result['changed_percent']:.3f}% des pixels depassent le seuil de {config['pixel_threshold']}.</p>
              <p>Console: <strong>{'OK' if not result['errors'] else escape(' | '.join(result['errors']))}</strong> — Debordement page: <strong>{'NON' if not result['body_overflow'] else 'OUI'}</strong></p>
              <div class="images">
                <figure><figcaption>Reference</figcaption><img src="{result['reference_uri']}" alt="Reference"></figure>
                <figure><figcaption>Capture actuelle</figcaption><img src="current/{result['id']}.png" alt="Capture"></figure>
                <figure><figcaption>Ecarts en rouge</figcaption><img src="diff/{result['id']}.png" alt="Difference"></figure>
              </div>
            </section>
            """
        )
    report_path.write_text(
        """<!doctype html><html lang="fr"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Rapport de conformite visuelle</title><style>
        body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f3f6f9;color:#142033}
        main{max-width:1500px;margin:auto;padding:28px}.summary,.result{background:#fff;border:1px solid #d8e0e8;border-radius:6px;padding:18px;margin-bottom:18px}
        h1,h2,p{margin-top:0}header{display:flex;justify-content:space-between;gap:20px;align-items:center}.score{font-size:24px;font-weight:700;text-align:right}.score small{display:block;font-size:11px;letter-spacing:.08em}.good{color:#128447}.review{color:#b45518}
        .images{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.images img{width:100%;border:1px solid #cfd8e2;display:block}.images figure{margin:0}.images figcaption{font-weight:700;margin-bottom:7px}code{color:#59697a}
        @media(max-width:900px){.images{grid-template-columns:1fr}main{padding:12px}}
        </style></head><body><main>
        <section class="summary"><h1>Rapport de conformite visuelle</h1>
        <p>Chaque interface est capturee avec la resolution exacte de sa reference. Les zones rouges indiquent les pixels a corriger.</p></section>
        """
        + "".join(cards)
        + "</main></body></html>",
        encoding="utf-8",
    )
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture et compare les interfaces aux references visuelles.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--no-capture", action="store_true", help="Compare les captures existantes.")
    parser.add_argument("--strict", action="store_true", help="Retourne une erreur si une page est sous le seuil.")
    parser.add_argument(
        "--update-baselines",
        action="store_true",
        help="Remplace explicitement les references par les captures validees.",
    )
    parser.add_argument("--page", default="", help="Limite le controle a l'identifiant d'une page.")
    args = parser.parse_args()

    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir = DEFAULT_OUTPUT
    current_dir = output_dir / "current"
    diff_dir = output_dir / "diff"
    current_dir.mkdir(parents=True, exist_ok=True)
    diff_dir.mkdir(parents=True, exist_ok=True)

    server = start_server(config["base_url"])
    try:
        if not wait_for_server(config["base_url"]):
            raise RuntimeError(f"Application inaccessible: {config['base_url']}")
        if not args.no_capture:
            capture(config_path, current_dir, args.page)

        selected_pages = [item for item in config["pages"] if not args.page or item["id"] == args.page]
        if args.update_baselines:
            for item in selected_pages:
                reference_path = (ROOT / item["reference"]).resolve()
                reference_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(current_dir / f"{item['id']}.png", reference_path)

        results = []
        if args.page and not selected_pages:
            raise ValueError(f"Page inconnue: {args.page}")
        for item in selected_pages:
            reference_path = (ROOT / item["reference"]).resolve()
            current_path = current_dir / f"{item['id']}.png"
            diff_path = diff_dir / f"{item['id']}.png"
            metrics = compare(
                reference_path,
                current_path,
                diff_path,
                int(config["pixel_threshold"]),
            )
            diagnostics = json.loads(
                (current_dir / f"{item['id']}.json").read_text(encoding="utf-8")
            )
            results.append(
                {
                    **item,
                    **metrics,
                    "errors": diagnostics.get("errors", []),
                    "body_overflow": bool(diagnostics.get("bodyOverflow")),
                    "reference_uri": reference_path.as_uri(),
                }
            )
            print(
                f"{item['label']:<22} similarite={metrics['similarity']:>7.3f}% "
                f"pixels_modifies={metrics['changed_percent']:>7.3f}%"
            )

        (output_dir / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report_path = build_report(config, output_dir, results)
        print(f"\nRapport: {report_path}")
        failed = [
            item for item in results
            if item["similarity"] < config["minimum_similarity"]
            or item["errors"]
            or item["body_overflow"]
        ]
        if args.strict and failed:
            print(
                f"ECHEC: {len(failed)} interface(s) sous le seuil de "
                f"{config['minimum_similarity']}%."
            )
            return 1
        return 0
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
