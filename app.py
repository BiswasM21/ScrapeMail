import re
from flask import Flask, render_template, request, jsonify
from concurrent.futures import ThreadPoolExecutor
import requests
from urllib.parse import urlparse

app = Flask(__name__)

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
NOISE_PREFIXES = ('noreply', 'no-reply', 'no_reply', 'donotreply', 'dontreply')
NOISE_DOMAINS = {'example.com', 'test.com', 'localhost', 'sample.com'}
MAX_EMAILS = 500
DOMAIN_CRAWL_TIMEOUT = 10
SEARCH_TIMEOUT = 15
SUB_PAGE_TIMEOUT = 5
MAX_INTERNAL_LINKS = 3
USER_AGENT = 'Mozilla/5.0 (compatible; ScrapeMail/1.0; +https://github.com/BiswasM21/ScrapeMail)'


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------

def normalize_domain(raw: str) -> str:
    """Strip https://, http://, www. prefix and path, return clean lowercase domain."""
    domain = raw.strip()
    # Handle cases where the input might be a full URL or just a domain
    if domain.startswith(('http://', 'https://')):
        parsed = urlparse(domain)
        domain = parsed.netloc
    domain = domain.lower()
    domain = re.sub(r'^www\.', '', domain)
    return domain.rstrip('/').rstrip('.')


def is_valid_domain(domain: str) -> bool:
    """Return True if domain has at least one dot and no spaces."""
    return bool(domain) and '.' in domain


def extract_emails(text: str) -> list[str]:
    """Find all email-like strings using EMAIL_RE regex."""
    return EMAIL_RE.findall(text)


def filter_noise(emails: list[str]) -> list[str]:
    """
    Remove noise emails:
    - Skip if local part (before @) is < 5 chars
    - Skip if starts with any NOISE_PREFIX (case-insensitive)
    - Skip if domain part is in NOISE_DOMAINS
    - Return in original order with exact duplicates removed
    """
    seen = set()
    result = []
    for email in emails:
        email_lower = email.lower()
        if email_lower in seen:
            continue
        local, _, domain_part = email_lower.partition('@')

        # Skip if local part < 5 chars
        if len(local) < 5:
            continue

        # Skip if local part starts with any noise prefix
        if any(local.startswith(prefix) for prefix in NOISE_PREFIXES):
            continue

        # Skip if domain is in noise list
        if domain_part in NOISE_DOMAINS:
            continue

        seen.add(email_lower)
        result.append(email_lower)
    return result


def tag_emails(emails: list[str], source: str) -> list[dict]:
    """Return [{address: e.lower(), source: source}]."""
    return [{'address': e.lower(), 'source': source} for e in emails]


# ---------------------------------------------------------------------------
# Domain Crawl Method
# ---------------------------------------------------------------------------

def crawl_domain(domain: str) -> tuple[list[dict], str | None]:
    """
    Crawl a domain's root page and up to MAX_INTERNAL_LINKS linked pages
    to extract email addresses.
    Returns (tagged_list, None) on success or (empty_list, error_string) on failure.
    """
    try:
        root_url = f'https://{domain}'
        response = requests.get(
            root_url,
            timeout=DOMAIN_CRAWL_TIMEOUT,
            headers={'User-Agent': USER_AGENT},
            allow_redirects=True
        )
        response.raise_for_status()
    except Exception as e:
        return [], f'Failed to fetch {root_url}: {str(e)}'

    all_text = response.text
    emails = extract_emails(all_text)

    # Find up to MAX_INTERNAL_LINKS same-domain links
    link_pattern = re.compile(rf'href=["\'](https?://{re.escape(domain)}[^"\']*)["\']', re.IGNORECASE)
    found_links = link_pattern.findall(all_text)
    # Dedupe while preserving order
    seen_links = set()
    internal_links = []
    for link in found_links:
        normalized = link.rstrip('/')
        if normalized not in seen_links and normalized != root_url.rstrip('/'):
            seen_links.add(normalized)
            internal_links.append(normalized)
            if len(internal_links) >= MAX_INTERNAL_LINKS:
                break

    # Fetch linked pages
    for link_url in internal_links:
        try:
            sub_response = requests.get(
                link_url,
                timeout=SUB_PAGE_TIMEOUT,
                headers={'User-Agent': USER_AGENT},
                allow_redirects=True
            )
            sub_response.raise_for_status()
            emails.extend(extract_emails(sub_response.text))
        except Exception:
            # Silently skip failed sub-pages
            pass

    filtered = filter_noise(emails)
    return tag_emails(filtered, 'domain'), None


