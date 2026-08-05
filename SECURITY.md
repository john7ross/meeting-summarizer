# Security policy

**English** · [Русский](SECURITY.ru.md)

## Reporting a vulnerability

**Please do not open a public issue for a vulnerability.** An issue is visible to
everyone the moment it is created, including the details of how to exploit it.

Use GitHub's private channel instead: the **Security** tab → **Report a
vulnerability**. It creates a private advisory visible only to the maintainers.

> If that link is not present on the repository, private vulnerability reporting
> has not been enabled yet (it is **off by default** on GitHub, including for
> repositories created before the account-level switch was turned on). It is
> enabled under *Settings → Code security → Private vulnerability reporting*.

Expect a first response within about a week. There is no bug-bounty programme.

## What NOT to attach

The point of a report is the bug, not your data. Please **never** include:

- real API keys or tokens (OpenAI/Anthropic/Google/xAI/Qwen/Mistral/DeepSeek,
  HuggingFace, `JWT_SECRET_KEY`, the contents of `config/.jwt_secret`);
- your Google Apps Script webhook URL — anyone holding it can write to your sheet;
- `config/server.db`, `config/history.json`, or any file from `transcripts/`,
  `uploads/`, `recordings/`, `rag_knowledge_base/` — these contain real meeting
  content;
- full logs without reading them first: they can carry file paths, meeting names
  and account names.

Redact, or describe the shape of the data instead of pasting it. If a
reproduction genuinely needs a recording, say so and we will agree on a channel.

## Scope

This project runs **on your own machine or your own server**. There is no service
operated by the maintainers, so there is nothing hosted to test against — please
do not attack anyone else's installation.

In scope: authentication and session handling in the web cabinet, isolation
between accounts, path traversal in upload/export/download, command injection
into the transcription or AI subprocesses, the URL-intake host policy, secrets
that leak into logs or exports, and privilege checks on the admin endpoints.

Out of scope: anything requiring physical or administrator access to the machine
running the app; denial of service by feeding it enormous files; vulnerabilities
in third-party components (report those upstream — see
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)).

## Operational notes for anyone deploying the web cabinet

- Set `JWT_SECRET_KEY` explicitly in production. Without it the server generates a
  random secret into `config/.jwt_secret` at startup, which means every restart
  invalidates existing sessions and the file must be protected like a password.
- The **first account registered on a fresh installation becomes the
  administrator.** Register it yourself before exposing the port to anyone else.
- Put it behind TLS. `TRUSTED_PROXIES` controls which peers may set
  `X-Forwarded-*`; leaving it at the default trusts only the local proxy.
- See [server/DEPLOYMENT.md](server/DEPLOYMENT.md) for the full deployment guide.
