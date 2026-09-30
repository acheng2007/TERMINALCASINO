# Tests

From the repository root (`TERMINALCASINO/`), use an environment with pytest installed (it is listed in `requirements.txt`).
Run the full suite with:

```shell
python -m pytest
```

Run only the standard and expanded slots tests with:

```shell
python -m pytest tests/test_slots.py -q
```

The slots tests use fresh accounts and configurations, scripted input, mocked rendering and animation, and deterministic outcomes.
They cover payouts, bet validation, round flow, and both public gameplay entrypoints through the session summary.
