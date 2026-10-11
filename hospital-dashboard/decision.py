"""Request routing with the fine-tuned Laya checkpoint (see ../decision-model).

BELLO_LAYA_MODEL   checkpoint directory (default ~/laya-out/ckpt/laya-patient)
BELLO_LAYA_DEVICE  mps | cuda | cpu (default: the first one available)
"""
import os
from pathlib import Path
import sys
import threading

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'decision-model'))
from build_dataset import QUESTIONS, state  # the labels the checkpoint was trained on

MODEL_DIR=Path(os.path.expanduser(os.environ.get('BELLO_LAYA_MODEL','~/laya-out/ckpt/laya-patient')))
# The checkpoint's department labels -> the shared queues the dashboards use.
DEPARTMENTS={'Nurse':'Primary Nurse','Housekeeping & facilities':'Housekeeping & Facilities',
    'food services':'Food Services','Unit Clerk / IT Maintenance':'Unit Clerk / IT Maintenance'}
# 77 of 1064 training rows are TRUE; a 0.5 cut misses some of them (decision-model README).
MORE_INFO_THRESHOLD=0.3
_agent=None
_lock=threading.Lock()

def device():
    wanted=os.environ.get('BELLO_LAYA_DEVICE')
    if wanted:return wanted
    import torch
    if torch.backends.mps.is_available():return 'mps'
    if torch.cuda.is_available():return 'cuda'
    return 'cpu'

def load_model():
    global _agent
    with _lock:
        if _agent is None:
            if not MODEL_DIR.is_dir():
                raise RuntimeError(f'Decision model missing at {MODEL_DIR}. Train it (decision-model/README.md) or set BELLO_LAYA_MODEL.')
            from laya import load
            _agent=load(str(MODEL_DIR),device=device())
    return _agent

def classify(english_text):
    """English request text -> category, department, urgency 1-10 and the raw probabilities."""
    agent=load_model()
    with _lock:
        answers=agent.predict_batch([state({'patient_request':english_text})],QUESTIONS)[0]['answers']
    category,department,urgency=answers['category'],answers['department'],answers['urgency']
    level=int(max(urgency['probabilities'],key=urgency['probabilities'].get))
    more_info=float(answers['more_info_needed']['noul'])
    return {'category':category['choice'],'department':DEPARTMENTS[department['choice']],
        'urgency':level+1,'urgency_confidence':float(urgency['answer_confidence']),
        'more_info_needed':more_info>=MORE_INFO_THRESHOLD,
        'scores':{'category':category['probabilities'],'department':department['probabilities'],
            'urgency':urgency['probabilities'],'more_info_needed':more_info}}
