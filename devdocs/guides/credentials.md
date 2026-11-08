# DT+ API keys

One model for the desktop app. The CLI and tools are environment only.

## Sources

| Source | Written by | Cleared by |
|---|---|---|
| Saved keys (Windows Credential Manager, service `DTFlow`) | Connection, Save key | Connection, Remove |
| `DRAWTHINGS_API_KEY` in the process environment | you, outside the app | unset it and restart DTFlow |

The environment is read when the process starts. A value set with `setx`, in System Properties or in a PowerShell profile only reaches a DTFlow launched afterwards from a session that has it.

## Which key is used

1. The active saved key. Save key makes the new key active; the Active key list switches between keys.
2. `DRAWTHINGS_API_KEY`, when no key is saved or when "Environment key" is selected in the list.
3. Otherwise "No API key available".

An empty or invalid selected key fails clearly and never falls back to the other source. The CLI (`dtflow check -b cloud` and the other commands) uses only `DRAWTHINGS_API_KEY`, so scripts stay reproducible.

## What the Connection card shows

- Active key list: every saved key (by name) plus the environment key with its four-character hint.
- Status line: which key is used and where it comes from; when the environment variable is also set but not used, it says so.
- Name (optional) when saving. Without a name a key is called `Key ABCD••••` (the four characters after `dk_`), or `Key` plus four characters of a one-way digest for other key shapes. A name never contains a key.
- Remove removes the selected saved key only. The environment key cannot be removed in the app.

Saved secrets live only in Credential Manager, one account per key. The credential-free index (`<state>/cloud_keys.json`) holds ids, optional names and the active choice.

## Daily stats

Counts are attributed to a key by an opaque digest recorded with each saved attempt. Every available key has a row for the day, even at zero, under the same name the Connection card uses. Images saved before key tracking began are shown in one separate line, since no key can be assigned to them.
