"""Create local configuration once and initialize the database/admin."""
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
config = ROOT / '.env'
if not config.exists():
    with config.open('x', encoding='utf-8') as handle:
        handle.write('APP_ENV=local\nPUBLIC_ORIGIN=http://localhost:8000\nCOOKIE_SECURE=false\nAPP_SECRET=' + secrets.token_urlsafe(48) + '\n')
    if os.name != 'nt': config.chmod(0o600)
subprocess.run([sys.executable, '-m', 'app.manage', 'init'], cwd=ROOT / 'backend', check=True)
