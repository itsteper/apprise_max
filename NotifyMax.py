# ~/.config/apprise/plugins/NotifyMax.py
#
# Apprise plugin for MAX Messenger (https://max.ru/)
#
# URL format:
#   max://BOT_TOKEN/chat_id:CHAT_ID
#   max://BOT_TOKEN/user_id:USER_ID

import re
import requests
from json import dumps, loads
from urllib.parse import quote

from ..common import NotifyFormat, NotifyType
from ..locale import gettext_lazy as _
from ..utils.parse import parse_list, validate_regex
from .base import NotifyBase


# Регулярка для target: "chat_id:123", "user_id:-456", или просто число
IS_TARGET_RE = re.compile(
    r"^(?:(?P<type>chat_id|user_id):)?(?P<idno>-?[0-9]{1,32})$",
    re.IGNORECASE,
)

# Символы, которые нужно экранировать в HTML-режиме
HTML_ESCAPE_MAP = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
)


class NotifyMax(NotifyBase):
    """
    Уведомления через мессенджер MAX (platform-api.max.ru).
    """

    service_name = "MAX Messenger"
    service_url = "https://max.ru/"

    secure_protocol = "max"
    protocol = "max"

    # КЛЮЧЕВОЕ: TEXT — чтобы Apprise не конвертировал в HTML
    # (&nbsp;, <br/> и т.п.). Сами передаём format="html" в MAX API.
    notify_format = NotifyFormat.TEXT

    # URL API
    notify_url = "https://platform-api.max.ru/messages"

    # Максимальная длина сообщения
    body_maxlen = 4000

    # Заголовок формируем сами (оборачиваем в <b>...</b>)
    title_maxlen = 200

    # Шаблоны URL
    templates = (
        "{schema}://{bot_token}",
        "{schema}://{bot_token}/{targets}",
    )

    # Описание токенов URL
    template_tokens = dict(
        NotifyBase.template_tokens,
        **{
            "bot_token": {
                "name": _("Bot Token"),
                "type": "string",
                "private": True,
                "required": True,
                "regex": (r"^(?P<key>[A-Za-z0-9_\-]{10,})$", "i"),
            },
            "target_user": {
                "name": _("Target Chat/User ID"),
                "type": "string",
                "map_to": "targets",
                "regex": (
                    r"^((chat_id|user_id):)?-?[0-9]{1,32}$",
                    "i",
                ),
            },
            "targets": {
                "name": _("Targets"),
                "type": "list:string",
            },
        },
    )

    # Query-параметры
    template_args = dict(
        NotifyBase.template_args,
        **{
            "to": {"alias_of": "targets"},
        },
    )

    def __init__(self, bot_token, targets, **kwargs):
        super().__init__(**kwargs)

        self.bot_token = validate_regex(
            bot_token,
            *self.template_tokens["bot_token"]["regex"],
            fmt="{key}",
        )
        if not self.bot_token:
            msg = f"The MAX Bot Token specified ({bot_token}) is invalid."
            self.logger.warning(msg)
            raise TypeError(msg)

        self.targets = []
        for target in parse_list(targets):
            results = IS_TARGET_RE.match(str(target))
            if not results:
                self.logger.warning(
                    f"Dropped invalid MAX target ({target}) specified."
                )
                continue
            self.targets.append(
                {
                    "type": (results.group("type") or "chat_id").lower(),
                    "id": int(results.group("idno")),
                }
            )

    @staticmethod
    def _escape_html(text):
        """Экранирует &, <, > — \\n оставляем нетронутыми."""
        if not text:
            return text
        for k, v in HTML_ESCAPE_MAP:
            text = text.replace(k, v)
        return text

    def send(self, body, title="", notify_type=NotifyType.INFO, **kwargs):
        """Отправка уведомления в MAX."""

        if not self.targets:
            self.logger.warning("There are no MAX targets to notify.")
            return False

        headers = {
            "Authorization": self.bot_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self.app_id,
        }

        # Экранируем HTML-спецсимволы, \n оставляем как есть
        safe_body = self._escape_html(body)
        safe_title = self._escape_html(title)

        # Жирный заголовок + тело
        if safe_title:
            text = f"<b>{safe_title}</b>\n\n{safe_body}"
        else:
            text = safe_body

        has_error = False
        for target in self.targets:
            if target["type"] == "user_id":
                url = f"{self.notify_url}?user_id={quote(str(target['id']), safe='')}"
            else:
                url = f"{self.notify_url}?chat_id={quote(str(target['id']), safe='')}"

            # В MAX всегда отправляем format="html" — он корректно
            # отрисует и <b>...</b>, и \n как переносы строк.
            payload = {
                "text": text,
                "format": "html",
            }

            self.throttle()
            self.logger.debug(f"MAX POST URL: {url}")
            self.logger.debug(f"MAX Payload: {payload}")

            try:
                r = requests.post(
                    url,
                    data=dumps(payload),
                    headers=headers,
                    verify=self.verify_certificate,
                    timeout=self.request_timeout,
                    allow_redirects=self.redirects,
                )

                self.logger.debug(f"MAX Response Status: {r.status_code}")
                self.logger.debug(f"MAX Response Body: {r.content!r}")

                if r.status_code not in (
                    requests.codes.ok,
                    requests.codes.created,
                ):
                    status_str = self.http_response_code_lookup(r.status_code)
                    try:
                        error_msg = loads(r.content).get("message", "unknown")
                    except (AttributeError, TypeError, ValueError):
                        error_msg = r.content.decode("utf-8", "replace")[:500]

                    self.logger.warning(
                        f"Failed to send MAX notification to "
                        f"{target['type']}:{target['id']}: "
                        f"{error_msg or status_str}, error={r.status_code}."
                    )
                    has_error = True
                    continue

            except requests.RequestException as e:
                self.logger.warning(
                    "A connection error occurred sending MAX notification."
                )
                self.logger.debug(f"Socket Exception: {e!s}")
                has_error = True
                continue

            self.logger.info(
                f"Sent MAX notification to {target['type']}:{target['id']}."
            )

        return not has_error

    @property
    def url_identifier(self):
        return (self.secure_protocol, self.bot_token)

    def url(self, privacy=False, *args, **kwargs):
        """Собираем URL обратно из объекта."""
        params = {}
        params.update(self.url_parameters(privacy=privacy, *args, **kwargs))

        targets = "/".join(f"{t['type']}:{t['id']}" for t in self.targets)

        return "{schema}://{token}/{targets}/?{params}".format(
            schema=self.secure_protocol,
            token=self.pprint(self.bot_token, privacy, safe=""),
            targets=targets,
            params=NotifyMax.urlencode(params),
        )

    @staticmethod
    def parse_url(url):
        """Разбор URL max://TOKEN[/chat_id:-123][/user_id:456]"""
        results = NotifyBase.parse_url(url, verify_host=False)
        if not results:
            return None

        bot_token = NotifyMax.unquote(results["host"])
        if not bot_token:
            return None

        targets = NotifyMax.split_path(results["fullpath"])

        if "to" in results["qsd"] and results["qsd"]["to"]:
            targets.extend(NotifyMax.parse_list(results["qsd"]["to"]))

        results["bot_token"] = bot_token
        results["targets"] = targets
        return results