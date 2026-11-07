# Try Rulesmith with sample data

A five-minute walkthrough of every part of Rulesmith: rules, a clean upload,
failing rows, a broken rule, a wide Parquet file, the CSV report, the
pass-rate trend and two rejected uploads. Every file here is small and
synthetic, and every result below is exact, so you can check the app against
it step by step.

> **Warning: the live apps are shared and public.** Everyone can see and
> change everything on https://rulesmith-dev.fly.dev and
> https://rulesmith-prod.fly.dev. There is no login, and nothing can be
> deleted there (there is no dataset delete). Upload only the synthetic files
> in this folder, never your own data.

## Where to run it

Pick one:

- **Locally**, from the repo root:

  ```bash
  uv sync
  uv run uvicorn rulesmith.app:app --reload
  ```

  Then open http://localhost:8000/datasets. Or run `make up` (Docker) and open
  the same address.
- **On a live app**: https://rulesmith-dev.fly.dev/datasets or
  https://rulesmith-prod.fly.dev/datasets (see the warning above).

## The files

| File | What it shows |
|---|---|
| `01-orders-clean.csv` | a clean upload |
| `02-orders-with-bad-rows.csv` | failing rows, including one row that fails two rules |
| `03-orders-old-export.json` | a JSON file, and a broken rule (this older export has no `currency` column) |
| `04-orders-wide.parquet` | a Parquet file with a wide table |
| `05-orders-wrong-extension.txt` | a rejected upload (CSV text, wrong extension) |
| `06-orders-truncated.json` | a rejected upload (a JSON array cut off part way) |

Every data file has an `order_id` column with unique values. The failing-rows
table shows no row numbers, so use `order_id` to tell rows apart. All values
are made up (`CUST-001`, city `Sampleton`, warehouse `WH-01` and so on).

The Parquet file is built from data written in `make_parquet.py`. To rebuild
it, run this from the repo root:

```bash
uv run python sample-data/make_parquet.py
```

## 1. Create a dataset

On the Datasets page, create a new dataset with a name of your own, for
example `Sample orders - <your initials>`. Dataset names are not unique, and
on the shared apps other people may have made a "Sample orders" dataset too.

The results below assume this fresh dataset, with all four rules from step 2
added before the first upload.

## 2. Add the four rules

On the dataset page, click **New rule** once per row and copy the name and the
expression into the form exactly as written:

| Rule name | Expression |
|---|---|
| Amount is positive | `amount > 0` |
| Quantity between 1 and 100 | `quantity >= 1 and quantity <= 100` |
| Known status | `status == 'paid' or status == 'shipped' or status == 'refunded'` |
| Currency is EUR | `currency == 'EUR'` |

The results below name each rule. Find a rule's result by its name, not by
where it sits on the page.

## 3. Upload the files in order

Upload each file with the **Upload data file** form on the dataset page. A
successful upload opens the new run's page. Click **Back to** your dataset to
upload the next file. The dataset page's Runs list shows the same summary line
next to each run.

### Upload 1: `01-orders-clean.csv`

File: `01-orders-clean.csv`, 10 rows, `ORD-0001` to `ORD-0010`.

Run summary line: `Clean pass: 4 passed, 0 failed, 0 broken.`

- **Amount is positive**: PASS, 10 passed, 0 failed.
- **Quantity between 1 and 100**: PASS, 10 passed, 0 failed.
- **Known status**: PASS, 10 passed, 0 failed.
- **Currency is EUR**: PASS, 10 passed, 0 failed.

### Upload 2: `02-orders-with-bad-rows.csv`

File: `02-orders-with-bad-rows.csv`, 10 rows, `ORD-0011` to `ORD-0020`.

Run summary line: `2 passed, 2 failed, 0 broken.`

- **Amount is positive**: FAIL, 8 passed, 2 failed. "Expand failing rows" shows, in order: `ORD-0013`, `ORD-0017`.
- **Quantity between 1 and 100**: FAIL, 8 passed, 2 failed. "Expand failing rows" shows, in order: `ORD-0015`, `ORD-0017`.
- **Known status**: PASS, 10 passed, 0 failed.
- **Currency is EUR**: PASS, 10 passed, 0 failed.

One failing row has amount `0`, one has quantity `0`, and one has a negative
amount and a quantity over 100, so it fails both rules.

### Upload 3: `03-orders-old-export.json`

File: `03-orders-old-export.json`, 8 rows, `ORD-0021` to `ORD-0028`.

Run summary line: `2 passed, 1 failed, 1 broken.`

- **Amount is positive**: PASS, 8 passed, 0 failed.
- **Quantity between 1 and 100**: PASS, 8 passed, 0 failed.
- **Known status**: FAIL, 7 passed, 1 failed. "Expand failing rows" shows, in order: `ORD-0024`.
- **Currency is EUR**: BROKEN, reason `unknown column(s): currency`. It has no "Expand failing rows" button.

This older export has no `currency` column, so Currency is EUR cannot run and
is shown as BROKEN. The other three rules still ran. The row that fails Known
status has status `pending`.

Click **Download report (CSV)**. The file holds a header, then one row per
rule. The broken rule's counts are empty and its reason is filled in:

```csv
rule_name,verdict,pass_count,fail_count,broken_reason
Amount is positive,pass,8,0,
Quantity between 1 and 100,pass,8,0,
Known status,fail,7,1,
Currency is EUR,broken,,,unknown column(s): currency
```

### Upload 4: `04-orders-wide.parquet`

File: `04-orders-wide.parquet`, 6 rows, `ORD-0029` to `ORD-0034`, 22 columns.

Run summary line: `3 passed, 1 failed, 0 broken.`

- **Amount is positive**: PASS, 6 passed, 0 failed.
- **Quantity between 1 and 100**: PASS, 6 passed, 0 failed.
- **Known status**: PASS, 6 passed, 0 failed.
- **Currency is EUR**: FAIL, 4 passed, 2 failed. "Expand failing rows" shows, in order: `ORD-0031`, `ORD-0033`.

Both failing rows are in `USD`. The expanded failing-rows table for Currency
is EUR is wider than the page and scrolls sideways.

### The trend after upload 4

Go back to the dataset page. The **Row pass rate trend** chart has 4 points with a line between them.
Its legend lists them oldest first:

1. `100.0% row pass rate (40/40 rows evaluated)` (upload 1)
2. `90.0% row pass rate (36/40 rows evaluated)` (upload 2)
3. `95.8% row pass rate (23/24 rows evaluated)` (upload 3)
4. `91.7% row pass rate (22/24 rows evaluated)` (upload 4)

Each legend entry starts with `Run <id> (<timestamp>):`. Run ids and
timestamps will differ from reader to reader.

The trend counts rows, not rules: for each run it adds up the passing rows
and the evaluated rows of every rule that was not broken. Rows under a broken
rule are left out of both counts, so upload 3 counts only the three rules
that ran.

### Upload 5: `05-orders-wrong-extension.txt`

The upload is rejected. The dataset page shows this error:

`Unrecognized file extension: '.txt'. Expected .csv, .json, or .parquet.`

No run is created. The dataset page still lists 4 runs and the trend still has 4 points.

### Upload 6: `06-orders-truncated.json`

The upload is rejected. The dataset page shows an error that starts with:

`Could not parse uploaded file:`

The rest of the message names a temporary file on the server, so it differs
each time.

No run is created. The dataset page still lists 4 runs and the trend still has 4 points.
