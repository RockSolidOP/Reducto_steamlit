"""Thin trampoline to the refactored Streamlit app in app/main.py.

Run the app with either of:
- streamlit run app.py
- streamlit run app/main.py
"""

from __future__ import annotations

from app.main import run


if __name__ == "__main__":
    run()
else:
    # Allow `streamlit run app.py` to execute as well
    run()

