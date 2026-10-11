"""Explicit one-time model download; runtime transcription stays offline."""
import argparse
from pathlib import Path
from faster_whisper.utils import download_model
from local_stt import MODEL_DIR

def main():
    p=argparse.ArgumentParser();p.add_argument('--model',choices=['tiny','base'],default='tiny');p.add_argument('--output',type=Path,default=MODEL_DIR);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    download_model(a.model,output_dir=str(a.output))
    print(f'Model downloaded to {a.output}')
if __name__=='__main__':main()
