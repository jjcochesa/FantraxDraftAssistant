# ADP drafts

Drop one file per online draft here, then run `python build_adp.py` from the repo
root to (re)build `data/adp.csv`, which the app reads as its ADP source.

## Format

- **One `.txt` file per draft.** Name them however (`draft_01.txt`, `espn_mock.txt`).
- **One player per line, in pick order** — pick 1 on the first line, pick 2 next, etc.
- Blank lines and lines starting with `#` are ignored (use `#` for notes).
- A leading number is fine and stripped: `1. Bruno Fernandes`, `24) Saka`, or just `Bruno Fernandes` all work.
- Names only have to be *close* — they're matched to the Fantrax pool with the
  same accent-stripping / first-name+surname matcher the app uses.

## Example (`draft_01.txt`)

```
# 12-team snake, source: Reddit mock 2026-08-01
1. Mohamed Salah
2. Erling Haaland
3. Bruno Fernandes
4. Cole Palmer
...
```

## After adding drafts

```bash
python build_adp.py
```

It prints the top 20 by ADP and any names that didn't match the pool (so you can
fix a spelling if needed). Commit the updated `data/adp.csv` to deploy it.
