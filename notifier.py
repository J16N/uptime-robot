import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from pydantic import HttpUrl

from config import Settings

site_down_html_template = """\
<html>
<head>
    <style>
        :root {
            font-size: 14px;
        }

        p {
            font-size: 0.9rem;
        }

        pre.codeblock {
            background-color: #705713;
            font-size: 0.7rem;
            padding: 1rem;
            border-radius: 0.5rem;
        }

        code {
            color: #d9d9db;
        }

        pre.codeblock a {
            color: #6dcbd6 !important;
        }
    </style>
</head>
<body>
    <p>Dear {name},</p>
    <p>The <a href='{link}'>site</a> is unavailable, please investigate!</p>
    <p>{stacktrace}</p>
    <p>
        Have a nice day!<br>
        &nbsp;&nbsp;&nbsp;&nbsp; — Uptime Robot
    </p>
</body>
</html>
"""

site_down_text_template = """\
Dear {name},
The site ({link}) is down, please investigate!
{stacktrace}
Have a nice day!
    — Uptime Robot
"""

site_up_html_template = """\
<html>
<head>
    <style>
        :root {
            font-size: 14px;
        }

        p {
            font-size: 0.9rem;
        }

        pre.codeblock {
            background-color: #705713;
            font-size: 0.7rem;
            padding: 1rem;
            border-radius: 0.5rem;
        }

        code {
            color: #d9d9db;
        }

        pre.codeblock a {
            color: #6dcbd6 !important;
        }
    </style>
</head>
<body>
    <p>Dear {name},</p>
    <p>The <a href='{link}'>site</a> is up again!</p>
    <p>{stacktrace}</p>
    <p>
        Have a nice day!<br>
        &nbsp;&nbsp;&nbsp;&nbsp; — Uptime Robot
    </p>
</body>
</html>
"""

site_up_text_template = """\
Dear {name},
The site ({link}) is up again!
{stacktrace}
Have a nice day!
    — Uptime Robot
"""

log = logging.getLogger(__name__)


class Mailer:
    """
    A class used to handle sending emails using SMTP over SSL. This class provides
    methods to get an SMTP client and close the client connection. It uses asynchronous
    methods to perform login and quit operations in a non-blocking manner.

    Attributes
    ----------
    _client: :class:`Optional[smtplib.SMTP_SSL])`
        A private class attribute to store the SMTP client instance.
    """

    _client: Optional[smtplib.SMTP_SSL] = None

    @classmethod
    async def get_client(cls, settings: Settings) -> smtplib.SMTP_SSL:
        if cls._client is None:
            cls._client = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT)
            await asyncio.to_thread(
                cls._client.login, settings.SMTP_USERNAME, settings.SMTP_PASSWORD
            )
        return cls._client

    @classmethod
    async def close_client(cls):
        if cls._client is not None:
            await asyncio.to_thread(cls._client.quit)
            cls._client = None


async def send_email(
    message: MIMEMultipart,
    settings: Settings,
    auto_close_client: bool,
):
    """
    Sends an email using the provided settings and message.

    Params
    ------
    message: :class:`email.mime.multipart.MIMEMultipart`
        The email message to be sent.
    settings: :class:`Settings`
        The settings object containing email configuration.
    auto_close_client: :class:`bool`
        Whether to automatically close the email client after sending.

    Raises
    ------
    smtplib.SMTPException
        If sending the email fails after the specified number of retries.

    Returns
    -------
    None
    """

    client = await Mailer.get_client(settings)
    retries = settings.SEND_EMAIL_RETRIES

    for retry in range(retries):
        try:
            await asyncio.to_thread(
                client.sendmail,
                settings.MAIL_FROM,
                settings.RECIPIENTS,
                message.as_string(),
            )
            break

        except smtplib.SMTPException as e:
            log.error(f"Failed to send email: {e}")
            await asyncio.sleep(2**retry)
    else:
        log.error(f"Failed to send email after {retries} retries")

    if auto_close_client:
        await Mailer.close_client()


async def notify(
    link: HttpUrl,
    settings: Settings,
    site_down: bool,
    *,
    auto_close: bool = False,
    stacktrace: str = "",
):
    """
    Sends a notification email when a site is down.

    Params
    ------
    link: :class:`pydantic.HttpUrl`
        The URL of the site that is down.
    settings: :class:`Settings`
        The settings object containing configuration such as email recipients and sender.
    site_down: :class:`bool`
        Whether the site is down.
    auto_close: :class:`bool` :default:False
        Whether to automatically close the email connection after sending. Defaults to False.
    stacktrace: :class:`str` :default:""
        The stack trace to include in the email, if any. Defaults to an empty string.

    Returns
    -------
    None
    """

    if site_down:
        html = (
            site_down_html_template.replace("{name}", settings.NAME)
            .replace("{link}", str(link))
            .replace(
                "{stacktrace}",
                (
                    f"<pre class='codeblock'><code>{stacktrace}</code></pre>"
                    if stacktrace
                    else ""
                ),
            )
        )
        text = (
            site_down_text_template.replace("{name}", settings.NAME)
            .replace("{link}", str(link))
            .replace("{stacktrace}", f"\n{stacktrace}\n" if stacktrace else "")
        )
    else:
        html = (
            site_up_html_template.replace("{name}", settings.NAME)
            .replace("{link}", str(link))
            .replace(
                "{stacktrace}",
                (
                    f"<pre class='codeblock'><code>{stacktrace}</code></pre>"
                    if stacktrace
                    else ""
                ),
            )
        )
        text = (
            site_up_text_template.replace("{name}", settings.NAME)
            .replace("{link}", str(link))
            .replace("{stacktrace}", f"\n{stacktrace}\n" if stacktrace else "")
        )

    if stacktrace:
        html = html.replace("{stacktrace}", f"<pre><code>{stacktrace}</code></pre>")

    message = MIMEMultipart("alternative")
    message["Subject"] = f"{'Site Down' if site_down else 'Site Up'} - Uptime Robot"
    message["From"] = settings.MAIL_FROM
    message["To"] = ", ".join(settings.RECIPIENTS)
    message.attach(MIMEText(text, "plain"))
    message.attach(MIMEText(html, "html"))

    await send_email(message, settings, auto_close)


if __name__ == "__main__":
    settings = Settings()
    asyncio.run(
        notify(HttpUrl("https://example.com"), settings, False, auto_close=True)
    )