# ---------------------------------------------------------------------------
# Web Search Method
# ---------------------------------------------------------------------------

def search_for_emails(domain: str) -> tuple[list[dict], str | None]:
    """
    Run two parallel DuckDuckGo HTML queries to find email addresses
    associated with a domain.
    Returns (tagged_list, warning) where warning is None if all queries succeeded.
    """
    base_url = 'https://html.duckduckgo.com/html/'
    queries = [
        f'@{domain}',
        f'site:{domain} "%40{domain}"',
    ]
    all_snippets = []
    errors = []

    for query in queries:
        try:
            response = requests.get(
                base_url,
                params={'q': query},
                timeout=SEARCH_TIMEOUT,
                headers={'User-Agent': USER_AGENT}
            )
            response.raise_for_status()
            html = response.text

            # Extract snippet text: look for <a class="result__snippet">...</a>
            # or general <p> tag text within result blocks
            snippet_texts = []

            # Pattern 1: explicit result__snippet anchor text
            snippet_tag_re = re.compile(r'<a class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
            for match in snippet_tag_re.finditer(html):
                # Strip HTML tags from snippet content
                inner = re.sub(r'<[^>]+>', ' ', match.group(1))
                snippet_texts.append(inner)

            # Pattern 2: paragraph text within result blocks
            result_block_re = re.compile(r'<div class="result[^"]*"[^>]*>(.*?)</div>', re.DOTALL | re.IGNORECASE)
            for block in result_block_re.finditer(html):
                block_text = re.sub(r'<[^>]+>', ' ', block.group(1))
                snippet_texts.append(block_text)

            all_snippets.extend(snippet_texts)

        except Exception as e:
            errors.append(f'Query "{query}" failed: {str(e)}')

    combined_text = ' '.join(all_snippets)
    emails = extract_emails(combined_text)
    filtered = filter_noise(emails)

    warning = None
    if errors:
        warning = '; '.join(errors)

    return tag_emails(filtered, 'web search'), warning


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/scrape', methods=['POST'])
def scrape():
    if not request.is_json:
        return jsonify({'error': 'Request body must be JSON'}), 400

    data = request.get_json()
    raw_domain = data.get('domain', '').strip()
    if not raw_domain:
        return jsonify({'error': 'Missing "domain" field'}), 400

    domain = normalize_domain(raw_domain)
    if not is_valid_domain(domain):
        return jsonify({'error': f'Invalid domain: {domain}'}), 400

    # Run both methods in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        crawl_future = executor.submit(crawl_domain, domain)
        search_future = executor.submit(search_for_emails, domain)

        crawl_result = crawl_future.result()
        search_result = search_future.result()

    crawl_emails, crawl_error = crawl_result
    search_emails, search_warning = search_result

    # Both failed
    if not crawl_emails and not search_emails:
        return jsonify({
            'error': 'Both scraping methods failed',
            'details': {
                'domain_crawl': crawl_error,
                'web_search': search_warning,
            }
        }), 500

    # Merge results, deduping case-insensitively
    merged: dict[str, dict] = {}
    for entry in crawl_emails:
        key = entry['address']
        merged[key] = entry
    for entry in search_emails:
        key = entry['address']
        if key not in merged:
            merged[key] = entry

    # Sort: domain-source first, then web-search, then alphabetically
    def sort_key(item: dict) -> tuple:
        src = item[1]['source']
        addr = item[1]['address']
        # Primary: domain=0, web search=1
        primary = 0 if src == 'domain' else 1
        return (primary, addr)

    sorted_emails = [item[1] for item in sorted(merged.items(), key=sort_key)]
    sorted_emails = sorted_emails[:MAX_EMAILS]

    # Count by source
    domain_count = sum(1 for e in sorted_emails if e['source'] == 'domain')
    search_count = sum(1 for e in sorted_emails if e['source'] == 'web search')

    response: dict = {
        'domain': domain,
        'emails': sorted_emails,
        'total': len(sorted_emails),
        'counts': {
            'domain': domain_count,
            'web search': search_count,
        }
    }

    if search_warning:
        response['warning'] = search_warning

    return jsonify(response), 200


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
