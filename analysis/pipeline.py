from process_results import process, ROOT
from create_charts import create as charts
from create_report import create as report
from scaling import calculate
from experiment_audit import create as audit
import pandas as pd
import hashlib
import json

data, _, maximum = process()
audit(data)
calculate(maximum).to_csv(ROOT/'results/processed/scaling.csv',index=False)
charts()
if not pd.read_csv(ROOT/'results/processed/quick-results.csv').empty:
    quick_output=ROOT/'reports/charts/quick'
    inputs=[ROOT/'results/processed/quick-summary.csv',ROOT/'results/processed/startup.csv',ROOT/'analysis/create_charts.py',ROOT/'analysis/process_results.py']
    fingerprint=hashlib.sha256(b''.join(path.read_bytes() for path in inputs)).hexdigest()
    cache=quick_output/'input-fingerprint.txt';artifacts=quick_output/'artifacts.json'
    existing=json.loads(artifacts.read_text()) if artifacts.exists() else []
    if not cache.exists() or cache.read_text()!=fingerprint or not existing or not all((ROOT/item['path']).exists() for item in existing):
        charts(quick_output,quick=True);cache.write_text(fingerprint)
report()
