# VoltRelay Energy: root-cause analysis (Data Analytics Hackathon, Gradient Learnings)

VoltRelay is an EV battery-swapping network for 2W/3W delivery riders in 6 Indian cities. From Jan 2024 to Jun 2025, swaps and revenue grew while service failures, new-rider churn and margin per swap all got worse. This project finds the operational causes and recommends where to spend the next budget.

## Contents

| Path | What |
|---|---|
| `notebooks/voltrelay_analysis.ipynb` | Full analysis: data audit, cleaning, the 6 questions, churn model |
| `notebooks/build_notebook.py` | Source that generates the notebook |
| `*.csv` (repo root) | The 6 small organizer tables, plus 5,000-row samples of the two large ones |
| `outputs/` | Tables and charts produced by the notebook |

## Run it

1. Put all 8 organizer CSVs in Google Drive under `MyDrive/VoltRelay/`.
2. Open `notebooks/voltrelay_analysis.ipynb` in Google Colab and choose **Runtime → Run all**.
3. The last cell downloads `outputs.zip` (all tables, charts, `dq_log.csv` and `key_numbers.json`).

## Results

**Full report: [`reports/REPORT.md`](reports/REPORT.md)** · **Interactive dashboard: [`dashboard/index.html`](dashboard/index.html)** · **Slides: [`slides/VoltRelay_slides.pdf`](slides/VoltRelay_slides.pdf)** · LinkedIn post: [`reports/LINKEDIN_POST.md`](reports/LINKEDIN_POST.md)

Root causes found: (1) Gen1 chargers slow down sharply in summer heat, so cabinets run out of charged packs; (2) a bad battery batch from the new supplier Kyron; (3) the ZipDrop 28% discount amendment. The station expansion, price rise, firmware and competitor were ruled out.

## Findings from the data audit

- **Bad battery batch:** Kyron lots KY-2407, KY-2408 and KY-2409 (about 1,460 packs) average 61% state of health, against about 80% for every other lot. 1,438 of them were retired on the same day (2025-06-15) with the label "end_of_life", although they were under a year old.
- **Hidden battery complaints:** About 9,500 support tickets are filed as "other", but their comments describe weak vehicles or low range.
- **Dirty data:** Rider cities are written 21 different ways, two test stations appear in the swap and ticket data, and some readings are impossible (negative km, km per charge above 300, SoH above 100%).
