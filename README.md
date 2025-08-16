# Vultr Multi-Region Latency Tester

Measure HTTP latency to popular CEX/DEX endpoints while provisioning short‑lived Vultr instances across multiple regions. The script runs for a selected duration (5, 15, or 60 minutes), aggregates results, estimates costs, and prompts to destroy instances (auto‑destroys after 30s if no answer) to avoid unnecessary charges.

## Features
- Multi‑region instance provisioning on Vultr (Cloud Compute `vc2-1c-2gb`, Ubuntu 22.04).
- Latency measurements to curated CEX/DEX endpoints using async HTTP (`aiohttp`).
- Interactive duration selector: 5, 15, or 60 minutes (`1h` also accepted).
- Aggregated results (pivot + Top 10) printed to console and saved to CSV.
- Cost estimation proportional to the selected test duration.
- Safe teardown: prompts for destruction and defaults to destroy after 30s of inactivity.
- Structured logging to console and rotating file `launch-gpt5.log`.

## Prerequisites
- Python 3.10+
- A Vultr API key with permissions to create/destroy instances
- macOS/Linux shell (tested on macOS)

## Installation
1) Create and activate a virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

2) Install dependencies
```bash
pip install -r requirements.txt
```

3) Configure environment variables
- Create a `.env` file (already git‑ignored) and set your key:
```bash
# .env
VULTR_API_KEY=your-vultr-api-key
```
- Or export it in your shell:
```bash
export VULTR_API_KEY=your-vultr-api-key
```

## Usage
Run the main script:
```bash
source .venv/bin/activate
python launch-gpt5.py
```
You will be prompted to choose a duration: `5`, `15`, or `60` minutes (`1h` also accepted). The script will:
- Create instances in default regions: Tokyo (`nrt`), Singapore (`sgp`), Frankfurt (`fra`), New York (`ewr`), Seoul (`icn`).
- Wait for instances to become active.
- Perform repeated latency measurements during the selected time window.
- Print a pivot table and Top 10 latencies.
- Save results to a timestamped CSV file: `vultr_latency_test_YYYYMMDD_HHMMSS_<duration>m.csv`.
- Estimate the test cost and ask whether to destroy instances.
  - If you do not answer within 30 seconds, instances are destroyed automatically.

## Output
- CSV: `vultr_latency_test_<timestamp>_<duration>m.csv`
- Log file: `launch-gpt5.log` (rotating)
- Console summary: pivot table (average latency per region) and Top‑10 best latencies

## Billing Notes (Vultr)
- Instances are billed hourly/minute with a monthly cap. There is no long‑term commitment.
- Stopped instances still incur charges; only destroying them stops billing.
- This project prompts for teardown and destroys instances automatically after 30s if unattended.

## Configuration
- Regions: update `regions_to_deploy` in `launch-gpt5.py` (default: `nrt`, `sgp`, `fra`, `ewr`, `icn`).
- Plan/OS: `VULTR_PLAN_ID = "vc2-1c-2gb"`, `VULTR_OS_ID = 1743` (Ubuntu 22.04).
- Endpoints: see `REGION_EXCHANGE_MAP` inside `launch-gpt5.py`.
- Measurement interval: currently 30 seconds between iterations.

## Troubleshooting
- 400 Invalid user_data (check base64 encoding): The script encodes `user_data` in Base64 as required. If it persists, verify your API key and account permissions.
- Permission errors: Ensure the API key is active and authorized for instance create/delete.
- No results saved: If the measurement window completes without successful requests, the script will print a warning and skip CSV output.

## Security
- Secrets are stored in `.env` which is listed in `.gitignore`.
- Never commit API keys to version control.

## License
This project is provided as‑is for demonstration and testing purposes. Review and adapt before production use.
