"""Explicit one-time download of the NLLB-200 translation model (about 2.5 GB); runtime stays offline.

  python prepare_translation_model.py
"""
import argparse
from pathlib import Path
from huggingface_hub import snapshot_download
from translation import MODEL_DIR

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',default='facebook/nllb-200-distilled-600M');p.add_argument('--output',type=Path,default=MODEL_DIR);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    snapshot_download(a.repo,local_dir=str(a.output),allow_patterns=['*.json','*.model','*.bin','*.safetensors'])
    print(f'Translation model downloaded to {a.output}')
if __name__=='__main__':main()
