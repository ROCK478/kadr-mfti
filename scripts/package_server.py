"""Package only deployable sources; never include local workspace or keys."""
import argparse
import hashlib
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]
ALLOWED=['app','scripts','examples','docs','licenses','deploy','Dockerfile','requirements.txt','README.md','LICENSE','THIRD_PARTY.md']


def package(output):
    output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(output,'w:gz') as archive:
        for entry in ALLOWED:
            path=ROOT/entry
            for file in sorted(path.rglob('*')) if path.is_dir() else [path]:
                relative=file.relative_to(ROOT)
                if not file.is_file() or file.is_symlink() or '__pycache__' in relative.parts:continue
                if file.name=='.env' or file.suffix in {'.pyc','.sqlite3','.pem','.key'}:continue
                archive.add(file,arcname=str(relative),recursive=False)
    with tarfile.open(output) as archive:
        names=archive.getnames()
        assert not any(name.startswith(('data/','models/','.git/','Для ')) or name.endswith(('.sqlite3','.key','.pem')) for name in names)
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix+'.sha256').write_text(digest+'  '+output.name+'\n')
    print(f'{output}\n{len(names)} files, {output.stat().st_size} bytes\nSHA256 {digest}')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output');args=parser.parse_args();package(args.output)
