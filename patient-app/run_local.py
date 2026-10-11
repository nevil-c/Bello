"""Single-machine mode: launch a matching backend and patient UI on this device; keep credentials out of source code.
For bedside units that use the LAN server, run nurse_call.py with BELLO_SERVER and BELLO_API_TOKEN instead."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parent

def main():
    root=tk.Tk();root.withdraw()
    token=secrets.token_urlsafe(32)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    server=f'http://127.0.0.1:{port}'
    backend_env=os.environ.copy()
    backend_env.pop('ELEVENLABS_API_KEY',None)
    backend_env.update(BELLO_API_TOKEN=token)
    # The small model this mode has always used; the LAN server defaults to large-v3-turbo.
    backend_env.setdefault('BELLO_WHISPER_MODEL','tiny')
    patient_env=os.environ.copy();patient_env.pop('ELEVENLABS_API_KEY',None)
    patient_env.update(BELLO_API_TOKEN=token,BELLO_SERVER=server)
    backend=None
    try:
        backend=subprocess.Popen([sys.executable,str(ROOT.parent/'hospital-dashboard/backend.py'),'--port',str(port)],
            env=backend_env,stdout=subprocess.DEVNULL)
        ready=False
        for _ in range(600):
            if backend.poll() is not None:break
            try:
                with urlopen(Request(server+'/api/requests',headers={'X-Bello-Token':token}),timeout=.5):ready=True
                break
            except Exception:time.sleep(.1)
        if not ready:raise RuntimeError('The local backend could not start. Install backend requirements and run prepare_model.py. Check the terminal for details.')
        print(f'Bello server: {server}. Staff apps: BELLO_SERVER={server} python staff-app/clerk.py (session token: {token})',flush=True)
        root.destroy()
        subprocess.run([sys.executable,str(ROOT/'nurse_call.py')],env=patient_env,check=True)
    except Exception as error:
        try:messagebox.showerror('Bello setup',str(error),parent=root)
        except tk.TclError:print(str(error),file=sys.stderr)
    finally:
        if backend and backend.poll() is None:
            backend.terminate()
            try:backend.wait(timeout=5)
            except subprocess.TimeoutExpired:backend.kill();backend.wait()

if __name__=='__main__':main()
