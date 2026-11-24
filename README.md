# Mag AI Zine

Build a weekly EPUB from feeds, individual article pages and newsletter emails. Choose the articles yourself or use your usual AI assistant to guide selection.

## First issue

From the checkout, with Python 3.11 or newer:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m mag init
python -m mag gather
python -m mag prompt
```

Enter a feed URL for ongoing coverage or an article URL for a single piece. Edit MAGAZINE.md between issues. Under “## Themes”, use a “###” heading per theme and a bullet per URL. Always, Avoid and Never are editorial instructions for your assistant. For email, enter a Gmail label and set GMAIL_USER and GMAIL_APP_PASSWORD in your local environment. Labelled emails appear under the first theme.

Paste the prompt output into your assistant, or ask Claude Code or Codex to run it in this checkout. Select the IDs you want. **[Preview]** means the source shows signs of an excerpt. A selected preview can still appear in the EPUB. An unlabelled article can also be incomplete.

The assistant writes your choices to the issue's picks.yaml. For manual selection use:

```yaml
picks:
  - id: c01
  - id: c03
```

Run `python -m mag build --issue YYYY-Www` using the issue ID printed by gather. Open the EPUB in out/. To try the build without making picks, run `python -m mag build --auto 1` for the current week. Build only creates local files. To deliver, approve your Gmail sender in your Kindle settings, set KINDLE_EMAIL alongside the Gmail variables, and run `python -m mag send --issue YYYY-Www --epub out/Mag-AI-Zine-YYYY-Www.epub`.

## Weekly reminder

Use this text in your assistant's scheduler or run it manually each week in this checkout:

> Run python -m mag gather and python -m mag prompt. Follow the editorial brief, show the source and [Preview] labels, and ask me which items to include. Wait for my picks before building. Show me the resulting EPUB.

Each runner needs access to the same local issue directory. Generated files and personal settings are gitignored. To test changes run `python -m unittest discover -s tests -v`.

Based on [Claude-Mag](https://github.com/tomchute/Claude-Mag). MIT licensed.
