# Privacy and secrets (NFR-5, NFR-6)

- Never store or log full email bodies. Notion gets subject, sender, a ≤200-char snippet, and extracted fields.
- Send the AI only the text it needs (subject + trimmed body, signatures and quoted replies stripped).
- Secrets come from env vars (`NOTION_TOKEN`, `NOTION_DATABASE_ID`, `ANTHROPIC_API_KEY`, `WEBHOOK_SECRET`). Never hard-code, print, or commit them.
- The webhook rejects requests without a valid `WEBHOOK_SECRET`.
- Sample emails use fake domains (`*.example`) only.
