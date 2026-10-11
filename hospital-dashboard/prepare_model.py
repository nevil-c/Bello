"""Explicit one-time model download; runtime transcription stays offline.

  python prepare_model.py                       # large-v3-turbo (LAN server default)
  python prepare_model.py --model large-v3      # full large-v3, needed for BELLO_EN_ENGINE=whisper
  python prepare_model.py --model tiny          # small enough for a Pi-only setup
"""
import argparse
from pathlib import Path
from faster_whisper.utils import download_model

MODELS=['tiny','base','small','medium','large-v3','large-v3-turbo']

def main():
    p=argparse.ArgumentParser();p.add_argument('--model',choices=MODELS,default='large-v3-turbo');p.add_argument('--output',type=Path);a=p.parse_args()
    output=a.output or Path(__file__).parent/'models'/a.model
    output.mkdir(parents=True,exist_ok=True)
    download_model(a.model,output_dir=str(output))
    print(f'Model downloaded to {output}. Select it with BELLO_WHISPER_MODEL={a.model} (or BELLO_MODEL_DIR).')
if __name__=='__main__':main()
