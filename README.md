# ICR Parser

A Python package for parsing PRA (Paperwork Reduction Act) XML data and scraping detailed ICR (Information Collection Request) information from reginfo.gov.

## Features

- Downloads and parses PRA XML files from reginfo.gov
- Stores ICR data in PostgreSQL database
- Scrapes detailed ICR information including authorizing statutes
- Tracks changes and updates over time
- Supports incremental updates with `--new-only` flag

## Installation

### Using uv (recommended)

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create and activate virtual environment with uv
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
uv pip sync pyproject.toml

# Or simply run with uv (no activation needed)
uv run python local_pra_parser.py
```

### Using standard venv and pip

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install psycopg2-binary requests beautifulsoup4 python-dateutil pandas python-dotenv
```

## Configuration

1. Copy the example environment file:
```bash
cp .env.example .env
```

2. Edit `.env` with your database credentials:
```env
DB_HOST=your-database-host.amazonaws.com
DB_NAME=your-database-name
DB_USER=your-username
DB_PASSWORD=your-password
DB_PORT=5432
DB_SCHEMA=beta
```

## Usage

### Daily Operations

Run both parsers to update your database with the latest ICR data:

```bash
# With uv (no activation needed)
uv run python local_pra_parser.py
uv run python new_icr_scraper.py --new-only

# Or with activated virtual environment
python local_pra_parser.py
python new_icr_scraper.py --new-only

# Run both in parallel
python local_pra_parser.py & python new_icr_scraper.py --new-only
```

### Command Options

#### local_pra_parser.py
- `--force-refresh` - Force re-download of all XML files

#### new_icr_scraper.py
- `--new-only` - Only process ICRs marked as needing scraping
- `--limit N` - Limit processing to N ICRs
- `--specific ICR_REF` - Process a specific ICR reference number
- `--resume ICR_REF` - Resume processing from a specific ICR
- `--dry-run` - Show what would be done without making changes
- `--no-prioritize` - Don't prioritize ICRs by file type

## Project Structure

```
icr-parser/
├── local_pra_parser.py      # XML parser and database updater
├── new_icr_scraper.py       # Detailed ICR information scraper
├── scrape_single_icr.py     # Single ICR scraping module
├── pyproject.toml           # Project dependencies (for uv)
├── .env.example             # Environment variables template
├── .env                     # Your environment variables (not in git)
├── .gitignore               # Git ignore rules
├── data/                    # Downloaded XML files (auto-created)
│   ├── latest/              # Most recent XML files
│   └── YYYYMMDD/            # Timestamped backups
├── temp/                    # Temporary scraping files (auto-created)
└── .venv/                   # Virtual environment (if using uv or venv)
```

## Database Schema

The scripts work with PostgreSQL and expect these tables (created automatically if missing):

### pra_xml_icrs
Stores parsed XML data for each ICR:
- ICR reference numbers and control numbers
- Agency information
- Titles and abstracts
- Status and dates
- Burden estimates
- Tracking fields for scraping

### pra_xml_icr_changes
Tracks changes to ICRs over time:
- Change type (new/changed)
- Old and new data as JSON
- Timestamps

### pra_icr_details
Stores detailed scraped information:
- Full ICR details from web pages
- Authorizing statutes with links
- Federal Register notices
- Review dates and actions
- Public comment information

## Development Setup

```bash
# Clone or download the package
cd icr-parser

# Set up with uv
uv venv
uv pip sync pyproject.toml

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Test the connection
uv run python -c "from dotenv import load_dotenv; load_dotenv(); import os; print('DB_HOST:', os.getenv('DB_HOST'))"
```

## Data Flow

1. **XML Download**: `local_pra_parser.py` downloads XML files from reginfo.gov
2. **Parse & Store**: Parses XML and stores in `pra_xml_icrs` table
3. **Change Detection**: Identifies new and changed ICRs
4. **Detail Scraping**: `new_icr_scraper.py` scrapes full details for flagged ICRs
5. **Update Database**: Stores detailed information in `pra_icr_details`

## XML Sources

The parser downloads from these reginfo.gov endpoints:
- Current Inventory Report
- Pending Paperwork
- Reviews Concluded
- Expiration Report

## Notes

- The scraper includes rate limiting to be respectful of reginfo.gov
- XML files are cached locally to reduce unnecessary downloads
- Daily files are checked for updates, monthly files only when changed
- All timestamps are stored in UTC
- The `.gitignore` file ensures sensitive data (.env, data files) aren't committed

## Troubleshooting

### Database Connection Issues
- Verify environment variables are set correctly
- Check network connectivity to database host
- Ensure database user has necessary permissions

### Virtual Environment Issues
- Make sure you've activated the virtual environment
- Or use `uv run` to automatically handle the environment

### Targeted Scraping
- Occasionally, an ICR may not be included in the published XML files
- Use `--specific ICR_REF` to target individual ICRs

### Performance
- Use `--limit` to process in smaller batches
- The `--new-only` flag significantly reduces processing time
- Consider running during off-peak hours

## License

[Your license here]

## Support

For issues or questions, please [contact information or issue tracker]