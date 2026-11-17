"""Layer 1 scrapers, keyed by the source's exact name in the Airtable Sources table.
A source in Airtable with no entry here is never run (e.g. Harmonic, until Polsky grants access)."""
from . import page_lists, polsky_directory, polsky_news

REGISTRY = {
    "Polsky news feed": polsky_news.run,
    "Demo day & exhibitor lists": page_lists.run,
    "Polsky company directory": polsky_directory.run,
}
