# Trade Authenticity Risk Scorer

IS4234 Group 14 technology demonstrator. Before a bank pays a receivables-discounting request, the scorer gives it a fraud risk score from 0 to 100, holds or blocks risky requests until a named reviewer decides, and logs every step.

This is a course project. It is not a bank system and is not affiliated with any organisation named in it. The Hin Leong request packs are reconstructions from the public court record, not the original documents.

## Run it

Hosted: open the Streamlit link in the demonstrator guide. No login or installation is needed.

Locally:

```
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Push this folder to a public GitHub repository.
2. Go to https://share.streamlit.io and sign in with GitHub.
3. Choose "Create app", pick the repository, branch `main` and main file `app.py`.
4. Choose a URL and press Deploy.

## Files

| File | Purpose |
|---|---|
| `app.py` | The Streamlit screens: officer, reviewer, board, compliance and audit |
| `engine.py` | The scoring rules: three checks, bands and override rules |
| `cases.py` | Document reader, example source tables and sample cases |
| `make_samples.py` | Regenerates the PDFs in `sample_docs/` (needs `reportlab`) |
| `sample_docs/` | Three sample request packs |

## What is live and what is example data

- Live: reading the PDF pack, the file metadata check, all scoring, the hold and release workflow, the evidence log, the board totals, and the buyer lookup in the public GLEIF register.
- Example data, labelled on screen: Trade Finance Registry, ship tracking, terminal confirmation, ownership records, benchmark prices, news, and the company accounts and signals.

State is kept per browser session. Reloading the page or pressing "Reset demonstrator" restores the starting portfolio.
