#!/usr/bin/env python3
"""Vendor pinned browser assets without a Node.js build step.

Run once on an Internet-connected machine before building the Docker image:
    python scripts/vendor_assets.py

Only downloads a fixed manifest from jsDelivr; never loads URLs from project files.
The app switches to local assets atomically once .ready is present.
"""
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
from urllib.request import urlopen, Request
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'source' / 'static' / 'vendor' / 'npm'
READY = ROOT / 'source' / 'static' / 'vendor' / '.ready'
ASSETS = (
    '@tabler/core@1.7.4/dist/css/tabler.min.css',
    '@tabler/core@1.7.4/dist/js/tabler.min.js',
    '@tabler/icons-webfont@3.35.0/dist/tabler-icons.min.css',
    'codemirror@5.65.20/lib/codemirror.min.css',
    'codemirror@5.65.20/theme/material-darker.min.css',
    'codemirror@5.65.20/lib/codemirror.min.js',
    'codemirror@5.65.20/mode/markdown/markdown.min.js',
    'marked@15.0.12/marked.min.js',
    'dompurify@3.2.7/dist/purify.min.js',
)
BASE='https://cdn.jsdelivr.net/npm/'
LIMIT=12*1024*1024


def download_relative(relative):
    if relative.startswith('/') or '..' in Path(relative).parts or not relative:
        raise ValueError('Unsafe vendor path')
    address = BASE + relative
    with urlopen(Request(address,headers={'User-Agent':'SpecWeave-asset-vendor/1.0'}),timeout=30) as response:
        if response.status!=200:raise RuntimeError(f'Fetch failed: {address}')
        content = response.read(LIMIT+1)
        if len(content)>LIMIT:raise RuntimeError(f'Asset too large: {relative}')
        if content.lstrip().lower().startswith((b'<!doctype html',b'<html')):
            raise RuntimeError('Unexpected HTML response from CDN')
    path=DEST/relative
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_bytes(content)
    tmp.replace(path)
    return hashlib.sha256(content).hexdigest(),content


def main():
    READY.unlink(missing_ok=True)
    hashes={}
    queue=list(ASSETS)
    done=set()
    while queue:
        path=queue.pop(0)
        if path in done:continue
        done.add(path)
        sha,content=download_relative(path)
        hashes[path]=sha
        print(f'{path}: {len(content)} bytes')
        if path.endswith('.css'):
            # Resolve local font dependencies referenced by the CSS.
            decoded=content.decode('utf-8')
            for raw in re.findall(r'url\(\s*["\']?([^"\')]+)',decoded):
                raw=raw.strip().split('?',1)[0].split('#',1)[0]
                if raw.startswith(('data:','//')):continue
                resolved=urlparse(urljoin(BASE+path,raw))
                if resolved.netloc!='cdn.jsdelivr.net' or not resolved.path.startswith('/npm/'):
                    continue
                subpath=unquote(resolved.path[len('/npm/'):])
                if not subpath.endswith(('.woff2','.woff','.ttf','.svg')):continue
                if subpath not in done:queue.append(subpath)
    READY.parent.mkdir(parents=True,exist_ok=True)
    READY.write_text(json.dumps({'files':hashes,'source':BASE},indent=2,sort_keys=True),encoding='utf-8')
    print(f'Ready: {len(hashes)} local assets. Rebuild image to ship them.')


if __name__=='__main__':main()
