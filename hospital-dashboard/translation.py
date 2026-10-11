"""Offline text translation with NLLB-200 (distilled 600M). Download once with prepare_translation_model.py.

Used for two things: the English text the decision model reads, and the patient screen's UI strings.
NLLB-200 is licensed CC-BY-NC 4.0 (non-commercial use only).

BELLO_TRANSLATION_MODEL_DIR  model directory (default models/nllb-200-distilled-600M)
BELLO_TRANSLATION_DEVICE     mps | cuda | cpu (default: the first one available)
"""
import json
import os
from pathlib import Path
import threading

ROOT=Path(__file__).resolve().parent
MODEL_DIR=Path(os.environ.get('BELLO_TRANSLATION_MODEL_DIR',str(ROOT/'models/nllb-200-distilled-600M')))
LANGUAGES=json.loads((ROOT.parent/'patient-app/languages.json').read_text(encoding='utf-8'))
NLLB={code:nllb for code,_,nllb in LANGUAGES if nllb}
_model=None
_lock=threading.Lock()

class Unsupported(Exception):
    pass

def nllb_code(language):
    code=NLLB.get(language)
    if not code:raise Unsupported(f'No translation model support for language {language!r}')
    return code

def pick_device(torch):
    wanted=os.environ.get('BELLO_TRANSLATION_DEVICE')
    if wanted:return wanted
    if torch.backends.mps.is_available():return 'mps'
    if torch.cuda.is_available():return 'cuda'
    return 'cpu'

def load_model():
    global _model
    with _lock:
        if _model is None:
            if not (MODEL_DIR/'config.json').is_file():
                raise RuntimeError(f'Translation model missing at {MODEL_DIR}. Run prepare_translation_model.py first.')
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
            device=pick_device(torch)
            tokenizer=AutoTokenizer.from_pretrained(str(MODEL_DIR),local_files_only=True)
            model=AutoModelForSeq2SeqLM.from_pretrained(str(MODEL_DIR),local_files_only=True).to(device).eval()
            _model=(tokenizer,model,device,torch)
    return _model

def translate(texts,source,target,batch_size=16):
    """Translate a list of strings between two Whisper language codes."""
    if source==target:return list(texts)
    src,tgt=nllb_code(source),nllb_code(target)
    tokenizer,model,device,torch=load_model()
    out=[]
    with _lock:  # one generation at a time; the model is shared by every request thread
        tokenizer.src_lang=src
        for start in range(0,len(texts),batch_size):
            batch=texts[start:start+batch_size]
            inputs=tokenizer(batch,return_tensors='pt',padding=True,truncation=True,max_length=256).to(device)
            with torch.inference_mode():
                tokens=model.generate(**inputs,forced_bos_token_id=tokenizer.convert_tokens_to_ids(tgt),
                    max_new_tokens=256,num_beams=2)
            out.extend(t.strip() for t in tokenizer.batch_decode(tokens,skip_special_tokens=True))
    return out

def to_english(text,language):
    if not language or language=='en':return text
    return translate([text],language,'en')[0]

def translate_strings(strings,language):
    """{key: English text} -> {key: text in language}."""
    keys=list(strings)
    return dict(zip(keys,translate([strings[k] for k in keys],'en',language)))
