# 0033. A key for this session only

## Context

The desktop app keeps the Anthropic key encrypted in the secret store of the system (ADR 0026), and refuses to keep it when there is no store that can protect it: it would be plain text. When the store is locked, or there is none, the key card said *"Your key cannot be saved safely here"*, and offered nothing but to check again. The app could not be given a key at all, so the services started without Claude, unless the key was in `.env`.

A locked store is not rare: on Linux it asks for a password, and the app already stops waiting for it after 15 seconds (ADR 0026). What was missing is a way to use a key that does not need the store.

## Decision

- **A key can be used for this session only.** **Use this session only** checks the key with Anthropic, in the same way and with the same rules as saving it (nothing is sent if it does not look like a key, the listing of models spends no tokens), and, if it is accepted, keeps it in the memory of the main process. It is never written to a file, and it is forgotten when the app closes or when **Stop using it** is pressed.
- **When the store cannot keep a key, this is the way.** The card says why, and the main button is **Check and use this session**. Enter does the same. The field is there in both cases, where before it was only there when the store worked.
- **It does not ask the secret store anything.** The handler checks the key and keeps it in memory, and does not call the vault, not even to tell its status. A locked store can make a call wait up to 15 seconds, which is exactly when this is wanted, so asking it would make the user wait for nothing. The page changes its own copy of the status.
- **The key of the session is the one the services get.** When the services are started, the key is the session's if there is one, and then the store is not asked at all, or the saved one otherwise. A key of the session is used instead of a saved one; the card says so. This is one function, `keyForServices`, with its own tests, because a wrong order would make the app wait for a locked store, or use the wrong key.
- **The page sees only its mask.** The status has the first and last characters of the key of the session (`sk-ant-…4321`), as it has for a saved one, and no function gives the key back. Reloading the page does not lose the key, because the main process holds it.
- **It travels as the saved key does.** To the services, through the environment of the one process that starts them, never in a file or a command line, and it is hidden from the output the app shows.
- **The card is honest about the services.** They keep the key in their environment until they are restarted without it, so closing the app does not take it out of containers that are running. The card says so, and forgetting the key marks the services as waiting for a restart.
- **The bridge has twenty-two functions and five events.** `keyUseOnce` and `keyEndSession` are new. Neither returns the key.

## Consequences

- Someone whose secret store is locked, or who has none, can use Claude from the app, without editing `.env`.
- Someone who does not want a key kept can give it for one session.
- A key of the session is lost when the app closes, so it has to be given again. That is the point.
- The services still hold it in their environment, where anyone who can run a command in the container can read it, until they restart. This is the same as with a saved key, and the restart is the way to remove it.
- Measured with the real app (Electron 45.3.2, Ubuntu with GNOME) started with `--password-store=basic`, which makes the secret store unavailable as it was for the person who built this, and the real services in an isolated Compose project with its own containers, volumes and ports (the real ones were not touched). The check with Anthropic was replaced in the test process by one that answers by key, so the keys used were invented and nothing was sent to Anthropic:
  - The card said that the key cannot be saved, why, and that a key can be used for this session; the main button was **Check and use this session**. A refused key was told as refused and nothing was kept. An accepted one, sent with Enter, showed *You are using a key for this session* with `sk-ant-…4321`, and the page held nothing of the key but its mask.
  - After restarting the services, the cli container had exactly that key, and the card said the system sees it.
  - The key was in no file of the app's data, in no source file and not in `.env`. During the whole restart, 79 samples of the processes of the computer found it in no command line.
  - Reloading the page kept the key, because the main process holds it. **Stop using it** forgot it, and after another restart the services had none. Closing the app and opening it again found no key, and none in any file.
  - 462 tests pass, and six changes that break a rule on purpose were each caught by a test: asking the secret store first, keeping a key that was refused, not forgetting the key, showing the whole key in its mask, losing the status of the session when the saved key is removed, and not checking the key at all.
- Found while checking, and not part of the app: compose fills `ANTHROPIC_API_KEY` from `.env` when the variable is not in the environment of the command, so a test project must define it, empty, or its containers get the real key. It was seen in time, and the containers were recreated without it before anything ran in them.
- Not measured: a secret store that is really locked and waits for a password, a real answer from Anthropic, and the services of the person who built this.
