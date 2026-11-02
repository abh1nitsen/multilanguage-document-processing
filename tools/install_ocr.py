"""Install locked OCR dependencies; bootstrap npm locally if only Node is available."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

root = Path(__file__).resolve().parents[1]
node = shutil.which("node")
if not node:
    raise SystemExit("Install Node.js from https://nodejs.org first.")
npm = shutil.which("npm")
if npm:
    command = [npm]
else:
    target = root / "data/tooling/npm"
    cli = target / "package/bin/npm-cli.js"
    if not cli.exists():
        # Pinned installer, integrity verified from the official npm registry metadata.
        with urllib.request.urlopen("https://registry.npmjs.org/npm/12.2.1", timeout=30) as response:
            metadata = json.load(response)
        distribution = metadata["dist"]
        with urllib.request.urlopen(distribution["tarball"], timeout=30) as response:
            content = response.read(30 * 1024 * 1024)
        algorithm, digest = distribution["integrity"].split("-", 1)
        if algorithm != "sha512" or base64.b64encode(hashlib.sha512(content).digest()).decode() != digest:
            raise SystemExit("npm download failed integrity validation.")
        target.mkdir(parents=True, exist_ok=True)
        archive = target / "npm.tgz"
        archive.write_bytes(content)
        with tarfile.open(archive) as package:
            package.extractall(target, filter="data")
        archive.unlink()
    command = [node, str(cli)]
raise SystemExit(subprocess.run(command + ["ci", "--ignore-scripts"], cwd=root, shell=False).returncode)
