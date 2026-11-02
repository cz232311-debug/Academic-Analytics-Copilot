"""Reproduce upstream behavior with synthetic CSVs and Streamlit's real AppTest.

Only file_uploader is replaced; the pinned upstream app itself is unchanged.
This is an audit probe, not a test suite for the future Copilot product.
"""
import io
import faulthandler
import ipaddress
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
import socket

ROOT = Path(__file__).resolve().parents[2]
# Do not pass host API credentials or the real user profile to the audited app.
system_env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH") if key in os.environ}
os.environ.clear()
os.environ.update(system_env)
for key, directory in {"TEMP": "tmp", "TMP": "tmp", "HOME": "home", "USERPROFILE": "home",
                       "APPDATA": "home/appdata", "LOCALAPPDATA": "home/localappdata"}.items():
    target = ROOT / ".audit" / directory
    target.mkdir(parents=True, exist_ok=True)
    os.environ[key] = str(target)
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
os.environ["MPLBACKEND"] = "Agg"
os.environ["MPLCONFIGDIR"] = str(ROOT / ".audit/mpl")
for thread_variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[thread_variable] = "1"

faulthandler.dump_traceback_later(180, exit=True)
print("Preloading native dependencies on the main thread", flush=True)
import pandas
print("Loaded pandas", flush=True)
import seaborn
print("Loaded seaborn", flush=True)
import plotly.express
print("Loaded plotly.express", flush=True)
import statsmodels.api
print("Loaded statsmodels.api", flush=True)
from sklearn.linear_model import LinearRegression
print("Loaded sklearn.linear_model", flush=True)
import matplotlib.pyplot as plt
from streamlit.testing.v1 import AppTest
faulthandler.cancel_dump_traceback_later()

VALID = "Name,Math,Science,Gender\n" + "\n".join(
    f"Synthetic-{i},{a},{b},{'F' if i % 2 else 'M'}"
    for i, (a, b) in enumerate(zip([50, 60, 70, 80, 90, 100], [60, 65, 75, 70, 85, 90]), 1)
)
CASES = {
    "initial_page": None,
    "valid_two_subjects": VALID,
    "one_subject": "Name,Math\nSynthetic-A,70\nSynthetic-B,80\n",
    "no_numeric_subject": "Name,Gender\nSynthetic-A,F\nSynthetic-B,M\n",
    "numeric_class": "Name,Class,Math,Science\nSynthetic-A,1,70,80\nSynthetic-B,2,80,90\nSynthetic-C,1,90,95\n",
    "out_of_range": "Name,Math,Science\nSynthetic-A,-10,150\nSynthetic-B,120,80\nSynthetic-C,50,60\n",
    "no_complete_rows": "Name,Math,Science\nSynthetic-A,70,\nSynthetic-B,,80\nSynthetic-C,90,\nSynthetic-D,,95\n",
    "constant_feature": "Name,Math,Science\nSynthetic-A,60,80\nSynthetic-B,70,80\nSynthetic-C,90,80\n",
    "malformed_csv": "Name,Math,Science\nSynthetic-A,60,80\nSynthetic-B,70,80,extra\n",
    "empty_file": "",
    "normalized_duplicate_headers": "Name,Math, math ,Science\nSynthetic-A,60,70,80\nSynthetic-B,70,80,90\n",
    "partially_missing_scores": "Name,Math,Science\nSynthetic-A,60,80\nSynthetic-B,70,90\nSynthetic-C,,95\nSynthetic-D,50,\n",
}


def main() -> None:
    records = []
    for case, content in CASES.items():
        print(f"START {case}", flush=True)
        faulthandler.dump_traceback_later(60, exit=True)
        record = {"case": case}
        upload = io.StringIO(content) if content is not None else None
        with patch("streamlit.file_uploader", return_value=upload):
            app = AppTest.from_file(str(ROOT / ".audit/runtime/app.py"), default_timeout=45)
            try:
                app.run()
                record["exceptions"] = [{"type": e.type, "message": e.message} for e in app.exception]
                record["warnings"] = [w.value for w in app.warning]
                record["dataframe_count"] = len(app.dataframe)
                if app.dataframe:
                    frame = app.dataframe[0].value
                    record["columns"] = list(frame.columns)
                    record["null_counts"] = {str(c): int(v) for c, v in frame.isna().sum().items()}
                if len(app.dataframe) >= 3:
                    record["top_performer_totals"] = [float(x) for x in app.dataframe[2].value["total score"]]
                    record["top_performer_averages"] = [float(x) for x in app.dataframe[2].value["average score"]]
                if case == "valid_two_subjects" and not app.exception:
                    assert record["top_performer_totals"] == [190, 175, 150, 145, 125, 110]
                    assert record["null_counts"]["gender"] == 6
                    record["assertions"] = {"known_totals_match": True, "category_corruption_reproduced": True}
                    upload.seek(0)
                    app.button[0].click().run()
                    record["prediction_exceptions"] = [e.message for e in app.exception]
                    record["prediction_display"] = [s.value for s in app.success]
                record["probe_status"] = "completed"
            except Exception as exc:
                record["probe_status"] = "harness_error"
                record["harness_error"] = f"{type(exc).__name__}: {exc}"
        records.append(record)
        plt.close("all")
        faulthandler.cancel_dump_traceback_later()
        print(json.dumps(record, ensure_ascii=True), flush=True)
    output = {"python": sys.version, "method": "AppTest; only file_uploader mocked; synthetic CSVs",
              "cases": records}
    (ROOT / "docs/audit/evidence/runtime-probes.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    if any(x["probe_status"] != "completed" for x in records):
        raise SystemExit(1)


if __name__ == "__main__":
    # Windows asyncio needs loopback sockets. Deny external Python socket connections.
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def restricted_connect(sock, address, *, connect_ex=False):
        try:
            allowed = isinstance(address, tuple) and ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            raise PermissionError("Audit probe blocks non-loopback sockets")
        return (original_connect_ex if connect_ex else original_connect)(sock, address)

    with patch("socket.socket.connect", restricted_connect), \
         patch("socket.socket.connect_ex", lambda sock, address: restricted_connect(sock, address, connect_ex=True)):
        main()
