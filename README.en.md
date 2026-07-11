# paper-radar · Technical Research Radar

Personalized literature tracking for machine learning, data science, computer vision, robotics, and textile engineering. It fetches public scholarly sources, ranks results by a preference model, and publishes them to a private Cloudflare Pages site.

## Sources and constraints

- **ML:** arXiv `cs.LG`, OpenReview, and OpenAlex ML topics.
- **Data science:** OpenAlex, Crossref, and dblp results constrained to data mining, statistical learning, analytics, and article/proceedings types.
- **Computer vision:** arXiv `cs.CV`, OpenReview, and OpenAlex CV topics.
- **Robotics:** arXiv `cs.RO`, OpenReview, and OpenAlex robotics/control topics.
- **Textile engineering:** Textile Research Journal, Journal of The Textile Institute, and AUTEX Research Journal metadata, plus a required textile/fiber/fabric/yarn/weaving/nonwoven/smart-textile keyword match.

`config.example.yaml` contains the complete feed configuration. OpenAlex, Crossref, and Semantic Scholar metadata enrichment never relaxes the textile admission rule.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp interest_model.example.json interest_model.json
python fetch_and_score.py
python enrich.py --limit 20
```

Supported feeds are `rss`, `openalex`, `crossref`, `semantic_scholar`, `dblp`, `openreview`, and the legacy-compatible `pubmed_search`. See the Chinese [README](README.md) for architecture, deployment, and security guidance.

## License

[MIT](LICENSE).
