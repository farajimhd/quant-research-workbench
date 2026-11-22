"""Laptop-only immutable deployment of the already pushed revision."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,hashlib,json,subprocess,tarfile
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--revision',required=True);a=p.parse_args()
    repo=Path(__file__).resolve().parents[3]
    revision=subprocess.check_output(['git','rev-parse',a.revision],cwd=repo,text=True).strip()
    remote=subprocess.check_output(['git','rev-parse','@{upstream}'],cwd=repo,text=True).strip()
    if revision!=remote:raise ValueError('Deploy only pushed current upstream revision')
    base=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML');runtime=base/'runtimes/bar_gpt'
    snapshot=base/'codes'/('bargpt-phase1-v22-'+revision[:9])
    if snapshot.exists():raise FileExistsError(snapshot)
    if not runtime.is_dir():raise FileNotFoundError('Required workstation runtime root')
    archive=runtime/('source-v22-'+revision[:9]+'.tar')
    subprocess.run(['git','archive','--format=tar','-o',str(archive),revision,'research/bar_gpt','research/mlops','research/__init__.py','pipelines'],cwd=repo,check=True)
    snapshot.mkdir()
    with tarfile.open(archive) as tar:tar.extractall(snapshot,filter='data')
    files={}
    with tarfile.open(archive) as tar:
        for member in tar:
            if not member.isfile():continue
            expected=hashlib.sha256(tar.extractfile(member).read()).hexdigest()
            actual=hashlib.sha256((snapshot/member.name).read_bytes()).hexdigest()
            if actual!=expected:raise ValueError(member.name)
            files[member.name]=expected
    proof=dict(revision=revision,files=files)
    (runtime/('deployment-'+revision[:9]+'.json')).write_text(json.dumps(proof,indent=2))
    print(json.dumps(dict(revision=revision,snapshot=str(snapshot),verified_files=len(files))))


if __name__=='__main__':main()
