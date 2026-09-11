"""Windows/macOS/Linux source launcher. No globally installed Python packages required."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / '.venv'
PYTHON = VENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def run(args, cwd=ROOT):
    subprocess.run([str(arg) for arg in args], cwd=cwd, check=True)


def fingerprint(files):
    h = hashlib.sha256()
    for p in sorted(files):
        h.update(str(p.relative_to(ROOT)).encode()); h.update(p.read_bytes())
    return h.hexdigest()


def main():
    if sys.version_info < (3, 11): raise RuntimeError('请安装 Python 3.11 或更新版本，并将其加入 PATH。')
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm: raise RuntimeError('请安装 Node.js 22 LTS，然后重新打开此窗口。')
    if not PYTHON.exists(): run([sys.executable, '-m', 'venv', VENV])
    state_file = VENV / 'wenjuan-build-state.json'
    try: state = json.loads(state_file.read_text())
    except (FileNotFoundError, ValueError): state = {}
    requirement_hash = fingerprint([ROOT / 'backend/requirements.txt'])
    if state.get('requirements') != requirement_hash:
        run([PYTHON, '-m', 'pip', 'install', '-r', ROOT / 'backend/requirements.txt'])
        state['requirements'] = requirement_hash
    package_hash = fingerprint([ROOT / 'frontend/package.json', ROOT / 'frontend/package-lock.json'])
    if state.get('packages') != package_hash or not (ROOT / 'frontend/node_modules').exists():
        run([npm, 'ci', '--no-audit', '--no-fund'], ROOT / 'frontend')
        state['packages'] = package_hash
    sources = list((ROOT / 'frontend/src').rglob('*'))
    sources += [ROOT / 'frontend/index.html', ROOT / 'frontend/vite.config.ts', ROOT / 'frontend/tsconfig.json', ROOT / 'frontend/package-lock.json']
    source_hash = fingerprint([p for p in sources if p.is_file()])
    if state.get('frontend') != source_hash or not (ROOT / 'frontend/dist/index.html').exists():
        run([npm, 'run', 'build'], ROOT / 'frontend')
        state['frontend'] = source_hash
    state_file.write_text(json.dumps(state), encoding='utf-8')
    run([PYTHON, ROOT / 'scripts/setup.py'])
    print('\n回答页：http://localhost:8000\n控制台：http://localhost:8000/admin\n保持本窗口运行，按 Ctrl+C 停止。\n', flush=True)
    run([PYTHON, '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000', '--workers', '1'], ROOT / 'backend')

if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: pass
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print('\n启动未完成：' + str(error), file=sys.stderr)
        sys.exit(1)
