"""Frozen shared-library evidence for negative-only paper risk reduction."""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from sentiment import sentiment_core as core, weight_config as cfg


def load_records(path):
    path=Path(path)
    if not path.is_file():
        return []
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        records=[json.loads(row[0]) for row in db.execute('SELECT data FROM messages ORDER BY rowid')]
    db.close()
    return records


def preopen_time(day):
    timestamp=pd.Timestamp(day).normalize()
    return timestamp.tz_localize('UTC') if timestamp.tzinfo is None else timestamp.tz_convert('UTC')


def visible_records(records, symbol, day, include_general=False):
    cutoff=preopen_time(day)
    return [m for m in records if (m['asset']==symbol or (include_general and m['asset']=='GENERAL'))
            and max(pd.Timestamp(m['published']),pd.Timestamp(m['captured']),pd.Timestamp(m['available'])) < cutoff]


def risk_frames(frames, records, include_general=False):
    output={}
    for symbol,frame in frames.items():
        matched=[m for m in records if m['asset']==symbol or (include_general and m['asset']=='GENERAL')]
        times=np.array([max(pd.Timestamp(m[k]).timestamp() for k in ('published','captured','available')) for m in matched])
        publication=np.array([pd.Timestamp(m['published']).timestamp() for m in matched])
        static=np.array([m['source_weight']*m['content_weight'] for m in matched])
        half=np.array([m['half_life_hours'] for m in matched])
        directions=np.array([m['direction'] for m in matched])
        rows=[]
        for day in frame.index:
            cutoff=preopen_time(day).timestamp()
            mask=times<cutoff
            age=np.maximum(0.,(cutoff-publication[mask])/3600.)
            weights=static[mask]*np.exp2(-age/half[mask])
            total=float(weights.sum())
            coverage=total/(total+cfg.KAPPA)
            z=float(np.tanh(float(np.dot(weights,directions[mask]))/(total+cfg.KAPPA)/cfg.TAU))
            # Fixed risk_control mode; positive sentiment never increases exposure.
            multiplier=float(core.sentiment_multiplier(z,coverage,mode='risk_control'))
            rows.append((z,coverage,min(1.,max(0.,multiplier))))
        output[symbol]=pd.DataFrame(rows,index=frame.index,columns=['z','coverage','multiplier'])
    return output
