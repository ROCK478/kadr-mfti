from pathlib import Path
import argparse
import ssl
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.local_model import MODEL_BYTES, MODEL_NAME, MODEL_URL, ROOT, verify


def download(directory):
    import certifi
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / MODEL_NAME
    if final.exists():
        try:
            verify(final)
            print("Model verified:", final)
            return
        except ValueError:
            final.unlink()
    partial = final.with_suffix('.gguf.part')
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(MODEL_URL, timeout=60, context=context) as response, partial.open('wb') as f:
            done = 0
            last = -1
            while chunk := response.read(1024 * 1024):
                f.write(chunk)
                done += len(chunk)
                pct = done * 100 // MODEL_BYTES
                if pct // 5 != last:
                    last = pct // 5
                    print(f'Model: {pct}%', flush=True)
        verify(partial)
        partial.replace(final)
        print('Model installed:', final)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=ROOT / 'models')
    download(parser.parse_args().directory)
