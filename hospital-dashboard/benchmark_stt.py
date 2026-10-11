"""Measure actual device performance: python benchmark_stt.py request.wav."""
import argparse
import json
import resource
import sys
import time
from pathlib import Path
from local_stt import load_model, transcribe

def main():
    p=argparse.ArgumentParser();p.add_argument('audio',type=Path);a=p.parse_args()
    start=time.monotonic();load_model();load=time.monotonic()-start
    start=time.monotonic();result=transcribe(a.audio.read_bytes(),a.audio.suffix.lstrip('.'));elapsed=time.monotonic()-start
    memory=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024)
    print(json.dumps(dict(model_load_seconds=round(load,2),transcription_seconds=round(elapsed,2),peak_process_mib=round(memory,1),**result),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
