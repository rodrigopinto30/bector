# 0027. Choosing the project from the desktop app

## Context

The desktop app (ADR 0026) sets up Docker, the key and the services, but the project to fix still had to be copied to `workspace/` or named in `.env` by hand. Stage 2 of the app is to choose a project and fix from the window. It comes in steps, each one checked on its own. This one is choosing the folder. Running the tests and finding a fix from the app, and reviewing and applying a patch with the confirmation in the app's own window, come after it.

## Decision

- **A section of the menu, Project, with one job: which folder is the project.** It shows the folder with its full path, says what is worth knowing about it, and says whether the services have it.
- **The folder is chosen only with the system's folder dialog, opened by the main process.** The bridge function takes no argument, so the page cannot name a folder: it can only ask for the dialog, and only the path the dialog returns is used. A flawed or compromised page cannot ask to mount `/` or the home folder in the containers. A test sends paths from the page and checks that they are ignored.
- **The folder reaches the containers as `WORKSPACE_PATH`, in the environment of the process that starts them.** `docker-compose.yml` already had `${WORKSPACE_PATH:-./workspace}:/workspace`. Checked in an isolated Compose project: the variable of the process wins over `.env`, and without either, `./workspace` is used. Checked with a real container: a path with spaces, an ampersand and a dollar sign is mounted literally. `.env` is never written, as with the key.
- **What a folder must be.** An absolute path, an existing folder, and the real path with links resolved is what is kept, so one folder is never kept under two names. It is refused if it is:
  - the root of the disk, the whole home folder, or a folder of the system (`/bin`, `/boot`, `/dev`, `/etc`, `/lib*`, `/proc`, `/run`, `/sbin`, `/sys`, `/usr`), also behind a link;
  - not readable and writable by this user, because a fix is written into it;
  - a path with a colon, which Compose's volume syntax cannot take.

  It is only commented on, never refused, when it is empty or has no `.py` file, `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, `pytest.ini`, `tox.ini` or `tests`, `test` or `src` folder at its top, because the system works on Python projects only.
- **The choice is kept in a file of the app, checked again every time.** `project.json` in the app's data folder, mode 600, written in one step. A folder can be moved or deleted after it was chosen: the card then says why it cannot be used, and the app refuses to start the services with it instead of silently using the default folder. A damaged file counts as no choice.
- **The app asks Docker which folder the cli container has.** It reads the mount of `/workspace` of that container, with `docker inspect`, and compares real paths with the folder that should be there: the chosen one, or the default of `.env`. A mismatch is told next to the folder and on the services card, and works like a new key: the button offers to restart, and starting recreates the services, also to go back to the default folder. When Docker does not say, nothing is claimed.
- **The app and the terminal can disagree.** The choice lives in the app. Starting the services from a terminal without `WORKSPACE_PATH` gives them the folder of `.env`, and the app notices it the next time it asks.
- **The Electron program moves to `desktop/.electron`.** Found while checking this step: raising the version to 1.2.0 changed `desktop/package-lock.json`, `./desktop.sh` reinstalled the packages with `npm ci`, and that deleted `node_modules` and the 270 MB program in it. It now lives outside, the tools ignore it, and `./desktop.sh run` refuses to start with a version other than the one the app asks for.

## Consequences

- Someone can point the system at a project anywhere on the disk without editing `.env`, and see whether the services use it.
- Changing the project recreates the containers, like changing the key.
- The index of a project (`healer index`) is per folder and is not made by the app yet.
- Not yet done: running the tests and finding a fix from the app, and applying a patch with the confirmation in the app's own window (the rule of ADR 0025 holds: never from the panel's page).
- Measured with the real app (Electron 45.3.2, Ubuntu with GNOME), with the folder dialog replaced in the test process by one that returns a chosen folder, and the rest real:
  - The section showed the default folder and said the services were working on it, from a real `docker inspect` of the running cli container.
  - A closed dialog changed nothing. The root of the disk, `/etc`, the home folder, a file, a folder that does not exist and a path with a colon were refused, each with its reason, and the choice did not change.
  - An empty folder and a folder with only a `package.json` were accepted with their comments; a Python project was accepted with none.
  - With a project chosen and the services on the default folder, the card said they still used another folder, and the services card offered **Restart and open the panel**. The restart was not pressed: it recreates the real containers.
  - The choice survived a reload of the page, the message went when changing section and with its close button, and **Use the default folder** removed the file.
- Not measured: the native dialog itself, and a restart of the real services with a chosen folder. The logic is covered by tests with fakes, and the mechanisms by the isolated Compose project and the real container above.
