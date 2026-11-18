"""Wait for the full batch, then verify and render its assembled result."""
import json
import subprocess
import sys
import time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/zeeland-wind'


def main():
    while True:
        try:state=json.loads((OUT/'batch-state.json').read_text())
        except (OSError,ValueError):time.sleep(10);continue
        counts=state.get('counts',{})
        ready=(state.get('status') in ['complete','partial_with_explicit_gaps']
            and counts.get('pending')==0 and counts.get('running')==0)
        if ready:break
        time.sleep(10)
    try:
        for script in ['verify_zeeland_wind.py','preview_zeeland_wind.py']:
            subprocess.run([sys.executable,str(ROOT/'scripts'/script)],check=True)
        status=dict(status='finished',completed_utc=datetime.now(timezone.utc).isoformat(),
            map=str(OUT/'zeeland-wind.gpkg'),project=str(OUT/'zeeland-wind.qgs'),
            report=str(OUT/'zeeland-wind-report.md'),batch_counts=counts,
            coverage_status=state['status'])
        (OUT/'finalization.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
        print(json.dumps(status,indent=2),flush=True)
    except Exception as error:
        (OUT/'finalization.json').write_text(json.dumps(dict(status='failed',error=str(error)),indent=2),encoding='utf-8')
        raise


if __name__=='__main__':main()
