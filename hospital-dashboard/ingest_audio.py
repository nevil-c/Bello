"""Submit a recording: python3 ingest_audio.py 8 /path/to/request.wav
Set BELLO_API_TOKEN to the private token printed by backend.py.
"""
import argparse,json,os,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('request_id',type=int);p.add_argument('file',type=Path);p.add_argument('--server',default='http://127.0.0.1:8766');a=p.parse_args()
    token=os.environ.get('BELLO_API_TOKEN')
    if not token:p.error('Set BELLO_API_TOKEN to the backend token first')
    req=Request(f'{a.server}/api/requests/{a.request_id}/audio',data=a.file.read_bytes(),headers={'X-Bello-Token':token,'Content-Type':'application/octet-stream'})
    try:
        with urlopen(req,timeout=150) as response:result=json.load(response)
        deadline=time.monotonic()+600
        while result.get('state') in ('Queued','Transcribing'):
            if time.monotonic()>deadline:raise SystemExit('Still processing. Check the dashboard before retrying.')
            time.sleep(1)
            status=Request(f'{a.server}/api/requests/{a.request_id}',headers={'X-Bello-Token':token})
            with urlopen(status,timeout=15) as response:result=json.load(response)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except HTTPError as e:raise SystemExit(e.read().decode())
if __name__=='__main__':main()
