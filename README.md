# ScrapeMail

Email scraping tool — extracts email addresses from any domain using direct web crawling and web search queries.

## Features

- **Domain Crawl** — Fetches the domain root and up to 3 internal pages
- **Web Search** — Queries DuckDuckGo for `@domain.com` email mentions across the web
- **Parallel Execution** — Both methods run simultaneously for speed
- **Deduplication** — Results merged and deduplicated (case-insensitive)
- **Export** — Copy all, download as CSV or JSON
- **Noise Filtering** — Excludes `noreply`, `example.com`, and other common noise patterns

## Setup

```bash
# Clone the repository
git clone https://github.com/BiswasM21/ScrapeMail.git
cd ScrapeMail

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the app
python app.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

## Usage

1. Enter a domain name (e.g., `google.com`, `github.com`)
2. Click **Scrape**
3. View results — emails are tagged as `domain` (found on the site) or `web search` (found via search queries)
4. Copy individual emails, copy all, or download as CSV/JSON

## Notes

- Web search uses DuckDuckGo's HTML interface (no API key required)
- Respects rate limits — do not scrape aggressively
- Some websites may block automated requests
