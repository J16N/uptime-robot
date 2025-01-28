import asyncio
import logging
import os
import traceback
from pathlib import Path

import aiofiles
from aiohttp import ClientSession, ClientTimeout
from pydantic import HttpUrl, ValidationError
from watchfiles import awatch

from config import Settings
from notifier import Mailer, notify

logging.basicConfig(
    filename="uptime.log",
    encoding="utf-8",
    level=logging.INFO,
    format="[%(levelname)s] [%(asctime)s] %(message)s",
)
log = logging.getLogger(__name__)


async def send_request(
    url: HttpUrl,
    session: ClientSession,
    settings: Settings,
    retries: int,
    site_down: bool,
) -> tuple[int, bool]:
    """
    Send a HEAD request to the given URL and notify if the response status is not 200.
    If the request fails, notify the user and retry. Return the number of retries.

    Params
    ------
    url: :class:`pydantic.HttpUrl`
        The URL to monitor
    session: :class:`aiohttp.ClientSession`
        The aiohttp ClientSession object
    settings: :class:`config.Settings`
        The settings object
    retries: :class:`int`
        The number of retries
    site_down: :class:`bool`
        Whether the site is down

    Returns
    -------
    The number of retries and whether the site is down
    """

    try:
        async with session.head(str(url), allow_redirects=True) as response:
            if response.status == 200:
                if site_down:
                    site_down = False
                    await notify(HttpUrl(str(response.url)), settings, site_down)

                return (0, site_down)

            if site_down:
                return (retries, site_down)

            site_down = True
            log.info(f"Site '{response.url}' with response status '{response.status}'")
            await notify(HttpUrl(str(response.url)), settings, site_down)

    except Exception as e:
        log.error(f"Failed to monitor site '{url}': {e}")

        if not site_down:
            stacktrace = traceback.format_exc()
            await notify(url, settings, True, stacktrace=stacktrace)

        retries += 1
        site_down = True

    return (retries, site_down)


async def monitor_links(url: HttpUrl, settings: Settings):
    """
    Monitors the specified URL by sending periodic HTTP requests.

    Params
    ------
    url: :class:`pydantic.HttpUrl`
        The URL to be monitored
    settings: :class:`config.Settings`
        Configuration settings for the monitoring process, including request retries,
        timeout, and monitor interval

    Returns
    -------
    None
    """

    retries = 0
    site_down = False
    max_retries = settings.REQUEST_RETRIES
    timeout = ClientTimeout(total=settings.REQUEST_TIMEOUT)
    async with ClientSession(timeout=timeout) as session:
        while retries < max_retries:
            (retries, site_down) = await send_request(
                url, session, settings, retries, site_down
            )
            await asyncio.sleep(settings.MONITOR_INTERVAL)


async def spawn_site_monitors(
    file: Path,
    store: dict[str, asyncio.Task],
    settings: Settings,
):
    """
    Spawns site monitor tasks for URLs listed in the given file and manages the lifecycle
    of these tasks. This function reads URLs from the specified file, validates them, and
    creates asynchronous tasks to monitor each URL. It also handles the cancellation of
    tasks for URLs that are no longer present in the file.

    Params
    ------
    file: :class:`pathlib.Path`
        The path to the file containing the list of URLs to monitor
    store: :class:`dict[str, asyncio.Task]`
        A dictionary to store the active monitoring tasks, keyed by URL
    settings: :class:`config.Settings`
        Configuration settings for the monitoring tasks

    Raises
    ------
    ValidationError
        If a URL in the file is invalid
    """

    current_links = set(store.keys())

    async with aiofiles.open(file, "r") as f:
        async for line in f:
            line = line.strip()
            if not line or line in store:
                continue
            try:
                url = HttpUrl(line)
            except ValidationError:
                log.error(f"Invalid URL: {line}")
                continue
            task = asyncio.create_task(monitor_links(url, settings), name=line)
            store[line] = task
            current_links.discard(line)

    for link in current_links:
        task = store.pop(link)
        task.cancel()


async def monitor_file(file: Path):
    """
    Monitors a given file for changes and spawns site monitors accordingly.

    Params
    ------
    file: :class:`pathlib.Path`
        The path to the file to be monitored

    Returns
    -------
    None
    """

    store: dict[str, asyncio.Task] = {}
    settings = Settings()

    if file.exists():
        await spawn_site_monitors(file, store, settings)
    else:
        os.makedirs(file.parent, exist_ok=True)
        file.touch()

    async for _ in awatch(file):
        await spawn_site_monitors(file, store, settings)

    for task in store.values():
        try:
            await task
        except asyncio.CancelledError:
            print(f"Task '{task.get_name()}' was cancelled")
            pass


if __name__ == "__main__":
    try:
        asyncio.run(monitor_file(Path("./monitor.txt")))
    except KeyboardInterrupt:
        ...
    except Exception as e:
        print(e)
    finally:
        asyncio.run(Mailer.close_client())
