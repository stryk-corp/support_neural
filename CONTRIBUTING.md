# Contributing

Create a virtual environment and install the development tools:

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Run the checks from the parent directory of this repository:

```powershell
$env:PYTHONPATH = '..'
ruff check .
coverage run -m unittest discover -s tests -p "test_*.py" -v
coverage report
```

Keep changes small and include tests for behavior changes. Do not commit secrets or private knowledge.
